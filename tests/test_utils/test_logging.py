"""Tests for utils.logging — file-based logging setup and crash handlers."""

from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest


class TestSetupLogging:
    def test_creates_rotating_file_handler(self, tmp_path: Path):
        from ytm_player.utils.logging import setup_logging

        log_file = tmp_path / "ytm.log"
        setup_logging(level="INFO", log_file=log_file, max_bytes=1024, backup_count=2)

        root = logging.getLogger()
        rotating = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        assert len(rotating) == 1
        h = rotating[0]
        assert Path(h.baseFilename) == log_file
        assert h.maxBytes == 1024
        assert h.backupCount == 2

    def test_respects_level(self, tmp_path: Path):
        from ytm_player.utils.logging import setup_logging

        setup_logging(level="DEBUG", log_file=tmp_path / "ytm.log")
        assert logging.getLogger().getEffectiveLevel() == logging.DEBUG

    def test_idempotent(self, tmp_path: Path):
        """Calling setup_logging twice must not duplicate handlers."""
        from ytm_player.utils.logging import setup_logging

        log_file = tmp_path / "ytm.log"
        setup_logging(level="INFO", log_file=log_file)
        setup_logging(level="INFO", log_file=log_file)
        rotating = [h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)]
        assert len(rotating) == 1

    def test_writes_to_file(self, tmp_path: Path):
        from ytm_player.utils.logging import setup_logging

        log_file = tmp_path / "ytm.log"
        setup_logging(level="DEBUG", log_file=log_file)
        logging.getLogger("test").error("hello world")
        # Force flush.
        for h in logging.getLogger().handlers:
            h.flush()
        assert log_file.exists()
        assert "hello world" in log_file.read_text()

    @pytest.fixture(autouse=True)
    def _reset_logging(self):
        """Tear down handlers between tests to avoid leakage."""
        yield
        root = logging.getLogger()
        for h in list(root.handlers):
            root.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass


class TestInstallExcepthooks:
    def test_main_thread_excepthook_writes_crash_file(self, tmp_path: Path):
        from ytm_player.utils.logging import install_excepthooks

        crash_dir = tmp_path / "crashes"
        install_excepthooks(crash_dir=crash_dir, keep=5)

        # Simulate an uncaught exception by calling the installed hook directly.
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            exc_type, exc_value, exc_tb = sys.exc_info()
            assert exc_type is not None and exc_value is not None and exc_tb is not None
            sys.excepthook(exc_type, exc_value, exc_tb)

        files = sorted(crash_dir.glob("ytm-crash-*.log"))
        assert len(files) == 1
        text = files[0].read_text()
        assert "RuntimeError: boom" in text
        assert "Traceback" in text

    def test_thread_excepthook_writes_crash_file(self, tmp_path: Path):
        from ytm_player.utils.logging import install_excepthooks

        crash_dir = tmp_path / "crashes"
        install_excepthooks(crash_dir=crash_dir, keep=5)

        try:
            raise RuntimeError("thread boom")
        except RuntimeError:
            exc_type, exc_value, exc_tb = sys.exc_info()
            assert exc_type is not None and exc_value is not None and exc_tb is not None
            args = threading.ExceptHookArgs(
                (exc_type, exc_value, exc_tb, threading.current_thread())
            )
            threading.excepthook(args)

        files = sorted(crash_dir.glob("ytm-crash-*.log"))
        assert len(files) == 1
        assert "thread boom" in files[0].read_text()

    def test_keep_caps_old_crash_files(self, tmp_path: Path):
        from ytm_player.utils.logging import install_excepthooks

        crash_dir = tmp_path / "crashes"
        crash_dir.mkdir()
        # Pre-populate with 5 fake old crash files.
        for i in range(5):
            f = crash_dir / f"ytm-crash-2025010{i}-000000.log"
            f.write_text(f"old crash {i}")

        install_excepthooks(crash_dir=crash_dir, keep=3)

        # Trigger one new crash.
        try:
            raise ValueError("new")
        except ValueError:
            exc_type, exc_value, exc_tb = sys.exc_info()
            assert exc_type is not None and exc_value is not None and exc_tb is not None
            sys.excepthook(exc_type, exc_value, exc_tb)

        files = sorted(crash_dir.glob("ytm-crash-*.log"))
        assert len(files) == 3, f"expected 3, got {len(files)}: {files}"

    def test_keyboard_interrupt_does_not_create_crash_file(self, tmp_path: Path):
        from ytm_player.utils.logging import install_excepthooks

        crash_dir = tmp_path / "crashes"
        install_excepthooks(crash_dir=crash_dir, keep=5)

        try:
            raise KeyboardInterrupt()
        except KeyboardInterrupt:
            exc_type, exc_value, exc_tb = sys.exc_info()
            assert exc_type is not None and exc_value is not None and exc_tb is not None
            sys.excepthook(exc_type, exc_value, exc_tb)

        files = list(crash_dir.glob("ytm-crash-*.log"))
        assert files == []

    def test_thread_hook_chains_to_default(self, tmp_path: Path, capsys):
        from ytm_player.utils.logging import install_excepthooks

        crash_dir = tmp_path / "crashes"
        install_excepthooks(crash_dir=crash_dir, keep=5)

        try:
            raise RuntimeError("chain me")
        except RuntimeError:
            exc_type, exc_value, exc_tb = sys.exc_info()
            assert exc_type is not None and exc_value is not None and exc_tb is not None
            args = threading.ExceptHookArgs(
                (exc_type, exc_value, exc_tb, threading.current_thread())
            )
            threading.excepthook(args)

        # File written.
        files = list(crash_dir.glob("ytm-crash-*.log"))
        assert len(files) == 1
        # Default thread excepthook writes traceback to stderr.
        captured = capsys.readouterr()
        assert "chain me" in captured.err

    @pytest.fixture(autouse=True)
    def _reset_excepthooks(self):
        original_sys = sys.excepthook
        original_thread = threading.excepthook
        yield
        sys.excepthook = original_sys
        threading.excepthook = original_thread


