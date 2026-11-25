"""Diagnostic gathering for `ytm doctor --local`.

The detailed report, for the user's own eyes: version, paths, process status,
MPRIS / media keys, recent ERROR/WARNING, recent mpv warnings, faulthandler
trace, last crash file, active hooks. It quotes logs and crash files, so it is
not meant for public posting; the paste-safe report is utils/report.py. All
output passes through redaction for recognized auth headers/tokens and
HTTP(S)/TCP URLs, which is best-effort: arbitrary secrets embedded in
diagnostic text are not guaranteed to be removed.
"""

from __future__ import annotations

import io
import os
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from ytm_player.utils.report import (
    LIBMPV_STATUS,
    crash_header_version,
    crash_staleness_status,
    exception_class,
    probe_libmpv,
    read_text,
    render_probe,
    run_probe,
)

# Stream URLs may contain the public IP and signed playback parameters.
# Remove the whole URL rather than guessing which query fields are sensitive.
_URL_PATTERN = re.compile(r"\b(?:https?|tcp)://[^\s<>\"']+", re.IGNORECASE)

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
    return _URL_PATTERN.sub("[URL REDACTED]", text)


def _mpv_version() -> str:
    """Return mpv version string, or a clear missing marker."""
    mpv_bin = shutil.which("mpv")
    if not mpv_bin:
        return "mpv: NOT FOUND in PATH"
    try:
        out = subprocess.run(
            # --no-config: without it mpv creates its config directory.
            [mpv_bin, "--no-config", "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=3,
            check=False,
        )
        text = (out.stdout or out.stderr or b"").decode("utf-8", errors="replace")
        first_line = text.splitlines()[0:1]
        return f"mpv: {first_line[0] if first_line else 'unknown'}"
    except (OSError, subprocess.SubprocessError):
        return "mpv: failed to execute"


def _probed(label: str, probe: str) -> str:
    """One status line from a probe run in a child interpreter.

    Loading python-mpv or dbus-fast runs third-party code, which may print or
    fail in any way; see report.run_probe().
    """
    result = run_probe(probe)
    return result if isinstance(result, str) else f"{label}: {render_probe(result, {})}"


def _libmpv_status() -> str:
    return _probed("libmpv", "local_libmpv")


def _libmpv_detail() -> str:
    """Report whether the libmpv shared library is loadable.

    The mpv CLI being on PATH does not imply libmpv is discoverable —
    Homebrew installs (macOS and Linux) put the library where
    ctypes.util.find_library can't see it from a non-brew Python
    (#90, #101, #104). Importing our player module exercises the full
    discovery chain including the brew-prefix fallback. Runs in the probe
    child (_libmpv_status).
    """
    try:
        from ytm_player.services.player import _IMPORT_ERROR_MSG  # type: ignore[attr-defined]

        first = str(_IMPORT_ERROR_MSG).splitlines()[-1]
        return f"libmpv: NOT LOADABLE ({first.strip()})"
    except ImportError:
        # No stub error message. That alone is not proof libmpv loaded:
        # python-mpv may be missing altogether.
        return f"libmpv: {render_probe(probe_libmpv(), LIBMPV_STATUS)}"


def _mpris_status() -> str:
    if sys.platform != "linux":
        return f"MPRIS: n/a ({platform.system()} uses its native media integration)"
    return _probed("MPRIS", "local_mpris")


def _mpris_detail() -> str:
    """Report whether MPRIS (playerctl / media keys) can run on this install.

    MPRIS needs dbus-fast, a Linux core dependency. If it's missing — or its
    build is incompatible and blew up while our interfaces were defined (#113) —
    the install is broken/partial, the TUI runs without D-Bus controls, and
    playerctl finds no player (#110) — so surface the gap and the fix here.
    doctor runs in its own process, so this reflects dependency availability —
    not whether the live TUI currently holds the bus name. Runs in the probe
    child (_mpris_status).
    """
    from ytm_player.services.mpris import BUS_NAME, DBUS_AVAILABLE

    if DBUS_AVAILABLE:
        return f"MPRIS: available (dbus-fast OK; registers as {BUS_NAME})"
    return (
        "MPRIS: UNAVAILABLE — dbus-fast is missing or failed to load (it's a "
        "Linux core dependency, so this install is broken/partial; an "
        "incompatible dbus-fast build logs the failure to ytm.log — see the "
        "recent-errors section below). Fix: reinstall ytm-player "
        "(pip install --force-reinstall ytm-player), or pip install dbus-fast."
    )


def _launches_tui(rest: list[str]) -> bool:
    """True when the arguments after the entry point start the TUI.

    That is `ytm` with nothing but --debug / --json, optionally closed by
    ``--``. Anything else is a subcommand (`ytm setup`, `ytm report`) or an
    option that prints and exits (--help, --version).
    """
    if rest and rest[-1] == "--":
        rest = rest[:-1]
    return all(arg in ("--debug", "--json") for arg in rest)


def _argv_is_ytm(argv: list[str]) -> bool:
    """True when *argv* is a ytm-player TUI process.

    Matches the `ytm` console script (which the kernel launches as
    ``python /…/bin/ytm`` — interpreter in argv[0], script path in argv[1], named .ytm-wrapped in the Nix package)
    and ``python -m ytm_player``, when what follows launches the TUI (see
    _launches_tui). Deliberately does NOT match unrelated processes whose
    cmdline merely contains a path like /ytm-player or …/ytm_player/… (e.g.
    an editor with the repo open), nor a script that is merely passed
    ``-m ytm_player`` or ``ytm`` as an argument.
    """
    if not argv:
        return False
    head = Path(argv[0]).name.lower()
    if head in ("ytm", "ytm.exe"):
        return _launches_tui(argv[1:])
    if not head.startswith(("python", "pypy")):
        return False
    # What the interpreter runs: skip its own options up to -m or the script.
    args = iter(argv[1:])
    for arg in args:
        if arg == "-m":
            return next(args, None) == "ytm_player" and _launches_tui(list(args))
        if arg in ("-X", "-W", "--check-hash-based-pycs"):
            next(args, None)
        elif not arg.startswith("-") or arg == "-" or arg.startswith("-c"):
            return Path(arg).name in ("ytm", ".ytm-wrapped") and _launches_tui(list(args))
    return False


def _split_cmdline(raw: bytes) -> list[str]:
    """Arguments of a /proc/<pid>/cmdline, which separates them with NUL."""
    if not raw:
        return []
    return raw.removesuffix(b"\x00").decode("utf-8", errors="replace").split("\x00")


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
        argv = _split_cmdline(cmdline_raw)
        if not argv:
            continue
        if _argv_is_ytm(argv):
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


# The level is the first [LEVEL] token of the line, i.e. the record's own
# header; a message that merely quotes "[WARNING]" does not count.
_MPV_WARNING_LINE = re.compile(
    r"[^\[]*\[(?:WARNING|ERROR|CRITICAL)\](?: \[[^\]]*\])?(?: [^\s:]+:)? mpv\["
)


def _recent_mpv_lines(log_file: Path, n: int = 20) -> str:
    """Return the last *n* WARNING+ log lines that contain 'mpv[' (our log_handler prefix)."""
    try:
        text = read_text(log_file)
    except OSError:
        return "(could not read log file)"
    if text is None:
        return "(log file is empty or missing)"
    matches = [ln for ln in io.StringIO(text).readlines() if _MPV_WARNING_LINE.match(ln)]
    if not matches:
        return "(no mpv warnings/errors logged)"
    return "".join(matches[-n:])


def _recent_faulthandler(crash_dir: Path) -> str:
    """Return the last 'Fatal Python error' block from crash_dir/faulthandler.log,
    or a marker if the file is missing/empty."""
    fh_log = crash_dir / "faulthandler.log"
    try:
        text = read_text(fh_log)
    except OSError:
        return "(could not read faulthandler.log)"
    if text is None:
        return "(no faulthandler trace — file does not exist)"
    if not text.strip():
        return "(faulthandler.log is empty — no fatal signals captured)"
    marker = "Fatal Python error:"
    last_idx = text.rfind(marker)
    if last_idx == -1:
        return text[-4000:]
    return text[last_idx:]


def _crash_staleness_note(content: str, current_version: str) -> str | None:
    """Warn when the most recent crash predates the running build.

    Crash files record a ``version:`` line in a metadata header (see
    ``write_crash_file``). If it names a version older than the one installed,
    the crash may already be fixed — flag it so a stale log isn't mistaken for
    a live bug. Crash files written before stamping existed have no version
    line and get a softer note.

    Only the crash file's metadata header is read (see
    ``report.crash_header_version``).
    """
    crash_version = crash_header_version(content)
    staleness = crash_staleness_status(crash_version, current_version)
    if staleness == "unversioned":
        return (
            "⚠ This crash file predates crash-version stamping (no version "
            "recorded). If it's from an older build it may already be fixed — "
            "reproduce on the current version before reporting."
        )
    if staleness == "older":
        return (
            f"⚠ This crash was recorded by ytm-player {crash_version}, "
            f"older than the installed {current_version} — it may already be "
            "fixed. Reproduce on the current version before reporting."
        )
    return None


LOCAL_BANNER = (
    "LOCAL diagnostics: review before sharing, do not paste the full output publicly.\n"
    "Tokens and URLs are redacted on a best-effort basis; other private text may remain.\n"
    "For a report that is safe to post, run `ytm report`."
)


def _recent_crash(crash_dir: Path, current_version: str) -> str:
    from ytm_player.utils.logging import get_recent_crash

    crash = get_recent_crash(crash_dir)
    if crash is None:
        return "(no crash files found)"
    path, content = crash
    lines = [f"From: {path}", content]
    note = _crash_staleness_note(content, current_version)
    if note is not None:
        lines.append(note)
    return "\n".join(lines)


def _safe(label: str, collect: Callable[[], str]) -> str:
    """Run one collector; an unexpected failure becomes a fixed status line."""
    try:
        return collect()
    except Exception as exc:
        return f"{label}: error ({exception_class(type(exc).__name__)})"


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
    from ytm_player.utils.logging import get_recent_log_lines, list_active_hooks

    sections: list[str] = [LOCAL_BANNER, ""]

    sections.append("=== ytm-player diagnostics ===")
    sections.append(f"Version: {__version__}")
    sections.append(
        f"Python:  {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )
    sections.append(f"OS:      {platform.system()} {platform.release()}")
    sections.append(f"Machine: {platform.machine()}")
    sections.append(_safe("mpv", _mpv_version))
    sections.append(_safe("libmpv", _libmpv_status))

    sections.append("")
    sections.append("=== Paths ===")
    sections.append(f"config:   {CONFIG_FILE}")
    sections.append(f"theme:    {THEME_FILE}")
    sections.append(f"session:  {SESSION_STATE_FILE}")
    sections.append(f"log:      {LOG_FILE}")
    sections.append(f"crashes:  {CRASH_DIR}")

    sections.append("")
    sections.append("=== Process status ===")
    sections.append(_safe("process status", _running_status))

    sections.append("")
    sections.append("=== MPRIS / media keys ===")
    sections.append(_safe("MPRIS", _mpris_status))

    sections.append("")
    sections.append("=== Recent ERROR/WARNING (last 20) ===")
    filtered = _safe("log", lambda: get_recent_log_lines(LOG_FILE, n=20, min_level="WARNING"))
    sections.append(filtered if filtered else "(no warnings or errors logged)")

    sections.append("")
    sections.append("=== Recent mpv warnings/errors ===")
    sections.append(_safe("mpv log", lambda: _recent_mpv_lines(LOG_FILE)))

    sections.append("")
    sections.append("=== Most recent faulthandler trace ===")
    sections.append(_safe("faulthandler", lambda: _recent_faulthandler(CRASH_DIR)))

    sections.append("")
    sections.append("=== Most recent crash file ===")
    sections.append(_safe("crash file", lambda: _recent_crash(CRASH_DIR, __version__)))

    sections.append("")
    sections.append("=== Active hooks ===")
    sections.append(_safe("hooks", list_active_hooks))
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
