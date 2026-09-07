"""Tests for DownloadService (filesystem logic only — no yt-dlp calls)."""

import asyncio
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest

from ytm_player.services.download import DownloadResult, DownloadService


class TestDownloadResult:
    def test_success_result(self):
        r = DownloadResult(video_id="abc", success=True, file_path=Path("/tmp/abc.opus"))
        assert r.success
        assert r.error is None

    def test_failure_result(self):
        r = DownloadResult(video_id="abc", success=False, error="Network error")
        assert not r.success
        assert r.error == "Network error"


class TestIsDownloaded:
    def test_not_downloaded(self, tmp_path):
        svc = DownloadService(download_dir=tmp_path)
        assert svc.is_downloaded("nonexistent") is False

    def test_downloaded_opus(self, tmp_path):
        (tmp_path / "vid123.opus").write_bytes(b"\x00")
        svc = DownloadService(download_dir=tmp_path)
        assert svc.is_downloaded("vid123") is True

    def test_downloaded_m4a(self, tmp_path):
        (tmp_path / "vid456.m4a").write_bytes(b"\x00")
        svc = DownloadService(download_dir=tmp_path)
        assert svc.is_downloaded("vid456") is True


class TestGetPath:
    def test_returns_none_when_missing(self, tmp_path):
        svc = DownloadService(download_dir=tmp_path)
        assert svc.get_path("nope") is None

    def test_returns_path_for_existing(self, tmp_path):
        expected = tmp_path / "vid789.webm"
        expected.write_bytes(b"\x00")
        svc = DownloadService(download_dir=tmp_path)
        assert svc.get_path("vid789") == expected

    def test_prefers_opus_over_webm(self, tmp_path):
        (tmp_path / "vid.opus").write_bytes(b"\x00")
        (tmp_path / "vid.webm").write_bytes(b"\x00")
        svc = DownloadService(download_dir=tmp_path)
        assert svc.get_path("vid") == tmp_path / "vid.opus"


class TestActiveCount:
    def test_initial_active_count(self, tmp_path):
        svc = DownloadService(download_dir=tmp_path)
        assert svc.active_count == 0


async def test_cancelled_waiter_keeps_writer_owned_until_completion(tmp_path, monkeypatch):
    svc = DownloadService(download_dir=tmp_path)
    started = asyncio.Event()
    finished = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    path = tmp_path / "abcdefghijk.opus"

    def download(video_id):
        path.write_bytes(b"partial")
        loop.call_soon_threadsafe(started.set)
        try:
            assert release.wait(5), "test did not release download thread"
            path.write_bytes(b"complete")
            return DownloadResult(video_id, True, path)
        finally:
            loop.call_soon_threadsafe(finished.set)

    worker = Mock(side_effect=download)
    monkeypatch.setattr(svc, "_download_sync", worker)
    waiter = asyncio.create_task(svc.download("abcdefghijk"))
    owned = []
    try:
        await asyncio.wait_for(started.wait(), 3)
        owned = list(svc._active.values())
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert svc.active_count == 1
        assert svc.get_path("abcdefghijk") is None
        retry = await svc.download("abcdefghijk")
        assert retry.error == "Already downloading"
        assert worker.call_count == 1
    finally:
        release.set()
        await asyncio.wait_for(finished.wait(), 3)
        await asyncio.gather(waiter, return_exceptions=True)
        await asyncio.wait_for(asyncio.gather(*owned, return_exceptions=True), 3)
    assert svc.active_count == 0
    assert svc.get_path("abcdefghijk") == path
    assert path.read_bytes() == b"complete"


async def test_existing_download_is_reused_without_a_writer(tmp_path, monkeypatch):
    path = tmp_path / "abcdefghijk.opus"
    path.write_bytes(b"completed")
    svc = DownloadService(download_dir=tmp_path)
    worker = Mock(side_effect=AssertionError("must not redownload"))
    monkeypatch.setattr(svc, "_download_sync", worker)
    result = await svc.download("abcdefghijk")
    assert result.success
    assert result.file_path == path
    worker.assert_not_called()


