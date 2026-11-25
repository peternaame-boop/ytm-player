"""`ytm report`, bare `ytm doctor` and `ytm doctor --local`, run as real processes.

The public report is an allowlist: nothing from the user's files may reach
it. All three invocations must be read-only and silent on stderr.

Every process started here, and every interpreter it starts in turn, runs
with an audit hook (AUDIT_SITECUSTOMIZE) that records any attempt to write
or change something under the sandbox's config and cache locations. The
`sandbox` fixture fails a test in which that record is not empty. The same
module makes the modules named in YTM_TEST_BLOCK unimportable, as on a broken
install, in the CLI process and in the probe children alike.
"""

from __future__ import annotations

import ast
import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

import ytm_player
from ytm_player.utils.doctor import LOCAL_BANNER
from ytm_player.utils.report import numeric_version

PKG_DIR = Path(ytm_player.__file__).resolve().parent
SRC_DIR = PKG_DIR.parent
REPO_ROOT = Path(__file__).resolve().parents[2]
MARK = "ZQXCANARY"
# For text that must stay out of `--local` too, which prints the sandbox path (MARK).
NOISE_MARK = "ZQXNOISE"
INVOCATIONS = (["report"], ["doctor"], ["doctor", "--local"])

# Imported by every interpreter that has its directory on PYTHONPATH. Records
# write-mode opens and filesystem mutations under the watched roots, then
# hands over to the sitecustomize module it shadows, if there is one.
AUDIT_SITECUSTOMIZE = """
import importlib.machinery
import importlib.util
import os
import sys

_roots = [
    os.path.normcase(os.path.abspath(root))
    for root in os.environ["YTM_TEST_AUDIT_ROOTS"].split(os.pathsep)
]
_log = os.open(os.environ["YTM_TEST_AUDIT_LOG"], os.O_WRONLY | os.O_APPEND | os.O_CREAT)
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
_MUTATIONS = frozenset(
    "os.mkdir os.remove os.rename os.rmdir os.chmod os.chown os.utime os.truncate os.link "
    "os.symlink shutil.rmtree shutil.move shutil.copyfile shutil.copytree shutil.copymode "
    "shutil.copystat tempfile.mkstemp tempfile.mkdtemp".split()
)


def _inside(value):
    if isinstance(value, bytes):
        value = os.fsdecode(value)
    if not isinstance(value, (str, os.PathLike)):
        return False
    path = os.fspath(value)
    if isinstance(path, bytes):
        path = os.fsdecode(path)
    path = os.path.normcase(os.path.abspath(path))
    return any(path == root or path.startswith(root + os.sep) for root in _roots)


def _hook(event, args):
    try:
        if event == "open":
            path, mode, flags = args
            writes = isinstance(mode, str) and any(c in mode for c in "wxa+")
            writes = writes or (isinstance(flags, int) and flags & _WRITE_FLAGS)
            if not (writes and _inside(path)):
                return
        elif not (event in _MUTATIONS and any(_inside(arg) for arg in args)):
            return
        os.write(_log, f"{event} {args!r}\\n".encode("utf-8", "replace"))
    except Exception as exc:
        os.write(_log, f"audit hook failed: {exc!r}\\n".encode("utf-8", "replace"))


sys.addaudithook(_hook)

_blocked = set(filter(None, os.environ.get("YTM_TEST_BLOCK", "").split(",")))


class _Block:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in _blocked:
            raise ModuleNotFoundError(name)
        return None


if _blocked:
    sys.meta_path.insert(0, _Block())

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.machinery.PathFinder.find_spec(
    "sitecustomize", [p for p in sys.path if os.path.abspath(p or os.curdir) != _here]
)
if _spec is not None and _spec.loader is not None:
    _module = importlib.util.module_from_spec(_spec)
    sys.modules["sitecustomize"] = _module
    _spec.loader.exec_module(_module)
"""

# A module that prints in every way a module can, before the fixture's own code.
NOISE = f"""
import os, sys
print("{NOISE_MARK}_PYOUT")
print("{NOISE_MARK}_PYERR", file=sys.stderr)
sys.stdout.flush()
sys.stderr.flush()
os.write(1, b"{NOISE_MARK}_FDOUT\\n")
os.write(2, b"{NOISE_MARK}_FDERR\\n")
"""

# Third-party modules the process that prints a report must never load.
PARENT_MUST_NOT_IMPORT = (
    "tomli",
    "ytm_player.config.settings",
    "ytm_player.config.keymap",
    "requests",
    "ytmusicapi",
    "mpv",
    "dbus_fast",
    "packaging",
)

# Runs the CLI in-process and reports which modules that added to sys.modules.
IMPORT_TRACKER = """
import json, sys

before = set(sys.modules)
from ytm_player.cli import main

code = 0
try:
    main(json.loads(sys.argv[1]), prog_name="ytm")
except SystemExit as stop:
    code = stop.code
sys.stdout.flush()
print("YTM_TEST_ADDED " + json.dumps(sorted(set(sys.modules) - before)))
sys.exit(code)
"""


def _first_def(relpath: str) -> str:
    tree = ast.parse((PKG_DIR / relpath).read_bytes())
    return next(n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))


