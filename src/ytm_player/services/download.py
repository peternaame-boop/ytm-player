"""Background audio download service using yt-dlp."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from ytm_player.config.paths import SECURE_FILE_MODE
from ytm_player.config.settings import get_settings
from ytm_player.utils.formatting import VALID_VIDEO_ID

logger = logging.getLogger(__name__)

# Quality presets for downloading (prefer opus for smaller size).
_DOWNLOAD_FORMAT = "bestaudio[ext=webm]/bestaudio/best"


@dataclass
class DownloadResult:
    """Result of a single download attempt."""

    video_id: str
    success: bool
    file_path: Path | None = None
    error: str | None = None


class DownloadService:
    """Downloads audio files for offline playback using yt-dlp.

    Downloads are saved to the audio cache directory. Each download runs
    in a background thread to avoid blocking the event loop.
    """

    def __init__(self, download_dir: Path | None = None) -> None:
        settings = get_settings()
        self._download_dir = download_dir or settings.cache_dir
        self._active: set[str] = set()

    def _ensure_dir(self) -> None:
        self._download_dir.mkdir(parents=True, exist_ok=True)

    def _build_opts(self, output_path: str) -> dict:
        return {
            "format": _DOWNLOAD_FORMAT,
            "outtmpl": output_path,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "extract_flat": False,
            "writethumbnail": False,
            "writeinfojson": False,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "opus",
                    "preferredquality": "128",
                }
            ],
        }

    def _download_sync(self, video_id: str) -> DownloadResult:
        """Synchronous download (runs in a thread)."""
        import yt_dlp

        if not VALID_VIDEO_ID.match(video_id):
            return DownloadResult(video_id=video_id, success=False, error="Invalid video ID")

        self._ensure_dir()
        output_template = str(self._download_dir / f"{video_id}.%(ext)s")
        url = f"https://music.youtube.com/watch?v={video_id}"

        try:
            opts = self._build_opts(output_template)
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])

            # Find the downloaded file (extension may vary).
            for ext in ("opus", "webm", "m4a", "mp3", "ogg"):
                path = self._download_dir / f"{video_id}.{ext}"
                if path.exists():
                    os.chmod(path, SECURE_FILE_MODE)
                    return DownloadResult(video_id=video_id, success=True, file_path=path)

            return DownloadResult(
                video_id=video_id,
                success=False,
                error="Download completed but file not found",
            )

        except Exception as exc:
            logger.warning("Download failed for %s: %s", video_id, exc)
            return DownloadResult(video_id=video_id, success=False, error=str(exc))

    async def download(self, video_id: str) -> DownloadResult:
        """Download a single track asynchronously."""
        if video_id in self._active:
            return DownloadResult(video_id=video_id, success=False, error="Already downloading")

        self._active.add(video_id)
        try:
            return await asyncio.to_thread(self._download_sync, video_id)
        finally:
            self._active.discard(video_id)

    async def download_multiple(
        self,
        tracks: list[dict],
        on_progress: asyncio.Future | None = None,
    ) -> list[DownloadResult]:
        """Download multiple tracks sequentially, reporting progress.

        Calls on_progress callback with (completed, total) after each track.
        """
        results: list[DownloadResult] = []

        for i, track in enumerate(tracks):
            video_id = track.get("video_id", "")
            if not video_id:
                results.append(DownloadResult(video_id="", success=False, error="No video ID"))
                continue

            # Skip if already downloaded.
            if self.is_downloaded(video_id):
                results.append(
                    DownloadResult(
                        video_id=video_id, success=True, file_path=self.get_path(video_id)
                    )
                )
                continue

            result = await self.download(video_id)
            results.append(result)

        return results

    def is_downloaded(self, video_id: str) -> bool:
        """Check if a track has been downloaded."""
        return self.get_path(video_id) is not None

    def get_path(self, video_id: str) -> Path | None:
        """Return the path to a downloaded file, or None."""
        for ext in ("opus", "webm", "m4a", "mp3", "ogg"):
            path = self._download_dir / f"{video_id}.{ext}"
            if path.exists():
                return path
        return None

    @property
    def active_count(self) -> int:
        return len(self._active)
