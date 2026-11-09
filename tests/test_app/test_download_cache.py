"""Download completion and index repair through the real application handler."""

import asyncio
import sqlite3
import threading
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from tests.test_app.test_playback import _fresh_playback_host
from ytm_player.app._playback import PlaybackMixin
from ytm_player.services.cache import CacheManager
from ytm_player.services.download import DownloadResult, DownloadService

VID = "abcdefghijk"


@pytest.mark.parametrize("existing", [False, True])
async def test_download_handler_indexes_shared_directory(tmp_path, monkeypatch, existing):
    host = PlaybackMixin()
    host.notify = Mock()
    host.downloader = DownloadService(download_dir=tmp_path / "audio")
    host.cache = CacheManager(cache_dir=tmp_path / "audio", db_path=tmp_path / "cache.db")
    await host.cache.init()
    path = tmp_path / "audio" / f"{VID}.opus"

    def download(video_id):
        path.write_bytes(b"completed")
        return DownloadResult(video_id, True, path)

    worker = Mock(side_effect=download)
    monkeypatch.setattr(host.downloader, "_download_sync", worker)
    if existing:
        path.write_bytes(b"completed")
    try:
        await host._download_track({"video_id": VID, "title": "Song"})
        assert await host.cache.get(VID) == path
        assert path.read_bytes() == b"completed"
        assert worker.call_count == (0 if existing else 1)
        # Real playback consumes the indexed path without asking the resolver.
        playback = _fresh_playback_host()
        playback.cache = host.cache
        await playback.play_track({"video_id": VID, "title": "Song"})
        playback.stream_resolver.resolve.assert_not_awaited()
        assert playback.player.play.await_args.args[0] == str(path)
    finally:
        await host.cache.close()


async def test_index_failure_is_visible_and_retry_repairs_without_downloading(
    tmp_path, monkeypatch
):
    host = PlaybackMixin()
    host.notify = Mock()
    host.downloader = DownloadService(download_dir=tmp_path / "audio")
    host.cache = CacheManager(cache_dir=tmp_path / "audio", db_path=tmp_path / "cache.db")
    await host.cache.init()
    path = tmp_path / "audio" / f"{VID}.opus"
    path.write_bytes(b"completed")
    worker = Mock(side_effect=AssertionError("must not redownload completed file"))
    monkeypatch.setattr(host.downloader, "_download_sync", worker)
    index = host.cache._index
    monkeypatch.setattr(
        host.cache, "_index", AsyncMock(side_effect=sqlite3.OperationalError("locked"))
    )
    try:
        await host._download_track({"video_id": VID, "title": "Song"})
        assert "could not be indexed" in host.notify.call_args.args[0]
        assert host.notify.call_args.kwargs["severity"] == "warning"
        assert await host.cache.get(VID) is None
        assert path.read_bytes() == b"completed"
        monkeypatch.setattr(host.cache, "_index", index)
        await host._download_track({"video_id": VID, "title": "Song"})
        assert await host.cache.get(VID) == path
        worker.assert_not_called()
    finally:
        await host.cache.close()


async def test_cancelled_app_download_is_not_indexed_until_writer_finishes(tmp_path, monkeypatch):
    host = PlaybackMixin()
    host.notify = Mock()
    host.downloader = DownloadService(download_dir=tmp_path / "audio")
    host.cache = CacheManager(cache_dir=tmp_path / "audio", db_path=tmp_path / "cache.db")
    await host.cache.init()
    path = tmp_path / "audio" / f"{VID}.opus"
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    owned = []

    def download(video_id):
        path.write_bytes(b"partial")
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5), "test did not release download thread"
        path.write_bytes(b"completed")
        return DownloadResult(video_id, True, path)

    worker = Mock(side_effect=download)
    monkeypatch.setattr(host.downloader, "_download_sync", worker)
    track = {"video_id": VID, "title": "Song"}
    waiter = asyncio.create_task(host._download_track(track))
    try:
        await asyncio.wait_for(started.wait(), 3)
        owned = list(host.downloader._active.values())
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        await host._download_track(track)
        assert await host.cache.get(VID) is None
        assert "Already downloading" in host.notify.call_args.args[0]
        assert worker.call_count == 1
        release.set()
        await asyncio.wait_for(asyncio.gather(*owned), 3)
        await host._download_track(track)
        assert await host.cache.get(VID) == path
        assert path.read_bytes() == b"completed"
        assert worker.call_count == 1
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(waiter, *owned, return_exceptions=True), 3)
        await host.cache.close()


