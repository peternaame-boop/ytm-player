"""T8 (A4/L2): the CLI's IPC client returns the reply line without waiting for EOF.

After ``reply_now`` the server keeps the connection open until its handler
finishes, so a client that reads to EOF only returns (or times out) once the
handler is done. The client must stop at the first complete newline-framed
line, even when that line arrives over several receives.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import pytest

import ytm_player.config.paths as paths_mod
from ytm_player.ipc import (
    IPCServer,
    _ipc_exchange,
    _ipc_request_tcp,
    _ipc_request_unix,
    reply_now,
)


def _blocked_handler():
    """Handler that replies early, then stays blocked until *release* is set."""
    release = asyncio.Event()
    finished: list[str] = []

    async def handler(command: str, args: dict) -> dict:
        reply_now({"ok": True, "early": command})
        await release.wait()
        finished.append(command)
        return {"ok": True, "late": True}

    return handler, release, finished


async def test_tcp_client_gets_early_reply_while_handler_is_blocked(monkeypatch):
    tmp_dir = Path(tempfile.mkdtemp(prefix="ytm-ipc-"))
    port_file = tmp_dir / "ipc_port"
    handler, release, finished = _blocked_handler()
    server = IPCServer(handler)
    monkeypatch.setattr(paths_mod, "IPC_PORT_FILE", port_file)
    await server._start_tcp()
    try:
        resp = await asyncio.to_thread(_ipc_request_tcp, "next", {}, 5.0)
        assert resp == {"ok": True, "early": "next"}
        assert finished == []  # the handler had not finished when the client returned
    finally:
        release.set()
        await server.stop()
        try:
            tmp_dir.rmdir()
        except OSError:
            pass


@pytest.mark.skipif(sys.platform == "win32", reason="AF_UNIX is not available on Windows")
async def test_unix_client_gets_early_reply_while_handler_is_blocked(monkeypatch):
    # Short path: AF_UNIX sun_path is capped at 104 bytes on macOS.
    tmp_dir = Path(tempfile.mkdtemp(prefix="ytm-ipc-"))
    socket_path = tmp_dir / "s"
    handler, release, finished = _blocked_handler()
    server = IPCServer(handler)
    monkeypatch.setattr(paths_mod, "SOCKET_PATH", socket_path)
    await server.start()
    try:
        resp = await asyncio.to_thread(_ipc_request_unix, "next", {}, 5.0)
        assert resp == {"ok": True, "early": "next"}
        assert finished == []
    finally:
        release.set()
        await server.stop()
        try:
            tmp_dir.rmdir()
        except OSError:
            pass


class _OpenFragmentedSocket:
    """Socket stand-in: delivers the reply in fragments and never sends EOF.

    A further ``recv`` after the last fragment raises the timeout a real
    socket would raise while the server keeps the connection open.
    """

    def __init__(self, fragments: list[bytes]) -> None:
        self._fragments = list(fragments)
        self.sent = b""
        self.recv_calls = 0

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def shutdown(self, how: int) -> None:
        pass

    def recv(self, bufsize: int) -> bytes:
        self.recv_calls += 1
        if not self._fragments:
            raise TimeoutError("timed out")
        return self._fragments.pop(0)


@pytest.mark.parametrize(
    "fragments",
    [
        [b'{"ok": true, "ear', b'ly": "next"}\n'],
        [b'{"ok": tr', b'ue, "early": "next"}', b"\n"],
    ],
    ids=["newline-in-last-fragment", "newline-alone"],
)
def test_fragmented_reply_is_reassembled_without_eof(fragments):
    sock = _OpenFragmentedSocket(fragments)

    resp = _ipc_exchange(sock, {"command": "next", "args": {}})  # type: ignore[arg-type]

    assert resp == {"ok": True, "early": "next"}
    assert sock.recv_calls == len(fragments)
    assert sock.sent == b'{"command": "next", "args": {}}\n'
