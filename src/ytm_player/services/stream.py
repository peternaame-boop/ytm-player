"""Stream URL resolution using yt-dlp."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass

from ytm_player.utils.formatting import VALID_VIDEO_ID

logger = logging.getLogger(__name__)

# Cache resolved URLs for 5 hours (YouTube URLs typically expire after ~6 hours).
_CACHE_TTL_SECONDS = 5 * 60 * 60

# Maximum number of entries to keep in cache.  When exceeded, the oldest
# entries are evicted regardless of TTL.
_CACHE_MAX_SIZE = 128

# Quality presets mapping to yt-dlp format strings.
QUALITY_FORMATS: dict[str, str] = {
    "high": "bestaudio/best",
    "medium": "bestaudio[abr<=128]/bestaudio/best",
    "low": "bestaudio[abr<=64]/bestaudio/best",
}


@dataclass(frozen=True, slots=True)
class StreamInfo:
    """Resolved stream information for a YouTube Music track."""

    url: str
    video_id: str
    format: str  # e.g., "opus", "m4a"
    bitrate: int  # kbps
    duration: int  # seconds
    expires_at: float  # unix timestamp
    thumbnail_url: str | None = None


class StreamResolver:
    """Resolves YouTube Music video IDs to direct audio stream URLs.

    Uses the yt-dlp Python API to extract stream information without
    downloading. Caches results in memory with automatic expiry.
    """

    def __init__(self, quality: str = "high") -> None:
        self._quality = quality
        self._cache: dict[str, StreamInfo] = {}
        self._cache_lock = threading.Lock()
        self._pending: dict[str, asyncio.Future[StreamInfo | None]] = {}

    @property
    def quality(self) -> str:
        return self._quality

    @quality.setter
    def quality(self, value: str) -> None:
        if value not in QUALITY_FORMATS:
            raise ValueError(f"Unknown quality '{value}'. Choose from: {list(QUALITY_FORMATS)}")
        self._quality = value

    def _build_ydl_opts(self) -> dict:
        """Build yt-dlp options for audio extraction."""
        return {
            "format": QUALITY_FORMATS.get(self._quality, QUALITY_FORMATS["high"]),
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "noplaylist": True,
            # Skip video-related processing.
            "skip_download": True,
            # Avoid writing any files to disk.
            "writeinfojson": False,
            "writethumbnail": False,
        }

    def _resolve_sync(self, video_id: str) -> StreamInfo | None:
        """Synchronous stream resolution (runs in a thread) with retry."""
        import yt_dlp  # Lazy import: ~200-400ms, only needed on first stream resolution

        if not VALID_VIDEO_ID.match(video_id):
            logger.warning("Invalid video_id rejected: %r", video_id)
            return None
        url = f"https://music.youtube.com/watch?v={video_id}"
        delays = [0, 1.0, 2.0]  # initial attempt + 2 retries

        for attempt, delay in enumerate(delays):
            if delay > 0:
                time.sleep(delay)
            info = self._try_resolve(url, video_id, attempt)
            if info is not None:
                return info
        return None

    def _try_resolve(self, url: str, video_id: str, attempt: int) -> StreamInfo | None:
        """Single resolution attempt."""
        import yt_dlp  # Lazy import: needed for YoutubeDL and exception types

        try:
            ydl_opts = self._build_ydl_opts()
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            if info is None:
                logger.error("yt-dlp returned no info for video_id=%s", video_id)
                return None

            stream_url: str = info.get("url", "")
            if not stream_url:
                # Some formats nest the URL under requested_formats.
                formats = info.get("requested_formats") or []
                for fmt in formats:
                    if fmt.get("vcodec") == "none" or fmt.get("acodec") != "none":
                        stream_url = fmt.get("url", "")
                        break

            if not stream_url:
                logger.error("No stream URL found for video_id=%s", video_id)
                return None

            # Determine audio format and bitrate from the info dict.
            acodec = info.get("acodec", "unknown")
            audio_ext = info.get("audio_ext") or info.get("ext", "unknown")
            abr = int(info.get("abr") or info.get("tbr") or 0)
            duration = int(info.get("duration") or 0)
            thumbnail = info.get("thumbnail")

            # Pick a readable format name.
            fmt_name = acodec if acodec != "none" else audio_ext

            expires_at = time.time() + _CACHE_TTL_SECONDS

            return StreamInfo(
                url=stream_url,
                video_id=video_id,
                format=fmt_name,
                bitrate=abr,
                duration=duration,
                expires_at=expires_at,
                thumbnail_url=thumbnail,
            )

        except yt_dlp.utils.DownloadError as exc:
            logger.warning(
                "yt-dlp download error for video_id=%s (attempt %d): %s",
                video_id, attempt + 1, exc,
            )
            return None
        except Exception:
            logger.warning(
                "Unexpected error resolving stream for video_id=%s (attempt %d)",
                video_id, attempt + 1, exc_info=True,
            )
            return None

    def _get_cached(self, video_id: str) -> StreamInfo | None:
        """Return cached StreamInfo if it exists and hasn't expired."""
        with self._cache_lock:
            cached = self._cache.get(video_id)
            if cached is None:
                return None
            if time.time() >= cached.expires_at:
                del self._cache[video_id]
                return None
            return cached

    def _put_cache(self, info: StreamInfo) -> None:
        """Store a StreamInfo in the cache, evicting stale/excess entries."""
        with self._cache_lock:
            self._cache[info.video_id] = info

            # Prune expired entries on every write to prevent unbounded growth.
            now = time.time()
            expired = [vid for vid, si in self._cache.items() if now >= si.expires_at]
            for vid in expired:
                del self._cache[vid]

            # If still over the cap, evict the oldest entries by expires_at.
            if len(self._cache) > _CACHE_MAX_SIZE:
                sorted_ids = sorted(
                    self._cache, key=lambda vid: self._cache[vid].expires_at
                )
                excess = len(self._cache) - _CACHE_MAX_SIZE
                for vid in sorted_ids[:excess]:
                    del self._cache[vid]

    def resolve_sync(self, video_id: str) -> StreamInfo | None:
        """Resolve a video ID to a StreamInfo, using cache when possible.

        This is the synchronous version. Prefer `resolve()` in async code.
        """
        cached = self._get_cached(video_id)
        if cached is not None:
            logger.debug("Cache hit for video_id=%s", video_id)
            return cached

        logger.debug("Cache miss for video_id=%s, resolving via yt-dlp", video_id)
        info = self._resolve_sync(video_id)
        if info is not None:
            self._put_cache(info)
        return info

    def is_expired(self, video_id: str) -> bool:
        """Check if a cached stream URL has expired or will expire soon."""
        with self._cache_lock:
            cached = self._cache.get(video_id)
            if cached is None:
                return True
            # Consider expired if within 5 minutes of expiry (buffer for playback).
            return time.time() >= (cached.expires_at - 300)

    async def resolve(self, video_id: str) -> StreamInfo | None:
        """Resolve a video ID to a StreamInfo asynchronously.

        Runs the synchronous yt-dlp extraction in a thread to avoid
        blocking the event loop. Deduplicates concurrent requests for
        the same video_id.
        """
        cached = self._get_cached(video_id)
        if cached is not None:
            # Re-resolve if the URL will expire within 5 minutes.
            if time.time() < (cached.expires_at - 300):
                logger.debug("Cache hit for video_id=%s", video_id)
                return cached
            logger.debug("Cache entry near-expired for video_id=%s, re-resolving", video_id)
            self.invalidate(video_id)

        # Deduplicate concurrent requests for the same video
        if video_id in self._pending:
            return await self._pending[video_id]

        logger.debug("Cache miss for video_id=%s, resolving via yt-dlp", video_id)
        future: asyncio.Future[StreamInfo | None] = asyncio.get_running_loop().create_future()
        self._pending[video_id] = future
        try:
            info = await asyncio.to_thread(self._resolve_sync, video_id)
            if info is not None:
                self._put_cache(info)
            future.set_result(info)
            return info
        except Exception as exc:
            future.set_exception(exc)
            raise
        finally:
            self._pending.pop(video_id, None)

    async def prefetch(self, video_id: str) -> None:
        """Resolve a video ID in the background without blocking the caller.

        Used to pre-cache the next track's stream URL so playback starts
        instantly when the user hits next or the current track ends.
        """
        if self._get_cached(video_id) is not None:
            return  # Already cached, nothing to do.
        if video_id in self._pending:
            return  # Already being resolved.
        try:
            await self.resolve(video_id)
        except Exception:
            logger.debug("Prefetch failed for video_id=%s", video_id, exc_info=True)

    @staticmethod
    def warm_import() -> None:
        """Import yt_dlp eagerly to avoid the 200-400ms cold-start penalty."""
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            logger.warning("yt-dlp is not installed")

    def invalidate(self, video_id: str) -> None:
        """Remove a specific video ID from the cache."""
        with self._cache_lock:
            self._cache.pop(video_id, None)

    def clear_cache(self) -> None:
        """Remove all entries from the cache."""
        with self._cache_lock:
            self._cache.clear()

    def prune_expired(self) -> int:
        """Remove expired entries from the cache. Returns number removed."""
        with self._cache_lock:
            now = time.time()
            expired = [vid for vid, info in self._cache.items() if now >= info.expires_at]
            for vid in expired:
                del self._cache[vid]
            return len(expired)