APP_FUNC = _first_def("app/_app.py")


class Sandbox:
    """A private config/cache location the CLI is pointed at."""

    def __init__(self, root: Path, audit_dir: Path) -> None:
        self.root = root
        self.config_home = root / f"{MARK}_HOME"
        self.config_dir = self.config_home / "ytm-player"
        self.cache_home = root / "cache-home"
        self.extra_path: list[Path] = []
        # Kept outside root: the read-only tests snapshot everything under it.
        self.audit_log = audit_dir / "audit.log"
        self.fixtures = audit_dir / "fixtures"
        (audit_dir / "sitecustomize.py").write_text(AUDIT_SITECUSTOMIZE, encoding="utf-8")
        self.extra_pythonpath: list[Path] = [audit_dir]

    def make_config_dir(self) -> Path:
        (self.config_dir / "logs").mkdir(parents=True)
        (self.config_dir / "crashes").mkdir()
        return self.config_dir

    @property
    def log_file(self) -> Path:
        return self.config_dir / "logs" / "ytm.log"

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        pythonpath = [*map(str, self.extra_pythonpath), str(SRC_DIR), env.get("PYTHONPATH", "")]
        env.update(
            XDG_CONFIG_HOME=str(self.config_home),
            XDG_CACHE_HOME=str(self.cache_home),
            APPDATA=str(self.config_home),
            LOCALAPPDATA=str(self.cache_home),
            PYTHONIOENCODING="utf-8",
            # An empty entry would put the working directory on sys.path.
            PYTHONPATH=os.pathsep.join(entry for entry in pythonpath if entry),
            PATH=os.pathsep.join([*map(str, self.extra_path), env.get("PATH", "")]),
            YTM_TEST_AUDIT_ROOTS=os.pathsep.join([str(self.config_home), str(self.cache_home)]),
            YTM_TEST_AUDIT_LOG=str(self.audit_log),
        )
        return env

    def run(self, *args: str, block: str = "") -> subprocess.CompletedProcess[str]:
        env = self._env()
        env["YTM_TEST_BLOCK"] = block
        command = [sys.executable, "-m", "ytm_player", *args]
        return subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", env=env, timeout=120
        )

    def run_code(self, code: str, *args: str) -> subprocess.CompletedProcess[str]:
        """Run *code* in the same environment the CLI gets."""
        return subprocess.run(
            [sys.executable, "-c", code, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=self._env(),
            timeout=120,
        )

    def mutations(self) -> str:
        """What the audit hook recorded: empty when nothing tried to write."""
        return self.audit_log.read_text(encoding="utf-8") if self.audit_log.exists() else ""

    def fake_mpv(self, stdout: str, stderr: str = "") -> None:
        """Put an `mpv` on PATH that prints the given text."""
        bin_dir = self.fixtures / "fake-bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            script = bin_dir / "mpv.cmd"
            script.write_text(f"@echo {stdout}\n@echo {stderr} 1>&2\n", encoding="utf-8")
        else:
            script = bin_dir / "mpv"
            script.write_text(
                f"#!/bin/sh\necho '{stdout}'\necho '{stderr}' >&2\n", encoding="utf-8"
            )
            script.chmod(0o755)
        self.extra_path.append(bin_dir)

    def fake_module(self, name: str, source: str) -> None:
        """Shadow an importable module with *source*."""
        self.fake_package(name, {f"{name}.py": source})

    def fake_package(self, name: str, files: dict[str, str]) -> None:
        """Shadow an importable module or package with *files*."""
        modules = self.fixtures / "fake-modules"
        for relpath, source in files.items():
            target = modules / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source, encoding="utf-8")
        if modules not in self.extra_pythonpath:
            self.extra_pythonpath.append(modules)

    def snapshot(self) -> dict[str, tuple[int, int, bytes | None]]:
        entries = {}
        for path in sorted(self.root.rglob("*")):
            info = path.lstat()
            content = path.read_bytes() if path.is_file() else None
            entries[path.relative_to(self.root).as_posix()] = (
                info.st_mode,
                info.st_mtime_ns,
                content,
            )
        return entries


@pytest.fixture
def sandbox(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Sandbox]:
    box = Sandbox(tmp_path, tmp_path_factory.mktemp("audit"))
    yield box
    assert box.mutations() == ""


