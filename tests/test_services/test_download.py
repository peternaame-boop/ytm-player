"""Tests for DownloadService (filesystem logic only — no yt-dlp calls)."""

from pathlib import Path

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
