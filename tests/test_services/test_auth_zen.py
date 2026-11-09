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


def test_extract_non_zen_browser_unchanged(monkeypatch):
    extractor = MagicMock()
    monkeypatch.setattr("yt_dlp.cookies.extract_cookies_from_browser", extractor)

    _extract_browser_jar("brave")

    extractor.assert_called_once_with("brave")


def test_zen_profile_roots_linux_order(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    roots = _zen_profile_roots()

    assert roots[0] == Path.home() / ".zen"
    assert roots[1] == tmp_path / "zen"