def test_the_audit_hook_sees_writes_from_a_process_and_its_children(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """The control for the fixture above: a hook that records nothing proves nothing."""
    box = Sandbox(tmp_path, tmp_path_factory.mktemp("audit"))
    box.make_config_dir()
    code = (
        "import os, pathlib, subprocess, sys\n"
        "home = pathlib.Path(os.environ['XDG_CONFIG_HOME'])\n"
        "(home / 'ytm-player' / 'config.toml').read_bytes() if False else None\n"
        "os.mkdir(home / 'made-by-parent')\n"
        'child = \'import os, pathlib; pathlib.Path(os.environ["XDG_CONFIG_HOME"], "f").open("w")\'\n'
        "subprocess.run([sys.executable, '-c', child], check=True)\n"
        "os.open(home / 'g', os.O_WRONLY | os.O_CREAT)\n"
        "list(home.iterdir()); (home / 'f').read_bytes()\n"
    )

    result = box.run_code(code)

    assert result.returncode == 0, result.stderr
    recorded = box.mutations().splitlines()
    assert [line.split()[0] for line in recorded] == ["os.mkdir", "open", "open"]


def _traceback(path: str, line: int, func: str, exception: str) -> str:
    return (
        "Traceback (most recent call last):\n"
        f'  File "{path}", line {line}, in {func}\n'
        f"    source_line({MARK}_SOURCE)\n"
        f"{exception}\n"
    )


def _log_record(level: str, logger: str, message: str, thread: str = "MainThread") -> str:
    return f"2026-11-24 10:00:00 [{level}] [{thread}] {logger}: {message}\n"


class TestPublicReport:
    def test_report_and_bare_doctor_print_the_same(self, sandbox: Sandbox) -> None:
        sandbox.make_config_dir()
        report = sandbox.run("report")
        doctor = sandbox.run("doctor")
        debug = sandbox.run("--debug", "report")
        debug_doctor = sandbox.run("--debug", "doctor")

        for result in (report, doctor, debug, debug_doctor):
            assert result.returncode == 0
            assert result.stderr == ""
        assert report.stdout.startswith("=== ytm-player report ===\n")
        assert f"ytm-player: {numeric_version(ytm_player.__version__)}\n" in report.stdout
        assert doctor.stdout == report.stdout
        assert debug.stdout == report.stdout
        assert debug_doctor.stdout == report.stdout

    def test_nothing_from_the_users_files_reaches_the_report(self, sandbox: Sandbox) -> None:
        config_dir = sandbox.make_config_dir()
        (config_dir / "config.toml").write_text(f"[general\n{MARK}_CONFIG = ", encoding="utf-8")
        own_file = f"/home/{MARK}_USER/site-packages/ytm_player/app/_app.py"
        sandbox.log_file.write_text(
            _log_record("WARNING", "ytm_player.app._app", f"{MARK}_WARNING")
            + _log_record(
                "ERROR",
                "ytm_player.app._app",
                f"Authorization: Bearer {MARK}_TOKEN; Cookie: SID={MARK}_COOKIE",
                f"{MARK}_THREAD",
            )
            + _traceback(own_file, 8675309, APP_FUNC, f"{MARK.title()}Error: {MARK}_EXC")
            + _log_record("WARNING", f"{MARK.lower()}_logger.mod", f"{MARK}_EXTERNAL")
            + _log_record(
                "WARNING",
                "ytm_player.services.player",
                f"mpv[ffmpeg]: https: HTTP error 403 Forbidden {MARK}_MPVWARN",
            )
            + _log_record("DEBUG", "ytm_player.services.player", f"mpv[cplayer]: {MARK}_MPVDEBUG"),
            encoding="utf-8",
        )
        crashes = config_dir / "crashes"
        (crashes / "faulthandler.log").write_text(
            f"Fatal Python error: {MARK}_FATAL\n\n"
            "Current thread 0x00007f (most recent call first):\n"
            f'  File "/opt/{MARK}_FH/ytm_player/app/_app.py", line 8675309 in {APP_FUNC}\n'
            f'  File "/opt/ytm_player/app/_app.py", line 3 in {MARK}_FUNC\n',
            encoding="utf-8",
        )
        (crashes / f"ytm-crash-{MARK}_NAME.log").write_text(
            f"=== Thread crash ({MARK}_LABEL) ===\n"
            "version:   0.1+privatecase\n"
            f"time:      {MARK}_TIME\n"
            "\n" + _traceback(own_file, 8675309, APP_FUNC, f"{MARK.title()}Error: {MARK}_CRASH"),
            encoding="utf-8",
        )
        sandbox.fake_mpv(f"{MARK}_MPVOUT", f"{MARK}_MPVERR")

        result = sandbox.run("report")

        assert result.returncode == 0
        assert result.stderr == ""
        out = result.stdout
        for private in (MARK.lower(), "8675309", "privatecase"):
            assert private not in out.lower()
        # What the program built in their place.
        frame = f"    ytm_player/app/_app.py in {APP_FUNC}\n"
        assert "config dir: custom\n" in out
        assert "config.toml: corrupt\n" in out
        assert "mpv: present (unparsed)\n" in out
        assert "  WARNING ytm_player.app._app - x1\n" in out
        assert f"  ERROR ytm_player.app._app other x1\n{frame}" in out
        assert "  WARNING external - x1\n" in out
        assert "mpv: 1 WARNING+ lines (HTTP 403: 1, HTTP 404: 0, HTTP 5xx: 0, TLS: 0)\n" in out
        assert f"faulthandler: fatal error recorded\n{frame}" in out
        assert f"latest crash: thread, version older, other\n{frame}" in out
        assert "crash files: 1\n" in out

    def test_line_numbers_only_for_real_lines_of_a_same_version_crash(
        self, sandbox: Sandbox
    ) -> None:
        source = (PKG_DIR / "utils" / "report.py").read_text(encoding="utf-8").splitlines()
        comment_line = next(
            number
            for number, text in enumerate(source, 1)
            if text.strip().startswith("# Identical signatures collapse")
        )
        code_line = comment_line + 1  # the statement the comment sits above
        path = "/site-packages/ytm_player/utils/report.py"
        crashes = sandbox.make_config_dir() / "crashes"
        (crashes / "ytm-crash-20261124-100000-000000.log").write_text(
            "=== Textual crash (App._handle_exception) ===\n"
            f"version:   {ytm_player.__version__}\n"
            "\n"
            "Traceback (most recent call last):\n"
            f'  File "{path}", line {comment_line}, in _log_section\n'
            f'  File "{path}", line 8675309, in _log_section\n'
            f'  File "{path}", line {code_line}, in _log_section\n'
            "KeyError: 'x'\n",
            encoding="utf-8",
        )

        result = sandbox.run("report")

        assert result.returncode == 0
        assert result.stderr == ""
        out = result.stdout
        assert "latest crash: textual, version same, KeyError\n" in out
        assert (
            "    ytm_player/utils/report.py in _log_section\n"
            "    ytm_player/utils/report.py in _log_section\n"
            f"    ytm_player/utils/report.py:{code_line} in _log_section\n"
        ) in out
        assert f":{comment_line} " not in out
        assert "8675309" not in out

    def test_logged_error_keeps_its_class_and_frame_without_the_message(
        self, sandbox: Sandbox
    ) -> None:
        """#181: 'Failed to initialize services' must stay diagnosable."""
        sandbox.make_config_dir()
        sandbox.log_file.write_text(
            _log_record("ERROR", "ytm_player.app._app", "Failed to initialize services")
            + _traceback(
                "/usr/lib/python3/site-packages/ytm_player/app/_app.py",
                700,
                APP_FUNC,
                "requests.exceptions.ConnectionError: https://media.invalid/x?sig=secret refused",
            ),
            encoding="utf-8",
        )

        out = sandbox.run("report").stdout

        assert (
            "  ERROR ytm_player.app._app ConnectionError x1\n"
            f"    ytm_player/app/_app.py in {APP_FUNC}\n"
        ) in out
        assert "Failed to initialize" not in out
        assert "media.invalid" not in out

    def test_mpv_version_is_parsed_from_stdout_only(self, sandbox: Sandbox) -> None:
        sandbox.fake_mpv(f"mpv v0.40.0 Copyright {MARK}_BUILD", f"{MARK}_STDERR")

        result = sandbox.run("report")

        assert "mpv: 0.40.0\n" in result.stdout
        assert MARK not in result.stdout + result.stderr

    def test_a_logger_name_that_only_resolves_to_a_file_is_external(self, sandbox: Sandbox) -> None:
        sandbox.make_config_dir()
        sandbox.log_file.write_text(
            _log_record("WARNING", "ytm_player.utils/report", "x")
            + _log_record("WARNING", "ytm_player.app/_app", "x"),
            encoding="utf-8",
        )

        out = sandbox.run("report").stdout

        assert "  WARNING external - x2\n" in out
        assert "utils/report" not in out
        assert "app/_app" not in out


class TestNoisyDependencies:
    """Nothing a dependency prints may reach the output, however it prints it."""

    @pytest.mark.parametrize("args", INVOCATIONS)
    def test_the_printing_process_loads_no_dependency(
        self, sandbox: Sandbox, args: list[str]
    ) -> None:
        """What is never imported there cannot print there, on any Python version."""
        config_dir = sandbox.make_config_dir()
        (config_dir / "config.toml").write_text("[general]\n", encoding="utf-8")
        (config_dir / "crashes" / "ytm-crash-20261124-100000-000000.log").write_text(
            "=== Thread crash ===\nversion:   0.0.1\n\nTraceback\n", encoding="utf-8"
        )

        result = sandbox.run_code(IMPORT_TRACKER, json.dumps(args))

        assert result.returncode == 0
        assert result.stderr == ""
        output, _, added = result.stdout.rpartition("YTM_TEST_ADDED ")
        assert output.startswith(("=== ytm-player report ===", "LOCAL diagnostics"))
        loaded = set(json.loads(added))
        assert ("ytm_player.utils.report" in loaded) == ("--local" in args)
        assert [name for name in PARENT_MUST_NOT_IMPORT if name in loaded] == []

    def test_an_import_finder_that_prints_when_asked_for_a_dependency(
        self, sandbox: Sandbox
    ) -> None:
        """A finder installed in the environment runs for every lookup of a module."""
        finder = (
            "import os, sys\n"
            "class _Loud:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name in ('mpv', 'dbus_fast', 'yt_dlp', 'ytm_player.utils.report', 'ytm_player.utils.doctor'):\n"
            f"            os.write(1, b'{NOISE_MARK}_FDOUT\\n')\n"
            f"            os.write(2, b'{NOISE_MARK}_FDERR\\n')\n"
            "        return None\n"
            "    def find_distributions(self, context=None):\n"
            f"        os.write(1, b'{NOISE_MARK}_METADATA\\n')\n"
            "        return ()\n"
            "sys.meta_path.insert(0, _Loud())\n"
        )
        usercustomize = sandbox.fixtures / "finder"
        usercustomize.mkdir(parents=True)
        (usercustomize / "ytm_test_finder.py").write_text(finder, encoding="utf-8")
        (usercustomize / "ytm_test_finder.pth").write_text("import ytm_test_finder\n", "utf-8")
        # Install the finder in the collector and probe children as well.
        with (sandbox.extra_pythonpath[0] / "sitecustomize.py").open("a", encoding="utf-8") as hook:
            hook.write(f"\nimport site\nsite.addsitedir({str(usercustomize)!r})\n")
        launcher = "from ytm_player.cli import main\nmain(['report'], prog_name='ytm')\n"

        result = sandbox.run_code(launcher)

        assert result.returncode == 0
        assert result.stderr == ""
        assert NOISE_MARK not in result.stdout
        assert result.stdout.startswith("=== ytm-player report ===\n")

    @pytest.mark.parametrize("args", INVOCATIONS)
    def test_python_mpv_and_dbus_fast(self, sandbox: Sandbox, args: list[str]) -> None:
        sandbox.fake_module("mpv", NOISE + "raise RuntimeError('broken build')\n")
        sandbox.fake_module("dbus_fast", NOISE + "raise TypeError('broken build')\n")

        result = sandbox.run(*args)

        assert result.returncode == 0
        assert result.stderr == ""
        assert NOISE_MARK not in result.stdout
        if args == ["doctor", "--local"]:
            assert "libmpv: error (RuntimeError)\n" in result.stdout
            if sys.platform == "linux":
                assert "MPRIS: error (TypeError)\n" in result.stdout
        else:
            assert "libmpv: NOT LOADABLE (RuntimeError)\n" in result.stdout
            if sys.platform == "linux":
                assert "mpris: unavailable (dbus-fast incompatible: TypeError)\n" in result.stdout

    @pytest.mark.parametrize("args", INVOCATIONS)
    def test_packaging_and_tomli(self, sandbox: Sandbox, args: list[str]) -> None:
        config_dir = sandbox.make_config_dir()
        (config_dir / "config.toml").write_text("[general]\n", encoding="utf-8")
        (config_dir / "crashes" / "ytm-crash-20261124-100000-000000.log").write_text(
            "=== Thread crash ===\nversion:   0.0.1\n\nTraceback\n", encoding="utf-8"
        )
        sandbox.fake_module("packaging", NOISE + "raise ImportError('broken build')\n")
        if sys.version_info < (3, 11):
            sandbox.fake_module("tomli", NOISE + "raise ImportError('broken build')\n")

        result = sandbox.run(*args)

        assert result.returncode == 0
        assert result.stderr == ""
        assert NOISE_MARK not in result.stdout
        if args != ["doctor", "--local"]:
            assert "latest crash: thread, version unknown, -\n" in result.stdout
            status = "unknown" if sys.version_info < (3, 11) else "ok"
            assert f"config.toml: {status}\n" in result.stdout


class TestReadOnly:
    @pytest.mark.parametrize("args", INVOCATIONS)
    def test_missing_config_dir_is_not_created(self, sandbox: Sandbox, args: list[str]) -> None:
        result = sandbox.run(*args)

        assert result.returncode == 0
        assert result.stderr == ""
        assert sandbox.snapshot() == {}

    @pytest.mark.parametrize("args", INVOCATIONS)
    def test_corrupt_config_is_left_alone(self, sandbox: Sandbox, args: list[str]) -> None:
        config_dir = sandbox.make_config_dir()
        (config_dir / "config.toml").write_text("[general\nbroken = ", encoding="utf-8")
        (config_dir / "config.toml.bak").write_text("older backup", encoding="utf-8")
        sandbox.log_file.write_text(
            _log_record("ERROR", "ytm_player.app._app", "boom"), encoding="utf-8"
        )
        before = sandbox.snapshot()

        result = sandbox.run(*args)

        assert result.returncode == 0
        assert result.stderr == ""
        assert sandbox.snapshot() == before

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs FIFOs")
    @pytest.mark.parametrize("args", INVOCATIONS)
    def test_fifos_in_place_of_the_files_do_not_hang(
        self, sandbox: Sandbox, args: list[str]
    ) -> None:
        config_dir = sandbox.make_config_dir()
        fifos = [
            config_dir / "config.toml",
            sandbox.log_file,
            config_dir / "crashes" / "faulthandler.log",
            config_dir / "crashes" / "ytm-crash-20261124-100000-000000.log",
        ]
        for fifo in fifos:
            os.mkfifo(fifo)

        result = sandbox.run(*args)  # a blocked read ends in the run's timeout

        assert result.returncode == 0
        assert result.stderr == ""
        if args != ["doctor", "--local"]:
            assert "config.toml: unreadable\n" in result.stdout
            assert "log: unreadable\n" in result.stdout
            assert "faulthandler: unreadable\n" in result.stdout
            assert "latest crash: unreadable\n" in result.stdout

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
    @pytest.mark.parametrize("args", INVOCATIONS)
    def test_read_only_config_dir(self, sandbox: Sandbox, args: list[str]) -> None:
        config_dir = sandbox.make_config_dir()
        (config_dir / "config.toml").write_text("[general]\n", encoding="utf-8")
        read_only = stat.S_IRUSR | stat.S_IXUSR
        for directory in (config_dir / "logs", config_dir / "crashes", config_dir):
            directory.chmod(read_only)
        try:
            before = sandbox.snapshot()
            result = sandbox.run(*args)
            after = sandbox.snapshot()
        finally:
            for directory in (config_dir, config_dir / "logs", config_dir / "crashes"):
                directory.chmod(stat.S_IRWXU)

        assert result.returncode == 0
        assert result.stderr == ""
        assert after == before


class TestBrokenInstall:
    def test_python_mpv_missing_never_reads_as_ok(self, sandbox: Sandbox) -> None:
        result = sandbox.run("report", block="mpv")

        assert result.returncode == 0
        assert result.stderr == ""
        assert "python-mpv: missing\n" in result.stdout
        assert "libmpv: python-mpv not installed\n" in result.stdout

    def test_python_mpv_missing_in_local_diagnostics(self, sandbox: Sandbox) -> None:
        result = sandbox.run("doctor", "--local", block="mpv")

        assert result.returncode == 0
        assert result.stderr == ""
        assert "libmpv: python-mpv not installed\n" in result.stdout
        assert "libmpv: OK" not in result.stdout

    def test_libmpv_load_failure_hides_the_loader_path(self, sandbox: Sandbox) -> None:
        sandbox.fake_module("mpv", f"raise OSError('cannot load /home/{MARK}/libmpv.so')\n")

        result = sandbox.run("report")

        assert result.returncode == 0
        assert result.stderr == ""
        assert "libmpv: NOT LOADABLE (OSError)\n" in result.stdout
        assert MARK not in result.stdout

    def test_an_empty_module_called_mpv_is_not_ok(self, sandbox: Sandbox) -> None:
        sandbox.fake_module("mpv", "")

        result = sandbox.run("report")

        assert result.returncode == 0
        assert result.stderr == ""
        assert "libmpv: unknown\n" in result.stdout

    @pytest.mark.skipif(sys.platform == "win32", reason="needs a shell script as mpv")
    def test_mpv_output_that_is_not_text_in_local_diagnostics(self, sandbox: Sandbox) -> None:
        bin_dir = sandbox.fixtures / "fake-bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "mpv").write_text("#!/bin/sh\nprintf 'mpv \\377\\376 v1\\n'\n", encoding="utf-8")
        (bin_dir / "mpv").chmod(0o755)
        sandbox.extra_path.append(bin_dir)

        result = sandbox.run("doctor", "--local")

        assert result.returncode == 0
        assert result.stderr == ""
        assert "\nmpv: mpv \ufffd\ufffd v1\n" in result.stdout

    @pytest.mark.skipif(sys.platform != "linux", reason="MPRIS is Linux-only")
    @pytest.mark.parametrize("args", [["report"], ["doctor", "--local"]])
    def test_incompatible_dbus_fast(self, sandbox: Sandbox, args: list[str]) -> None:
        """A dbus-fast that cannot even be imported."""
        sandbox.fake_module("dbus_fast", f"raise TypeError('{NOISE_MARK}')\n")

        result = sandbox.run(*args)

        assert result.returncode == 0
        assert result.stderr == ""
        assert NOISE_MARK not in result.stdout
        if args == ["report"]:
            assert "mpris: unavailable (dbus-fast incompatible: TypeError)\n" in result.stdout
        else:
            assert "MPRIS: error (TypeError)\n" in result.stdout

    @pytest.mark.skipif(sys.platform != "linux", reason="MPRIS is Linux-only")
    @pytest.mark.parametrize("args", [["report"], ["doctor", "--local"]])
    def test_dbus_fast_that_fails_while_the_interfaces_are_defined(
        self, sandbox: Sandbox, args: list[str]
    ) -> None:
        """#113: the import works, the decorators raise. services/mpris.py
        records the class and logs the traceback, which must stay off stderr."""
        sandbox.fake_package(
            "dbus_fast",
            {
                "dbus_fast/__init__.py": "class Variant:\n    def __init__(self, *a): pass\n",
                "dbus_fast/aio.py": "class MessageBus: pass\n",
                "dbus_fast/constants.py": "class PropertyAccess:\n    READ = 1\n    READWRITE = 2\n",
                "dbus_fast/service.py": (
                    "class ServiceInterface:\n    def __init__(self, *a): pass\n"
                    f"def dbus_property(*a, **k):\n    raise TypeError('{NOISE_MARK}')\n"
                    "def method(*a, **k):\n    return lambda f: f\n"
                    "signal = method\n"
                ),
            },
        )

        result = sandbox.run(*args)

        assert result.returncode == 0
        assert result.stderr == ""
        assert NOISE_MARK not in result.stdout
        if args == ["report"]:
            assert "mpris: unavailable (dbus-fast incompatible: TypeError)\n" in result.stdout
        else:
            assert "MPRIS: UNAVAILABLE" in result.stdout

    @pytest.mark.skipif(sys.platform != "linux", reason="MPRIS is Linux-only")
    def test_dbus_fast_missing(self, sandbox: Sandbox) -> None:
        result = sandbox.run("report", block="dbus_fast")

        assert result.returncode == 0
        assert result.stderr == ""
        assert "dbus-fast: missing\n" in result.stdout
        assert "mpris: unavailable (dbus-fast missing)\n" in result.stdout

    @pytest.mark.parametrize("module", ["requests", "ytmusicapi"])
    def test_report_works_without(self, sandbox: Sandbox, module: str) -> None:
        result = sandbox.run("report", block=module)

        assert result.returncode == 0
        assert result.stderr == ""
        assert result.stdout.startswith("=== ytm-player report ===\n")
        if module == "ytmusicapi":
            assert "ytmusicapi: missing\n" in result.stdout


# Sections whose lines describe the machine the test runs on.
_MACHINE_SECTIONS = (
    "=== ytm-player diagnostics ===",
    "=== Paths ===",
    "=== Process status ===",
    "=== MPRIS / media keys ===",
)

GOLDEN_INFO_MPV_LINE = _log_record("INFO", "ytm_player.services.player", "mpv[cplayer] Playing")

GOLDEN_LOG = (
    _log_record("INFO", "ytm_player.app._app", "started")
    + _log_record("WARNING", "ytm_player.services.stream", "slow: https://example.invalid/s?ip=1")
    + GOLDEN_INFO_MPV_LINE
    + _log_record("WARNING", "ytm_player.services.player", "mpv[ffmpeg] stream ended early")
    + _log_record("ERROR", "ytm_player.app._app", "Failed to initialize services")
    + _traceback("/x/ytm_player/app/_app.py", 700, "on_mount", "ConnectionError: refused")
    + _log_record("DEBUG", "ytm_player.services.auth", "not shown")
    + _log_record("ERROR", "ytm_player.services.auth", "rejected, cookie: SID=secret")
)

GOLDEN_FAULTHANDLER = (
    "Fatal Python error: Aborted\n\n"
    "Current thread 0x01 (most recent call first):\n"
    '  File "/x/ytm_player/app/_app.py", line 1 in older\n'
    "Fatal Python error: Segmentation fault\n\n"
    "Current thread 0x02 (most recent call first):\n"
    '  File "/x/ytm_player/services/player.py", line 10 in play\n'
)


def _crash_file(label: str, version: str) -> str:
    return (
        f"=== {label} ===\n"
        f"version:   {version}\n"
        "time:      2026-11-24T10:00:00\n"
        "python:    3.11.0\n"
        "platform:  Linux 6.0 (x86_64)\n"
        "\n" + _traceback("/x/ytm_player/app/_app.py", 700, "on_mount", "ValueError: token=abc")
    )


def _write_golden_fixture(config_dir: Path) -> None:
    """The files GOLDEN_MASTER_DOCTOR was generated from. Bytes: no newline translation."""
    files = {
        "logs/ytm.log": GOLDEN_LOG,
        "crashes/faulthandler.log": GOLDEN_FAULTHANDLER,
        "crashes/ytm-crash-20261123-100000-000000.log": _crash_file("older crash", "0.0.2"),
        "crashes/ytm-crash-20261124-100000-000000.log": _crash_file("sys.excepthook", "0.0.1"),
    }
    for relpath, text in files.items():
        (config_dir / relpath).write_bytes(text.encode("utf-8"))


def _normalise(output: str) -> str:
    """Blank out what differs from machine to machine; the line structure stays."""
    lines = []
    section = ""
    for line in output.splitlines():
        if line.startswith("=== "):
            section = line
        elif line and section in _MACHINE_SECTIONS[:2]:
            line = f"{line.partition(':')[0]}: <machine>"
        elif line and section in _MACHINE_SECTIONS:
            line = "<machine>"
        elif line.startswith("From: "):
            line = "From: <machine>"
        else:
            line = re.sub(r"(older than the installed) \S+", r"\1 <version>", line)
        lines.append(line)
    return "\n".join(lines)


# Master's `ytm doctor` (11a88ae) on _write_golden_fixture(), through _normalise().
GOLDEN_MASTER_DOCTOR = """\
=== ytm-player diagnostics ===
Version: <machine>
Python: <machine>
OS: <machine>
Machine: <machine>
mpv: <machine>
libmpv: <machine>

=== Paths ===
config: <machine>
theme: <machine>
session: <machine>
log: <machine>
crashes: <machine>

=== Process status ===
<machine>

=== MPRIS / media keys ===
<machine>

=== Recent ERROR/WARNING (last 20) ===
2026-11-24 10:00:00 [WARNING] [MainThread] ytm_player.services.stream: slow: [URL REDACTED]
2026-11-24 10:00:00 [WARNING] [MainThread] ytm_player.services.player: mpv[ffmpeg] stream ended early
2026-11-24 10:00:00 [ERROR] [MainThread] ytm_player.app._app: Failed to initialize services
Traceback (most recent call last):
  File "/x/ytm_player/app/_app.py", line 700, in on_mount
    source_line(ZQXCANARY_SOURCE)
ConnectionError: refused
2026-11-24 10:00:00 [ERROR] [MainThread] ytm_player.services.auth: rejected, cookie: [REDACTED]


=== Recent mpv warnings/errors ===
2026-11-24 10:00:00 [INFO] [MainThread] ytm_player.services.player: mpv[cplayer] Playing
2026-11-24 10:00:00 [WARNING] [MainThread] ytm_player.services.player: mpv[ffmpeg] stream ended early


=== Most recent faulthandler trace ===
Fatal Python error: Segmentation fault

Current thread 0x02 (most recent call first):
  File "/x/ytm_player/services/player.py", line 10 in play


=== Most recent crash file ===
From: <machine>
=== sys.excepthook ===
version:   0.0.1
time:      2026-11-24T10:00:00
python:    3.11.0
platform:  Linux 6.0 (x86_64)

Traceback (most recent call last):
  File "/x/ytm_player/app/_app.py", line 700, in on_mount
    source_line(ZQXCANARY_SOURCE)
ValueError: token=[REDACTED]

⚠ This crash was recorded by ytm-player 0.0.1, older than the installed <version> — it may already be fixed. Reproduce on the current version before reporting.

=== Active hooks ===
sys.excepthook:        default (unhooked)
threading.excepthook:  default (unhooked)
sys.unraisablehook:    default (unhooked)
faulthandler:          disabled

Note: this section reflects the doctor subcommand's own process. Hooks are installed inside the long-running ytm TUI process — their effect is visible above (crashes/, faulthandler.log)."""


class TestLocalDiagnostics:
    def test_banner_and_traceback(self, sandbox: Sandbox) -> None:
        sandbox.make_config_dir()
        sandbox.log_file.write_text(
            _log_record("ERROR", "ytm_player.app._app", "Failed to initialize services")
            + _traceback("/x/ytm_player/app/_app.py", 700, APP_FUNC, "ConnectionError: refused"),
            encoding="utf-8",
        )

        result = sandbox.run("doctor", "--local")

        assert result.returncode == 0
        assert result.stderr == ""
        assert result.stdout.startswith(
            "LOCAL diagnostics: review before sharing, do not paste the full output publicly."
        )
        assert "Failed to initialize services" in result.stdout
        assert "Traceback (most recent call last):" in result.stdout
        assert "ConnectionError: refused" in result.stdout

    def test_output_is_what_doctor_printed_before_apart_from_the_approved_changes(
        self, sandbox: Sandbox
    ) -> None:
        _write_golden_fixture(sandbox.make_config_dir())

        result = sandbox.run("doctor", "--local")

        # The two approved changes: the banner, and mpv lines below WARNING are left out.
        expected = f"{LOCAL_BANNER}\n\n{GOLDEN_MASTER_DOCTOR.replace(GOLDEN_INFO_MPV_LINE, '')}"
        assert result.stderr == ""
        assert _normalise(result.stdout) == expected


_FILLER = ("x" * 99 + "\n") * (9 * 1024 * 1024 // 100)  # 9 MiB


class TestLargeFiles:
    """No size of log or crash file hides what the report is for."""

    @pytest.mark.parametrize("args", [["report"], ["doctor", "--local"]])
    def test_an_error_followed_by_megabytes_of_debug_records(
        self, sandbox: Sandbox, args: list[str]
    ) -> None:
        sandbox.make_config_dir()
        debug = _log_record("DEBUG", "ytm_player.services.player", "y" * 40)
        sandbox.log_file.write_bytes(
            (
                _log_record("ERROR", "ytm_player.app._app", "the early error")
                + debug * (9 * 1024 * 1024 // len(debug))
            ).encode("utf-8")
        )

        result = sandbox.run(*args)

        assert result.returncode == 0
        assert result.stderr == ""
        if args == ["report"]:
            assert "log: last 1 WARNING+ records\n  ERROR ytm_player.app._app - x1\n" in (
                result.stdout
            )
        else:
            assert "ytm_player.app._app: the early error\n" in result.stdout

    @pytest.mark.parametrize("args", [["report"], ["doctor", "--local"]])
    def test_a_crash_whose_exception_line_comes_after_megabytes(
        self, sandbox: Sandbox, args: list[str]
    ) -> None:
        crashes = sandbox.make_config_dir() / "crashes"
        (crashes / "ytm-crash-20261124-100000-000000.log").write_bytes(
            (
                "=== Thread crash (worker) ===\n"
                "version:   0.0.1\n"
                "\n"
                "Traceback (most recent call last):\n"
                f'  File "/x/ytm_player/app/_app.py", line 700, in {APP_FUNC}\n'
                + _FILLER.replace("x" * 99, "    " + "x" * 95)
                + "KeyError: 'the late exception'\n"
            ).encode("utf-8")
        )

        result = sandbox.run(*args)

        assert result.returncode == 0
        assert result.stderr == ""
        if args == ["report"]:
            assert (
                "latest crash: thread, version older, KeyError\n"
                f"    ytm_player/app/_app.py in {APP_FUNC}\n"
            ) in result.stdout
        else:
            assert "KeyError: 'the late exception'\n" in result.stdout


def test_issue_form_asks_only_for_the_public_report() -> None:
    form = (REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml").read_text(encoding="utf-8")
    field = form[form.index("id: doctor") : form.index("id: extra")]

    assert "ytm doctor" not in form
    assert "On 2.3.0 or newer, run `ytm report`" in field
    assert "On older versions, leave this field blank" in field
    assert "required: false" in field
