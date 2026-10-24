"""Tests for IPC validation logic."""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import pytest

from ytm_player.ipc import (
    _MAX_MSG,
    _VALID_COMMANDS,
    IPCServer,
    _ipc_request_tcp,
    _ipc_request_unix,
    get_running_pid,
    is_tui_running,
    remove_pid,
    try_claim_pid,
)


async def _tcp_roundtrip(port: int, obj: dict, *, split: bool = False) -> dict:
    """Send *obj* as a newline-framed line to the TCP server and read one reply.

    When *split* is set, the line is written in two flushes with a delay
    between them to exercise the server's reassembly of fragmented messages.
    """
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    data = (json.dumps(obj) + "\n").encode()
    if split and len(data) > 4:
        mid = len(data) // 2
        writer.write(data[:mid])
        await writer.drain()
        await asyncio.sleep(0.1)
        writer.write(data[mid:])
        await writer.drain()
    else:
        writer.write(data)
        await writer.drain()
    resp = json.loads(await asyncio.wait_for(reader.readline(), timeout=5))
    writer.close()
    await writer.wait_closed()
    return resp


class TestGetRunningPid:
    """PID-file liveness resolution behind the single-instance guard."""

    @pytest.fixture
    def pid_file(self, tmp_path, monkeypatch):
        pid_file = tmp_path / "ytm.pid"
        monkeypatch.setattr("ytm_player.ipc.PID_FILE", pid_file)
        return pid_file

    def test_no_pid_file_means_not_running(self, pid_file):
        assert get_running_pid() is None

    def test_live_process_returns_pid(self, pid_file, monkeypatch):
        pid_file.write_text("12345", encoding="utf-8")
        monkeypatch.setattr("ytm_player.ipc._is_pid_alive", lambda pid: True)
        # Identity pinned to "ours" — this test covers liveness resolution;
        # TestPidIdentityCheck covers the recycled-PID paths.
        monkeypatch.setattr("ytm_player.ipc._is_ytm_process", lambda pid: True)
        assert get_running_pid() == 12345
        assert pid_file.exists()

    def test_dead_process_cleans_stale_pid_file(self, pid_file, monkeypatch):
        pid_file.write_text("12345", encoding="utf-8")
        monkeypatch.setattr("ytm_player.ipc._is_pid_alive", lambda pid: False)
        assert get_running_pid() is None
        assert not pid_file.exists()

    def test_garbage_pid_file_cleaned_up(self, pid_file):
        pid_file.write_text("not-a-pid", encoding="utf-8")
        assert get_running_pid() is None
        assert not pid_file.exists()

    def test_is_tui_running_delegates(self, pid_file, monkeypatch):
        pid_file.write_text("777", encoding="utf-8")
        monkeypatch.setattr("ytm_player.ipc._is_pid_alive", lambda pid: True)
        monkeypatch.setattr("ytm_player.ipc._is_ytm_process", lambda pid: True)
        assert is_tui_running() is True


class TestTryClaimPid:
    """Atomic PID-file claim for the single-instance launch guard."""

    @pytest.fixture
    def pid_file(self, tmp_path, monkeypatch):
        pid_file = tmp_path / "ytm.pid"
        monkeypatch.setattr("ytm_player.ipc.PID_FILE", pid_file)
        return pid_file

    def test_claim_succeeds_and_records_own_pid(self, pid_file):
        import os

        assert try_claim_pid() is None
        assert pid_file.read_text(encoding="utf-8") == str(os.getpid())

    def test_second_claim_returns_holders_pid(self, pid_file, monkeypatch):
        """Our own PID is genuinely alive, so no liveness mocking needed.

        Identity is pinned to "ours" because the recorded PID here is the
        pytest process, not a real ytm entry point (in production the
        holder IS a ytm process).
        """
        import os

        monkeypatch.setattr("ytm_player.ipc._is_ytm_process", lambda pid: True)
        assert try_claim_pid() is None
        assert try_claim_pid() == os.getpid()

    def test_stale_holder_is_evicted_and_reclaimed(self, pid_file, monkeypatch):
        import os

        pid_file.write_text("99999", encoding="utf-8")
        monkeypatch.setattr("ytm_player.ipc._is_pid_alive", lambda pid: False)
        assert try_claim_pid() is None
        assert pid_file.read_text(encoding="utf-8") == str(os.getpid())

    def test_remove_pid_only_removes_own_claim(self, pid_file, monkeypatch):
        # Another instance's claim must survive our cleanup.
        monkeypatch.setattr("ytm_player.ipc._is_pid_alive", lambda pid: True)
        pid_file.write_text("424242", encoding="utf-8")
        remove_pid()
        assert pid_file.exists()

        # Our own claim is removed.
        pid_file.unlink()
        assert try_claim_pid() is None
        remove_pid()
        assert not pid_file.exists()


