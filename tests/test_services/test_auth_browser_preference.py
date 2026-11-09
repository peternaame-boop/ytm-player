"""Tests for pinning the browser that cookies are read from."""

import logging
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from ytm_player.cli import main
from ytm_player.config.settings import Settings
from ytm_player.services.auth import _BROWSERS, DEFAULT_ACCOUNT_PROBE_SLOTS, AuthManager


def _auth_jar():
    cookie = MagicMock()
    cookie.domain = ".youtube.com"
    cookie.name = "SAPISID"
    cookie.value = "token"
    return [cookie]


def _no_global_settings(monkeypatch):
    def _boom():
        raise AssertionError("auth must not read the global settings")

    monkeypatch.setattr("ytm_player.config.settings.get_settings", _boom)


def test_cookie_browser_empty_means_auto_detect():
    assert AuthManager(cookie_browser="")._cookie_browser is None
    assert AuthManager(cookie_browser="   ")._cookie_browser is None
    assert AuthManager()._cookie_browser is None


def test_cookie_browser_is_normalised():
    assert AuthManager(cookie_browser=" Brave ")._cookie_browser == "brave"


def test_unknown_cookie_browser_warns_and_auto_detects(caplog):
    with caplog.at_level(logging.WARNING, logger="ytm_player.services.auth"):
        auth = AuthManager(cookie_browser="braev")

    assert auth._cookie_browser is None
    assert "'braev'" in caplog.text
    for browser in _BROWSERS:
        assert browser in caplog.text


def test_blank_cookie_browser_does_not_warn(caplog):
    with caplog.at_level(logging.WARNING, logger="ytm_player.services.auth"):
        AuthManager(cookie_browser="  ")

    assert caplog.text == ""


def test_detect_browser_only_tries_the_pinned_browser(monkeypatch):
    _no_global_settings(monkeypatch)
    monkeypatch.setattr("ytm_player.services.auth._patch_yt_dlp_browsers", lambda: None)
    extractor = MagicMock(return_value=_auth_jar())
    monkeypatch.setattr("ytm_player.services.auth._extract_browser_jar", extractor)

    detected = AuthManager(cookie_browser="brave")._detect_browser()

    assert detected is not None
    assert detected[0] == "brave"
    extractor.assert_called_once_with("brave")


def test_detect_browser_does_not_fall_back_when_pinned_browser_fails(monkeypatch):
    monkeypatch.setattr("ytm_player.services.auth._patch_yt_dlp_browsers", lambda: None)
    extractor = MagicMock(side_effect=RuntimeError("no cookies"))
    monkeypatch.setattr("ytm_player.services.auth._extract_browser_jar", extractor)

    assert AuthManager(cookie_browser="brave")._detect_browser() is None
    extractor.assert_called_once_with("brave")


def test_unknown_cookie_browser_detects_across_all_browsers(monkeypatch):
    monkeypatch.setattr("ytm_player.services.auth._patch_yt_dlp_browsers", lambda: None)
    extractor = MagicMock(side_effect=RuntimeError("no cookies"))
    monkeypatch.setattr("ytm_player.services.auth._extract_browser_jar", extractor)

    assert AuthManager(cookie_browser="braev")._detect_browser() is None
    assert [c.args[0] for c in extractor.call_args_list] == list(_BROWSERS)


def test_setup_interactive_uses_the_pinned_browser(monkeypatch):
    _no_global_settings(monkeypatch)
    extract = MagicMock(return_value=True)
    monkeypatch.setattr(AuthManager, "_extract_and_save", extract)
    manual = MagicMock()
    monkeypatch.setattr(AuthManager, "_setup_manual", manual)

    result = AuthManager(cookie_browser="brave").setup_interactive()

    assert result is True
    extract.assert_called_once_with("brave", interactive=True, slots=DEFAULT_ACCOUNT_PROBE_SLOTS)
    manual.assert_not_called()


def test_explicit_browser_argument_overrides_the_pin(monkeypatch):
    extract = MagicMock(return_value=True)
    monkeypatch.setattr(AuthManager, "_extract_and_save", extract)

    AuthManager(cookie_browser="brave").setup_interactive(browser="firefox")

    extract.assert_called_once_with("firefox", interactive=True, slots=DEFAULT_ACCOUNT_PROBE_SLOTS)


@pytest.mark.parametrize("file_ok", [True, False])
def test_setup_tries_the_cookies_file_before_the_pinned_browser(monkeypatch, tmp_path, file_ok):
    order: list[str] = []

    def from_file(self, path, interactive=False, slots=DEFAULT_ACCOUNT_PROBE_SLOTS):
        order.append("file")
        return file_ok

    def from_browser(self, browser, interactive=False, slots=DEFAULT_ACCOUNT_PROBE_SLOTS):
        order.append(browser)
        return True

    monkeypatch.setattr(AuthManager, "_refresh_from_cookies_file", from_file)
    monkeypatch.setattr(AuthManager, "_extract_and_save", from_browser)
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    auth = AuthManager(cookies_file=str(cookies), cookie_browser="brave")

    assert auth.setup_interactive() is True
    assert order == (["file"] if file_ok else ["file", "brave"])


def test_explicit_browser_skips_the_cookies_file(monkeypatch, tmp_path):
    from_file = MagicMock(return_value=True)
    monkeypatch.setattr(AuthManager, "_refresh_from_cookies_file", from_file)
    extract = MagicMock(return_value=True)
    monkeypatch.setattr(AuthManager, "_extract_and_save", extract)
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    auth = AuthManager(cookies_file=str(cookies), cookie_browser="brave")

    assert auth.setup_interactive(browser="firefox") is True
    from_file.assert_not_called()
    extract.assert_called_once_with("firefox", interactive=True, slots=DEFAULT_ACCOUNT_PROBE_SLOTS)


def test_manual_setup_skips_file_and_browser(monkeypatch):
    extract = MagicMock()
    monkeypatch.setattr(AuthManager, "_extract_and_save", extract)
    from_file = MagicMock()
    monkeypatch.setattr(AuthManager, "_refresh_from_cookies_file", from_file)
    monkeypatch.setattr(AuthManager, "_setup_manual", MagicMock(return_value=True))

    assert AuthManager(cookie_browser="brave").setup_interactive(manual=True) is True
    extract.assert_not_called()
    from_file.assert_not_called()


def test_cli_setup_passes_the_configured_browser(monkeypatch):
    settings = Settings()
    settings.yt_dlp.cookie_browser = "brave"
    seen: dict = {}

    def fake_manager(**kwargs):
        seen.update(kwargs)
        manager = MagicMock()
        manager.is_authenticated.return_value = False
        manager.setup_interactive.return_value = True
        return manager

    monkeypatch.setattr("ytm_player.cli.AuthManager", fake_manager)
    monkeypatch.setattr("ytm_player.cli.get_settings", lambda: settings)
    monkeypatch.setattr("ytm_player.cli.get_running_pid", lambda: None)

    result = CliRunner().invoke(main, ["setup"])

    assert result.exit_code == 0, result.output
    assert seen["cookie_browser"] == "brave"
