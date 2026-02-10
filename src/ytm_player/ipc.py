"""IPC utilities for detecting whether the TUI is running.

Uses a PID file at ~/.config/ytm-player/ytm.pid to track the running
TUI process. The TUI app should call ``write_pid()`` on start and
``remove_pid()`` on exit.
"""

from __future__ import annotations

import os

from ytm_player.config.paths import PID_FILE


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
        # Process doesn't exist -- stale PID file.
        PID_FILE.unlink(missing_ok=True)
        return False


def write_pid() -> None:
    """Write the current process PID to the PID file."""
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def remove_pid() -> None:
    """Remove the PID file."""
    PID_FILE.unlink(missing_ok=True)