class TestPidIdentityCheck:
    """Recycled-PID handling: a live PID that is not a ytm process is stale.

    After a crash leaves ytm.pid behind, the OS may hand the recorded PID to
    an unrelated process; the guard must not block launch forever on it
    (previously the user had to delete ytm.pid by hand).
    """

    @pytest.fixture
    def pid_file(self, tmp_path, monkeypatch):
        pid_file = tmp_path / "ytm.pid"
        monkeypatch.setattr("ytm_player.ipc.PID_FILE", pid_file)
        monkeypatch.setattr("ytm_player.ipc._is_pid_alive", lambda pid: True)
        return pid_file

    def test_recycled_pid_of_unrelated_process_is_cleaned(self, pid_file, monkeypatch):
        pid_file.write_text("12345", encoding="utf-8")
        # raising=False so this test fails on behavior (not on setup) against
        # code that has no _cmdline_of seam at all.
        monkeypatch.setattr(
            "ytm_player.ipc._cmdline_of",
            lambda pid: ["/usr/lib/systemd/systemd-oomd"],
            raising=False,
        )
        assert get_running_pid() is None
        assert not pid_file.exists()

    def test_recycled_pid_is_reclaimed_by_launch(self, pid_file, monkeypatch):
        import os

        pid_file.write_text("54321", encoding="utf-8")
        monkeypatch.setattr(
            "ytm_player.ipc._cmdline_of",
            lambda pid: ["/usr/bin/unrelated-daemon"],
            raising=False,
        )
        assert try_claim_pid() is None
        assert pid_file.read_text(encoding="utf-8") == str(os.getpid())

    def test_live_ytm_process_still_blocks(self, pid_file, monkeypatch):
        pid_file.write_text("12345", encoding="utf-8")
        monkeypatch.setattr(
            "ytm_player.ipc._cmdline_of",
            lambda pid: ["/usr/bin/python3", "-m", "ytm_player"],
            raising=False,
        )
        assert get_running_pid() == 12345
        assert pid_file.exists()

    def test_unknown_cmdline_counts_as_ytm(self, pid_file, monkeypatch):
        """Only positive evidence of NOT-ours may unblock; unknown blocks."""
        pid_file.write_text("12345", encoding="utf-8")
        monkeypatch.setattr("ytm_player.ipc._cmdline_of", lambda pid: None, raising=False)
        assert get_running_pid() == 12345
        assert pid_file.exists()


class TestStaleEvictionSafety:
    """Stale cleanup must never delete a claim it did not judge stale.

    Two launchers can both judge the same file stale; the slower one's
    cleanup must not destroy the faster one's freshly published claim.
    """

    @pytest.fixture
    def pid_file(self, tmp_path, monkeypatch):
        pid_file = tmp_path / "ytm.pid"
        monkeypatch.setattr("ytm_player.ipc.PID_FILE", pid_file)
        return pid_file

    def test_fresh_claim_survives_racing_eviction(self, pid_file):
        import ytm_player.ipc as ipc_mod

        # We judged "999" stale; before our unlink lands, another launcher
        # evicted the file and claimed it — the content no longer matches.
        pid_file.write_text("31337", encoding="utf-8")
        assert ipc_mod._unlink_pid_if_still("999") is False
        assert pid_file.read_text(encoding="utf-8") == "31337"

    def test_matching_stale_content_is_removed(self, pid_file):
        import ytm_player.ipc as ipc_mod

        pid_file.write_text("999", encoding="utf-8")
        assert ipc_mod._unlink_pid_if_still("999") is True
        assert not pid_file.exists()

    def test_missing_file_is_noop(self, pid_file):
        import ytm_player.ipc as ipc_mod

        assert ipc_mod._unlink_pid_if_still("999") is False  # must not raise
        assert not pid_file.exists()

    def test_stale_branch_respects_claim_landed_mid_probe(self, pid_file, monkeypatch):
        """The dead-PID cleanup in get_running_pid must re-check content.

        The liveness/identity probes take real time; a concurrent launcher
        can evict the same stale file and publish a fresh claim in that
        window. Simulated here by swapping the file content from inside
        the liveness probe.
        """
        pid_file.write_text("999", encoding="utf-8")

        def dies_but_someone_claims(pid):
            pid_file.write_text("31337", encoding="utf-8")
            return False

        monkeypatch.setattr("ytm_player.ipc._is_pid_alive", dies_but_someone_claims)
        assert get_running_pid() is None
        assert pid_file.read_text(encoding="utf-8") == "31337"