class TestWriteCrashFileFallback:
    """write_crash_file must self-bootstrap when install_excepthooks was skipped.

    Regression for the ``crashes/ dir empty after a crash`` bug — silent
    None-return masked the real failure mode and made diagnostics useless.
    """

    @pytest.fixture(autouse=True)
    def _reset_module_state(self, monkeypatch):
        from ytm_player.utils import logging as logmod

        monkeypatch.setattr(logmod, "_crash_dir", None)
        yield

    def test_falls_back_to_paths_crash_dir_when_unconfigured(self, tmp_path: Path, monkeypatch):
        """If install_excepthooks was never called, write_crash_file should
        still produce a file using config.paths.CRASH_DIR rather than silently
        returning None.
        """
        from ytm_player.config import paths
        from ytm_player.utils.logging import write_crash_file

        crash_dir = tmp_path / "crashes"
        monkeypatch.setattr(paths, "CRASH_DIR", crash_dir)

        result = write_crash_file("traceback body", label="Test crash")

        assert result is not None
        assert result.exists()
        assert "Test crash" in result.read_text()
        assert "traceback body" in result.read_text()

    def test_crash_file_records_app_version_and_metadata(self, tmp_path: Path, monkeypatch):
        """Crash files self-identify so a stale log isn't mistaken for a live
        bug, and `ytm doctor` can compare the recorded version to the install.
        """
        import re

        from ytm_player import __version__
        from ytm_player.config import paths
        from ytm_player.utils.logging import write_crash_file

        crash_dir = tmp_path / "crashes"
        monkeypatch.setattr(paths, "CRASH_DIR", crash_dir)

        result = write_crash_file("the traceback", label="Test crash")

        assert result is not None
        text = result.read_text(encoding="utf-8")
        # Label stays the first line (doctor + existing tests rely on it).
        assert text.startswith("=== Test crash ===")
        assert re.search(rf"^version:\s*{re.escape(__version__)}$", text, re.MULTILINE)
        assert re.search(r"^time:\s*\S+", text, re.MULTILINE)
        assert re.search(r"^python:\s*\S+", text, re.MULTILINE)
        assert re.search(r"^platform:\s*\S+", text, re.MULTILINE)
        assert "the traceback" in text

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="NTFS ignores POSIX chmod(0o500) bits — can't simulate read-only dir on Windows.",
    )
    def test_logs_oserror_instead_of_silent_none(self, tmp_path: Path, monkeypatch, caplog):
        """When the write fails, we must log the reason — silent failure
        is what hid the original ``crashes dir empty`` symptom for hours.
        """
        from ytm_player.utils import logging as logmod

        # Point at a non-writable path so os.open raises OSError.
        crash_dir = tmp_path / "ro-crashes"
        crash_dir.mkdir()
        crash_dir.chmod(0o500)
        monkeypatch.setattr(logmod, "_crash_dir", crash_dir)

        try:
            with caplog.at_level("ERROR", logger="ytm_player.utils.logging"):
                result = logmod.write_crash_file("body", label="ReadOnly")

            assert result is None
            assert any("failed to write" in rec.getMessage().lower() for rec in caplog.records)
        finally:
            crash_dir.chmod(0o700)


