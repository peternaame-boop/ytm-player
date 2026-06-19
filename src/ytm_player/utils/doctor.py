"""Diagnostic gathering for the `ytm doctor` command.

Produces a single-string report users can paste directly into a GitHub
issue. v2 covers eight sections so every failure class is visible:
version, paths, process status, recent ERROR/WARNING, recent mpv
warnings, faulthandler trace, last crash file, active hooks. All
output passes through a redaction layer so auth tokens never leak.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

_REDACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Match header-value pairs — consume the rest of the line to catch multi-word tokens
    re.compile(r"(authorization\s*[:=]\s*)(.+)", re.IGNORECASE),
    re.compile(r"(cookie\s*[:=]\s*)(.+)", re.IGNORECASE),
    re.compile(r"(bearer\s+)(\S+)", re.IGNORECASE),
    re.compile(r"(token\s*[:=]\s*)(\S+)", re.IGNORECASE),
    re.compile(r"(x-goog-pageid\s*[:=]\s*)(\S+)", re.IGNORECASE),
    re.compile(r"(SAPISID\s*=\s*)(\S+)"),
)


def _redact(text: str) -> str:
    for pat in _REDACT_PATTERNS:
        text = pat.sub(r"\1[REDACTED]", text)
    return text


def _mpv_version() -> str:
    """Return mpv version string, or a clear missing marker."""
    mpv_bin = shutil.which("mpv")
    if not mpv_bin:
        return "mpv: NOT FOUND in PATH"
    try:
        out = subprocess.run(
            [mpv_bin, "--version"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        first_line = (out.stdout or out.stderr or "").splitlines()[0:1]
        return f"mpv: {first_line[0] if first_line else 'unknown'}"
    except (OSError, subprocess.SubprocessError):
        return "mpv: failed to execute"


def _libmpv_status() -> str:
    """Report whether the libmpv shared library is loadable.

    The mpv CLI being on PATH does not imply libmpv is discoverable —
    Homebrew installs (macOS and Linux) put the library where
    ctypes.util.find_library can't see it from a non-brew Python
    (#90, #101, #104). Importing our player module exercises the full
    discovery chain including the brew-prefix fallback.
    """
    try:
        from ytm_player.services.player import _IMPORT_ERROR_MSG  # type: ignore[attr-defined]

        first = str(_IMPORT_ERROR_MSG).splitlines()[-1]
        return f"libmpv: NOT LOADABLE ({first.strip()})"
    except ImportError:
        return "libmpv: OK"


def _running_status() -> str:
    """Best-effort check whether a ytm TUI process is currently running.

    Uses /proc on Linux (no psutil dependency); on other platforms or if
    /proc is unavailable, returns a 'cannot determine' marker.
    """
    proc_root = Path("/proc")
    if not proc_root.exists():
        return "(cannot determine — /proc not available on this OS)"
    pids: list[tuple[int, int]] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        cmdline_path = entry / "cmdline"
        try:
            cmdline_raw = cmdline_path.read_bytes()
        except OSError:
            continue
        cmdline = cmdline_raw.replace(b"\x00", b" ").decode("utf-8", errors="replace")
        argv = cmdline.split()
        if not argv:
            continue
        if "/ytm" in cmdline or argv[0].endswith("/ytm"):
            try:
                status = (entry / "status").read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rss_kb = 0
            for line in status.splitlines():
                if line.startswith("VmRSS:"):
                    try:
                        rss_kb = int(line.split()[1])
                    except (IndexError, ValueError):
                        rss_kb = 0
                    break
            pids.append((int(entry.name), rss_kb))
    pids = [(pid, rss) for pid, rss in pids if pid != os.getpid()]
    if not pids:
        return "ytm not running"
    parts = [f"PID {pid} (RSS {rss // 1024} MB)" for pid, rss in pids]
    return "ytm running: " + ", ".join(parts)


def _recent_mpv_lines(log_file: Path, n: int = 20) -> str:
    """Return the last *n* log lines that contain 'mpv[' (our log_handler prefix)."""
    if not log_file.exists():
        return "(log file is empty or missing)"
    try:
        with open(log_file, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return "(could not read log file)"
    matches = [ln for ln in lines if "mpv[" in ln]
    if not matches:
        return "(no mpv warnings/errors logged)"
    return "".join(matches[-n:])


def _recent_faulthandler(crash_dir: Path) -> str:
    """Return the last 'Fatal Python error' block from crash_dir/faulthandler.log,
    or a marker if the file is missing/empty."""
    fh_log = crash_dir / "faulthandler.log"
    if not fh_log.exists():
        return "(no faulthandler trace — file does not exist)"
    try:
        text = fh_log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(could not read faulthandler.log)"
    if not text.strip():
        return "(faulthandler.log is empty — no fatal signals captured)"
    marker = "Fatal Python error:"
    last_idx = text.rfind(marker)
    if last_idx == -1:
        return text[-4000:]
    return text[last_idx:]


def gather_diagnostics() -> str:
    """Return a multi-section text report describing the install."""
    from ytm_player import __version__
    from ytm_player.config.paths import (
        CONFIG_FILE,
        CRASH_DIR,
        LOG_FILE,
        SESSION_STATE_FILE,
        THEME_FILE,
    )
    from ytm_player.utils.logging import (
        get_recent_crash,
        get_recent_log_lines,
        list_active_hooks,
    )

    sections: list[str] = []

    sections.append("=== ytm-player diagnostics ===")
    sections.append(f"Version: {__version__}")
    sections.append(
        f"Python:  {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )
    sections.append(f"OS:      {platform.system()} {platform.release()}")
    sections.append(f"Machine: {platform.machine()}")
    sections.append(_mpv_version())
    sections.append(_libmpv_status())

    sections.append("")
    sections.append("=== Paths ===")
    sections.append(f"config:   {CONFIG_FILE}")
    sections.append(f"theme:    {THEME_FILE}")
    sections.append(f"session:  {SESSION_STATE_FILE}")
    sections.append(f"log:      {LOG_FILE}")
    sections.append(f"crashes:  {CRASH_DIR}")

    sections.append("")
    sections.append("=== Process status ===")
    sections.append(_running_status())

    sections.append("")
    sections.append("=== Recent ERROR/WARNING (last 20) ===")
    filtered = get_recent_log_lines(LOG_FILE, n=20, min_level="WARNING")
    sections.append(filtered if filtered else "(no warnings or errors logged)")

    sections.append("")
    sections.append("=== Recent mpv warnings/errors ===")
    sections.append(_recent_mpv_lines(LOG_FILE))

    sections.append("")
    sections.append("=== Most recent faulthandler trace ===")
    sections.append(_recent_faulthandler(CRASH_DIR))

    sections.append("")
    sections.append("=== Most recent crash file ===")
    crash = get_recent_crash(CRASH_DIR)
    if crash is None:
        sections.append("(no crash files found)")
    else:
        path, content = crash
        sections.append(f"From: {path}")
        sections.append(content)

    sections.append("")
    sections.append("=== Active hooks ===")
    sections.append(list_active_hooks())
    # `ytm doctor` is a short-lived subcommand running in its own Python
    # process — it doesn't go through the TUI startup path in cli.py, so
    # the hooks above always read as default/disabled here. The TUI process
    # has them installed; the proof is the artifacts above (faulthandler.log
    # gets created on TUI startup, crashes/ contains files when hooks fired).
    sections.append("")
    sections.append(
        "Note: this section reflects the doctor subcommand's own process. "
        "Hooks are installed inside the long-running ytm TUI process — "
        "their effect is visible above (crashes/, faulthandler.log)."
    )

    return _redact("\n".join(sections))