@pytest.mark.parametrize("cancel_waiter", [False, True])
@pytest.mark.parametrize("outcome", ["success", "failure", "exception"])
async def test_writer_lifecycle_and_retry(tmp_path, monkeypatch, caplog, cancel_waiter, outcome):
    """Ownership follows the thread, including failure after losing its caller."""
    svc = DownloadService(download_dir=tmp_path)
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    owned = []

    def download(video_id):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5), "test did not release download thread"
        if outcome == "exception":
            raise RuntimeError("worker exploded")
        return DownloadResult(
            video_id, outcome == "success", error="failure" if outcome == "failure" else None
        )

    worker = Mock(side_effect=download)
    monkeypatch.setattr(svc, "_download_sync", worker)
    waiter = asyncio.create_task(svc.download("abcdefghijk"))
    try:
        await asyncio.wait_for(started.wait(), 3)
        owned = list(svc._active.values())
        if cancel_waiter:
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        assert svc.active_count == 1
        assert (await svc.download("abcdefghijk")).error == "Already downloading"
        assert worker.call_count == 1
    finally:
        release.set()
        completed = await asyncio.wait_for(asyncio.gather(*owned, return_exceptions=True), 3)
        caller_result = await asyncio.wait_for(asyncio.gather(waiter, return_exceptions=True), 3)

    assert svc.active_count == 0
    if outcome == "exception":
        assert isinstance(completed[0], RuntimeError)
        assert "Download worker failed" in caplog.text
        assert "worker exploded" in caplog.text
        if not cancel_waiter:
            assert isinstance(caller_result[0], RuntimeError)
    else:
        assert completed[0].success is (outcome == "success")
    # Failure must not permanently reserve the video, nor leave unobserved errors.
    monkeypatch.setattr(
        svc, "_download_sync", Mock(return_value=DownloadResult("abcdefghijk", True))
    )
    assert (await svc.download("abcdefghijk")).success
    assert svc.active_count == 0


async def test_download_rejects_invalid_id_before_file_lookup(tmp_path, monkeypatch):
    svc = DownloadService(download_dir=tmp_path)
    lookup = Mock(side_effect=AssertionError("invalid ID must not reach filesystem"))
    monkeypatch.setattr(svc, "get_path", lookup)
    assert (await svc.download("../outside")).error == "Invalid video ID"
    lookup.assert_not_called()


@pytest.mark.parametrize(
    "outcome", ["success", "context_failure", "no_output", "conflict", "nonzero_status"]
)
async def test_real_download_publishes_only_after_success_and_cleans_its_stage(
    tmp_path, monkeypatch, outcome
):
    svc = DownloadService(download_dir=tmp_path)
    video_id = "abcdefghijk"
    public_path = tmp_path / f"{video_id}.opus"
    stages = []

    class FakeYDL:
        def __init__(self, opts):
            self.path = Path(opts["outtmpl"] % {"ext": "opus"})
            stages.append(self.path.parent)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            assert not public_path.exists() or outcome == "conflict"
            if outcome == "context_failure":
                raise RuntimeError("context failed")
            return False

        def download(self, urls):
            assert self.path.parent != tmp_path
            if outcome != "no_output":
                self.path.write_bytes(b"complete audio")
            if outcome == "conflict":
                public_path.write_bytes(b"unrelated existing file")
            return 1 if outcome == "nonzero_status" else 0

    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYDL)
    result = await svc.download(video_id)
    assert all(not stage.exists() for stage in stages)
    assert svc.active_count == 0
    if outcome == "success":
        assert result.success
        assert result.file_path == public_path
        assert public_path.read_bytes() == b"complete audio"
    else:
        assert not result.success
        if outcome == "conflict":
            assert result.error == "Download destination already exists"
            assert public_path.read_bytes() == b"unrelated existing file"
        else:
            assert not public_path.exists()
