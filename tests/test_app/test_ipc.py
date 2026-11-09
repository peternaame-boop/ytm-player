"""Tests for IPCMixin._handle_ipc_command dispatch + seek parsing.

The CLI shells (`ytm play`, `ytm seek 1:30`, etc.) all hit
_handle_ipc_command via the Unix socket. These tests cover the
no-player guards, unknown-command fallthrough, and the three seek
formats (relative, mm:ss, absolute seconds).
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ytm_player.app._ipc import IPCMixin
from ytm_player.ipc import IPCServer


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


class TestTransportRepliesBeforeTheTrackStarts:
    """`ytm next`/`ytm prev` must not time out while the track resolves (#201)."""

    async def test_next_replies_before_skipping(self):
        h = _fresh_ipc_host()
        order: list[str] = []
        h._play_next = AsyncMock(side_effect=lambda: order.append("skip"))
        with patch("ytm_player.app._ipc.reply_now", side_effect=lambda p: order.append("reply")):
            result = await h._handle_ipc_command("next", {})
        assert order == ["reply", "skip"]
        assert result == {"ok": True}

    async def test_prev_replies_before_going_back(self):
        h = _fresh_ipc_host()
        order: list[str] = []
        h._play_previous = AsyncMock(side_effect=lambda: order.append("back"))
        with patch("ytm_player.app._ipc.reply_now", side_effect=lambda p: order.append("reply")):
            result = await h._handle_ipc_command("prev", {})
        assert order == ["reply", "back"]
        assert result == {"ok": True}

    @pytest.mark.parametrize("command", ["next", "prev", "play_index"])
    async def test_no_player_still_reports_the_error(self, command):
        h = _fresh_ipc_host()
        h.player = None
        with patch("ytm_player.app._ipc.reply_now") as early:
            result = await h._handle_ipc_command(command, {})
        early.assert_not_called()
        assert result == {"ok": False, "error": "player not ready"}

    @pytest.mark.parametrize("command", ["next", "prev", "play_index"])
    async def test_cli_gets_ok_while_the_stream_is_still_resolving(self, command, monkeypatch):
        # Real server and the app's handler; the skip is held until the end.
        h = _fresh_ipc_host()
        resolving = asyncio.Event()
        release = asyncio.Event()

        async def held(*_track) -> None:
            resolving.set()
            await release.wait()

        h._play_next = AsyncMock(side_effect=held)
        h._play_previous = AsyncMock(side_effect=held)
        h.queue.jump_to.return_value = {"video_id": "v1"}
        h.play_track = AsyncMock(side_effect=held)

        tmp_dir = Path(tempfile.mkdtemp(prefix="ytm-ipc-"))
        port_file = tmp_dir / "ipc_port"
        import ytm_player.config.paths as paths_mod

        monkeypatch.setattr(paths_mod, "IPC_PORT_FILE", port_file)
        server = IPCServer(h._handle_ipc_command)
        await server._start_tcp()
        try:
            port = int(port_file.read_text(encoding="utf-8").strip())
            token = port_file.with_name("ipc_token").read_text(encoding="utf-8").strip()
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            request = {"command": command, "args": {"index": 0}, "token": token}
            writer.write((json.dumps(request) + "\n").encode())
            await writer.drain()
            reply = json.loads(await asyncio.wait_for(reader.readline(), timeout=5))
            assert reply == {"ok": True}
            assert resolving.is_set() and not release.is_set()
            release.set()
            assert await asyncio.wait_for(reader.read(), timeout=5) == b""
            writer.close()
            await writer.wait_closed()
        finally:
            release.set()
            if server._server is not None:
                server._server.close()
                await server._server.wait_closed()
            port_file.unlink(missing_ok=True)
            port_file.with_name("ipc_token").unlink(missing_ok=True)
            try:
                tmp_dir.rmdir()
            except OSError:
                pass


class TestPlayIndex:
    @pytest.mark.parametrize("shuffled", [False, True])
    @pytest.mark.parametrize("index", [0, 2, 5])
    async def test_selects_visible_occurrence(self, shuffled, index, queue_manager, sample_tracks):
        h = _fresh_ipc_host()
        h.queue = queue_manager
        h.queue.add_multiple([*sample_tracks, sample_tracks[2]])
        if shuffled:
            with patch(
                "ytm_player.services.queue.random.shuffle",
                side_effect=lambda items: items.reverse(),
            ):
                h.queue.toggle_shuffle()
        visible = h.queue.tracks
        h.queue.jump_to(1)
        order = []
        h.play_track = AsyncMock(side_effect=lambda track: order.append("play"))

        with patch(
            "ytm_player.app._ipc.reply_now",
            side_effect=lambda payload: order.append("reply"),
        ) as early:
            result = await h._handle_ipc_command("play_index", {"index": index})

        assert result == {"ok": True}
        early.assert_called_once_with({"ok": True})
        assert order == ["reply", "play"]
        h.play_track.assert_awaited_once_with(visible[index])
        assert h.queue.current_index == index
        assert h.queue.current_track is visible[index]
        expected_next = visible[index + 1] if index < len(visible) - 1 else None
        assert h.queue.next_track() is expected_next
        h.queue.jump_to(index)
        expected_previous = visible[index - 1] if index > 0 else None
        assert h.queue.previous_track() is expected_previous

    @pytest.mark.parametrize(
        "args",
        [
            {},
            *({"index": value} for value in (-1, 5, 10**30, None, True, False, 1.0, "1", [], {})),
        ],
    )
    async def test_invalid_index_preserves_position(self, args, queue_manager, sample_tracks):
        h = _fresh_ipc_host()
        h.queue = queue_manager
        h.queue.add_multiple(sample_tracks)
        h.queue.jump_to(1)
        h.play_track = AsyncMock()

        with patch("ytm_player.app._ipc.reply_now") as early:
            result = await h._handle_ipc_command("play_index", args)

        assert result == {"ok": False, "error": f"invalid index: {args.get('index')}"}
        assert h.queue.current_index == 1
        h.play_track.assert_not_awaited()
        early.assert_not_called()

    async def test_empty_queue_returns_error(self, queue_manager):
        h = _fresh_ipc_host()
        h.queue = queue_manager
        h.play_track = AsyncMock()

        with patch("ytm_player.app._ipc.reply_now") as early:
            result = await h._handle_ipc_command("play_index", {"index": 0})

        assert result == {"ok": False, "error": "invalid index: 0"}
        h.play_track.assert_not_awaited()
        early.assert_not_called()
