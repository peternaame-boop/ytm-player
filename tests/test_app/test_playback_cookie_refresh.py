"""Tests for refreshing stale browser cookies after a stream failure."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from ytm_player.app._playback import _COOKIE_REFRESH_COOLDOWN_SECONDS, PlaybackMixin


def _host():
    host = PlaybackMixin()
    host.settings = SimpleNamespace(
        yt_dlp=SimpleNamespace(use_session_cookies=True, cookies_file="")
    )
    host.auth = MagicMock()
    host.auth.try_auto_refresh = MagicMock(return_value=True)
    host._last_cookie_refresh = 0.0
    return host


def _clock(monkeypatch, start=1000.0):
    now = {"value": start}
    monkeypatch.setattr("ytm_player.app._playback.time.monotonic", lambda: now["value"])
    return now


async def test_refreshes_from_the_browser(monkeypatch):
    host = _host()
    _clock(monkeypatch)

    assert await host._refresh_session_from_browser() is True
    host.auth.try_auto_refresh.assert_called_once_with()


async def test_cooldown_blocks_a_second_attempt(monkeypatch):
    host = _host()
    now = _clock(monkeypatch)

    assert await host._refresh_session_from_browser() is True
    now["value"] += 1.0
    assert await host._refresh_session_from_browser() is False
    assert host.auth.try_auto_refresh.call_count == 1


async def test_attempt_allowed_again_after_the_cooldown(monkeypatch):
    host = _host()
    now = _clock(monkeypatch)

    assert await host._refresh_session_from_browser() is True
    now["value"] += _COOKIE_REFRESH_COOLDOWN_SECONDS
    assert await host._refresh_session_from_browser() is True
    assert host.auth.try_auto_refresh.call_count == 2


async def test_no_auth_manager_does_nothing(monkeypatch):
    host = _host()
    host.auth = None
    _clock(monkeypatch)

    assert await host._refresh_session_from_browser() is False


async def test_skipped_when_streaming_anonymously(monkeypatch):
    host = _host()
    host.settings.yt_dlp.use_session_cookies = False
    _clock(monkeypatch)

    assert await host._refresh_session_from_browser() is False
    host.auth.try_auto_refresh.assert_not_called()


async def test_skipped_with_an_explicit_cookies_file(monkeypatch):
    host = _host()
    host.settings.yt_dlp.cookies_file = "/tmp/my-cookies.txt"
    _clock(monkeypatch)

    assert await host._refresh_session_from_browser() is False
    host.auth.try_auto_refresh.assert_not_called()


async def test_refresh_failure_is_swallowed_and_reported_false(monkeypatch):
    host = _host()
    host.auth.try_auto_refresh = MagicMock(side_effect=RuntimeError("keyring locked"))
    _clock(monkeypatch)

    assert await host._refresh_session_from_browser() is False
