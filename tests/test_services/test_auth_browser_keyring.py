"""Tests for the Linux keyring fallback in browser cookie extraction.

yt-dlp infers the Chromium cookie keyring from the desktop environment. On
desktops it reports as unknown (Hyprland/omarchy, Sway/i3, ...) it picks
BASICTEXT and silently decrypts nothing, so ytm-player retries the concrete
keyring backends. These tests pin that behaviour without touching a real
browser profile.
"""

import sys
import time
from http.cookiejar import Cookie
from unittest.mock import MagicMock

from ytm_player.services.auth import _extract_browser_jar, _has_youtube_auth_cookies


def _cookie(domain=".youtube.com", name="__Secure-3PAPISID", value="token", expires=None):
    return Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=True,
        path="/",
        path_specified=True,
        secure=True,
        expires=expires,
        discard=False,
        comment=None,
        comment_url=None,
        rest={},
    )


AUTH_COOKIES = [_cookie(name="SAPISID"), _cookie(name="__Secure-3PAPISID")]


def test_signed_in_needs_a_live_secure_3papisid():
    assert _has_youtube_auth_cookies([_cookie(name="__Secure-3PAPISID")])
    assert not _has_youtube_auth_cookies([_cookie(name="SAPISID")])
    assert not _has_youtube_auth_cookies([_cookie(value="")])
    assert not _has_youtube_auth_cookies([_cookie(expires=int(time.time()) - 60)])


def test_ignores_cookies_from_other_domains_or_other_names():
    assert not _has_youtube_auth_cookies([_cookie(domain=".google.com")])
    assert not _has_youtube_auth_cookies([_cookie(name="PREF")])
    assert not _has_youtube_auth_cookies([])


def test_returns_jar_when_default_extraction_has_auth_cookies(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    extractor = MagicMock(return_value=AUTH_COOKIES)
    monkeypatch.setattr("yt_dlp.cookies.extract_cookies_from_browser", extractor)

    jar = _extract_browser_jar("brave")

    assert jar is AUTH_COOKIES
    extractor.assert_called_once_with("brave")


def test_retries_with_keyring_when_default_has_no_cookies(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    calls = []

    def extractor(browser, *, keyring=None):
        calls.append((browser, keyring))
        return AUTH_COOKIES if keyring == "GNOMEKEYRING" else []

    monkeypatch.setattr("yt_dlp.cookies.extract_cookies_from_browser", extractor)

    jar = _extract_browser_jar("brave")

    assert jar is AUTH_COOKIES
    assert calls == [("brave", None), ("brave", "GNOMEKEYRING")]


def test_failed_keyring_backend_falls_through_to_the_next(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    attempted = []

    def extractor(browser, *, keyring=None):
        attempted.append(keyring)
        if keyring == "GNOMEKEYRING":
            raise RuntimeError("secret service unavailable")
        return AUTH_COOKIES if keyring == "KWALLET6" else []

    monkeypatch.setattr("yt_dlp.cookies.extract_cookies_from_browser", extractor)

    jar = _extract_browser_jar("brave")

    assert jar is AUTH_COOKIES
    assert attempted == [None, "GNOMEKEYRING", "KWALLET6"]


def test_returns_default_jar_when_no_keyring_works(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    default = []

    def extractor(browser, *, keyring=None):
        return default if keyring is None else []

    monkeypatch.setattr("yt_dlp.cookies.extract_cookies_from_browser", extractor)

    jar = _extract_browser_jar("brave")

    assert jar is default


def test_no_keyring_retry_outside_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    extractor = MagicMock(return_value=[])
    monkeypatch.setattr("yt_dlp.cookies.extract_cookies_from_browser", extractor)

    jar = _extract_browser_jar("brave")

    assert jar == []
    extractor.assert_called_once_with("brave")


def test_no_keyring_retry_for_firefox(monkeypatch):
    # Firefox does not encrypt cookies with a keyring, so a retry cannot help.
    monkeypatch.setattr(sys, "platform", "linux")
    extractor = MagicMock(return_value=[])
    monkeypatch.setattr("yt_dlp.cookies.extract_cookies_from_browser", extractor)

    jar = _extract_browser_jar("firefox")

    assert jar == []
    extractor.assert_called_once_with("firefox")
