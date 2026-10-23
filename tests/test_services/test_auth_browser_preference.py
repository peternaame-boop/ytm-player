"""Tests for pinning the browser that cookies are read from."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from ytm_player.services.auth import AuthManager, _configured_browser


def _settings(cookie_browser):
    return SimpleNamespace(yt_dlp=SimpleNamespace(cookie_browser=cookie_browser))


def _auth_jar():
    cookie = MagicMock()
    cookie.domain = ".youtube.com"
    cookie.name = "SAPISID"
    cookie.value = "token"
    return [cookie]


def test_configured_browser_empty_means_auto_detect(monkeypatch):
    monkeypatch.setattr("ytm_player.config.settings.get_settings", lambda: _settings(""))
    assert _configured_browser() is None


def test_configured_browser_is_normalised(monkeypatch):
    monkeypatch.setattr("ytm_player.config.settings.get_settings", lambda: _settings(" Brave "))
    assert _configured_browser() == "brave"


def test_detect_browser_only_tries_the_pinned_browser(monkeypatch):
    monkeypatch.setattr("ytm_player.services.auth._patch_yt_dlp_browsers", lambda: None)
    extractor = MagicMock(return_value=_auth_jar())
    monkeypatch.setattr("ytm_player.services.auth._extract_browser_jar", extractor)
    monkeypatch.setattr("ytm_player.services.auth._configured_browser", lambda: "brave")

    detected = AuthManager._detect_browser()

    assert detected is not None
    assert detected[0] == "brave"
    extractor.assert_called_once_with("brave")


def test_detect_browser_does_not_fall_back_when_pinned_browser_fails(monkeypatch):
    monkeypatch.setattr("ytm_player.services.auth._patch_yt_dlp_browsers", lambda: None)
    extractor = MagicMock(side_effect=RuntimeError("no cookies"))
    monkeypatch.setattr("ytm_player.services.auth._extract_browser_jar", extractor)
    monkeypatch.setattr("ytm_player.services.auth._configured_browser", lambda: "brave")

    assert AuthManager._detect_browser() is None
    extractor.assert_called_once_with("brave")


def test_setup_interactive_uses_the_pinned_browser(monkeypatch):
    monkeypatch.setattr("ytm_player.services.auth._configured_browser", lambda: "brave")
    extract = MagicMock(return_value=True)
    monkeypatch.setattr(AuthManager, "_extract_and_save", extract)
    manual = MagicMock()
    monkeypatch.setattr(AuthManager, "_setup_manual", manual)

    result = AuthManager().setup_interactive()

    assert result is True
    extract.assert_called_once_with("brave", interactive=True)
    manual.assert_not_called()


def test_explicit_browser_argument_overrides_the_pin(monkeypatch):
    monkeypatch.setattr("ytm_player.services.auth._configured_browser", lambda: "brave")
    extract = MagicMock(return_value=True)
    monkeypatch.setattr(AuthManager, "_extract_and_save", extract)

    AuthManager().setup_interactive(browser="firefox")

    extract.assert_called_once_with("firefox", interactive=True)
