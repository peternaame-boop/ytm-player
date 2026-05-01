"""File-based logging setup for ytm-player.

Why this exists: ytm-player runs inside Textual's alt-screen, which hides
stderr.  Calling logging.basicConfig() (which targets stderr by default)
means every logger.* call is silently lost — making bug reports
unactionable.  This module routes logs to a rotating file under
~/.config/ytm-player/logs/ytm.log and installs sys.excepthook /
threading.excepthook so unhandled crashes leave a paper trail under
~/.config/ytm-player/crashes/.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import traceback
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(threadName)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Module-level handle so setup_logging can be safely called twice.
_file_handler: RotatingFileHandler | None = None


def setup_logging(
    *,
    level: str = "WARNING",
    log_file: Path,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
) -> None:
    """Install a rotating file handler on the root logger.

    Idempotent — calling twice replaces the existing file handler rather
    than stacking duplicates.  Other handlers (e.g. an existing stderr
    handler from logging.basicConfig) are left in place; the caller is
    responsible for removing them if Textual is taking over the screen.
    """
    global _file_handler

    log_file.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    if _file_handler is not None and _file_handler in root.handlers:
        root.removeHandler(_file_handler)
        try:
            _file_handler.close()
        except Exception:
            logging.getLogger(__name__).debug("Failed to close prior file handler", exc_info=True)
        _file_handler = None

    handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))

    numeric_level = getattr(logging, level.upper(), logging.WARNING)
    root.setLevel(numeric_level)
    handler.setLevel(numeric_level)
    root.addHandler(handler)

    _file_handler = handler


def install_excepthooks(*, crash_dir: Path, keep: int = 10) -> None:
    """Install sys.excepthook and threading.excepthook to persist crashes.

    Each unhandled exception is written to crash_dir/ytm-crash-<TS>.log
    using O_NOFOLLOW so a pre-existing symlink can't redirect the write
    (defends against /tmp-style symlink attacks even though crash_dir
    should be in CONFIG_DIR with mode 0700).

    Old crash files beyond *keep* are pruned (oldest first).
    """
    crash_dir.mkdir(parents=True, exist_ok=True)

    def _write(traceback_text: str) -> Path | None:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = crash_dir / f"ytm-crash-{ts}.log"
        # O_NOFOLLOW defends against symlink redirection on POSIX; not
        # exposed by Windows' os module. Fall back to no-op there.
        flags = os.O_CREAT | os.O_WRONLY | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(str(path), flags, 0o600)
            try:
                os.write(fd, traceback_text.encode("utf-8"))
            finally:
                os.close(fd)
        except OSError:
            return None
        return path

    def _prune() -> None:
        try:
            files = sorted(crash_dir.glob("ytm-crash-*.log"))
            excess = len(files) - keep
            for old in files[:excess] if excess > 0 else []:
                try:
                    old.unlink()
                except OSError:
                    pass
        except Exception:
            pass

    def _sys_hook(exc_type, exc_value, exc_tb) -> None:
        # Don't pollute crash_dir with Ctrl-C exits.
        if not issubclass(exc_type, KeyboardInterrupt):
            text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            _write(f"=== Main thread crash ===\n{text}")
            _prune()
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        text = "".join(
            traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
        )
        _write(f"=== Thread crash ({args.thread.name}) ===\n{text}")  # type: ignore[reportOptionalMemberAccess]
        _prune()
        threading.__excepthook__(args)  # type: ignore[reportAttributeAccessIssue]

    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook


def get_recent_log_lines(log_file: Path, n: int = 50) -> str:
    """Return the last *n* lines of the log file (or empty string)."""
    if not log_file.exists():
        return ""
    try:
        with open(log_file, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:])
    except OSError:
        return ""


def get_recent_crash(crash_dir: Path) -> tuple[Path, str] | None:
    """Return (path, content) of the most recent crash file, or None."""
    if not crash_dir.exists():
        return None
    files = sorted(crash_dir.glob("ytm-crash-*.log"))
    if not files:
        return None
    latest = files[-1]
    try:
        return latest, latest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
