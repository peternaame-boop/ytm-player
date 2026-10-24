"""IPC utilities: PID-file single-instance enforcement and command channel.

The CLI launch path claims the PID file atomically via ``try_claim_pid()``
before starting the TUI (refusing when another live instance holds it), the
app calls ``remove_pid()`` on shutdown, and creates an ``IPCServer`` so CLI
commands can talk to the running TUI via ``ipc_request()``.

On Linux/macOS, uses Unix domain sockets.  On Windows, uses TCP on localhost.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import socket
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable

from ytm_player.config.paths import PID_FILE, SECURE_FILE_MODE, secure_chmod

logger = logging.getLogger(__name__)

_MAX_MSG = 65536  # 64 KB
_CLIENT_TIMEOUT = 5  # seconds

# Auth-token file for the TCP transport (Windows). Lives next to the IPC port
# file; the Unix-socket transport relies on 0600 socket perms instead.
_TOKEN_FILE_NAME = "ipc_token"

# Whitelist of valid IPC commands.
_VALID_COMMANDS = frozenset(
    {
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
)


# ---------------------------------------------------------------------------
# PID helpers
# ---------------------------------------------------------------------------


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is running."""
    if sys.platform == "win32":
        # os.kill(pid, 0) on Windows can actually kill processes.
        # Use OpenProcess instead.
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _cmdline_of(pid: int) -> list[str] | None:
    """Best-effort argv of *pid*; None when it cannot be determined.

    Linux reads ``/proc/<pid>/cmdline``; macOS asks ``ps``; Windows can only
    resolve the executable image path (the full command line would need
    ``NtQueryInformationProcess``), so it returns a single-element list.
    """
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            kernel32.QueryFullProcessImageNameW.argtypes = [
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.c_wchar_p,
                ctypes.POINTER(ctypes.c_ulong),
            ]
            buf = ctypes.create_unicode_buffer(32768)
            size = ctypes.c_ulong(len(buf))
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return None
            return [buf.value] if buf.value else None
        finally:
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle(handle)
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    if proc_cmdline.parent.parent.exists():  # /proc present → Linux-style probe
        try:
            raw = proc_cmdline.read_bytes()
        except OSError:
            return None
        argv = [a for a in raw.decode("utf-8", errors="replace").split("\x00") if a]
        return argv or None
    # No /proc (macOS): ask ps for the full command line.
    import subprocess

    try:
        # -ww: never truncate the command line to terminal width — a
        # truncated `.../bin/ytm` path would misclassify a live TUI as
        # not-ours and get its PID file deleted.
        out = subprocess.run(
            ["ps", "-ww", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    argv = out.stdout.strip().split()
    return argv or None


def _looks_like_ytm(argv: list[str]) -> bool:
    """Heuristic: does *argv* look like a ytm-player entry point?

    Errs on the side of True — a false "ours" merely preserves the
    always-block behavior, while a false "not ours" would let a second
    instance steal a live TUI's PID file. Only positive evidence that the
    process is something else entirely may return False.
    """
    for arg in argv:
        # Separator-agnostic basename: Windows image paths must also parse
        # when this rule is exercised off-Windows (tests, ps output quirks).
        name = arg.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if name == "ytm" or name.startswith("ytm.") or "ytm_player" in arg:
            return True
    if len(argv) == 1:
        # Windows image-name-only probe: a bare interpreter path cannot
        # rule out `python -m ytm_player`.
        name = argv[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
        if name.startswith("python"):
            return True
    return False


def _is_ytm_process(pid: int) -> bool:
    """Whether the live *pid* actually looks like a ytm-player process.

    Unknown (cmdline unavailable) counts as ours — see ``_looks_like_ytm``.
    """
    argv = _cmdline_of(pid)
    if argv is None:
        return True
    return _looks_like_ytm(argv)


def _unlink_pid_if_still(expected_raw: str) -> bool:
    """Remove PID_FILE only if it still holds *expected_raw*.

    Between judging a file stale (which may involve a subprocess identity
    probe) and deleting it, a concurrent launcher can evict the same stale
    file and publish its own claim — deleting blindly would destroy that
    fresh claim and let two instances pass the guard. Re-reading right
    before the unlink closes that window down to the gap between two
    adjacent syscalls; the remaining microsecond-scale TOCTOU is accepted
    (fully closing it needs an eviction lock with its own staleness
    problem).

    Returns True when the stale file was actually removed, so callers can
    scope any follow-up cleanup (Windows IPC port/token files) to a real
    eviction and never touch a fresh claimant's files.
    """
    try:
        if PID_FILE.read_text(encoding="utf-8") != expected_raw:
            return False
    except OSError:
        return False  # already gone — nothing to clean
    PID_FILE.unlink(missing_ok=True)
    return True


def get_running_pid() -> int | None:
    """Return the PID of the live ytm-player TUI process, or None.

    Cleans up the stale PID file (and IPC port file on Windows) when the
    recorded process is dead — or when its PID was recycled by an unrelated
    process after a crash — so a fresh launch can proceed.
    """
    if not PID_FILE.exists():
        return None
    try:
        raw = PID_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        # Unreadable — evict so the claim loop keeps making progress.
        PID_FILE.unlink(missing_ok=True)
        return None
    try:
        pid = int(raw.strip())
    except ValueError:
        _unlink_pid_if_still(raw)
        return None
    if _is_pid_alive(pid) and _is_ytm_process(pid):
        return pid
    # Process is dead (or the PID now belongs to an unrelated process) —
    # clean up stale PID file and IPC port/token files (Windows). The
    # liveness/identity probes above take real time, so only remove the
    # file if no concurrent launcher replaced it with a fresh claim since
    # we read it — and only touch the IPC files after a real eviction
    # (they may already belong to the fresh claimant).
    if _unlink_pid_if_still(raw) and sys.platform == "win32":
        from ytm_player.config.paths import IPC_PORT_FILE

        if IPC_PORT_FILE is not None:
            IPC_PORT_FILE.unlink(missing_ok=True)
            IPC_PORT_FILE.with_name(_TOKEN_FILE_NAME).unlink(missing_ok=True)
    return None


def is_tui_running() -> bool:
    """Return True if a ytm-player TUI process is alive."""
    return get_running_pid() is not None


def try_claim_pid() -> int | None:
    """Atomically claim the PID file for this process.

    Returns None when the claim succeeded (this process is now the
    recorded instance). Returns the other instance's PID when a live
    ytm-player already holds the file. Stale files are cleaned up and
    re-claimed.

    The claim publishes the file with its PID content in one atomic step
    (hard link of a pre-written temp file), so two processes launching
    simultaneously cannot both succeed — the loser sees the winner's PID —
    and no reader can ever observe a claimed-but-empty file.
    """
    while True:
        existing = get_running_pid()
        if existing is not None:
            return existing
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        if _try_create_pid_file():
            return None
        # Lost the race to a concurrently-launching instance — loop to
        # read who won (or clean up if it already died). Converges: an
        # existing file either names a live PID (returned) or a dead
        # one (cleaned, then re-claimed).


def _try_create_pid_file() -> bool:
    """Atomically publish PID_FILE naming this process.

    Returns False when the file already exists (another claimant won).
    """
    tmp = PID_FILE.with_name(f"{PID_FILE.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(str(os.getpid()), encoding="utf-8")
        secure_chmod(tmp, 0o600)
        try:
            os.link(tmp, PID_FILE)
            return True
        except FileExistsError:
            return False
        except OSError:
            # Filesystem without hard-link support — fall back to plain
            # exclusive create. This creates the file before its content
            # lands, so a concurrent get_running_pid() may garbage-collect
            # the momentarily-empty file and re-claim. The re-read below
            # detects that (our PID no longer in the file → defer); a
            # narrower interleaving remains theoretically possible and is
            # accepted on such exotic setups.
            try:
                fd = os.open(str(PID_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                return False
            try:
                os.write(fd, str(os.getpid()).encode("utf-8"))
            finally:
                os.close(fd)
            secure_chmod(PID_FILE, 0o600)
            try:
                return PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid())
            except OSError:
                return False
    finally:
        tmp.unlink(missing_ok=True)


def remove_pid() -> None:
    """Remove the PID file, but only if it still belongs to this process.

    Guards against deleting a newer instance's claim (possible when a
    user manually removed the file while this instance was running and
    another instance claimed it since).
    """
    try:
        if int(PID_FILE.read_text(encoding="utf-8").strip()) != os.getpid():
            return
    except (ValueError, OSError):
        pass  # Missing or unreadable — unlinking is harmless either way.
    PID_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# IPC auth token (TCP transport only)
# ---------------------------------------------------------------------------


def _token_file_path() -> Path | None:
    """Path of the TCP auth-token file, alongside the IPC port file.

    ``None`` on Unix, where the token mechanism is unused (IPC_PORT_FILE is
    ``None`` there; the Unix socket relies on 0600 perms instead).
    """
    from ytm_player.config.paths import IPC_PORT_FILE

    if IPC_PORT_FILE is None:
        return None
    return IPC_PORT_FILE.with_name(_TOKEN_FILE_NAME)


def _write_token_file(token: str) -> None:
    """Persist *token* owner-only next to the IPC port file."""
    token_path = _token_file_path()
    if token_path is None:
        return
    token_path.parent.mkdir(parents=True, exist_ok=True)
    # Never write the secret into a pre-existing inode — a stale file could
    # carry loose permission bits (O_TRUNC preserves them). Drop it, then
    # create exclusively with owner-only mode; O_EXCL failing means another
    # process raced the path, so fail closed rather than reuse its inode.
    # On Windows the mode bits are ignored (NTFS) but the file lives under
    # the per-user profile.
    token_path.unlink(missing_ok=True)
    fd = os.open(str(token_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, SECURE_FILE_MODE)
    try:
        os.write(fd, token.encode("utf-8"))
    finally:
        os.close(fd)
    secure_chmod(token_path, SECURE_FILE_MODE)


def _read_token() -> str | None:
    """Read the TCP auth token, or ``None`` when the file is absent/unreadable."""
    token_path = _token_file_path()
    if token_path is None or not token_path.exists():
        return None
    try:
        return token_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# IPC Server (runs inside the TUI's asyncio loop)
# ---------------------------------------------------------------------------

# Handler signature: async (command: str, args: dict) -> dict
IPCHandler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class IPCServer:
    """Async IPC server for CLI commands.

    Uses Unix domain sockets on Linux/macOS and TCP localhost on Windows.
    The *handler* receives ``(command, args)`` and must return a JSON-serialisable dict.
    """

    def __init__(self, handler: IPCHandler) -> None:
        self._handler = handler
        self._server: asyncio.AbstractServer | None = None
        # Set only on the TCP transport; when non-None every request must
        # carry a matching ``token``. Unix sockets leave this None.
        self._token: str | None = None

    async def start(self) -> None:
        if sys.platform == "win32":
            await self._start_tcp()
        else:
            await self._start_unix()

    async def _start_unix(self) -> None:
        import os

        from ytm_player.config.paths import SOCKET_PATH

        # Remove stale socket.
        SOCKET_PATH.unlink(missing_ok=True)
        # Tighten umask so the socket is created owner-only from the kernel's
        # perspective — closes the race between bind() and the secure_chmod()
        # below where another local user could otherwise connect.
        prev_umask = os.umask(0o077)
        try:
            self._server = await asyncio.start_unix_server(
                self._client_connected, path=str(SOCKET_PATH), limit=_MAX_MSG
            )
        finally:
            os.umask(prev_umask)
        secure_chmod(SOCKET_PATH, 0o600)
        logger.info("IPC server listening on %s", SOCKET_PATH)

    async def _start_tcp(self) -> None:
        from ytm_player.config.paths import IPC_PORT_FILE

        # _start_tcp is only invoked on Windows where IPC_PORT_FILE is set;
        # on Unix start_unix_server is used instead and IPC_PORT_FILE is None.
        assert IPC_PORT_FILE is not None
        # Bind to localhost with a random available port. Cap the read buffer at
        # _MAX_MSG so an oversized line raises instead of buffering unbounded.
        self._server = await asyncio.start_server(
            self._client_connected, host="127.0.0.1", port=0, limit=_MAX_MSG
        )
        # Save the port so the client can find us.
        addr = self._server.sockets[0].getsockname()
        port = addr[1]
        IPC_PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        IPC_PORT_FILE.write_text(str(port), encoding="utf-8")
        # The TCP path has no socket-permission protection (unlike the Unix
        # socket's 0600 mode), so any local process could otherwise connect and
        # drive playback / mutate account state. Gate it behind a random token
        # persisted owner-only next to the port file.
        self._token = secrets.token_hex(32)
        _write_token_file(self._token)
        logger.info("IPC server listening on 127.0.0.1:%d", port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        if self._token is not None:
            # TCP transport — remove the port and token files.
            from ytm_player.config.paths import IPC_PORT_FILE

            if IPC_PORT_FILE is not None:
                IPC_PORT_FILE.unlink(missing_ok=True)
            token_path = _token_file_path()
            if token_path is not None:
                token_path.unlink(missing_ok=True)
        else:
            from ytm_player.config.paths import SOCKET_PATH

            if SOCKET_PATH is not None:
                SOCKET_PATH.unlink(missing_ok=True)

        logger.info("IPC server stopped")

    async def _client_connected(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            # Newline-delimited framing: read exactly one JSON line. readline()
            # loops until it sees "\n" (or EOF), so fragmented writes are
            # reassembled instead of parsed as truncated JSON. It raises when a
            # single line exceeds the stream limit (_MAX_MSG).
            try:
                raw = await asyncio.wait_for(reader.readline(), timeout=_CLIENT_TIMEOUT)
            except (ValueError, asyncio.LimitOverrunError):
                await self._respond(writer, {"ok": False, "error": "payload too large"})
                return

            if not raw:
                return

            try:
                request = json.loads(raw.decode("utf-8", errors="replace"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                await self._respond(writer, {"ok": False, "error": "invalid JSON"})
                return

            if not isinstance(request, dict):
                await self._respond(writer, {"ok": False, "error": "expected JSON object"})
                return

            # TCP transport has no socket-permission auth — require a matching
            # token before dispatching (rejects any local process without it).
            if self._token is not None:
                token = request.get("token")
                # Compare as bytes: compare_digest raises TypeError on
                # non-ASCII str input, which would surface as "internal
                # error" instead of a proper auth rejection.
                if not isinstance(token, str) or not secrets.compare_digest(
                    token.encode("utf-8"), self._token.encode("utf-8")
                ):
                    await self._respond(writer, {"ok": False, "error": "authentication failed"})
                    return

            command = request.get("command", "")
            if not isinstance(command, str) or command not in _VALID_COMMANDS:
                await self._respond(writer, {"ok": False, "error": f"unknown command: {command}"})
                return

            args = request.get("args", {})
            if not isinstance(args, dict):
                args = {}

            response = await self._handler(command, args)
            await self._respond(writer, response)
        except asyncio.TimeoutError:
            logger.debug("IPC client timed out")
        except Exception:
            logger.debug("IPC client error", exc_info=True)
            try:
                await self._respond(writer, {"ok": False, "error": "internal error"})
            except Exception:
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _respond(self, writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
        """Write one newline-terminated JSON response line and flush."""
        writer.write((json.dumps(payload) + "\n").encode("utf-8"))
        await writer.drain()


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
    if sys.platform == "win32":
        return _ipc_request_tcp(command, args, timeout)
    return _ipc_request_unix(command, args, timeout)


def _ipc_exchange(sock: socket.socket, request: dict[str, Any]) -> dict[str, Any]:
    """Send one newline-framed request over *sock* and return the parsed reply.

    The server writes exactly one newline-terminated JSON line then closes, so
    reading to EOF yields that single reply line (json.loads ignores the
    trailing newline).
    """
    sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
    sock.shutdown(socket.SHUT_WR)

    chunks: list[bytes] = []
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)

    return json.loads(b"".join(chunks).decode("utf-8"))


def _ipc_request_unix(
    command: str,
    args: dict[str, Any] | None,
    timeout: float,
) -> dict[str, Any]:
    from ytm_player.config.paths import SOCKET_PATH

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(SOCKET_PATH))
        return _ipc_exchange(sock, {"command": command, "args": args or {}})
    finally:
        sock.close()


def _ipc_request_tcp(
    command: str,
    args: dict[str, Any] | None,
    timeout: float,
) -> dict[str, Any]:
    from ytm_player.config.paths import IPC_PORT_FILE

    if IPC_PORT_FILE is None or not IPC_PORT_FILE.exists():
        raise FileNotFoundError("IPC port file not found — is ytm-player running?")

    port = int(IPC_PORT_FILE.read_text(encoding="utf-8").strip())
    token = _read_token()
    if token is None:
        raise FileNotFoundError("IPC token file not found — is ytm-player running?")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(("127.0.0.1", port))
        return _ipc_exchange(sock, {"command": command, "args": args or {}, "token": token})
    finally:
        sock.close()
