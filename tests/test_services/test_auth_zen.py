"""Tests for Zen Browser cookie extraction."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ytm_player.services.auth import _BROWSERS, _extract_browser_jar, _zen_profile_roots


def test_zen_is_supported_browser():
    assert "zen" in _BROWSERS


def test_extract_zen_uses_first_existing_profile_root(tmp_path, monkeypatch):
    missing_root = tmp_path / "missing"
    zen_root = tmp_path / "zen"
    zen_root.mkdir()
    monkeypatch.setattr(
        "ytm_player.services.auth._zen_profile_roots",
        lambda: [missing_root, zen_root],
    )
    extractor = MagicMock()
    monkeypatch.setattr("yt_dlp.cookies.extract_cookies_from_browser", extractor)

    _extract_browser_jar("zen")

    extractor.assert_called_once_with("firefox", profile=str(zen_root))


def test_extract_zen_raises_when_no_profile_root_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "ytm_player.services.auth._zen_profile_roots",
        lambda: [tmp_path / "missing"],
    )
    extractor = MagicMock()
    monkeypatch.setattr("yt_dlp.cookies.extract_cookies_from_browser", extractor)

    with pytest.raises(FileNotFoundError, match="no Zen profile directory found"):
        _extract_browser_jar("zen")

    extractor.assert_not_called()


def test_extract_zen_skips_unusable_root_and_uses_next(tmp_path, monkeypatch):
    """A stale/empty dir (e.g. leftover ~/.zen) must not shadow the real profile."""
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    zen_root = tmp_path / "zen"
    zen_root.mkdir()
    monkeypatch.setattr(
        "ytm_player.services.auth._zen_profile_roots",
        lambda: [empty_root, zen_root],
    )

    def _fake_extract(browser, profile=None, **kwargs):
        assert browser == "firefox"
        if profile == str(empty_root):
            raise FileNotFoundError(f"could not find firefox cookies database in {profile!r}")
        sentinel = MagicMock()
        sentinel.__iter__.return_value = iter([])
        return sentinel

    monkeypatch.setattr("yt_dlp.cookies.extract_cookies_from_browser", _fake_extract)

    _extract_browser_jar("zen")  # must not raise


def test_extract_zen_prefers_signed_in_root_and_never_merges(tmp_path, monkeypatch):
    """A readable but signed-out root (e.g. leftover ~/.zen) must not win."""

    from http.cookiejar import Cookie

    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    monkeypatch.setattr(
        "ytm_player.services.auth._zen_profile_roots",
        lambda: [root_a, root_b],
    )

    def _cookie(name, value="v"):
        return Cookie(
            version=0,
            name=name,
            value=value,
            port=None,
            port_specified=False,
            domain=".youtube.com",
            domain_specified=True,
            domain_initial_dot=True,
            path="/",
            path_specified=True,
            secure=True,
            expires=None,
            discard=False,
            comment=None,
            comment_url=None,
            rest={},
        )

    signed_out = [_cookie("PREF"), _cookie("__Secure-3PAPISID", value="")]
    signed_in = [_cookie("__Secure-3PAPISID", value="pap"), _cookie("SID")]
    jars = {str(root_a): signed_out, str(root_b): signed_in}

    def _fake_extract(browser, profile=None, **kwargs):
        return list(jars[profile])

    monkeypatch.setattr("yt_dlp.cookies.extract_cookies_from_browser", _fake_extract)

    chosen = _extract_browser_jar("zen")

    assert [(c.name, c.value) for c in chosen] == [("__Secure-3PAPISID", "pap"), ("SID", "v")]

    # With no signed-in root, the first readable one is returned unchanged.
    jars[str(root_b)] = [_cookie("NID")]
    assert [c.name for c in _extract_browser_jar("zen")] == ["PREF", "__Secure-3PAPISID"]


def test_extract_zen_reraises_when_all_roots_unusable(tmp_path, monkeypatch):
    bad_root = tmp_path / "bad"
    bad_root.mkdir()
    monkeypatch.setattr(
        "ytm_player.services.auth._zen_profile_roots",
        lambda: [bad_root],
    )

    def _boom(browser, profile=None, **kwargs):
        raise FileNotFoundError("could not find firefox cookies database")

    monkeypatch.setattr("yt_dlp.cookies.extract_cookies_from_browser", _boom)

    with pytest.raises(FileNotFoundError, match="could not find firefox cookies database"):
        _extract_browser_jar("zen")


def test_extract_non_zen_browser_unchanged(monkeypatch):
    auth_cookie = MagicMock()
    auth_cookie.domain = ".youtube.com"
    auth_cookie.name = "SAPISID"
    auth_cookie.value = "token"
    extractor = MagicMock(return_value=[auth_cookie])
    monkeypatch.setattr("yt_dlp.cookies.extract_cookies_from_browser", extractor)

    _extract_browser_jar("brave")

    extractor.assert_called_once_with("brave")


def test_zen_profile_roots_linux_order(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    roots = _zen_profile_roots()

    assert roots[0] == Path.home() / ".zen"
    assert roots[1] == tmp_path / "zen"
