"""Public report for `ytm report` and bare `ytm doctor`.

The report is meant to be pasted into a public GitHub issue, so it is built
from an allowlist: every value printed is either a number, a word from a
closed set defined in this file, or a name verified against the installed
ytm_player package. No text is copied from logs, crash files, mpv, config
files or exception messages. `ytm doctor --local` (utils/doctor.py) is the
detailed, not-for-public counterpart.

Collecting the report does not create, change or delete ytm-player's own
files (config, session, logs, crash files, cache) and prints nothing on
stderr. This module imports only the standard library at module level so it
works on a broken install. These run in a child interpreter whose output is
discarded, see run_probe(), never in the process that prints the report:

- the lookups that ask the import system or the installed distributions
  about a dependency (find_spec() and importlib.metadata for yt-dlp,
  ytmusicapi, textual, python-mpv, dbus-fast);
- the imports of services/player.py (python-mpv, libmpv) and
  services/mpris.py (dbus-fast);
- tomli, on Python 3.10, to check config.toml;
- packaging, to compare a crash file's version with the installed one.

Whatever such code prints, at import or from a native library, cannot reach
the report.
"""

from __future__ import annotations

import ast
import contextlib
import importlib
import importlib.metadata
import importlib.util
import io
import json
import logging
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable, Generator
from functools import lru_cache
from pathlib import Path
from types import CodeType

_PKG_DIR = Path(__file__).resolve().parent.parent

MAX_FRAMES = 5
MAX_LOG_RECORDS = 20
# config.toml is parsed, so it is not read past this size.
MAX_CONFIG_BYTES = 1024 * 1024

# Exception class names that may be printed. Anything else prints as "other".
_LIBRARY_EXCEPTIONS = frozenset(
    {
        # requests
        "ChunkedEncodingError",
        "ConnectTimeout",
        "HTTPError",
        "JSONDecodeError",
        "ProxyError",
        "ReadTimeout",
        "RequestException",
        "SSLError",
        "Timeout",
        "TooManyRedirects",
        # ytmusicapi
        "YTMusicError",
        "YTMusicServerError",
        "YTMusicUserError",
        # yt_dlp
        "DownloadError",
        "ExtractorError",
        "GeoRestrictedError",
        "UnavailableVideoError",
        "YoutubeDLError",
        # dbus_fast
        "AuthError",
        "DBusError",
        "InvalidAddressError",
        "InvalidMessageError",
    }
)
_KNOWN_EXCEPTIONS = _LIBRARY_EXCEPTIONS | frozenset(
    {
        "ArithmeticError",
        "AssertionError",
        "AttributeError",
        "BaseException",
        "BaseExceptionGroup",
        "BlockingIOError",
        "BrokenPipeError",
        "BufferError",
        "BytesWarning",
        "ChildProcessError",
        "ConnectionAbortedError",
        "ConnectionError",
        "ConnectionRefusedError",
        "ConnectionResetError",
        "DeprecationWarning",
        "EOFError",
        "EncodingWarning",
        "EnvironmentError",
        "Exception",
        "ExceptionGroup",
        "FileExistsError",
        "FileNotFoundError",
        "FloatingPointError",
        "FutureWarning",
        "GeneratorExit",
        "IOError",
        "ImportError",
        "ImportWarning",
        "IndentationError",
        "IndexError",
        "InterruptedError",
        "IsADirectoryError",
        "KeyError",
        "KeyboardInterrupt",
        "LookupError",
        "MemoryError",
        "ModuleNotFoundError",
        "NameError",
        "NotADirectoryError",
        "NotImplementedError",
        "OSError",
        "OverflowError",
        "PendingDeprecationWarning",
        "PermissionError",
        "ProcessLookupError",
        "PythonFinalizationError",
        "RecursionError",
        "ReferenceError",
        "ResourceWarning",
        "RuntimeError",
        "RuntimeWarning",
        "StopAsyncIteration",
        "StopIteration",
        "SyntaxError",
        "SyntaxWarning",
        "SystemError",
        "SystemExit",
        "TabError",
        "TimeoutError",
        "TypeError",
        "UnboundLocalError",
        "UnicodeDecodeError",
        "UnicodeEncodeError",
        "UnicodeError",
        "UnicodeTranslateError",
        "UnicodeWarning",
        "UserWarning",
        "ValueError",
        "Warning",
        "WindowsError",
        "ZeroDivisionError",
        "_IncompleteInputError",
    }
)

