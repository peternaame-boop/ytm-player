"""Background audio download service using yt-dlp."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ytm_player.config.paths import SECURE_FILE_MODE, secure_chmod
from ytm_player.config.settings import get_settings
from ytm_player.services.yt_dlp_options import apply_configured_yt_dlp_options
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
        self._active: dict[str, asyncio.Task[DownloadResult]] = {}

    def _ensure_dir(self) -> None:
        self._download_dir.mkdir(parents=True, exist_ok=True)

    def _build_opts(self, output_path: str) -> dict:
        yt_dlp_settings = get_settings().yt_dlp
        opts = {
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
        return apply_configured_yt_dlp_options(opts, yt_dlp_settings)

    def _download_sync(self, video_id: str) -> DownloadResult:
        """Synchronous download (runs in a thread)."""
        import yt_dlp

        if not VALID_VIDEO_ID.match(video_id):
            return DownloadResult(video_id=video_id, success=False, error="Invalid video ID")

        self._ensure_dir()
        url = f"https://music.youtube.com/watch?v={video_id}"

        try:
            # FFmpeg can leave its final extension behind on failure. Keep all
            # job output private until yt-dlp and its context finish successfully.
            # Staging on the same filesystem allows atomic publication below.
            with tempfile.TemporaryDirectory(
                prefix=f".{video_id}-", dir=self._download_dir
            ) as stage:
                output_template = str(Path(stage) / f"{video_id}.%(ext)s")
                opts = self._build_opts(output_template)
                with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore[arg-type]
                    status = ydl.download([url])
                if status != 0:
                    return DownloadResult(
                        video_id=video_id, success=False, error="yt-dlp reported a download failure"
                    )

                # Find the downloaded file (extension may vary).
                for ext in ("opus", "webm", "m4a", "mp3", "ogg"):
                    path = Path(stage) / f"{video_id}.{ext}"
                    if path.is_file():
                        secure_chmod(path, SECURE_FILE_MODE)
                        dest = self._download_dir / path.name
                        if os.path.lexists(dest):
                            return DownloadResult(
                                video_id=video_id,
                                success=False,
                                error="Download destination already exists",
                            )
                        # The active-ID owner excludes other writers in this service.
                        # This check/replace is not an external-writer no-clobber lock.
                        os.replace(path, dest)
                        return DownloadResult(video_id=video_id, success=True, file_path=dest)

            return DownloadResult(
                video_id=video_id,
                success=False,
                error="Download completed but file not found",
            )

        except Exception as exc:
            logger.warning("Download failed for %s: %s", video_id, exc)
            return DownloadResult(video_id=video_id, success=False, error=str(exc))

    async def download(self, video_id: str) -> DownloadResult:
        """Download or reuse a completed track, retaining ownership until its thread exits.

        Cancelling the caller does not stop yt-dlp. Shield the owned task so
        retries cannot start another writer while that thread is still running.
        """
        if not VALID_VIDEO_ID.match(video_id):
            return DownloadResult(video_id=video_id, success=False, error="Invalid video ID")
        if video_id in self._active:
            return DownloadResult(video_id=video_id, success=False, error="Already downloading")

        existing = self.get_path(video_id)
        if existing is not None:
            return DownloadResult(video_id=video_id, success=True, file_path=existing)

        task = asyncio.create_task(asyncio.to_thread(self._download_sync, video_id))
        self._active[video_id] = task
        task.add_done_callback(lambda completed: self._download_done(video_id, completed))
        return await asyncio.shield(task)

    def _download_done(self, video_id: str, task: asyncio.Task[DownloadResult]) -> None:
        """Release the writer and observe errors even when its caller was cancelled."""
        if self._active.get(video_id) is task:
            del self._active[video_id]
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                logger.error(
                    "Download worker failed for %s",
                    video_id,
                    exc_info=(type(error), error, error.__traceback__),
                )

    async def download_multiple(
        self,
        tracks: list[dict],
    ) -> list[DownloadResult]:
        """Download multiple tracks sequentially."""
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
        """Return a completed download, never a file its worker may still be writing."""
        if video_id in self._active:
            return None
        for ext in ("opus", "webm", "m4a", "mp3", "ogg"):
            path = self._download_dir / f"{video_id}.{ext}"
            if path.exists():
                return path
        return None

    @property
    def active_count(self) -> int:
        return len(self._active)
