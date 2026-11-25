"""Unit tests for utils.report, the allowlist report behind `ytm report`.

The end-to-end guarantees (nothing private printed, read-only, silent) are
tested against real processes in tests/test_cli/test_report_command.py.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ytm_player.utils import report


class TestExceptionClass:
    @pytest.mark.parametrize("name", ["KeyError", "OSError", "DownloadError", "YTMusicServerError"])
    def test_known_names_pass(self, name: str) -> None:
        assert report.exception_class(name) == name

    @pytest.mark.parametrize("name", ["MyPrivateError", "", "str", "print", "key error"])
    def test_everything_else_is_other(self, name: str) -> None:
        assert report.exception_class(name) == "other"


class TestMpvStatus:
    def _run(self, monkeypatch, stdout: bytes, returncode: int = 0) -> str:
        monkeypatch.setattr(report.shutil, "which", lambda _name: "/usr/bin/mpv")
        monkeypatch.setattr(
            report.subprocess,
            "run",
            lambda *a, **kw: subprocess.CompletedProcess(a, returncode, stdout, b""),
        )
        return report._mpv_status()

    @pytest.mark.parametrize(
        ("stdout", "expected"),
        [
            (b"mpv v0.42.0 Copyright (c) 2000-2026 mpv/MPlayer/mplayer2 projects\n", "0.42.0"),
            (b"mpv 0.38.0-dirty Copyright\n", "0.38.0"),
            (b"mpv git-2026-01-01-abcdef Copyright\n", "present (unparsed)"),
            (b"", "present (unparsed)"),
            (b"something else mpv 1.2.3\n", "present (unparsed)"),
        ],
    )
    def test_version_token(self, monkeypatch, stdout: bytes, expected: str) -> None:
        assert self._run(monkeypatch, stdout) == expected

    def test_nonzero_exit(self, monkeypatch) -> None:
        assert self._run(monkeypatch, b"mpv v0.42.0", returncode=1) == "failed to execute"

    def test_not_on_path(self, monkeypatch) -> None:
        monkeypatch.setattr(report.shutil, "which", lambda _name: None)
        assert report._mpv_status() == "not found"

    def test_cannot_be_run(self, monkeypatch) -> None:
        def boom(*_args, **_kwargs):
            raise PermissionError("denied")

        monkeypatch.setattr(report.shutil, "which", lambda _name: "/usr/bin/mpv")
        monkeypatch.setattr(report.subprocess, "run", boom)
        assert report._mpv_status() == "failed to execute"


class TestDependencyVersion:
    def _lines(self, monkeypatch, result: object) -> list[str]:
        monkeypatch.setattr(report, "_dependencies", lambda: (("dep", "dep", ("dep",)),))
        monkeypatch.setattr(report, "run_probe", lambda _name: result)
        return report._dependency_versions()

    @pytest.mark.parametrize(
        ("result", "expected"),
        [
            ({"dep": [True, "1.2.3+private.build"]}, "1.2.3"),
            ({"dep": [True, "private-build"]}, "unknown"),
            ({"dep": [True, None]}, "unknown"),
            ({"dep": [True, 7]}, "unknown"),
            ({"dep": [False, None]}, "missing"),
            ({"dep": [False, "1.2.3"]}, "missing"),
            ({"dep": ["missing", None]}, "unknown"),
            ({"dep": "1.2.3"}, "unknown"),
            ({"other": [True, "1.2.3"]}, "unknown"),
            (["error", "KeyError"], "unknown"),
            (None, "unknown"),  # the child did not answer
        ],
    )
    def test_the_childs_answer_is_validated(self, monkeypatch, result, expected: str) -> None:
        assert self._lines(monkeypatch, result) == [f"dep: {expected}"]

    def _probe(self, monkeypatch, module: str, metadata) -> dict[str, list[object]]:
        monkeypatch.setattr(
            report, "_dependencies", lambda: (("dep", module, ("first", "second")),)
        )
        monkeypatch.setattr(report.importlib.metadata, "metadata", metadata)
        return report.probe_dependencies()

    def test_probe_reads_the_version_field(self, monkeypatch) -> None:
        def metadata(distribution: str) -> dict[str, str]:
            if distribution == "first":
                raise report.importlib.metadata.PackageNotFoundError(distribution)
            return {"Version": "1.2.3+private.build"}

        assert self._probe(monkeypatch, "json", metadata) == {"dep": [True, "1.2.3+private.build"]}

    @pytest.mark.parametrize("found", [{}, {"Version": 7}, None])
    def test_probe_without_a_usable_version_field(self, monkeypatch, found) -> None:
        assert self._probe(monkeypatch, "json", lambda _d: found) == {"dep": [True, None]}

    def test_probe_missing_module(self, monkeypatch) -> None:
        assert self._probe(monkeypatch, "no_such_module_zqx", lambda _d: {}) == {
            "dep": [False, None]
        }

    def test_broken_metadata_preserves_other_versions(self, monkeypatch) -> None:
        def metadata(name):
            if name == "first":
                raise ValueError("invalid metadata")
            return {"Version": "2.0"}

        monkeypatch.setattr(
            report,
            "_dependencies",
            lambda: (
                ("broken", "sys", ("first",)),
                ("healthy", "sys", ("second",)),
            ),
        )
        monkeypatch.setattr(report.importlib.metadata, "metadata", metadata)
        assert report.probe_dependencies() == {"broken": [None, None], "healthy": [True, "2.0"]}

    def test_probe_runs_in_a_child(self) -> None:
        result = report.run_probe("dependencies")
        assert isinstance(result, dict)
        assert result["textual"][0] is True
        assert report.numeric_version(result["textual"][1]) != "unknown"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2.2.0", "2.2.0"),
            ("2026.11.20.232912", "2026.11.20.232912"),  # yt-dlp nightly
            ("4.2+privatecase", "4.2"),
            ("1.0rc1", "1.0"),
            ("1.0.post2.dev3", "1.0"),
            ("123456789", "unknown"),  # too long: never cut short
            ("1.2.3.4.5.6", "unknown"),
            ("\uff11\uff12.3", "unknown"),  # full-width digits
            ("1.2\u0663", "unknown"),
            ("1.2 private", "unknown"),
            ("1.2+private case", "unknown"),
            ("", "unknown"),
            ("1.0a1.post2.dev3+local.1", "1.0"),
            ("1.0rc1rc2", "unknown"),  # one pre-release marker, not a run of them
            # Took seconds while the markers could repeat: must fail at once.
            ("1.0" + "rc" * 24 + "!", "unknown"),
            ("1.0" + "rc" * 2000 + "!", "unknown"),
        ],
    )
    def test_numeric_version(self, raw: str, expected: str) -> None:
        assert report.numeric_version(raw) == expected

    @pytest.mark.parametrize("raw", [None, 7, b"1.0", ["1.0"]])
    def test_numeric_version_of_something_that_is_not_text(self, raw: object) -> None:
        assert report.numeric_version(raw) == "unknown"


class TestProbes:
    def test_runs_in_a_child_and_returns_its_result(self) -> None:
        assert report.run_probe("staleness", "1.0.0", "1.9.4") == "older"
        assert report.crash_staleness_status("1.9.4", "1.9.4") == "same"
        assert report.crash_staleness_status(None, "1.9.4") == "unversioned"

    def test_an_unknown_probe_is_an_error_not_a_crash(self) -> None:
        assert report.run_probe("no_such_probe") == ["error", "KeyError"]

    @pytest.mark.parametrize(
        ("result", "expected"),
        [
            (["ok", None], "OK"),
            (["failed", "OSError"], "NOT LOADABLE (OSError)"),
            (["failed", "PrivateError"], "NOT LOADABLE (other)"),
            (["failed", None], "NOT LOADABLE (other)"),
            (["error", "KeyError"], "error (KeyError)"),
            (["made up", None], "unknown"),
            (["ok"], "unknown"),
            ("OK", "unknown"),
            ({"ok": 1}, "unknown"),
            (None, "unknown"),
        ],
    )
    def test_results_are_rendered_from_a_closed_set(self, result: object, expected: str) -> None:
        assert report.render_probe(result, report.LIBMPV_STATUS) == expected


class TestLibmpvProbe:
    def _probe(self, monkeypatch, player: object) -> list[str | None]:
        monkeypatch.setattr(report, "_module_present", lambda _module: True)
        monkeypatch.setattr(report.importlib, "import_module", lambda _name: player)
        return report.probe_libmpv()

    def test_ok_needs_the_api_version_libmpv_reported(self, monkeypatch) -> None:
        player = SimpleNamespace(mpv=SimpleNamespace(MPV_VERSION=(2, 5)))
        assert self._probe(monkeypatch, player) == ["ok", None]

    @pytest.mark.parametrize("version", [None, (), (2,), (2, 5, 1), ("2", "5"), (True, 1), [2, 5]])
    def test_anything_else_is_unknown(self, monkeypatch, version: object) -> None:
        player = SimpleNamespace(mpv=SimpleNamespace(MPV_VERSION=version))
        assert self._probe(monkeypatch, player) == ["unknown", None]

    def test_an_empty_module_called_mpv_is_not_ok(self, monkeypatch) -> None:
        assert self._probe(monkeypatch, SimpleNamespace(mpv=SimpleNamespace())) == [
            "unknown",
            None,
        ]

    def test_the_stubbed_player_is_a_load_failure(self, monkeypatch) -> None:
        player = SimpleNamespace(_IMPORT_ERROR_MSG="Cannot load /private/libmpv.so")
        assert self._probe(monkeypatch, player) == ["failed", "OSError"]


class TestMprisStatus:
    @pytest.mark.skipif(sys.platform != "linux", reason="Linux-only MPRIS path")
    def test_incompatible_build_names_the_recorded_class(self, monkeypatch) -> None:
        import ytm_player.services.mpris as mpris_mod

        monkeypatch.setattr(report, "_module_present", lambda _module: True)
        monkeypatch.setattr(mpris_mod, "DBUS_AVAILABLE", False)
        monkeypatch.setattr(mpris_mod, "DBUS_ERROR_CLASS", "TypeError")
        result = report.probe_mpris()
        assert result == ["failed", "TypeError"]
        assert report.render_probe(result, report._MPRIS_STATUS) == (
            "unavailable (dbus-fast incompatible: TypeError)"
        )

    @pytest.mark.skipif(sys.platform == "linux", reason="non-Linux native-integration path")
    def test_non_linux_is_not_applicable(self) -> None:
        assert report._mpris_status() == "n/a"


class TestConfigFileStatus:
    def test_states(self, tmp_path: Path) -> None:
        config = tmp_path / "config.toml"
        assert report._config_file_status(config) == "missing"
        config.write_text("[general]\nx = 1\n", encoding="utf-8")
        assert report._config_file_status(config) == "ok"
        config.write_text("[general\n", encoding="utf-8")
        assert report._config_file_status(config) == "corrupt"
        config.write_bytes(b"\xff\xfe\x00")
        assert report._config_file_status(config) == "corrupt"

    def test_a_directory_in_its_place_is_unreadable(self, tmp_path: Path) -> None:
        (tmp_path / "config.toml").mkdir()
        assert report._config_file_status(tmp_path / "config.toml") == "unreadable"

    def test_an_oversized_file_is_not_read(self, tmp_path: Path) -> None:
        config = tmp_path / "config.toml"
        config.write_bytes(b"# padding\n" * (report.MAX_CONFIG_BYTES // 10 + 1))
        assert report._config_file_status(config) == "unreadable"


class TestFileReads:
    def test_a_log_is_read_whole_with_newline_line_endings(self, tmp_path: Path) -> None:
        log = tmp_path / "ytm.log"
        log.write_bytes(b"first\r\n" + b"x" * (2 * 1024 * 1024) + b"\r\nlast\r\n")

        text = report.read_text(log)

        assert text is not None
        assert text.startswith("first\n")
        assert text.endswith("\nlast\n")
        assert len(text) == len("first\n") + 2 * 1024 * 1024 + len("\nlast\n")

    def test_a_limit_stops_the_read(self, tmp_path: Path) -> None:
        (tmp_path / "f").write_bytes(b"0123456789")
        assert report.read_regular(tmp_path / "f", 4) == b"0123"

    def test_missing_file(self, tmp_path: Path) -> None:
        assert report.read_text(tmp_path / "nope.log") is None

    def test_an_unreadable_log_is_not_called_missing(self, tmp_path: Path) -> None:
        (tmp_path / "ytm.log").mkdir()
        (tmp_path / "faulthandler.log").mkdir()
        assert report._log_section(tmp_path / "ytm.log") == ["log: unreadable", "mpv: unreadable"]
        assert report._faulthandler_section(tmp_path) == ["faulthandler: unreadable"]

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs FIFOs")
    def test_a_fifo_is_refused_without_waiting_for_a_writer(self, tmp_path: Path) -> None:
        from ytm_player.utils.doctor import _recent_mpv_lines
        from ytm_player.utils.logging import get_recent_log_lines

        fifo = tmp_path / "ytm.log"
        os.mkfifo(fifo)

        assert report._log_section(fifo) == ["log: unreadable", "mpv: unreadable"]
        assert report._config_file_status(fifo) == "unreadable"
        assert _recent_mpv_lines(fifo) == "(could not read log file)"
        assert get_recent_log_lines(fifo) == ""


class TestLoggerName:
    @pytest.mark.parametrize(
        "name", ["ytm_player", "ytm_player.utils.report", "ytm_player.app._app", "ytm_player.app"]
    )
    def test_real_modules_pass(self, name: str) -> None:
        assert report._logger_name(name) == name

    @pytest.mark.parametrize(
        "name",
        [
            "ytm_player.utils/report",
            "ytm_player.app/_app",
            "ytm_player/utils/report",
            "ytm_player.utils.report.py",
            "ytm_player.utils.nope",
            "ytm_player.",
            "requests.adapters",
            "",
        ],
    )
    def test_everything_else_is_external(self, name: str) -> None:
        assert report._logger_name(name) == "external"


class TestPackageFiles:
    def test_a_directory_named_like_a_module_authorizes_no_logger_name(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        (tmp_path / "real.py").write_text("", encoding="utf-8")
        (tmp_path / "x.py").mkdir()
        (tmp_path / "sub" / "y.py").mkdir(parents=True)
        log = tmp_path / "ytm.log"
        log.write_text(
            "2026-11-24 10:00:00 [WARNING] [MainThread] ytm_player.x: a\n"
            "2026-11-24 10:00:00 [WARNING] [MainThread] ytm_player.sub.y: a\n"
            "2026-11-24 10:00:00 [ERROR] [MainThread] ytm_player.real: a\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(report, "_PKG_DIR", tmp_path)
        report._package_files.cache_clear()
        report._module_names.cache_clear()
        try:
            assert report._package_files() == frozenset({"real.py"})
            assert report._log_section(log)[1:3] == [
                "  WARNING external - x2",
                "  ERROR ytm_player.real - x1",
            ]
        finally:
            report._package_files.cache_clear()
            report._module_names.cache_clear()


class TestOwnLines:
    def test_only_lines_that_can_run(self) -> None:
        functions = report._functions("utils/report.py")
        assert functions is not None
        source = (report._PKG_DIR / "utils" / "report.py").read_text(encoding="utf-8").splitlines()
        for name, lines in functions.items():
            assert all(1 <= line <= len(source) for line in lines), name
        assert functions["<module>"]
        assert functions["build_report"]

    def test_line_zero_of_a_module_is_never_printed(self) -> None:
        frame = report._verified_frame
        path = "/x/ytm_player/utils/report.py"
        assert frame(path, "0", "<module>", with_line=True) == (
            "ytm_player/utils/report.py in <module>"
        )
        assert frame(path, "0", "build_report", with_line=True) == (
            "ytm_player/utils/report.py in build_report"
        )


class TestRunningStatus:
    @pytest.fixture(autouse=True)
    def _own_pid(self, monkeypatch):
        monkeypatch.setattr(report.os, "getpid", lambda: 42)

    def _proc(self, tmp_path: Path, cmdlines: dict[str, bytes]) -> Path:
        for pid, cmdline in cmdlines.items():
            (tmp_path / pid).mkdir()
            (tmp_path / pid / "cmdline").write_bytes(cmdline)
        (tmp_path / "self").mkdir()
        return tmp_path

    def test_yes_for_an_entry_point_under_a_path_with_a_space(self, tmp_path: Path) -> None:
        proc = self._proc(
            tmp_path,
            {
                "11": b"/usr/bin/firefox\x00",
                "12": b"/home/alice smith/venv/bin/python3\x00/home/alice smith/venv/bin/ytm\x00",
            },
        )
        assert report._running_status(proc) == "yes"

    def test_no_when_every_process_was_read(self, tmp_path: Path) -> None:
        proc = self._proc(tmp_path, {"11": b"python3\x00helper.py\x00ytm_player\x00", "12": b""})
        assert report._running_status(proc) == "no"

    @pytest.mark.skipif(
        sys.platform == "win32" or getattr(os, "geteuid", lambda: 1)() == 0,
        reason="needs POSIX permission bits and a non-root user",
    )
    def test_unknown_when_a_process_could_not_be_read(self, tmp_path: Path) -> None:
        proc = self._proc(tmp_path, {"11": b"/usr/bin/firefox\x00", "12": b"/usr/bin/ytm\x00"})
        (proc / "12" / "cmdline").chmod(0)
        assert report._running_status(proc) == "unknown"
        (proc / "13").mkdir()
        (proc / "13" / "cmdline").write_bytes(b"/usr/bin/ytm\x00")
        assert report._running_status(proc) == "yes"

    def test_a_process_that_ended_meanwhile_is_not_ytm(self, tmp_path: Path) -> None:
        proc = self._proc(tmp_path, {"11": b"/usr/bin/firefox\x00"})
        (proc / "12").mkdir()  # listed, then gone: no cmdline to read
        assert report._running_status(proc) == "no"

    def test_unknown_when_reading_a_live_process_failed(self, tmp_path: Path) -> None:
        """Any error other than "the process is gone" leaves the answer open."""
        proc = self._proc(tmp_path, {"11": b"/usr/bin/firefox\x00"})
        (proc / "12" / "cmdline").mkdir(parents=True)  # reading it raises an OSError
        assert report._running_status(proc) == "unknown"

    @pytest.mark.parametrize(
        "cmdline",
        [
            b"/usr/bin/python3\x00/usr/bin/ytm\x00setup\x00--manual\x00",
            b"/usr/bin/python3\x00/usr/bin/ytm\x00report\x00",
            b"/usr/bin/ytm\x00--help\x00",
            b"/usr/bin/ytm\x00\x00",
        ],
    )
    def test_a_ytm_command_that_is_not_the_tui(self, tmp_path: Path, cmdline: bytes) -> None:
        assert report._running_status(self._proc(tmp_path, {"11": cmdline})) == "no"

    def test_unknown_without_proc(self, tmp_path: Path) -> None:
        assert report._running_status(tmp_path / "no-proc") == "unknown"
        assert report._running_status(tmp_path) == "unknown"


class TestVerifiedFrame:
    def test_requires_a_real_package_file(self) -> None:
        frame = report._verified_frame
        assert frame("/x/ytm_player/utils/report.py", "1", "<module>", with_line=False) == (
            "ytm_player/utils/report.py in <module>"
        )
        assert frame("/x/ytm_player/utils/nope.py", "1", "<module>", with_line=False) is None
        assert frame("/x/ytm_player/../secret.py", "1", "<module>", with_line=False) is None
        assert frame("/x/site-packages/textual/app.py", "1", "run", with_line=False) is None
        assert frame("C:\\x\\ytm_player\\utils\\report.py", "1", "build_report", with_line=False)

    def test_requires_a_real_function(self) -> None:
        path = "/x/ytm_player/utils/report.py"
        assert report._verified_frame(path, "1", "not_a_function", with_line=False) is None
        assert report._verified_frame(path, "1", "<lambda>", with_line=False) is None


class TestTracebackSignature:
    def test_takes_the_exception_after_the_last_frame(self) -> None:
        lines = [
            "Traceback (most recent call last):",
            '  File "/x/ytm_player/utils/report.py", line 5, in build_report',
            "    return something()",
            "ValueError: first",
            "",
            "During handling of the above exception, another exception occurred:",
            "",
            "Traceback (most recent call last):",
            '  File "/x/site-packages/requests/api.py", line 9, in get',
            "    raise it",
            "requests.exceptions.ReadTimeout: took too long",
            "KeyError: a later line of a multi-line message",
        ]
        assert report._traceback_signature(lines, with_lines=False) == (
            "ReadTimeout",
            ["ytm_player/utils/report.py in build_report"],
        )

    def test_keeps_the_innermost_frames(self) -> None:
        frame = '  File "/x/ytm_player/utils/report.py", line 5, in build_report'
        last = '  File "/x/ytm_player/utils/report.py", line 5, in _collect'
        _name, frames = report._traceback_signature([frame] * 7 + [last], with_lines=False)
        assert len(frames) == report.MAX_FRAMES
        assert frames[-1] == "ytm_player/utils/report.py in _collect"

    def test_no_traceback(self) -> None:
        assert report._traceback_signature(["just a message"], with_lines=False) == (None, [])

    def test_an_absurd_line_number_is_not_a_frame(self) -> None:
        # int() refuses more than 4300 digits; such a line must not get that far.
        line = f'  File "/x/ytm_player/utils/report.py", line {"9" * 4301}, in build_report'
        assert report._traceback_signature([line, "KeyError: x"], with_lines=True) == (None, [])


class TestCrashHeader:
    @pytest.mark.parametrize(
        ("crash_version", "expected"),
        [
            ("1.9.4", "same"),
            ("1.0.0", "older"),
            ("2.0.0", "newer"),
            (None, "unversioned"),
            ("unknown", "unknown"),
            ("not-a-version", "unknown"),
        ],
    )
    def test_staleness(self, crash_version: str | None, expected: str) -> None:
        assert report.crash_staleness(crash_version, "1.9.4") == expected

    @pytest.mark.parametrize(
        ("label", "category"),
        [
            ("Main thread crash", "main_thread"),
            ("Thread crash (worker-1)", "thread"),
            ("Unraisable in <object>", "unraisable"),
            ("Asyncio loop exception", "asyncio"),
            ("Textual crash (App._handle_exception)", "textual"),
            ("Something private", "unknown"),
        ],
    )
    def test_category(self, tmp_path: Path, label: str, category: str) -> None:
        (tmp_path / "ytm-crash-1.log").write_text(f"=== {label} ===\n\nbody\n", encoding="utf-8")
        assert report._crash_section(tmp_path, "1.9.4") == [
            f"latest crash: {category}, version unversioned, -"
        ]

    def test_no_crash_files(self, tmp_path: Path) -> None:
        assert report._crash_section(tmp_path, "1.9.4") == ["latest crash: none"]

    def _crash_with_header(self, tmp_path: Path, header: str) -> list[str]:
        functions = report._functions("utils/report.py")
        assert functions is not None
        line = min(functions["build_report"])
        (tmp_path / "ytm-crash-1.log").write_text(
            f"{header}\n"
            "Traceback (most recent call last):\n"
            f'  File "/x/ytm_player/utils/report.py", line {line}, in build_report\n'
            "KeyError: 'x'\n",
            encoding="utf-8",
        )
        return [
            frame.replace(f":{line} ", ":LINE ")
            for frame in report._crash_section(tmp_path, "1.9.4")
        ]

    def test_line_numbers_for_a_crash_this_build_stamped(self, tmp_path: Path) -> None:
        assert self._crash_with_header(tmp_path, "=== Crash ===\nversion:   1.9.4\n") == [
            "latest crash: unknown, version same, KeyError",
            "    ytm_player/utils/report.py:LINE in build_report",
        ]

    @pytest.mark.parametrize(
        "header",
        [
            "version: 1.9.4\n",  # no banner
            "version: 1.9.4 extra\n",
            "=== Crash ===\nversion: 1.9.4 extra\n",
            "=== Crash ===\nversion: 1.9.4.0\n",  # equal as a version, not the same stamp
            # The line read for "same" is the line that must be exact: a later
            # exact-looking line cannot lend it line numbers.
            "=== Crash ===\nversion: 1.9.4.0 x\nversion: 1.9.4\n",
            "=== Crash ===\ntime: now\nversion: 1.9.4.0 x\n\nversion: 1.9.4\n",
            "=== Crash\nversion: 1.9.4\n",
        ],
    )
    def test_no_line_numbers_without_an_exact_stamp(self, tmp_path: Path, header: str) -> None:
        assert self._crash_with_header(tmp_path, header) == [
            "latest crash: unknown, version same, KeyError",
            "    ytm_player/utils/report.py in build_report",
        ]


class TestFaulthandlerSection:
    def test_prefers_the_crashing_thread(self, tmp_path: Path) -> None:
        (tmp_path / "faulthandler.log").write_text(
            "Fatal Python error: Segmentation fault\n\n"
            "Thread 0x01 (most recent call first):\n"
            '  File "/x/ytm_player/utils/report.py", line 5 in build_report\n'
            "\n"
            "Current thread 0x02 (most recent call first):\n"
            '  File "/x/lib/threading.py", line 5 in run\n'
            '  File "/x/ytm_player/utils/report.py", line 5 in _collect\n',
            encoding="utf-8",
        )
        assert report._faulthandler_section(tmp_path) == [
            "faulthandler: fatal error recorded",
            "    ytm_player/utils/report.py in _collect",
        ]

    def test_empty_file(self, tmp_path: Path) -> None:
        (tmp_path / "faulthandler.log").write_text("", encoding="utf-8")
        assert report._faulthandler_section(tmp_path) == ["faulthandler: none"]

    @pytest.mark.parametrize("header", ["Windows fatal exception: access violation", None])
    def test_the_latest_of_several_dumps_wins(self, tmp_path: Path, header: str | None) -> None:
        def dump(func: str) -> str:
            return (
                f"{header or 'Fatal Python error: Segmentation fault'}\n\n"
                "Current thread 0x01 (most recent call first):\n"
                f'  File "/x/ytm_player/utils/report.py", line 5 in {func}\n\n'
            )

        (tmp_path / "faulthandler.log").write_text(
            dump("build_report") + dump("_collect"), encoding="utf-8"
        )
        assert report._faulthandler_section(tmp_path) == [
            "faulthandler: fatal error recorded",
            "    ytm_player/utils/report.py in _collect",
        ]


class TestGuarded:
    def test_a_failing_collector_becomes_a_fixed_status(self) -> None:
        def boom() -> str:
            raise KeyError("private detail")

        class PrivateError(Exception):
            pass

        def private() -> str:
            raise PrivateError("private detail")

        assert report._guarded(boom) == "error (KeyError)"
        assert report._guarded(private) == "error (other)"
