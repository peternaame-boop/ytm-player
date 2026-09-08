"""Tests for IPCMixin._handle_ipc_command dispatch + seek parsing.

The CLI shells (`ytm play`, `ytm seek 1:30`, etc.) all hit
_handle_ipc_command via the Unix socket. These tests cover the
no-player guards, unknown-command fallthrough, and the three seek
formats (relative, mm:ss, absolute seconds).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from ytm_player.app._ipc import IPCMixin


def _fresh_ipc_host():
    h = IPCMixin()
    h.player = MagicMock()
    h.player.resume = AsyncMock()
    h.player.pause = AsyncMock()
    h.player.seek = AsyncMock()
    h.player.seek_absolute = AsyncMock()
    h.queue = MagicMock()
    h.queue.clear = MagicMock()
    h._refresh_queue_page = MagicMock()
    h.ytmusic = MagicMock()
    h._play_next = AsyncMock()
    h._play_previous = AsyncMock()
    return h


class TestUnknownCommand:
    async def test_unknown_command_returns_error(self):
        h = _fresh_ipc_host()
        result = await h._handle_ipc_command("does_not_exist", {})
        assert result["ok"] is False
        assert "unknown command" in result["error"]


class TestNoPlayerGuard:
    async def test_play_with_no_player_returns_error(self):
        h = _fresh_ipc_host()
        h.player = None
        result = await h._handle_ipc_command("play", {})
        assert result == {"ok": False, "error": "player not ready"}

    async def test_pause_with_no_player_returns_error(self):
        h = _fresh_ipc_host()
        h.player = None
        result = await h._handle_ipc_command("pause", {})
        assert result == {"ok": False, "error": "player not ready"}

    async def test_seek_with_no_player_returns_error(self):
        h = _fresh_ipc_host()
        h.player = None
        result = await h._handle_ipc_command("seek", {"offset": "+10"})
        assert result == {"ok": False, "error": "player not ready"}


class TestPlayPauseDispatch:
    async def test_play_calls_resume(self):
        h = _fresh_ipc_host()
        result = await h._handle_ipc_command("play", {})
        assert result == {"ok": True}
        h.player.resume.assert_awaited_once()

    async def test_pause_calls_pause(self):
        h = _fresh_ipc_host()
        result = await h._handle_ipc_command("pause", {})
        assert result == {"ok": True}
        h.player.pause.assert_awaited_once()

    async def test_queue_clear_clears_queue(self):
        h = _fresh_ipc_host()
        result = await h._handle_ipc_command("queue_clear", {})
        assert result == {"ok": True}
        h.queue.clear.assert_called_once()
        h._refresh_queue_page.assert_called_once()


class TestSeekParsing:
    async def test_relative_positive(self):
        h = _fresh_ipc_host()
        result = await h._ipc_seek({"offset": "+15"})
        assert result == {"ok": True}
        h.player.seek.assert_awaited_once_with(15.0)

    async def test_relative_negative(self):
        h = _fresh_ipc_host()
        result = await h._ipc_seek({"offset": "-10"})
        assert result == {"ok": True}
        h.player.seek.assert_awaited_once_with(-10.0)

    async def test_mm_ss_format(self):
        h = _fresh_ipc_host()
        result = await h._ipc_seek({"offset": "1:30"})
        assert result == {"ok": True}
        h.player.seek_absolute.assert_awaited_once_with(90.0)

    async def test_hh_mm_ss_format(self):
        h = _fresh_ipc_host()
        result = await h._ipc_seek({"offset": "1:00:00"})
        assert result == {"ok": True}
        h.player.seek_absolute.assert_awaited_once_with(3600.0)

    async def test_absolute_seconds(self):
        h = _fresh_ipc_host()
        result = await h._ipc_seek({"offset": "42"})
        assert result == {"ok": True}
        h.player.seek_absolute.assert_awaited_once_with(42.0)

    async def test_missing_offset(self):
        h = _fresh_ipc_host()
        result = await h._ipc_seek({})
        assert result == {"ok": False, "error": "missing offset"}

    async def test_invalid_offset(self):
        h = _fresh_ipc_host()
        result = await h._ipc_seek({"offset": "+notanumber"})
        assert result["ok"] is False
        assert "invalid offset" in result["error"]

    async def test_invalid_time_format(self):
        h = _fresh_ipc_host()
        result = await h._ipc_seek({"offset": "1:2:3:4"})
        assert result["ok"] is False
        assert "invalid time format" in result["error"]


class TestQueueAdd:
    async def test_queue_add_refreshes_queue_page(self):
        h = _fresh_ipc_host()
        h.ytmusic.get_watch_playlist = AsyncMock(
            return_value=[{"videoId": "v1", "title": "T", "artists": []}]
        )
        result = await h._handle_ipc_command("queue_add", {"video_id": "v1"})
        assert result == {"ok": True}
        h.queue.add.assert_called_once()
        assert h.queue.add.call_args.args[0]["video_id"] == "v1"
        h._refresh_queue_page.assert_called_once()

    async def test_unresolved_track_does_not_refresh(self):
        h = _fresh_ipc_host()
        h.ytmusic.get_watch_playlist = AsyncMock(return_value=[])
        result = await h._handle_ipc_command("queue_add", {"video_id": "v1"})
        assert result["ok"] is False
        h.queue.add.assert_not_called()
        h._refresh_queue_page.assert_not_called()
