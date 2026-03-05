"""Centralized path definitions for ytm-player.

Single source of truth for all filesystem paths used across the application.
Respects $XDG_CONFIG_HOME and $XDG_CACHE_HOME when set.
On Windows, uses %APPDATA% for config and %LOCALAPPDATA% for cache.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

SECURE_FILE_MODE = 0o600
SECURE_DIR_MODE = 0o700

# --- Platform-aware base directories ---

if sys.platform == "win32":
    _app_data = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    _local_data = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
    CONFIG_DIR = Path(_app_data) / "ytm-player"
    CACHE_DIR = Path(_local_data) / "ytm-player" / "audio"
    CACHE_DB = Path(_local_data) / "ytm-player" / "cache.db"
else:
    _xdg_config = os.environ.get("XDG_CONFIG_HOME")
    _xdg_cache = os.environ.get("XDG_CACHE_HOME")
    CONFIG_DIR = (
        (Path(_xdg_config) / "ytm-player")
        if _xdg_config
        else (Path.home() / ".config" / "ytm-player")
    )
    CACHE_DIR = (
        (Path(_xdg_cache) / "ytm-player" / "audio")
        if _xdg_cache
        else (Path.home() / ".cache" / "ytm-player" / "audio")
    )
    CACHE_DB = (
        (Path(_xdg_cache) / "ytm-player" / "cache.db")
        if _xdg_cache
        else (Path.home() / ".cache" / "ytm-player" / "cache.db")
    )

CONFIG_FILE = CONFIG_DIR / "config.toml"
AUTH_FILE = CONFIG_DIR / "auth.json"
OAUTH_FILE = CONFIG_DIR / "oauth.json"
OAUTH_CREDS_FILE = CONFIG_DIR / "oauth_creds.json"
SPOTIFY_CREDS_FILE = CONFIG_DIR / "spotify.json"
PID_FILE = CONFIG_DIR / "ytm.pid"

# IPC: Unix sockets on Linux/macOS, TCP localhost on Windows (see ipc.py).
# Unix sockets have a ~108 byte path limit. Use XDG_RUNTIME_DIR (short,
# per-user, tmpfs) when available, fall back to CONFIG_DIR.
if sys.platform == "win32":
    # Windows uses TCP; port is stored in this file by the server.
    IPC_PORT_FILE = CONFIG_DIR / "ipc_port"
    SOCKET_PATH = None  # Not used on Windows.
else:
    _xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    SOCKET_PATH = (
        (Path(_xdg_runtime) / "ytm-player.sock") if _xdg_runtime else (CONFIG_DIR / "ytm.sock")
    )
    IPC_PORT_FILE = None  # Not used on Unix.

KEYMAP_FILE = CONFIG_DIR / "keymap.toml"
THEME_FILE = CONFIG_DIR / "theme.toml"
RECENT_PLAYLISTS_FILE = CONFIG_DIR / "recent_playlists.json"
SESSION_STATE_FILE = CONFIG_DIR / "session.json"
HISTORY_DB = CONFIG_DIR / "history.db"
CRASH_LOG = Path(tempfile.gettempdir()) / "ytm-crash.log"

_dirs_ensured = False


def secure_chmod(path: Path | str, mode: int) -> None:
    """Set file permissions — no-op on Windows where Unix modes are meaningless."""
    if sys.platform != "win32":
        os.chmod(path, mode)


def ensure_dirs() -> None:
    """Create config and cache directories with secure permissions.

    Called lazily on first invocation (not at import time) so that merely
    importing the module does not create directories on disk — important
    for test isolation.
    """
    global _dirs_ensured
    if _dirs_ensured:
        return
    for _dir in (CONFIG_DIR, CACHE_DIR):
        _dir.mkdir(parents=True, exist_ok=True)
        secure_chmod(_dir, SECURE_DIR_MODE)
    _dirs_ensured = True
