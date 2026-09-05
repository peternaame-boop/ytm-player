"""Stream URL resolution using yt-dlp."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from ytm_player.config.settings import get_settings
from ytm_player.services.yt_dlp_options import (
    apply_configured_yt_dlp_options,
    normalize_cookiefile,
)
from ytm_player.utils.formatting import VALID_VIDEO_ID

logger = logging.getLogger(__name__)

# Cache resolved URLs for 5 hours (YouTube URLs typically expire after ~6 hours).
_CACHE_TTL_SECONDS = 5 * 60 * 60

# Maximum number of entries to keep in cache.  When exceeded, the oldest
# entries are evicted regardless of TTL.
_CACHE_MAX_SIZE = 128

# Treat a cached URL as stale this many seconds before its actual expiry so
# playback is never handed a URL that dies mid-stream.  Applied consistently
# by every read path (_get_cached, is_expired, resolve).
_EXPIRY_BUFFER_SECONDS = 300


def _is_stale(expires_at: float) -> bool:
    """True when a cached URL has passed, or is within the buffer of, expiry."""
    return time.time() >= expires_at - _EXPIRY_BUFFER_SECONDS


def _detect_stream_cookies() -> str | None:
    """Return the AuthManager-written stream cookiejar path if present, else None.

    AuthManager (services/auth.py) is the only component that ever decrypts
    browser cookies — during `ytm setup` or a validate()-triggered
    try_auto_refresh(). This function never triggers extraction itself; if
    the file doesn't exist (e.g. extraction failed, or extraction hasn't
    happened yet), resolution proceeds without cookies rather than
    decrypting independently.
    """
    from ytm_player.config.paths import STREAM_COOKIES_FILE

    return str(STREAM_COOKIES_FILE) if STREAM_COOKIES_FILE.exists() else None


def _session_cookiejar_signature() -> tuple[str, int, int] | None:
    """``(path, mtime_ns, size)`` of the stream cookiejar the resolver should be
    using right now, or None.

    None unless ``[yt_dlp] use_session_cookies`` is on (streaming is anonymous
    by default) and no explicit ``[yt_dlp] cookies_file`` is configured (that
    file is the user's own and goes through yt-dlp's ``cookiefile``), or when
    the jar file doesn't exist. Compared on every _get_ydl() call so a jar
    rewritten by ``ytm setup`` or a mid-session auto-refresh is picked up by
    the next resolve instead of the cached YoutubeDL instance keeping its
    stale in-memory copy.
    """
    settings = get_settings().yt_dlp
    if not settings.use_session_cookies or normalize_cookiefile(settings.cookies_file):
        return None
    path = _detect_stream_cookies()
    if path is None:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (path, st.st_mtime_ns, st.st_size)


class _YtDlpLogger:
    """Routes yt-dlp's own diagnostic messages into our logger.

    With quiet=True/no_warnings=True and no logger, yt-dlp discards its own
    warnings and errors entirely (report_warning/to_stderr only check
    params['logger'] before checking no_warnings/quiet) — so failures like
    "Signature solving failed" or "Skipping client ... since it does not
    support cookies" never reached ytm.log. Mirrors the log_handler pattern
    already used for mpv in player.py.
    """

    def debug(self, message: str) -> None:
        # yt-dlp routes both normal progress lines ("Downloading webpage")
        # and its own "[debug] ..."-prefixed messages through here.
        logger.debug("yt-dlp: %s", message)

    def info(self, message: str) -> None:
        # A default logger doesn't implement to_screen(), so without this
        # method, yt-dlp's own info-level messages during extract_info()
        # would be silently dropped rather than surfacing in ytm.log.
        logger.info("yt-dlp: %s", message)

    def warning(self, message: str, **_kwargs: object) -> None:
        logger.warning("yt-dlp: %s", message)

    def error(self, message: str) -> None:
        logger.error("yt-dlp: %s", message)


# bestaudio first (real audio-only DASH formats when the resolve is
# authenticated), then a combined audio+video format as the fallback for
# clients that expose no audio-only stream. mpv runs with video=False, so
# the video track of a combined format is never rendered.
_COMBINED_FALLBACK = "best[vcodec!=none][acodec!=none]"

QUALITY_FORMATS = {
    "high": f"bestaudio/{_COMBINED_FALLBACK}",
    "medium": f"bestaudio[abr<=128]/bestaudio/{_COMBINED_FALLBACK}",
    "low": f"bestaudio[abr<=64]/bestaudio/{_COMBINED_FALLBACK}",
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
        # yt_dlp.YoutubeDL — typed Any because yt-dlp ships no stubs.
        self._ydl: Any | None = None
        self._ydl_lock = threading.Lock()
        # Count of extract_info() calls currently running against self._ydl,
        # on background threads — guards _reset_ydl() against closing an
        # instance a resolve is still actively using. See _reset_ydl.
        self._active_resolves = 0
        # Bumped by _reset_ydl(). _get_ydl() snapshots this before its
        # unlocked opts build and re-checks it after — if it changed, a
        # reset landed while the build was in flight and the just-built
        # opts already have stale settings baked in, so the build is
        # retried instead of caching a stale instance.
        self._ydl_generation = 0
        # Signature of the stream cookiejar file self._ydl was built against
        # (see _session_cookiejar_signature); None when none was loaded.
        self._ydl_cookiejar_sig: tuple[str, int, int] | None = None
        # YoutubeDL is not thread-safe; a play resolve and a prefetch can run
        # on separate threads against the shared instance, so extract_info()
        # calls are serialized. Only the network call is held under it.
        self._extract_lock = threading.Lock()
        # Bumped by clear_cache(). A resolve that started before the clear
        # must not put its (pre-clear) result back into the emptied cache.
        self._cache_generation = 0

    @property
    def quality(self) -> str:
        return self._quality

    @quality.setter
    def quality(self, value: str) -> None:
        if value not in QUALITY_FORMATS:
            raise ValueError(f"Unknown quality '{value}'. Choose from: {list(QUALITY_FORMATS)}")
        if value != self._quality:
            self._quality = value
            self._reset_ydl()

    def _build_ydl_opts(self) -> dict:
        """Build yt-dlp options for audio extraction."""
        settings = get_settings().yt_dlp
        opts = {
            "format": QUALITY_FORMATS.get(self._quality, QUALITY_FORMATS["high"]),
            "quiet": True,
            "no_warnings": True,
            "logger": _YtDlpLogger(),
            "extract_flat": False,
            "noplaylist": True,
            # Skip video-related processing.
            "skip_download": True,
            # Avoid writing any files to disk.
            "writeinfojson": False,
            "writethumbnail": False,
            # Android client provides a non-PoT fallback path (legacy format 18)
            # that unblocks madeForKids content which the default web client refuses.
            "extractor_args": {"youtube": {"player_client": ["default", "android"]}},
        }
        return apply_configured_yt_dlp_options(opts, settings)

    def _build_ydl(self, opts: dict) -> tuple[Any, tuple[str, int, int] | None]:
        """Construct a YoutubeDL instance from *opts* and, when
        ``[yt_dlp] use_session_cookies`` is on, load the stream cookiejar into it.

        The jar is loaded into the instance's cookiejar rather than passed as
        ``cookiefile``: YoutubeDL.close() writes ``cookiefile`` back to disk
        from memory, so a discarded instance closing later (quality change,
        failure reset) would overwrite a jar that ``ytm setup`` or a session
        refresh had just replaced. Loaded this way the file is read-only to
        yt-dlp. A user-configured ``[yt_dlp] cookies_file`` still goes
        through ``cookiefile`` — that file is theirs, and write-back is
        yt-dlp's normal behaviour for it.

        Returns the instance and the jar signature it was built against.
        """
        import yt_dlp  # Lazy import

        # yt-dlp's _Params TypedDict is internal; the dict we build is a
        # plain options dict that yt-dlp accepts at runtime.
        ydl = yt_dlp.YoutubeDL(opts)  # type: ignore[arg-type]
        sig = _session_cookiejar_signature()
        if sig is not None:
            try:
                ydl.cookiejar.load(sig[0], ignore_discard=True, ignore_expires=True)
            except Exception:
                logger.warning("Could not load stream cookiejar %s", sig[0], exc_info=True)
        return ydl, sig

    def _get_ydl(self) -> Any:
        """Return a reusable YoutubeDL instance, creating it lazily, with
        _active_resolves already incremented on the caller's behalf.

        Incrementing _active_resolves here, still inside self._ydl_lock, at
        every return point closes a TOCTOU gap: with a separate, later
        increment, _reset_ydl() could observe _active_resolves == 0 for an
        instance a caller already held and was about to call extract_info()
        on, and close it out from under that in-flight call. Callers must
        decrement in a finally block (see _try_resolve).

        _build_ydl_opts() runs OUTSIDE self._ydl_lock, so a _reset_ydl()
        (clear_cache(), the quality setter) can land mid-build. If self._ydl
        is still None at that point there is nothing to reset and the reset
        would be silently lost — the in-flight build would then cache an
        instance with pre-reset settings. The generation check below
        catches that: a reset landing mid-build invalidates that build's
        result and the loop re-reads current settings.
        """
        while True:
            with self._ydl_lock:
                if self._ydl is not None:
                    if self._ydl_cookiejar_sig == _session_cookiejar_signature():
                        self._active_resolves += 1
                        return self._ydl
                    # The jar file changed since this instance loaded it
                    # (ytm setup / auto-refresh wrote a new one): drop the
                    # instance so the rebuild below loads the new cookies.
                    self._discard_ydl_locked()
                generation = self._ydl_generation

            opts = self._build_ydl_opts()

            with self._ydl_lock:
                if self._ydl is not None:
                    self._active_resolves += 1
                    return self._ydl
                if self._ydl_generation != generation:
                    continue  # reset landed mid-build; opts are stale, retry
                self._ydl, self._ydl_cookiejar_sig = self._build_ydl(opts)
                self._active_resolves += 1
                return self._ydl

    def _reset_ydl(self) -> None:
        """Discard the cached YoutubeDL instance, closing it only if safe.

        clear_cache() (and therefore this) can be triggered from the UI
        thread while a background resolve (a prefetch or an in-flight play)
        is still mid extract_info() on the SAME instance. YoutubeDL.close()
        tears down the shared request director's connection pool; closing
        it out from under a live request left that request hanging until an
        underlying socket timeout gave up (a ~30s stall). Detaching the
        reference is enough: the next _get_ydl() builds a fresh instance,
        the in-flight call keeps its own local reference to the old one and
        finishes normally, and that instance's pool is cleaned up by garbage
        collection once nothing references it.

        Always bumps _ydl_generation, even when self._ydl is already None —
        that's exactly the case that used to lose the reset silently. See
        _get_ydl for the other half.
        """
        with self._ydl_lock:
            self._ydl_generation += 1
            self._discard_ydl_locked()

    def _discard_ydl_locked(self) -> None:
        """Detach self._ydl (caller holds _ydl_lock), closing it only when no
        resolve is still using it — see _reset_ydl."""
        ydl, self._ydl = self._ydl, None
        self._ydl_cookiejar_sig = None
        if ydl is None or self._active_resolves > 0:
            return
        try:
            ydl.close()
        except Exception:
            pass

    def _resolve_sync(self, video_id: str) -> StreamInfo | None:
        """Synchronous stream resolution (runs in a thread) with retry."""

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
        import yt_dlp  # Lazy import: needed for exception types

        try:
            ydl = self._get_ydl()  # increments _active_resolves; see _get_ydl's docstring
            try:
                with self._extract_lock:
                    info = ydl.extract_info(url, download=False)
            finally:
                with self._ydl_lock:
                    self._active_resolves -= 1

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

        except yt_dlp.utils.DownloadError as exc:  # type: ignore[attr-defined]
            hint = ""
            if _session_cookiejar_signature() is not None and re.search(
                r"sign in|login_required|confirm you", str(exc), re.I
            ):
                hint = " (stream cookiejar may be stale/revoked — try `ytm setup` to refresh it)"
            logger.warning(
                "yt-dlp download error for video_id=%s (attempt %d): %s%s",
                video_id,
                attempt + 1,
                exc,
                hint,
            )
            return None
        except Exception:
            logger.warning(
                "Unexpected error resolving stream for video_id=%s (attempt %d)",
                video_id,
                attempt + 1,
                exc_info=True,
            )
            return None

    def _get_cached(self, video_id: str) -> StreamInfo | None:
        """Return cached StreamInfo if it exists and isn't (near-)expired."""
        with self._cache_lock:
            cached = self._cache.get(video_id)
            if cached is None:
                return None
            if _is_stale(cached.expires_at):
                del self._cache[video_id]
                return None
            return cached

    def _put_cache(self, info: StreamInfo, generation: int | None = None) -> None:
        """Store a StreamInfo in the cache, evicting stale/excess entries.

        *generation* is the _cache_generation the caller read before it
        started resolving; if clear_cache() ran in between, the result is
        from the old resolver setup and is dropped instead of repopulating
        the cache that was just cleared.
        """
        with self._cache_lock:
            if generation is not None and generation != self._cache_generation:
                logger.debug(
                    "Dropping resolve result for %s: cache cleared mid-resolve", info.video_id
                )
                return
            self._cache[info.video_id] = info

            # Prune expired entries on every write to prevent unbounded growth.
            now = time.time()
            expired = [vid for vid, si in self._cache.items() if now >= si.expires_at]
            for vid in expired:
                del self._cache[vid]

            # If still over the cap, evict the oldest entries by expires_at.
            if len(self._cache) > _CACHE_MAX_SIZE:
                sorted_ids = sorted(self._cache, key=lambda vid: self._cache[vid].expires_at)
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
        generation = self._cache_generation
        info = self._resolve_sync(video_id)
        if info is not None:
            self._put_cache(info, generation)
        return info

    def is_expired(self, video_id: str) -> bool:
        """Check if a cached stream URL has expired or will expire soon."""
        with self._cache_lock:
            cached = self._cache.get(video_id)
            if cached is None:
                return True
            return _is_stale(cached.expires_at)

    async def resolve(self, video_id: str) -> StreamInfo | None:
        """Resolve a video ID to a StreamInfo asynchronously.

        Runs the synchronous yt-dlp extraction in a thread to avoid
        blocking the event loop. Deduplicates concurrent requests for
        the same video_id.
        """
        # _get_cached already applies the expiry buffer, so a hit here is fresh
        # enough to hand to playback; near-expired entries were evicted and fall
        # through to a fresh resolve below.
        cached = self._get_cached(video_id)
        if cached is not None:
            logger.debug("Cache hit for video_id=%s", video_id)
            return cached

        # Deduplicate concurrent requests for the same video.  Shield the
        # shared future: cancelling a waiter task would otherwise propagate
        # into the bare future (Task.cancel cancels what it awaits) and break
        # the owner's set_result with InvalidStateError.
        if video_id in self._pending:
            return await asyncio.shield(self._pending[video_id])

        logger.debug("Cache miss for video_id=%s, resolving via yt-dlp", video_id)
        future: asyncio.Future[StreamInfo | None] = asyncio.get_running_loop().create_future()
        self._pending[video_id] = future
        generation = self._cache_generation
        try:
            info = await asyncio.to_thread(self._resolve_sync, video_id)
            if info is not None:
                self._put_cache(info, generation)
            if not future.done():
                future.set_result(info)
            return info
        except Exception as exc:
            if not future.done():
                future.set_exception(exc)
            raise
        finally:
            self._pending.pop(video_id, None)
            if not future.done():
                # Owner was cancelled before the future resolved (a BaseException
                # such as CancelledError bypasses the except above).  Cancel the
                # shared future so concurrent waiters get CancelledError instead
                # of hanging forever on an orphaned, never-resolved future.
                future.cancel()

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
        """Remove all entries from the cache and reset the yt-dlp instance."""
        with self._cache_lock:
            self._cache.clear()
            self._cache_generation += 1
        self._reset_ydl()

    def prune_expired(self) -> int:
        """Remove expired entries from the cache. Returns number removed."""
        with self._cache_lock:
            now = time.time()
            expired = [vid for vid, info in self._cache.items() if now >= info.expires_at]
            for vid in expired:
                del self._cache[vid]
            return len(expired)