class TestLooksLikeYtm:
    """Conservative match rule: False only on positive not-ours evidence.

    A false "ours" merely preserves the old always-block behavior; a false
    "not ours" would let a second instance steal a live TUI's PID file.
    """

    @pytest.mark.parametrize(
        ("argv", "expected"),
        [
            # Real entry points.
            (["/home/u/.venv/bin/python", "/home/u/.venv/bin/ytm"], True),
            (["/usr/bin/python3", "-m", "ytm_player"], True),
            (["C:\\Users\\u\\venv\\Scripts\\ytm.exe"], True),
            # Windows image-name-only probe: a bare interpreter path cannot
            # rule out `python -m ytm_player`.
            (["C:\\Python312\\python.exe"], True),
            # Positive not-ours evidence unblocks.
            (["C:\\Windows\\System32\\svchost.exe"], False),
            (["/usr/lib/systemd/systemd-oomd"], False),
            (["/usr/bin/python3", "/home/u/scripts/backup.py"], False),
            # An editor with a ytm_player file open blocks (conservative)
            # rather than risk stealing a live instance's claim.
            (["nvim", "src/ytm_player/ipc.py"], True),
        ],
    )
    def test_match(self, argv, expected):
        import ytm_player.ipc as ipc_mod

        assert ipc_mod._looks_like_ytm(argv) is expected

    @pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc probe is Linux-only")
    def test_cmdline_of_reads_own_process(self):
        import os

        import ytm_player.ipc as ipc_mod

        argv = ipc_mod._cmdline_of(os.getpid())
        assert argv is not None
        assert "pytest" in " ".join(argv)


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
            "like",
            "dislike",
            "unlike",
            "play_index",
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


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="IPCServerHandler tests use AF_UNIX which Windows doesn't support",
)
class TestIPCServerHandler:
    """Exercise the real IPCServer._client_connected handler via a Unix socket."""

    @pytest.fixture
    async def ipc_env(self, monkeypatch):
        """Start an IPCServer on a temp socket and yield a helper to send messages."""
        # AF_UNIX sun_path is capped at 104 bytes on macOS / 108 on Linux.
        # pytest's tmp_path on CI runners (e.g. /Users/runner/work/_temp/...
        # plus pytest-of-runner/pytest-N/test_xxx0/) blows past that. Use the
        # system tempdir with a short basename so the socket path stays small
        # on every platform.
        tmp_dir = Path(tempfile.mkdtemp(prefix="ytm-ipc-"))
        socket_path = tmp_dir / "s"

        async def handler(command: str, args: dict) -> dict:
            return {"ok": True, "command": command, "args": args}

        server = IPCServer(handler)

        # Patch SOCKET_PATH in the paths module so the server uses the temp path.
        import ytm_player.config.paths as paths_mod

        monkeypatch.setattr(paths_mod, "SOCKET_PATH", socket_path)

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
            socket_path.unlink(missing_ok=True)
            try:
                tmp_dir.rmdir()
            except OSError:
                pass

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


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Unix-socket framing tests use AF_UNIX which Windows doesn't support",
)
class TestIPCUnixFraming:
    """Newline framing over the Unix-socket transport (fragmentation, oversize)."""

    @pytest.fixture
    async def unix_env(self, monkeypatch):
        # Short socket path (AF_UNIX sun_path is capped at 104 bytes on macOS).
        tmp_dir = Path(tempfile.mkdtemp(prefix="ytm-ipc-"))
        socket_path = tmp_dir / "s"
        calls: list[str] = []

        async def handler(command: str, args: dict) -> dict:
            calls.append(command)
            return {"ok": True, "command": command, "args": args}

        server = IPCServer(handler)
        import ytm_player.config.paths as paths_mod

        monkeypatch.setattr(paths_mod, "SOCKET_PATH", socket_path)
        try:
            await server.start()
            yield socket_path, calls
        finally:
            await server.stop()
            socket_path.unlink(missing_ok=True)
            try:
                tmp_dir.rmdir()
            except OSError:
                pass

    async def test_fragmented_message_is_reassembled(self, unix_env):
        """A JSON line split across two writes must still parse (pre-fix: fails)."""
        socket_path, calls = unix_env
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
        line = json.dumps({"command": "play", "args": {}}).encode() + b"\n"
        mid = len(line) // 2
        writer.write(line[:mid])
        await writer.drain()
        await asyncio.sleep(0.1)
        writer.write(line[mid:])
        await writer.drain()
        resp = json.loads(await asyncio.wait_for(reader.readline(), timeout=5))
        writer.close()
        await writer.wait_closed()
        assert resp["ok"] is True
        assert calls == ["play"]

    async def test_oversized_line_rejected(self, unix_env):
        """A line above the cap yields a clean error and never dispatches."""
        socket_path, calls = unix_env
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
        blob = "x" * (_MAX_MSG + 4096)
        line = json.dumps({"command": "play", "args": {"blob": blob}}).encode() + b"\n"
        writer.write(line)
        try:
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        try:
            resp = json.loads(await asyncio.wait_for(reader.readline(), timeout=5))
            assert resp["ok"] is False
            assert "large" in resp["error"].lower()
        except (ConnectionResetError, ConnectionError):
            # Dropping the oversized connection is also acceptable — the point
            # is no hang (guarded by wait_for) and no crash (verified below).
            pass
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionResetError, ConnectionError):
            pass
        assert "play" not in calls

        # Server survived and still serves — proves no crash.
        r2, w2 = await asyncio.open_unix_connection(str(socket_path))
        w2.write(json.dumps({"command": "status", "args": {}}).encode() + b"\n")
        await w2.drain()
        resp2 = json.loads(await asyncio.wait_for(r2.readline(), timeout=5))
        w2.close()
        await w2.wait_closed()
        assert resp2["ok"] is True

    async def test_unix_client_roundtrip(self, monkeypatch):
        """The real blocking Unix client talks to the real server end-to-end."""
        tmp_dir = Path(tempfile.mkdtemp(prefix="ytm-ipc-"))
        socket_path = tmp_dir / "s"

        async def handler(command: str, args: dict) -> dict:
            return {"ok": True, "command": command}

        server = IPCServer(handler)
        import ytm_player.config.paths as paths_mod

        monkeypatch.setattr(paths_mod, "SOCKET_PATH", socket_path)
        try:
            await server.start()
            resp = await asyncio.to_thread(_ipc_request_unix, "status", {}, 5.0)
            assert resp == {"ok": True, "command": "status"}
        finally:
            await server.stop()
            socket_path.unlink(missing_ok=True)
            try:
                tmp_dir.rmdir()
            except OSError:
                pass