class TestFaulthandlerEnable:
    """faulthandler must be enabled to a file under the crash dir.

    We can't actually trigger a SIGSEGV in tests (would kill pytest), but we
    can verify the file handle is opened and faulthandler is enabled, and
    that faulthandler.dump_traceback() writes to the configured file.
    """

    def test_dump_traceback_writes_to_configured_file(self, tmp_path: Path):
        """faulthandler.enable(file=fh) routes dump_traceback() output to fh."""
        import faulthandler

        fh_path = tmp_path / "faulthandler.log"
        fh = fh_path.open("ab", buffering=0)
        try:
            faulthandler.enable(file=fh, all_threads=True)
            try:
                faulthandler.dump_traceback(file=fh, all_threads=True)
            finally:
                # Always disable to avoid bleeding into other tests.
                faulthandler.disable()
        finally:
            fh.close()

        assert fh_path.exists()
        content = fh_path.read_text(encoding="utf-8", errors="replace")
        # dump_traceback emits the literal phrase "Current thread"
        assert "Current thread" in content


class TestAsyncioExceptionHandlerPattern:
    """Exercise the REAL asyncio exception handler's crash-file write path.

    Calls ``YTMPlayerApp._asyncio_exception_handler`` directly rather than a
    replica, so a regression in the real handler is caught here. The method
    never touches instance state, so it is called unbound with a dummy
    ``self``. The handler swallows all internal errors, so assertions are on
    the crash-file side effect, not on any return value.
    """

    @pytest.fixture(autouse=True)
    def _reset_module_state(self, monkeypatch):
        from ytm_player.utils import logging as logmod

        monkeypatch.setattr(logmod, "_crash_dir", None)
        yield

    def test_asyncio_handler_writes_crash_file_with_exception(self, tmp_path: Path, monkeypatch):
        from ytm_player.app._app import YTMPlayerApp
        from ytm_player.utils import logging as logmod

        crash_dir = tmp_path / "crashes"
        monkeypatch.setattr(logmod, "_crash_dir", crash_dir)
        crash_dir.mkdir(parents=True, exist_ok=True)

        try:
            raise RuntimeError("loop boom")
        except RuntimeError as exc:
            context = {"exception": exc}

        # Unbound call with a dummy self — the handler never uses self.
        YTMPlayerApp._asyncio_exception_handler(object(), None, context)

        files = list(crash_dir.glob("ytm-crash-*.log"))
        assert len(files) == 1
        body = files[0].read_text(encoding="utf-8")
        assert "Asyncio loop exception" in body
        assert "RuntimeError: loop boom" in body

    def test_asyncio_handler_writes_crash_file_with_message_only(self, tmp_path: Path, monkeypatch):
        """Handler must also work when context has no exception (rare)."""
        from ytm_player.app._app import YTMPlayerApp
        from ytm_player.utils import logging as logmod

        crash_dir = tmp_path / "crashes"
        monkeypatch.setattr(logmod, "_crash_dir", crash_dir)
        crash_dir.mkdir(parents=True, exist_ok=True)

        context = {"message": "Custom asyncio warning"}
        YTMPlayerApp._asyncio_exception_handler(object(), None, context)

        files = list(crash_dir.glob("ytm-crash-*.log"))
        assert len(files) == 1
        assert "Custom asyncio warning" in files[0].read_text(encoding="utf-8")


class TestUnraisableHook:
    """install_excepthooks must also install sys.unraisablehook."""

    @pytest.fixture(autouse=True)
    def _reset(self):
        original = sys.unraisablehook
        yield
        sys.unraisablehook = original

    def test_unraisable_hook_writes_crash_file(self, tmp_path: Path, monkeypatch):
        from ytm_player.utils.logging import install_excepthooks

        crash_dir = tmp_path / "crashes"
        install_excepthooks(crash_dir=crash_dir, keep=5)

        # Mock __unraisablehook__ to avoid TypeError from SimpleNamespace.
        monkeypatch.setattr(sys, "__unraisablehook__", lambda _args: None)

        try:
            raise RuntimeError("unraisable boom")
        except RuntimeError:
            exc_type, exc_value, exc_tb = sys.exc_info()
            assert exc_type is not None and exc_value is not None and exc_tb is not None
            # Build a real UnraisableHookArgs.
            import types

            hook_args = types.SimpleNamespace(
                exc_type=exc_type,
                exc_value=exc_value,
                exc_traceback=exc_tb,
                err_msg=None,
                object="<test object>",
            )
            sys.unraisablehook(hook_args)  # pyright: ignore[reportArgumentType]

        files = list(crash_dir.glob("ytm-crash-*.log"))
        assert len(files) == 1
        body = files[0].read_text()
        assert "Unraisable" in body
        assert "<test object>" in body
        assert "unraisable boom" in body

    def test_unraisable_hook_chains_to_default(self, tmp_path: Path, monkeypatch):
        """Our hook chains to sys.__unraisablehook__ after writing the crash file."""
        from ytm_player.utils.logging import install_excepthooks

        crash_dir = tmp_path / "crashes"
        install_excepthooks(crash_dir=crash_dir, keep=5)

        chain_calls: list = []

        def mock_default_hook(args):  # type: ignore[no-untyped-def]
            chain_calls.append(args)

        monkeypatch.setattr(sys, "__unraisablehook__", mock_default_hook)

        try:
            raise RuntimeError("chain unraisable")
        except RuntimeError:
            exc_type, exc_value, exc_tb = sys.exc_info()
            assert exc_type is not None and exc_value is not None and exc_tb is not None
            import types

            hook_args = types.SimpleNamespace(
                exc_type=exc_type,
                exc_value=exc_value,
                exc_traceback=exc_tb,
                err_msg=None,
                object=None,
            )
            sys.unraisablehook(hook_args)  # pyright: ignore[reportArgumentType]

        assert len(chain_calls) == 1
        assert chain_calls[0] is hook_args


