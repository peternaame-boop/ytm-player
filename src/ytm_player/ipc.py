"""IPC utilities: PID-file single-instance enforcement and Unix-socket command channel.

The TUI app calls ``write_pid()`` / ``remove_pid()`` for single-instance checks,
and creates an ``IPCServer`` so CLI commands can talk to the running TUI via
``ipc_request()``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from typing import Any, Callable, Awaitable

from ytm_player.config.paths import PID_FILE, SOCKET_PATH

logger = logging.getLogger(__name__)

_MAX_MSG = 65536  # 64 KB
_CLIENT_TIMEOUT = 5  # seconds


# ---------------------------------------------------------------------------
# PID helpers (unchanged)
# ---------------------------------------------------------------------------


def is_tui_running() -> bool:
    """Return True if a ytm-player TUI process is alive."""
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        PID_FILE.unlink(missing_ok=True)
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        PID_FILE.unlink(missing_ok=True)
        return False


def write_pid() -> None:
    """Write the current process PID to the PID file."""
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def remove_pid() -> None:
    """Remove the PID file."""
    PID_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# IPC Server (runs inside the TUI's asyncio loop)
# ---------------------------------------------------------------------------

# Handler signature: async (command: str, args: dict) -> dict
IPCHandler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class IPCServer:
    """Async Unix-domain-socket server for IPC commands.

    The *handler* receives ``(command, args)`` and must return a JSON-serialisable dict.
    """

    def __init__(self, handler: IPCHandler) -> None:
        self._handler = handler
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        # Remove stale socket.
        SOCKET_PATH.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(
            self._client_connected, path=str(SOCKET_PATH)
        )
        logger.info("IPC server listening on %s", SOCKET_PATH)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        SOCKET_PATH.unlink(missing_ok=True)
        logger.info("IPC server stopped")

    async def _client_connected(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            raw = await asyncio.wait_for(reader.read(_MAX_MSG), timeout=_CLIENT_TIMEOUT)
            if not raw:
                return

            request = json.loads(raw.decode())
            command = request.get("command", "")
            args = request.get("args", {})

            response = await self._handler(command, args)
            writer.write(json.dumps(response).encode())
            await writer.drain()
        except asyncio.TimeoutError:
            logger.debug("IPC client timed out")
        except Exception:
            logger.debug("IPC client error", exc_info=True)
            try:
                writer.write(json.dumps({"ok": False, "error": "internal error"}).encode())
                await writer.drain()
            except Exception:
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# IPC Client (blocking, used by CLI commands)
# ---------------------------------------------------------------------------


def ipc_request(
    command: str,
    args: dict[str, Any] | None = None,
    timeout: float = _CLIENT_TIMEOUT,
) -> dict[str, Any]:
    """Send a command to the running TUI and return the response dict.

    Raises ``ConnectionRefusedError`` or ``FileNotFoundError`` when the
    TUI is unreachable.
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(SOCKET_PATH))
        payload = json.dumps({"command": command, "args": args or {}}).encode()
        sock.sendall(payload)
        sock.shutdown(socket.SHUT_WR)

        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)

        return json.loads(b"".join(chunks).decode())
    finally:
        sock.close()