_OS_FAMILIES = {"Linux": "Linux", "Darwin": "macOS", "Windows": "Windows"}
_MACHINES = frozenset(
    {"x86_64", "amd64", "arm64", "aarch64", "i386", "i686", "armv6l", "armv7l", "riscv64"}
)

# (label, importable module, distribution names to try)
_DEPENDENCIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("yt-dlp", "yt_dlp", ("yt-dlp",)),
    ("ytmusicapi", "ytmusicapi", ("ytmusicapi",)),
    ("textual", "textual", ("textual",)),
    ("python-mpv", "mpv", ("python-mpv", "mpv")),
)

# A whole PEP 440 style version: a bounded numeric release, then optional
# pre/post/dev markers and a local label. Only the release is ever printed.
_VERSION = re.compile(
    r"([0-9]{1,8}(?:\.[0-9]{1,8}){0,4})"
    r"(?:[._-]?(?:alpha|beta|preview|pre|rc|a|b|c)[._-]?[0-9]{0,8})?"
    r"(?:[._-]?(?:post|rev|r)[._-]?[0-9]{0,8})?"
    r"(?:[._-]?dev[._-]?[0-9]{0,8})?"
    r"(?:\+[a-z0-9]+(?:[._-][a-z0-9]+)*)?",
    re.ASCII | re.IGNORECASE,
)
_MPV_VERSION = re.compile(rb"mpv v?(\d{1,4}(?:\.\d{1,4}){0,3})(?![\d.])")

# "%(asctime)s [%(levelname)s] [%(threadName)s] %(name)s: %(message)s"
_LOG_RECORD = re.compile(
    r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d "
    r"\[(DEBUG|INFO|WARNING|ERROR|CRITICAL)\] \[[^\]]*\] ([^\s:]+): (.*)"
)
_REPORTED_LEVELS = frozenset({"WARNING", "ERROR", "CRITICAL"})
# Traceback frames read `File "x", line 1, in f`; faulthandler omits the comma.
_FRAME = re.compile(r'\s*File "([^"]+)", line ([0-9]{1,7}),? in (\S+)\s*\Z')
_EXCEPTION_LINE = re.compile(r"([A-Za-z_][\w.]*)(?::|\s*\Z)")

_MPV_TAGS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("HTTP 403", re.compile(r"HTTP error 403")),
    ("HTTP 404", re.compile(r"HTTP error 404")),
    ("HTTP 5xx", re.compile(r"HTTP error 5\d\d")),
    ("TLS", re.compile(r"\b(?:tls|ssl|certificate)\b", re.IGNORECASE)),
)

_CRASH_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("Main thread crash", "main_thread"),
    ("Thread crash", "thread"),
    ("Unraisable", "unraisable"),
    ("Asyncio loop exception", "asyncio"),
    ("Textual crash", "textual"),
)
# The metadata keys write_crash_file() stamps at the top of every crash file
# (logging._crash_metadata_header). Only these count as header lines: a
# traceback line that happens to be "Word: value" shaped (e.g. "ValueError: x")
# must not be mistaken for crash metadata.
_CRASH_META_KEYS = frozenset({"version", "time", "python", "platform"})
_CRASH_BANNER = re.compile(r"=== .* ===")
_CRASH_VERSION_LINE = re.compile(r"version:\s+(\S+)")
_CRASH_STALENESS = frozenset({"same", "older", "newer", "unversioned", "unknown"})
_FAULT_HEADERS = ("Fatal Python error:", "Windows fatal exception:")