class TestGetRecentLogLinesFilter:
    """get_recent_log_lines must support filtering by level >= threshold."""

    def test_no_filter_returns_all_recent(self, tmp_path: Path):
        from ytm_player.utils.logging import get_recent_log_lines

        log = tmp_path / "ytm.log"
        log.write_text(
            "2026-04-30 01:00:00 [DEBUG] [MainThread] foo: trace 1\n"
            "2026-04-30 01:00:01 [INFO] [MainThread] foo: info 1\n"
            "2026-04-30 01:00:02 [WARNING] [MainThread] foo: warn 1\n"
            "2026-04-30 01:00:03 [ERROR] [MainThread] foo: err 1\n"
        )
        out = get_recent_log_lines(log, n=10)
        assert "trace 1" in out
        assert "info 1" in out
        assert "warn 1" in out
        assert "err 1" in out

    def test_min_level_warning_filters_below(self, tmp_path: Path):
        from ytm_player.utils.logging import get_recent_log_lines

        log = tmp_path / "ytm.log"
        log.write_text(
            "2026-04-30 01:00:00 [DEBUG] [MainThread] foo: trace 1\n"
            "2026-04-30 01:00:01 [INFO] [MainThread] foo: info 1\n"
            "2026-04-30 01:00:02 [WARNING] [MainThread] foo: warn 1\n"
            "2026-04-30 01:00:03 [ERROR] [MainThread] foo: err 1\n"
            "2026-04-30 01:00:04 [CRITICAL] [MainThread] foo: critical 1\n"
        )
        out = get_recent_log_lines(log, n=10, min_level="WARNING")
        assert "trace 1" not in out
        assert "info 1" not in out
        assert "warn 1" in out
        assert "err 1" in out
        assert "critical 1" in out

    def test_min_level_error_filters_warning(self, tmp_path: Path):
        from ytm_player.utils.logging import get_recent_log_lines

        log = tmp_path / "ytm.log"
        log.write_text(
            "2026-04-30 01:00:02 [WARNING] [MainThread] foo: warn 1\n"
            "2026-04-30 01:00:03 [ERROR] [MainThread] foo: err 1\n"
        )
        out = get_recent_log_lines(log, n=10, min_level="ERROR")
        assert "warn 1" not in out
        assert "err 1" in out

    def test_unknown_level_returns_all(self, tmp_path: Path):
        from ytm_player.utils.logging import get_recent_log_lines

        log = tmp_path / "ytm.log"
        log.write_text("2026-04-30 [DEBUG] foo\n")
        out = get_recent_log_lines(log, n=10, min_level="BOGUS")
        assert "DEBUG" in out

    def test_min_level_lowercase_accepted(self, tmp_path: Path):
        """min_level should be case-insensitive."""
        from ytm_player.utils.logging import get_recent_log_lines

        log = tmp_path / "ytm.log"
        log.write_text("2026-04-30 [INFO] foo: info 1\n2026-04-30 [WARNING] foo: warn 1\n")
        out = get_recent_log_lines(log, n=10, min_level="warning")
        assert "info 1" not in out
        assert "warn 1" in out


class TestListActiveHooks:
    def test_reports_all_four_hook_categories(self):
        from ytm_player.utils.logging import list_active_hooks

        out = list_active_hooks()
        # The output must always mention all four hook categories.
        assert "sys.excepthook" in out
        assert "threading.excepthook" in out
        assert "sys.unraisablehook" in out
        assert "faulthandler" in out

    def test_reports_default_when_unhooked(self, monkeypatch):
        """When hooks haven't been installed, report 'default' explicitly."""
        from ytm_player.utils.logging import list_active_hooks

        # Reset all hooks to the defaults
        monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)
        monkeypatch.setattr(threading, "excepthook", threading.__excepthook__)
        monkeypatch.setattr(sys, "unraisablehook", sys.__unraisablehook__)

        out = list_active_hooks()
        assert "default" in out.lower()