@pytest.mark.parametrize("cancel_waiter", [False, True])
async def test_failed_postprocessing_output_is_never_reused_on_retry(
    tmp_path, monkeypatch, cancel_waiter
):
    """Real service/application path: final-extension leftovers stay in staging."""
    host = PlaybackMixin()
    host.notify = Mock()
    audio_dir = tmp_path / "audio"
    host.downloader = DownloadService(download_dir=audio_dir)
    host.cache = CacheManager(cache_dir=audio_dir, db_path=tmp_path / "cache.db")
    await host.cache.init()
    public_path = audio_dir / f"{VID}.opus"
    unrelated = audio_dir / "unrelated.opus"
    unrelated.write_bytes(b"leave this alone")
    outputs = []
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    owned = []

    class FakeYDL:
        def __init__(self, opts):
            self.path = Path(opts["outtmpl"] % {"ext": "opus"})
            outputs.append(self.path)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def download(self, urls):
            assert urls == [f"https://music.youtube.com/watch?v={VID}"]
            if len(outputs) == 1:
                self.path.write_bytes(b"partial FFmpeg output")
                loop.call_soon_threadsafe(started.set)
                assert release.wait(5), "test did not release yt-dlp thread"
                raise RuntimeError("FFmpeg postprocessing failed")
            self.path.write_bytes(b"complete audio")
            return 0

    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYDL)
    track = {"video_id": VID, "title": "Song"}
    waiter = asyncio.create_task(host._download_track(track))
    try:
        await asyncio.wait_for(started.wait(), 3)
        owned = list(host.downloader._active.values())
        assert not public_path.exists()
        if cancel_waiter:
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        release.set()
        results = await asyncio.wait_for(asyncio.gather(*owned), 3)
        await asyncio.wait_for(asyncio.gather(waiter, return_exceptions=True), 3)
        assert not results[0].success
        assert "FFmpeg postprocessing failed" in results[0].error
        assert await host.cache.get(VID) is None
        assert host.downloader.get_path(VID) is None
        # A restarted service cannot discover failed staging output either.
        assert DownloadService(download_dir=audio_dir).get_path(VID) is None
        assert set(audio_dir.iterdir()) == {unrelated}

        await host._download_track(track)
        assert len(outputs) == 2, "retry must really download, not index partial bytes"
        assert outputs[0].parent != outputs[1].parent
        assert all(not output.parent.exists() for output in outputs)
        assert await host.cache.get(VID) == public_path
        assert public_path.read_bytes() == b"complete audio"
        assert unrelated.read_bytes() == b"leave this alone"
        assert set(audio_dir.iterdir()) == {unrelated, public_path}
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(waiter, *owned, return_exceptions=True), 3)
        await host.cache.close()


async def test_directory_failure_is_reported_and_releases_ownership(tmp_path, monkeypatch):
    """R1: a permission/directory failure is a failed download, not an exception in the worker."""
    host = PlaybackMixin()
    host.notify = Mock()
    host.downloader = DownloadService(download_dir=tmp_path / "audio")
    host.cache = CacheManager(cache_dir=tmp_path / "audio", db_path=tmp_path / "cache.db")
    await host.cache.init()
    monkeypatch.setattr(
        host.downloader,
        "_ensure_dir",
        Mock(side_effect=PermissionError(13, "Permission denied")),
    )
    try:
        await host._download_track({"video_id": VID, "title": "Song"})  # must not raise
        notices = [c.args[0] for c in host.notify.call_args_list]
        assert notices[0] == "Downloading: Song"
        assert notices[-1].startswith("Download failed: ")
        assert "Permission denied" in notices[-1]
        assert host.notify.call_args.kwargs["severity"] == "error"
        assert host.downloader.active_count == 0
        assert not host.downloader.is_downloading(VID)
        assert await host.cache.get(VID) is None
    finally:
        await host.cache.close()


@pytest.mark.parametrize("existing", [False, True])
async def test_oversized_download_is_reported_as_not_retained(tmp_path, monkeypatch, existing):
    """R2: bounded LRU eviction stays; the notice must not claim the file was kept."""
    host = PlaybackMixin()
    host.notify = Mock()
    audio = tmp_path / "audio"
    host.downloader = DownloadService(download_dir=audio)
    host.cache = CacheManager(cache_dir=audio, db_path=tmp_path / "cache.db", max_size_mb=1)
    await host.cache.init()
    path = audio / f"{VID}.opus"
    payload = b"x" * (2 * 1024 * 1024)

    def download(video_id):
        path.write_bytes(payload)
        return DownloadResult(video_id, True, path)

    worker = Mock(side_effect=download)
    monkeypatch.setattr(host.downloader, "_download_sync", worker)
    if existing:
        path.write_bytes(payload)
    try:
        await host._download_track({"video_id": VID, "title": "Song"})
        notices = [c.args[0] for c in host.notify.call_args_list]
        assert notices[-1] == "Not retained in cache: Song doesn't fit within the cache size limit."
        assert host.notify.call_args.kwargs["severity"] == "warning"
        assert "Downloaded: Song" not in notices
        assert "Already downloaded." not in notices
        assert worker.call_count == (0 if existing else 1)
        assert await host.cache.get(VID) is None
        assert not path.exists()
    finally:
        await host.cache.close()


async def test_second_press_while_downloading_gives_one_notice(tmp_path, monkeypatch):
    """R4: an active download yields one informational notice and keeps its writer."""
    host = PlaybackMixin()
    host.notify = Mock()
    host.downloader = DownloadService(download_dir=tmp_path / "audio")
    host.cache = CacheManager(cache_dir=tmp_path / "audio", db_path=tmp_path / "cache.db")
    await host.cache.init()
    path = tmp_path / "audio" / f"{VID}.opus"
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()

    def download(video_id):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5), "test did not release download thread"
        path.write_bytes(b"completed")
        return DownloadResult(video_id, True, path)

    worker = Mock(side_effect=download)
    monkeypatch.setattr(host.downloader, "_download_sync", worker)
    track = {"video_id": VID, "title": "Song"}
    first = asyncio.create_task(host._download_track(track))
    try:
        await asyncio.wait_for(started.wait(), 3)
        assert host.downloader.is_downloading(VID)
        before = host.notify.call_count

        await host._download_track(track)

        added = host.notify.call_args_list[before:]
        assert [c.args[0] for c in added] == ["Already downloading: Song"]
        assert "severity" not in added[0].kwargs
        assert host.downloader.active_count == 1
        assert worker.call_count == 1
    finally:
        release.set()
        await asyncio.wait_for(first, 3)
        await host.cache.close()
    assert not host.downloader.is_downloading(VID)
    assert host.notify.call_args.args[0] == "Downloaded: Song"
