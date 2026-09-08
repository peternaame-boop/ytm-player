"""Tests for utils.doctor — diagnostic gathering for `ytm doctor`."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


class TestDiagnosticUrlRedaction:
    @pytest.mark.parametrize(
        "url",
        [
            "https://media.invalid/play?ip=192.0.2.42&sig=fake-signature&pot=fake-token",
            "HTTP://media.invalid/play?ip=2001%3Adb8%3A%3A42&signature=fake-signature",
            "tcp://[2001:db8::42]:443",
            "TcP://192.0.2.42:443",
            "https://media.invalid/a(b)?unknown=fake-secret",
        ],
    )
    @pytest.mark.parametrize("quotes", ["", "'", '"'])
    def test_removes_whole_url_preserving_error_text(self, url, quotes):
        from ytm_player.utils.doctor import _redact

        message = f"HTTP 403 opening {quotes}{url}{quotes} failed\nnext diagnostic line"
        assert _redact(message) == (
            f"HTTP 403 opening {quotes}[URL REDACTED]{quotes} failed\nnext diagnostic line"
        )

    def test_multiple_urls_and_header_redaction(self):
        from ytm_player.utils.doctor import _redact

        assert _redact(
            "https://one.invalid/?secret=one http://two.invalid/?secret=two\n"
            "Authorization: Bearer fake-auth\nCookie: SAPISID=fake-cookie\n"
            "Bearer fake-bearer\nstatus: 403; retry unavailable"
        ) == (
            "[URL REDACTED] [URL REDACTED]\nAuthorization: [REDACTED]\n"
            "Cookie: [REDACTED]\nBearer [REDACTED]\nstatus: 403; retry unavailable"
        )

    def test_normal_text_is_unchanged(self):
        from ytm_player.utils.doctor import _redact

        text = "Version: 2.1.0\nHTTP 403\nmpv: available\nconfig: /tmp/ytm/config.toml"
        assert _redact(text) == text

    def test_public_report_redacts_synthetic_logs_and_crash_without_rewriting(
        self, monkeypatch, tmp_path
    ):
        from ytm_player.config import paths
        from ytm_player.utils import doctor
        from ytm_player.utils import logging as log_utils

        for name in ("CONFIG_FILE", "THEME_FILE", "SESSION_STATE_FILE", "LOG_FILE"):
            monkeypatch.setattr(paths, name, tmp_path / name)
        crash_dir = tmp_path / "crashes"
        crash_dir.mkdir()
        monkeypatch.setattr(paths, "CRASH_DIR", crash_dir)
        for name in ("_mpv_version", "_libmpv_status", "_running_status", "_mpris_status"):
            monkeypatch.setattr(doctor, name, lambda: "synthetic collector")
        monkeypatch.setattr(log_utils, "list_active_hooks", lambda: "synthetic hooks")
        url = "https://media.invalid/play?ip=192.0.2.42&sig=fake-signature&pot=fake-token"
        payload = f"[WARNING] mpv[file]: HTTP 403 opening '{url}' failed\n"
        paths.LOG_FILE.write_text(payload, encoding="utf-8")
        crash = crash_dir / "ytm-crash-20260908-000000-000000.log"
        crash.write_text(f"=== Crash ===\nversion: 2.0.0\n\n{payload}", encoding="utf-8")
        fault = crash_dir / "faulthandler.log"
        fault.write_text(f"Fatal Python error: synthetic\n{payload}", encoding="utf-8")
        before = {p: p.read_bytes() for p in (paths.LOG_FILE, crash, fault)}

        report = doctor.gather_diagnostics()

        assert report.count("[URL REDACTED]") == 4  # log, mpv, faulthandler, crash
        assert "HTTP 403" in report
        assert "=== Paths ===" in report
        assert "synthetic hooks" in report
        for private in ("192.0.2.42", "fake-signature", "fake-token", "media.invalid"):
            assert private not in report
        assert {p: p.read_bytes() for p in before} == before


class TestGatherDiagnosticsExisting:
    """v1 sections must still work."""

    def test_includes_version(self):
        from ytm_player.utils.doctor import gather_diagnostics

        report = gather_diagnostics()
        from ytm_player import __version__

        assert __version__ in report

    def test_includes_python_version(self):
        import sys

        from ytm_player.utils.doctor import gather_diagnostics

        report = gather_diagnostics()
        assert f"{sys.version_info.major}.{sys.version_info.minor}" in report

    def test_includes_platform(self):
        import platform

        from ytm_player.utils.doctor import gather_diagnostics

        report = gather_diagnostics()
        assert platform.system() in report


class TestGatherDiagnosticsV2:
    """v2 must include 8 sections in order, with redaction."""

    def test_section_headers_present(self):
        from ytm_player.utils.doctor import gather_diagnostics

        report = gather_diagnostics()
        assert "=== ytm-player diagnostics ===" in report
        assert "=== Paths ===" in report
        assert "=== Process status ===" in report
        assert "=== MPRIS / media keys ===" in report
        assert "=== Recent ERROR/WARNING (last 20) ===" in report
        assert "=== Recent mpv warnings/errors ===" in report
        assert "=== Most recent faulthandler trace ===" in report
        assert "=== Most recent crash file ===" in report
        assert "=== Active hooks ===" in report

    def test_section_order(self):
        from ytm_player.utils.doctor import gather_diagnostics

        report = gather_diagnostics()
        order = [
            "=== ytm-player diagnostics ===",
            "=== Paths ===",
            "=== Process status ===",
            "=== MPRIS / media keys ===",
            "=== Recent ERROR/WARNING (last 20) ===",
            "=== Recent mpv warnings/errors ===",
            "=== Most recent faulthandler trace ===",
            "=== Most recent crash file ===",
            "=== Active hooks ===",
        ]
        positions = [report.index(h) for h in order]
        assert positions == sorted(positions), f"Sections out of order: {positions}"

    def test_redacts_authorization_header(self, monkeypatch, tmp_path: Path):
        from ytm_player.config import paths
        from ytm_player.utils.doctor import gather_diagnostics

        log = tmp_path / "ytm.log"
        log.write_text("2026-04-30 [WARNING] foo: Authorization: Bearer abc123secret\n")
        monkeypatch.setattr(paths, "LOG_FILE", log)

        report = gather_diagnostics()
        assert "abc123secret" not in report
        assert "[REDACTED]" in report

    def test_redacts_cookie_header(self, monkeypatch, tmp_path: Path):
        from ytm_player.config import paths
        from ytm_player.utils.doctor import gather_diagnostics

        log = tmp_path / "ytm.log"
        log.write_text("2026-04-30 [WARNING] foo: Cookie: SAPISID=secret\n")
        monkeypatch.setattr(paths, "LOG_FILE", log)

        report = gather_diagnostics()
        assert "SAPISID=secret" not in report

    def test_mpv_section_filters_for_mpv_prefix(self, monkeypatch, tmp_path: Path):
        from ytm_player.config import paths
        from ytm_player.utils.doctor import gather_diagnostics

        log = tmp_path / "ytm.log"
        log.write_text(
            "2026-04-30 [WARNING] ytm_player: regular warning\n"
            "2026-04-30 [WARNING] ytm_player.services.player: mpv[ao]: format mismatch\n"
            "2026-04-30 [ERROR] ytm_player.services.player: mpv[file]: cannot open\n"
        )
        monkeypatch.setattr(paths, "LOG_FILE", log)

        report = gather_diagnostics()
        start = report.index("=== Recent mpv warnings/errors ===")
        end = report.index("=== Most recent faulthandler trace ===")
        mpv_section = report[start:end]
        assert "mpv[ao]: format mismatch" in mpv_section
        assert "mpv[file]: cannot open" in mpv_section
        assert "regular warning" not in mpv_section

    def test_faulthandler_section_shows_last_block_when_present(self, monkeypatch, tmp_path: Path):
        from ytm_player.config import paths
        from ytm_player.utils.doctor import gather_diagnostics

        crash_dir = tmp_path / "crashes"
        crash_dir.mkdir()
        fh = crash_dir / "faulthandler.log"
        fh.write_text(
            "Fatal Python error: Segmentation fault\n\n"
            "Current thread 0x0 (most recent call first):\n"
            "  File 'a.py', line 1 in foo\n"
        )
        monkeypatch.setattr(paths, "CRASH_DIR", crash_dir)

        report = gather_diagnostics()
        start = report.index("=== Most recent faulthandler trace ===")
        end = report.index("=== Most recent crash file ===")
        section = report[start:end]
        assert "Fatal Python error: Segmentation fault" in section

    def test_faulthandler_section_when_absent(self, monkeypatch, tmp_path: Path):
        from ytm_player.config import paths
        from ytm_player.utils.doctor import gather_diagnostics

        crash_dir = tmp_path / "crashes"
        crash_dir.mkdir()
        monkeypatch.setattr(paths, "CRASH_DIR", crash_dir)

        report = gather_diagnostics()
        start = report.index("=== Most recent faulthandler trace ===")
        end = report.index("=== Most recent crash file ===")
        section = report[start:end]
        body = section.lower()
        assert "no faulthandler trace" in body or "(empty" in body

    def test_active_hooks_section_lists_all(self):
        from ytm_player.utils.doctor import gather_diagnostics

        report = gather_diagnostics()
        start = report.index("=== Active hooks ===")
        section = report[start:]
        assert "sys.excepthook" in section
        assert "threading.excepthook" in section
        assert "sys.unraisablehook" in section
        assert "faulthandler" in section


class TestCrashStaleness:
    """A stale crash from an older build must not read as a live bug (#89)."""

    def test_flags_crash_from_older_version(self):
        from ytm_player.utils.doctor import _crash_staleness_note

        note = _crash_staleness_note("=== Crash ===\nversion: 1.0.0\ntrace", "1.9.4")
        assert note is not None
        assert "1.0.0" in note
        assert "may already be fixed" in note

    def test_no_note_for_current_version(self):
        from ytm_player.utils.doctor import _crash_staleness_note

        assert _crash_staleness_note("version: 1.9.4\ntrace", "1.9.4") is None

    def test_no_note_for_newer_version(self):
        from ytm_player.utils.doctor import _crash_staleness_note

        assert _crash_staleness_note("version: 2.0.0\ntrace", "1.9.4") is None

    def test_soft_note_when_version_unrecorded(self):
        from ytm_player.utils.doctor import _crash_staleness_note

        note = _crash_staleness_note("=== Crash ===\ntrace only, no version line", "1.9.4")
        assert note is not None
        assert "no version recorded" in note

    def test_no_note_for_unknown_sentinel(self):
        from ytm_player.utils.doctor import _crash_staleness_note

        assert _crash_staleness_note("=== Crash ===\nversion: unknown\ntrace", "1.9.4") is None


class TestMprisStatus:
    """MPRIS section must tell Linux users when dbus-fast is missing (#110)."""

    @pytest.mark.skipif(sys.platform != "linux", reason="Linux-only MPRIS path")
    def test_unavailable_reports_fix(self, monkeypatch):
        import ytm_player.services.mpris as mpris_mod
        from ytm_player.utils.doctor import _mpris_status

        monkeypatch.setattr(mpris_mod, "DBUS_AVAILABLE", False)
        status = _mpris_status()
        assert "UNAVAILABLE" in status
        assert "dbus-fast" in status
        assert "reinstall" in status.lower()

    @pytest.mark.skipif(sys.platform != "linux", reason="Linux-only MPRIS path")
    def test_available_reports_bus_name(self, monkeypatch):
        import ytm_player.services.mpris as mpris_mod
        from ytm_player.utils.doctor import _mpris_status

        monkeypatch.setattr(mpris_mod, "DBUS_AVAILABLE", True)
        status = _mpris_status()
        assert "available" in status
        assert mpris_mod.BUS_NAME in status

    @pytest.mark.skipif(sys.platform == "linux", reason="non-Linux native-integration path")
    def test_non_linux_is_not_applicable(self):
        from ytm_player.utils.doctor import _mpris_status

        assert "n/a" in _mpris_status()

    def test_no_note_for_invalid_version_string(self):
        from ytm_player.utils.doctor import _crash_staleness_note

        # An unparseable version must not crash diagnostics or emit a verdict.
        assert (
            _crash_staleness_note("=== Crash ===\nversion: not-a-version\ntrace", "1.9.4") is None
        )

    def test_ignores_version_line_inside_traceback_body(self):
        from ytm_player.utils.doctor import _crash_staleness_note

        # A version-shaped line buried in the traceback must not be parsed as
        # the crash version — only the metadata header counts (Codex nit #1).
        content = (
            "=== Crash ===\n"
            "Traceback (most recent call last):\n"
            "  File 'x.py', line 1, in <module>\n"
            "version: 0.0.1\n"
        )
        note = _crash_staleness_note(content, "1.9.4")
        assert note is not None
        assert "no version recorded" in note

    def test_ignores_exception_shaped_first_line_in_old_crash(self):
        from ytm_player.utils.doctor import _crash_staleness_note

        # Old unstamped crash whose first body line is itself "Word: value"
        # shaped (an exception line) must not be walked as metadata down to a
        # later version-shaped line (Codex 2nd-pass finding).
        content = "=== Crash ===\nValueError: bad config\nversion: 0.0.1\n"
        note = _crash_staleness_note(content, "1.9.4")
        assert note is not None
        assert "no version recorded" in note

    def test_diagnostics_report_flags_stale_crash(self, monkeypatch, tmp_path: Path):
        from ytm_player.config import paths
        from ytm_player.utils.doctor import gather_diagnostics

        crash_dir = tmp_path / "crashes"
        crash_dir.mkdir()
        (crash_dir / "ytm-crash-20200101-000000-000000.log").write_text(
            "=== Crash ===\nversion: 0.0.1\nsome traceback", encoding="utf-8"
        )
        monkeypatch.setattr(paths, "CRASH_DIR", crash_dir)

        report = gather_diagnostics()
        assert "may already be fixed" in report


class TestArgvIsYtm:
    """The running-instance matcher must see real entry points and skip
    bystanders (an editor with the repo open, say)."""

    def test_console_script_launch(self):
        # Console scripts run as `python /…/bin/ytm`: interpreter in argv[0],
        # script path in argv[1]. This is the shape every pip/AUR install has.
        from ytm_player.utils.doctor import _argv_is_ytm

        assert _argv_is_ytm(["/home/u/.venv/bin/python3", "/home/u/.venv/bin/ytm"])

    def test_bare_ytm(self):
        from ytm_player.utils.doctor import _argv_is_ytm

        assert _argv_is_ytm(["/usr/bin/ytm"])

    def test_python_dash_m(self):
        from ytm_player.utils.doctor import _argv_is_ytm

        assert _argv_is_ytm(["/usr/bin/python3", "-m", "ytm_player"])

    def test_editor_on_repo_is_not_ytm(self):
        from ytm_player.utils.doctor import _argv_is_ytm

        assert not _argv_is_ytm(["nvim", "/home/u/AI/ytm-player/src/ytm_player/cli.py"])

    def test_unrelated_process(self):
        from ytm_player.utils.doctor import _argv_is_ytm

        assert not _argv_is_ytm(["/usr/bin/firefox", "--new-window"])