LIBMPV_STATUS = {
    "ok": "OK",
    "unknown": "unknown",
    "missing": "python-mpv not installed",
    "failed": "NOT LOADABLE ({})",
}
_MPRIS_STATUS = {
    "na": "n/a",
    "ok": "available",
    "missing": "unavailable (dbus-fast missing)",
    "failed": "unavailable (dbus-fast incompatible: {})",
}
_CONFIG_STATUS = frozenset({"ok", "missing", "corrupt", "unreadable"})

_PROBE_TIMEOUT = 30
_PROBE_MARK = b"ytm-player-probe:"
# The child gets the parent's sys.path: an entry script can add its site
# directories itself (the NixOS wrapper does), which a fresh interpreter
# would not have.
_PROBE_SOURCE = """\
import sys
sys.path[:] = sys.argv[1:]
import json, logging
logging.disable(logging.CRITICAL)
request = json.loads(sys.stdin.buffer.read())
from ytm_player.utils import report
report.probe_main(request["name"], request["args"])
"""


@contextlib.contextmanager
def silenced() -> Generator[None, None, None]:
    """Keep collection off stdout and stderr.

    Importing a service module can log (services/mpris.py logs a traceback
    for an incompatible dbus-fast build); with no handler configured that
    would reach stderr through logging.lastResort.
    """
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            yield
    finally:
        logging.disable(previous)


def exception_class(name: str) -> str:
    """Return *name* when it is a known exception class, else ``other``."""
    return name if name in _KNOWN_EXCEPTIONS else "other"


def _guarded(collect: Callable[[], str]) -> str:
    """Run one collector; an unexpected failure becomes a fixed status."""
    try:
        return collect()
    except Exception as exc:
        return f"error ({exception_class(type(exc).__name__)})"


# ---------------------------------------------------------------------------
# Probes: third-party code runs in a child interpreter
# ---------------------------------------------------------------------------


