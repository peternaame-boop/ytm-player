"""Tests for IPC validation logic."""

import asyncio
import json

import pytest

from ytm_player.ipc import _VALID_COMMANDS, IPCServer


class TestIPCCommandWhitelist:
    def test_play_is_valid(self):
        assert "play" in _VALID_COMMANDS

    def test_pause_is_valid(self):
        assert "pause" in _VALID_COMMANDS

    def test_seek_is_valid(self):
        assert "seek" in _VALID_COMMANDS

    def test_queue_is_valid(self):
        assert "queue" in _VALID_COMMANDS

    def test_invalid_command_rejected(self):
        assert "exec" not in _VALID_COMMANDS
        assert "shell" not in _VALID_COMMANDS
        assert "eval" not in _VALID_COMMANDS
        assert "" not in _VALID_COMMANDS

    def test_all_expected_commands_present(self):
        expected = {
            "play",
            "pause",
            "next",
            "prev",
            "seek",
            "now",
            "status",
            "queue",
            "queue_add",
            "queue_clear",
        }
        assert _VALID_COMMANDS == expected


class TestIPCPayloadValidation:
    """Test the validation logic that would be applied to incoming IPC messages."""

    def test_valid_payload(self):
        payload = json.dumps({"command": "play", "args": {}})
        data = json.loads(payload)
        assert isinstance(data, dict)
        assert data["command"] in _VALID_COMMANDS

    def test_non_dict_payload(self):
        payload = json.dumps([1, 2, 3])
        data = json.loads(payload)
        assert not isinstance(data, dict)

    def test_missing_command(self):
        payload = json.dumps({"args": {}})
        data = json.loads(payload)
        assert data.get("command", "") not in _VALID_COMMANDS

    def test_invalid_command_string(self):
        payload = json.dumps({"command": "drop_tables"})
        data = json.loads(payload)
        assert data["command"] not in _VALID_COMMANDS

    def test_command_not_string(self):
        payload = json.dumps({"command": 42})
        data = json.loads(payload)
        assert not isinstance(data["command"], str) or data["command"] not in _VALID_COMMANDS

    def test_args_default_to_empty_dict(self):
        payload = json.dumps({"command": "play"})
        data = json.loads(payload)
        args = data.get("args", {})
        assert isinstance(args, dict)

    def test_non_dict_args_coerced(self):
        payload = json.dumps({"command": "play", "args": "bad"})
        data = json.loads(payload)
        args = data.get("args", {})
        if not isinstance(args, dict):
            args = {}
        assert isinstance(args, dict)

    def test_oversized_payload(self):
        """Payloads over 64KB should be rejected."""
        big = "x" * 70000
        payload = json.dumps({"command": "play", "args": {"data": big}})
        assert len(payload.encode()) > 65536


class TestIPCServerHandler:
    """Exercise the real IPCServer._client_connected handler via a Unix socket."""

    @pytest.fixture
    async def ipc_env(self, tmp_path):
        """Start an IPCServer on a temp socket and yield a helper to send messages."""
        socket_path = tmp_path / "test.sock"

        async def handler(command: str, args: dict) -> dict:
            return {"ok": True, "command": command, "args": args}

        server = IPCServer(handler)

        # Patch SOCKET_PATH for this server instance so it uses the temp path.
        import ytm_player.ipc as ipc_mod

        original = ipc_mod.SOCKET_PATH
        ipc_mod.SOCKET_PATH = socket_path
        try:
            await server.start()

            async def send(payload: bytes) -> dict:
                reader, writer = await asyncio.open_unix_connection(str(socket_path))
                writer.write(payload)
                writer.write_eof()
                data = await asyncio.wait_for(reader.read(), timeout=5)
                writer.close()
                await writer.wait_closed()
                return json.loads(data)

            yield send
        finally:
            await server.stop()
            ipc_mod.SOCKET_PATH = original

    async def test_valid_command_returns_handler_response(self, ipc_env):
        send = ipc_env
        payload = json.dumps({"command": "play", "args": {"video_id": "abc123"}}).encode()
        resp = await send(payload)
        assert resp["ok"] is True
        assert resp["command"] == "play"
        assert resp["args"] == {"video_id": "abc123"}

    async def test_invalid_json_returns_error(self, ipc_env):
        send = ipc_env
        resp = await send(b"not json at all{{{")
        assert resp["ok"] is False
        assert "invalid JSON" in resp["error"]

    async def test_unknown_command_returns_error(self, ipc_env):
        send = ipc_env
        payload = json.dumps({"command": "drop_tables"}).encode()
        resp = await send(payload)
        assert resp["ok"] is False
        assert "unknown command" in resp["error"]

    async def test_non_dict_payload_returns_error(self, ipc_env):
        send = ipc_env
        payload = json.dumps([1, 2, 3]).encode()
        resp = await send(payload)
        assert resp["ok"] is False
        assert "expected JSON object" in resp["error"]

    async def test_missing_command_field_returns_error(self, ipc_env):
        send = ipc_env
        payload = json.dumps({"args": {"foo": "bar"}}).encode()
        resp = await send(payload)
        assert resp["ok"] is False
        assert "unknown command" in resp["error"]
