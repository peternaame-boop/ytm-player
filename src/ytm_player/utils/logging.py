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
import re
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


_crash_dir: Path | None = None
_crash_keep: int = 10


def _crash_metadata_header(label: str) -> str:
    """Build the self-identifying header prepended to every crash file.

    Records the app version, time, Python, and platform so a crash file is
    interpretable on its own and ``ytm doctor`` can tell whether a crash
    predates the running build — otherwise a stale, already-fixed crash log
    reads as a live bug (the #89 ANSI-theme crash log did exactly that).
    """
    import platform

    try:
        from ytm_player import __version__
    except Exception:
        __version__ = "unknown"
    return (
        f"=== {label} ===\n"
        f"version:   {__version__}\n"
        f"time:      {datetime.now().isoformat(timespec='seconds')}\n"
        f"python:    {platform.python_version()}\n"
        f"platform:  {platform.system()} {platform.release()} ({platform.machine()})\n"
        "\n"
    )


def write_crash_file(traceback_text: str, *, label: str = "Crash") -> Path | None:
    """Write *traceback_text* to a fresh crash file in the configured crash dir.

    Module-level so any code path that catches an exception that would
    otherwise be silently swallowed (notably ``App._handle_exception`` —
    Textual's worker/render error path which bypasses ``sys.excepthook``)
    can persist it for later inspection via ``ytm doctor``.

    Returns the written path, or None if writing failed.

    Self-bootstraps: if ``install_excepthooks`` was never called (e.g.
    a unit-test harness imports the App directly without going through
    ``cli.py``), falls back to ``paths.CRASH_DIR`` so we never silently
    swallow a crash trace.
    """
    global _crash_dir
    if _crash_dir is None:
        try:
            from ytm_player.config.paths import CRASH_DIR

            CRASH_DIR.mkdir(parents=True, exist_ok=True)
            _crash_dir = CRASH_DIR
        except Exception:
            logging.getLogger(__name__).exception(
                "write_crash_file: no _crash_dir configured and fallback failed"
            )
            return None
    ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = _crash_dir / f"ytm-crash-{ts}.log"
    flags = os.O_CREAT | os.O_WRONLY | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(path), flags, 0o600)
        try:
            os.write(fd, (_crash_metadata_header(label) + traceback_text).encode("utf-8"))
        finally:
            os.close(fd)
    except OSError:
        # Surface the *reason* — silent-None made the previous bug invisible.
        logging.getLogger(__name__).exception("write_crash_file: failed to write %s", path)
        return None
    _prune_crash_dir()
    return path


def _prune_crash_dir() -> None:
    if _crash_dir is None:
        return
    try:
        files = sorted(_crash_dir.glob("ytm-crash-*.log"))
        excess = len(files) - _crash_keep
        for old in files[:excess] if excess > 0 else []:
            try:
                old.unlink()
            except OSError:
                pass
    except Exception:
        pass


def install_excepthooks(*, crash_dir: Path, keep: int = 10) -> None:
    """Install sys.excepthook and threading.excepthook to persist crashes.

    Each unhandled exception is written to crash_dir/ytm-crash-<TS>.log
    using O_NOFOLLOW so a pre-existing symlink can't redirect the write
    (defends against /tmp-style symlink attacks even though crash_dir
    should be in CONFIG_DIR with mode 0700).

    Old crash files beyond *keep* are pruned (oldest first).

    Note: this hooks Python-level uncaught exceptions only. Textual's
    ``App._handle_exception`` path (worker / render / event errors)
    bypasses ``sys.excepthook`` — see ``write_crash_file`` for the
    module-level writer used by the App's ``_handle_exception`` override.
    """
    global _crash_dir, _crash_keep
    crash_dir.mkdir(parents=True, exist_ok=True)
    _crash_dir = crash_dir
    _crash_keep = keep

    def _sys_hook(exc_type, exc_value, exc_tb) -> None:
        # Don't pollute crash_dir with Ctrl-C exits.
        if not issubclass(exc_type, KeyboardInterrupt):
            text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            write_crash_file(text, label="Main thread crash")
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        text = "".join(
            traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
        )
        thread_name = args.thread.name if args.thread is not None else "<unknown>"
        write_crash_file(text, label=f"Thread crash ({thread_name})")
        threading.__excepthook__(args)

    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook

    def _unraisable_hook(args) -> None:
        """Capture errors swallowed during finalisation (__del__, weakref
        callbacks, generator close, etc.).

        Default behaviour prints to stderr — invisible inside Textual.
        """
        text = "".join(
            traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
        )
        obj_repr = repr(args.object) if args.object is not None else "<unknown>"
        write_crash_file(text, label=f"Unraisable in {obj_repr}")
        sys.__unraisablehook__(args)

    sys.unraisablehook = _unraisable_hook


# The [LEVEL] token our log format writes on a record's first line:
# "%(asctime)s [%(levelname)s] [%(threadName)s] %(name)s: %(message)s"
_LEVEL_TOKEN = re.compile(r"\[(DEBUG|INFO|WARNING|ERROR|CRITICAL)\]")
# Lines kept under one filtered record (a logger.exception traceback): the
# last ones, which end in the exception itself.
_MAX_CONTINUATION_LINES = 40


def get_recent_log_lines(log_file: Path, n: int = 50, *, min_level: str | None = None) -> str:
    """Return the last *n* lines of *log_file*.

    With *min_level* (case-insensitive: DEBUG / INFO / WARNING / ERROR /
    CRITICAL) it returns the last *n* records at or above that level
    instead, each with the lines logged under it, such as the traceback
    of a logger.exception call (#181), capped at the last
    _MAX_CONTINUATION_LINES. Unknown level → no filter applied."""
    if not log_file.exists():
        return ""
    try:
        with open(log_file, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return ""
    if min_level is not None:
        order = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        try:
            threshold = order.index(min_level.upper())
        except ValueError:
            return "".join(lines[-n:])
        wanted = set(order[threshold:])
        # A line without a [LEVEL] token continues the record above it.
        records: list[list[str]] = []
        keep = False
        for ln in lines:
            token = _LEVEL_TOKEN.search(ln)
            if token is not None:
                keep = token.group(1) in wanted
                if keep:
                    records.append([ln])
            elif keep:
                records[-1].append(ln)
        out: list[str] = []
        for first, *rest in records[-n:]:
            out.append(first)
            if len(rest) > _MAX_CONTINUATION_LINES:
                hidden = len(rest) - _MAX_CONTINUATION_LINES
                out.append(f"    ... ({hidden} earlier lines not shown)\n")
                rest = rest[-_MAX_CONTINUATION_LINES:]
            out.extend(rest)
        return "".join(out)
    return "".join(lines[-n:])


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


def list_active_hooks() -> str:
    """Return a human-readable summary of which crash hooks are installed.

    Used by ytm doctor's 'Active hooks' section as a sanity check —
    if any hook reports 'default (unhooked)' when ytm is running, the
    diagnostic story is broken and finalisation/fatal-signal failures
    will be invisible.
    """
    import faulthandler as _fh

    lines = [
        f"sys.excepthook:        {'installed' if sys.excepthook is not sys.__excepthook__ else 'default (unhooked)'}",
        f"threading.excepthook:  {'installed' if threading.excepthook is not threading.__excepthook__ else 'default (unhooked)'}",
        f"sys.unraisablehook:    {'installed' if sys.unraisablehook is not sys.__unraisablehook__ else 'default (unhooked)'}",
        f"faulthandler:          {'enabled' if _fh.is_enabled() else 'disabled'}",
    ]
    return "\n".join(lines)