def run_probe(name: str, *args: str) -> object:
    """Run one probe in a child interpreter; None when that did not work.

    The child's stdout and stderr never reach ours, so nothing a dependency
    prints while being imported, and nothing a native library writes to the
    process's file descriptors, can end up in a report. The result is the JSON
    value on the child's last marker line. It is not trusted: the caller turns
    it into one of its own fixed statuses.
    """
    if not sys.executable:
        return None
    path = [p for p in sys.path if isinstance(p, str)]
    request = {"name": name, "args": args}
    try:
        done = subprocess.run(
            [sys.executable, "-B", "-c", _PROBE_SOURCE, *path],
            input=json.dumps(request).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    for line in reversed(done.stdout.splitlines()):
        if line.startswith(_PROBE_MARK):
            try:
                return json.loads(line[len(_PROBE_MARK) :])
            except ValueError:
                return None
    return None


def probe_main(name: str, args: list[str]) -> None:
    """Child side of run_probe(): run the probe, write its result as one line."""
    try:
        result: object = _PROBES[name](*args)
    except Exception as exc:
        result = ["error", type(exc).__name__]
    line = b"\n" + _PROBE_MARK + json.dumps(result).encode("utf-8") + b"\n"
    sys.stdout.flush()
    os.write(1, line)


def render_probe(result: object, statuses: dict[str, str]) -> str:
    """Turn a probe's ``[code, exception class]`` into one of *statuses*.

    A probe that raised reports ``error``; anything unexpected is ``unknown``.
    """
    if isinstance(result, list) and len(result) == 2 and isinstance(result[0], str):
        code, name = result
        known = exception_class(name) if isinstance(name, str) else "other"
        if code == "error":
            return f"error ({known})"
        if code in statuses:
            return statuses[code].format(known)
    return "unknown"


# ---------------------------------------------------------------------------
# Versions and components
# ---------------------------------------------------------------------------


def _module_present(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def numeric_version(raw: object) -> str:
    """The numeric release of *raw*, or ``unknown`` unless all of it is a version."""
    match = _VERSION.fullmatch(raw) if isinstance(raw, str) else None
    return match.group(1) if match else "unknown"


def _dependencies() -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    if sys.platform == "linux":
        return (*_DEPENDENCIES, ("dbus-fast", "dbus_fast", ("dbus-fast",)))
    return _DEPENDENCIES


def probe_dependencies() -> dict[str, list[object]]:
    """``[present, raw version]`` per dependency label.

    Asks the import system and the installed distributions, which runs
    whatever finders the environment has installed: call it through
    run_probe().
    """
    found: dict[str, list[object]] = {}
    for label, module, distributions in _dependencies():
        try:
            if not _module_present(module):
                found[label] = [False, None]
                continue
            raw: object = None
            for distribution in distributions:
                try:
                    metadata = importlib.metadata.metadata(distribution)
                except importlib.metadata.PackageNotFoundError:
                    continue
                # Not version(): that raises for metadata without a Version field.
                if metadata is not None and "Version" in metadata:
                    raw = metadata["Version"]
                break
            found[label] = [True, raw if isinstance(raw, str) else None]
        except Exception:
            # A broken finder or METADATA file affects only this dependency.
            found[label] = [None, None]
    return found


def _dependency_versions() -> list[str]:
    """One ``label: version`` line per dependency, from the child's answers."""
    result = run_probe("dependencies")
    lines = []
    for label, _module, _distributions in _dependencies():
        entry = result.get(label) if isinstance(result, dict) else None
        if not (isinstance(entry, list) and len(entry) == 2):
            version = "unknown"
        elif entry[0] is False:
            version = "missing"
        else:
            version = numeric_version(entry[1])
        lines.append(f"{label}: {version}")
    return lines


def _os_status() -> str:
    family = _OS_FAMILIES.get(platform.system(), "other")
    machine = platform.machine().lower()
    return f"{family} {machine if machine in _MACHINES else 'other'}"


def _mpv_status() -> str:
    mpv_bin = shutil.which("mpv")
    if not mpv_bin:
        return "not found"
    try:
        out = subprocess.run(
            [mpv_bin, "--no-config", "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "failed to execute"
    if out.returncode != 0:
        return "failed to execute"
    match = _MPV_VERSION.match(out.stdout)
    return match.group(1).decode("ascii") if match else "present (unparsed)"


def probe_libmpv() -> list[str | None]:
    """``ok`` only when python-mpv is installed and libmpv answered a call.

    Importing our player module runs the full discovery chain, including the
    Homebrew-prefix fallback (#90, #101, #104). python-mpv reads the libmpv
    client API version while it is imported, so a (major, minor) pair of
    integers there means the library really loaded. Imports third-party code:
    call it through run_probe().
    """
    if not _module_present("mpv"):
        return ["missing", None]
    try:
        player = importlib.import_module("ytm_player.services.player")
    except Exception as exc:
        return ["failed", type(exc).__name__]
    if hasattr(player, "_IMPORT_ERROR_MSG"):
        return ["failed", "OSError"]  # the only error player.py turns into a stub
    version = getattr(getattr(player, "mpv", None), "MPV_VERSION", None)
    if (
        isinstance(version, tuple)
        and len(version) == 2
        and all(type(part) is int for part in version)
    ):
        return ["ok", None]
    return ["unknown", None]


def libmpv_status() -> str:
    return render_probe(run_probe("libmpv"), LIBMPV_STATUS)


def probe_mpris() -> list[str | None]:
    """Imports third-party code: call it through run_probe()."""
    if sys.platform != "linux":
        return ["na", None]
    if not _module_present("dbus_fast"):
        return ["missing", None]
    try:
        mpris = importlib.import_module("ytm_player.services.mpris")
    except Exception as exc:
        return ["failed", type(exc).__name__]
    if mpris.DBUS_AVAILABLE:
        return ["ok", None]
    return ["failed", mpris.DBUS_ERROR_CLASS]


def _mpris_status() -> str:
    if sys.platform != "linux":
        return _MPRIS_STATUS["na"]
    return render_probe(run_probe("mpris"), _MPRIS_STATUS)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def _config_dir_status(config_dir: Path) -> str:
    if sys.platform == "win32":
        default = Path.home() / "AppData" / "Roaming" / "ytm-player"
    else:
        default = Path.home() / ".config" / "ytm-player"
    return "default" if config_dir == default else "custom"


def read_regular(path: Path, limit: int = -1) -> bytes | None:
    """The content of a regular file, or its first *limit* bytes.

    None when there is no such file. Raises OSError when it cannot be read or
    is not a regular file. The file is opened without blocking and checked
    after opening, so a FIFO in its place cannot hang the command.
    """
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            return handle.read(limit)
    finally:
        os.close(fd)


def read_text(path: Path) -> str | None:
    """read_regular() of a whole log or crash file, decoded, with newline line endings."""
    data = read_regular(path)
    if data is None:
        return None
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n")


def probe_config_file(config_file: str) -> str:
    """On Python 3.10 this imports tomli: call it through run_probe() there."""
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        # Python 3.10 backport via PyPI
        import tomli as tomllib  # pyright: ignore[reportMissingImports]

    try:
        raw = read_regular(Path(config_file), MAX_CONFIG_BYTES + 1)
    except OSError:
        return "unreadable"
    if raw is None:
        return "missing"
    if len(raw) > MAX_CONFIG_BYTES:
        return "unreadable"
    try:
        tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return "corrupt"
    return "ok"


def _config_file_status(config_file: Path) -> str:
    if sys.version_info >= (3, 11):
        return probe_config_file(str(config_file))
    status = run_probe("config", str(config_file))
    return status if isinstance(status, str) and status in _CONFIG_STATUS else "unknown"


def _presence(path: Path) -> str:
    return "present" if path.is_file() else "missing"


def _crash_files(crash_dir: Path) -> list[Path]:
    try:
        return sorted(crash_dir.glob("ytm-crash-*.log"))
    except OSError:
        return []


def _running_status(proc_root: Path = Path("/proc")) -> str:
    """``yes`` / ``no`` from /proc, ``unknown`` when that cannot be told.

    Never ipc.get_running_pid(): that deletes stale PID files.
    """
    from ytm_player.utils.doctor import _argv_is_ytm, _split_cmdline

    try:
        entries = [entry for entry in proc_root.iterdir() if entry.name.isdigit()]
    except OSError:
        return "unknown"
    if not entries:
        return "unknown"
    own_pid = str(os.getpid())
    hidden = False
    for entry in entries:
        if entry.name == own_pid:
            continue
        try:
            argv = _split_cmdline((entry / "cmdline").read_bytes())
        except (FileNotFoundError, ProcessLookupError):
            continue  # the process ended while we were looking
        except OSError:
            hidden = True  # a process we could not look at could be ytm
            continue
        if _argv_is_ytm(argv):
            return "yes"
    return "unknown" if hidden else "no"


# ---------------------------------------------------------------------------
# Error signatures
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _package_files() -> frozenset[str]:
    """Every .py file of the installed package, as posix paths under ytm_player/."""
    return frozenset(
        p.relative_to(_PKG_DIR).as_posix() for p in _PKG_DIR.rglob("*.py") if p.is_file()
    )


def _own_lines(code: CodeType) -> set[int]:
    """The source lines *code* has instructions for.

    co_lines() also lists line 0 (a module's synthetic RESUME) and empty
    ranges for code the compiler removed; neither is a line that can run.
    """
    return {line for start, end, line in code.co_lines() if line and line > 0 and start < end}


@lru_cache(maxsize=None)
def _functions(relpath: str) -> dict[str, frozenset[int]] | None:
    """Map each def in a package file to the executable lines of its own code.

    The file is parsed and compiled, never run.
    """
    try:
        source = (_PKG_DIR / relpath).read_bytes()
        tree = ast.parse(source)
        module_code = compile(tree, relpath, "exec")
    except (OSError, SyntaxError, ValueError):
        return None
    defs = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    lines: dict[str, set[int]] = {name: set() for name in defs}
    lines["<module>"] = _own_lines(module_code)
    pending = [module_code]
    while pending:
        code = pending.pop()
        for const in code.co_consts:
            if isinstance(const, CodeType):
                pending.append(const)
                if const.co_name in defs:
                    lines[const.co_name] |= _own_lines(const)
    return {name: frozenset(found) for name, found in lines.items()}


def _verified_frame(path: str, line: str, func: str, *, with_line: bool) -> str | None:
    """Format a traceback frame, or None unless it checks out against the package.

    Printed only when the file is one of ours and *func* is a def in it; the
    line number only when asked for and it is an executable line of that def.
    """
    parts = path.replace("\\", "/").split("/")
    if "ytm_player" not in parts[:-1]:
        return None
    start = len(parts) - parts[::-1].index("ytm_player")
    relpath = "/".join(parts[start:])
    if relpath not in _package_files():
        return None
    functions = _functions(relpath)
    if functions is None or func not in functions:
        return None
    if with_line and int(line) in functions[func]:
        return f"ytm_player/{relpath}:{int(line)} in {func}"
    return f"ytm_player/{relpath} in {func}"


def _traceback_signature(lines: list[str], *, with_lines: bool) -> tuple[str | None, list[str]]:
    """Return (exception class, innermost first-party frames) of a traceback."""
    frames: list[str] = []
    last_frame = -1
    for index, line in enumerate(lines):
        match = _FRAME.match(line)
        if match is None:
            continue
        last_frame = index
        frame = _verified_frame(*match.groups(), with_line=with_lines)
        if frame is not None:
            frames.append(frame)
    if last_frame == -1:
        return None, []
    name: str | None = None
    # The exception line is the first unindented line after the last frame.
    for line in lines[last_frame + 1 :]:
        if line[:1].isspace() or not line.strip():
            continue
        match = _EXCEPTION_LINE.match(line)
        name = exception_class(match.group(1).rpartition(".")[2]) if match else "other"
        break
    return name, frames[-MAX_FRAMES:]


@lru_cache(maxsize=1)
def _module_names() -> frozenset[str]:
    """The dotted name of every module of the installed package."""
    names = set()
    for relpath in _package_files():
        parts = relpath[: -len(".py")].split("/")
        if parts[-1] == "__init__":
            parts.pop()
        names.add(".".join(["ytm_player", *parts]))
    return frozenset(names)


def _logger_name(name: str) -> str:
    return name if name in _module_names() else "external"


def _log_section(log_file: Path) -> list[str]:
    try:
        text = read_text(log_file)
    except OSError:
        return ["log: unreadable", "mpv: unreadable"]
    if text is None:
        return ["log: no log file", "mpv: no log file"]
    records: list[tuple[str, str, list[str]]] = []
    mpv_counts = dict.fromkeys((tag for tag, _ in _MPV_TAGS), 0)
    mpv_total = 0
    keep = False
    for line in text.splitlines():
        match = _LOG_RECORD.match(line)
        if match is None:
            if keep:
                records[-1][2].append(line)
            continue
        level, logger_name, message = match.groups()
        keep = False
        if level not in _REPORTED_LEVELS:
            continue
        if message.startswith("mpv["):
            mpv_total += 1
            for tag, pattern in _MPV_TAGS:
                if pattern.search(message):
                    mpv_counts[tag] += 1
            continue
        keep = True
        records.append((level, logger_name, []))

    # Identical signatures collapse into one entry with a count.
    entries: dict[tuple[str, str, str, tuple[str, ...]], int] = {}
    for level, logger_name, continuation in records[-MAX_LOG_RECORDS:]:
        name, frames = _traceback_signature(continuation, with_lines=False)
        key = (level, _logger_name(logger_name), name or "-", tuple(frames))
        entries[key] = entries.pop(key, 0) + 1

    out = [f"log: last {min(len(records), MAX_LOG_RECORDS)} WARNING+ records"]
    for (level, logger_name, name, frames), count in entries.items():
        out.append(f"  {level} {logger_name} {name} x{count}")
        out.extend(f"    {frame}" for frame in frames)
    tags = ", ".join(f"{tag}: {count}" for tag, count in mpv_counts.items())
    out.append(f"mpv: {mpv_total} WARNING+ lines ({tags})")
    return out


def _faulthandler_section(crash_dir: Path) -> list[str]:
    try:
        text = read_text(crash_dir / "faulthandler.log")
    except OSError:
        return ["faulthandler: unreadable"]
    if text is None or not text.strip():
        return ["faulthandler: none"]
    # The file is appended to: the last header starts the latest dump.
    start = max(text.rfind(header) for header in _FAULT_HEADERS)
    block = text[max(start, 0) :].splitlines()
    # faulthandler lists the most recent call first; prefer the crashing thread.
    current: list[str] = []
    for index, line in enumerate(block):
        if line.startswith("Current thread"):
            for frame_line in block[index + 1 :]:
                if not _FRAME.match(frame_line):
                    break
                current.append(frame_line)
            break
    for candidates in (current, block):
        frames = []
        for line in candidates:
            match = _FRAME.match(line)
            frame = _verified_frame(*match.groups(), with_line=False) if match else None
            if frame is not None:
                frames.append(frame)
        if frames:
            return ["faulthandler: fatal error recorded"] + [
                f"    {frame}" for frame in frames[:MAX_FRAMES]
            ]
    return ["faulthandler: fatal error recorded (no ytm_player frames)"]


def crash_header(content: str) -> tuple[str | None, bool]:
    """Return (``version:`` value, strict) from a crash file's metadata header.

    Only the metadata header (the contiguous known ``key: value`` lines just
    after the ``=== label ===`` banner) is parsed. Scanning the whole body, or
    accepting any key-shaped line, would let an exception line (``ValueError:
    x``) or a ``version:``-shaped traceback line pass for the crash version.

    *strict* says the stamp is exactly what write_crash_file() writes: the
    file opens with a whole ``=== label ===`` banner and the version line
    holds nothing but the version.
    """
    lines = content.splitlines()
    banner = bool(lines) and _CRASH_BANNER.fullmatch(lines[0]) is not None
    start = 1 if lines and lines[0].startswith("===") else 0
    for line in lines[start:]:
        if not line.strip():
            break  # blank line ends the metadata header
        meta = re.match(r"(\w+):\s+(\S+)", line)
        if meta is None or meta.group(1) not in _CRASH_META_KEYS:
            break  # not a known header key -> header is over (e.g. a traceback line)
        if meta.group(1) == "version":
            return meta.group(2), banner and _CRASH_VERSION_LINE.fullmatch(line) is not None
    return None, False


def crash_header_version(content: str) -> str | None:
    """The ``version:`` value of a crash file's metadata header, if any."""
    return crash_header(content)[0]


def crash_staleness(crash_version: str | None, current_version: str) -> str:
    """Compare a crash file's version with the installed one.

    Returns ``same``, ``older``, ``newer``, ``unversioned`` (the file predates
    version stamping) or ``unknown``. Imports packaging: outside tests, call
    it through crash_staleness_status().
    """
    if crash_version is None:
        return "unversioned"
    try:
        from packaging.version import InvalidVersion, Version
    except ImportError:
        return "unknown"
    try:
        crash, current = Version(crash_version), Version(current_version)
    except InvalidVersion:
        return "unknown"
    if crash == current:
        return "same"
    return "older" if crash < current else "newer"


def crash_staleness_status(crash_version: str | None, current_version: str) -> str:
    """crash_staleness(), run through run_probe() because it imports packaging."""
    if crash_version is None:
        return "unversioned"
    staleness = run_probe("staleness", crash_version, current_version)
    if isinstance(staleness, str) and staleness in _CRASH_STALENESS:
        return staleness
    return "unknown"


def _crash_section(crash_dir: Path, current_version: str) -> list[str]:
    files = _crash_files(crash_dir)
    if not files:
        return ["latest crash: none"]
    try:
        content = read_text(files[-1])
    except OSError:
        content = None
    if content is None:
        return ["latest crash: unreadable"]
    first_line = content.split("\n", 1)[0]
    category = "unknown"
    for label, name in _CRASH_CATEGORIES:
        if first_line.startswith(f"=== {label}"):
            category = name
            break
    version, strict = crash_header(content)
    staleness = crash_staleness_status(version, current_version)
    # Line numbers only for a crash this exact build recorded.
    with_lines = strict and staleness == "same" and version == current_version
    name, frames = _traceback_signature(content.splitlines(), with_lines=with_lines)
    out = [f"latest crash: {category}, version {staleness}, {name or '-'}"]
    out.extend(f"    {frame}" for frame in frames)
    return out


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def build_report() -> str:
    """Return the public report. Read-only; safe to paste into a public issue."""
    with silenced():
        return "\n".join(_collect())


def _collect() -> list[str]:
    from ytm_player import __version__
    from ytm_player.config.paths import CONFIG_DIR, CONFIG_FILE, CRASH_DIR, LOG_FILE

    info = sys.version_info
    out = [
        "=== ytm-player report ===",
        f"ytm-player: {numeric_version(__version__)}",
        f"python: {info.major}.{info.minor}.{info.micro}",
        f"os: {_guarded(_os_status)}",
    ]
    try:
        out += _dependency_versions()
    except Exception as exc:
        out.append(f"dependencies: error ({exception_class(type(exc).__name__)})")
    out.append(f"mpv: {_guarded(_mpv_status)}")
    out.append(f"libmpv: {_guarded(libmpv_status)}")
    out.append(f"mpris: {_guarded(_mpris_status)}")

    out += [
        "",
        "=== state ===",
        f"config dir: {_guarded(lambda: _config_dir_status(CONFIG_DIR))}",
        f"config.toml: {_guarded(lambda: _config_file_status(CONFIG_FILE))}",
        f"ytm.log: {_guarded(lambda: _presence(LOG_FILE))}",
        f"faulthandler.log: {_guarded(lambda: _presence(CRASH_DIR / 'faulthandler.log'))}",
        f"crash files: {_guarded(lambda: str(len(_crash_files(CRASH_DIR))))}",
        f"running: {_guarded(_running_status)}",
        "",
        "=== errors ===",
    ]
    sections: tuple[tuple[str, Callable[[], list[str]]], ...] = (
        ("log", lambda: _log_section(LOG_FILE)),
        ("faulthandler", lambda: _faulthandler_section(CRASH_DIR)),
        ("latest crash", lambda: _crash_section(CRASH_DIR, __version__)),
    )
    for label, section in sections:
        try:
            out += section()
        except Exception as exc:
            out.append(f"{label}: error ({exception_class(type(exc).__name__)})")
    out += [
        "",
        "Nothing above is copied from your logs or files. For the full detail, run",
        "`ytm doctor --local` and review it before sharing any of it.",
    ]
    return out


def _local_probe(name: str) -> Callable[[], str]:
    def probe() -> str:
        from ytm_player.utils import doctor

        return getattr(doctor, name)()

    return probe


# What run_probe() can ask the child for.
_PROBES: dict[str, Callable[..., object]] = {
    "dependencies": probe_dependencies,
    "libmpv": probe_libmpv,
    "mpris": probe_mpris,
    "config": probe_config_file,
    "staleness": crash_staleness,
    "local_libmpv": _local_probe("_libmpv_detail"),
    "local_mpris": _local_probe("_mpris_detail"),
}
