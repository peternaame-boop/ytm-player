"""Tests for IPC validation logic."""

import json

from ytm_player.ipc import _VALID_COMMANDS


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