class TestIPCTcpAuth:
    """Auth-token gating + framing over the TCP transport (all platforms)."""

    @pytest.fixture
    async def tcp_env(self, monkeypatch):
        tmp_dir = Path(tempfile.mkdtemp(prefix="ytm-ipc-"))
        port_file = tmp_dir / "ipc_port"
        calls: list[str] = []

        async def handler(command: str, args: dict) -> dict:
            calls.append(command)
            return {"ok": True, "command": command, "args": args}

        server = IPCServer(handler)
        import ytm_player.config.paths as paths_mod

        monkeypatch.setattr(paths_mod, "IPC_PORT_FILE", port_file)
        await server._start_tcp()
        port = int(port_file.read_text(encoding="utf-8").strip())
        token = port_file.with_name("ipc_token").read_text(encoding="utf-8").strip()
        try:
            yield port, token, calls
        finally:
            if server._server is not None:
                server._server.close()
                await server._server.wait_closed()
            port_file.unlink(missing_ok=True)
            port_file.with_name("ipc_token").unlink(missing_ok=True)
            try:
                tmp_dir.rmdir()
            except OSError:
                pass

    async def test_valid_token_is_dispatched(self, tcp_env):
        port, token, calls = tcp_env
        resp = await _tcp_roundtrip(port, {"command": "play", "args": {}, "token": token})
        assert resp["ok"] is True
        assert resp["command"] == "play"
        assert calls == ["play"]

    async def test_wrong_token_rejected_and_not_dispatched(self, tcp_env):
        port, _token, calls = tcp_env
        resp = await _tcp_roundtrip(port, {"command": "play", "args": {}, "token": "deadbeef"})
        assert resp["ok"] is False
        assert "auth" in resp["error"].lower()
        assert calls == []

    async def test_missing_token_rejected_and_not_dispatched(self, tcp_env):
        port, _token, calls = tcp_env
        resp = await _tcp_roundtrip(port, {"command": "play", "args": {}})
        assert resp["ok"] is False
        assert "auth" in resp["error"].lower()
        assert calls == []

    async def test_fragmented_message_is_reassembled(self, tcp_env):
        port, token, calls = tcp_env
        resp = await _tcp_roundtrip(
            port, {"command": "play", "args": {}, "token": token}, split=True
        )
        assert resp["ok"] is True
        assert calls == ["play"]

    async def test_tcp_client_roundtrip_reads_token(self, monkeypatch):
        """The real blocking TCP client reads the token file and authenticates."""
        tmp_dir = Path(tempfile.mkdtemp(prefix="ytm-ipc-"))
        port_file = tmp_dir / "ipc_port"

        async def handler(command: str, args: dict) -> dict:
            return {"ok": True, "command": command}

        server = IPCServer(handler)
        import ytm_player.config.paths as paths_mod

        monkeypatch.setattr(paths_mod, "IPC_PORT_FILE", port_file)
        await server._start_tcp()
        try:
            resp = await asyncio.to_thread(_ipc_request_tcp, "status", {}, 5.0)
            assert resp == {"ok": True, "command": "status"}
        finally:
            if server._server is not None:
                server._server.close()
                await server._server.wait_closed()
            port_file.unlink(missing_ok=True)
            port_file.with_name("ipc_token").unlink(missing_ok=True)
            try:
                tmp_dir.rmdir()
            except OSError:
                pass

    async def test_start_tcp_writes_files_and_stop_removes_them(self, monkeypatch):
        tmp_dir = Path(tempfile.mkdtemp(prefix="ytm-ipc-"))
        port_file = tmp_dir / "ipc_port"
        token_file = port_file.with_name("ipc_token")

        async def handler(command: str, args: dict) -> dict:
            return {"ok": True}

        server = IPCServer(handler)
        import ytm_player.config.paths as paths_mod

        monkeypatch.setattr(paths_mod, "IPC_PORT_FILE", port_file)
        await server._start_tcp()
        assert port_file.exists()
        assert token_file.exists()
        assert len(token_file.read_text(encoding="utf-8").strip()) >= 32
        await server.stop()
        assert not port_file.exists()
        assert not token_file.exists()
        try:
            tmp_dir.rmdir()
        except OSError:
            pass

    @pytest.mark.skipif(sys.platform == "win32", reason="NTFS ignores POSIX mode bits")
    async def test_token_file_is_owner_only(self, monkeypatch):
        tmp_dir = Path(tempfile.mkdtemp(prefix="ytm-ipc-"))
        port_file = tmp_dir / "ipc_port"

        async def handler(command: str, args: dict) -> dict:
            return {"ok": True}

        server = IPCServer(handler)
        import ytm_player.config.paths as paths_mod

        monkeypatch.setattr(paths_mod, "IPC_PORT_FILE", port_file)
        await server._start_tcp()
        try:
            token_file = port_file.with_name("ipc_token")
            assert (token_file.stat().st_mode & 0o777) == 0o600
        finally:
            await server.stop()
            try:
                tmp_dir.rmdir()
            except OSError:
                pass


def test_whitelist_matches_handler_cases():
    """The frozenset gate and the mixin's match arms must not drift apart.

    Reads app/_ipc.py as text (no heavy app import) and compares its
    ``case "..."`` command literals against _VALID_COMMANDS.
    """
    import re

    import ytm_player

    src_path = Path(ytm_player.__file__).parent / "app" / "_ipc.py"
    text = src_path.read_text(encoding="utf-8")
    cases = set(re.findall(r'case "(\w+)":', text))
    assert cases == set(_VALID_COMMANDS)
