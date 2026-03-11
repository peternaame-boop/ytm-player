"""Tests for _save_youtube_cookies multi-account selection."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ytm_player.services.auth import AuthManager

_PATCH_SAPISID = patch("ytm_player.services.auth.sapisid_from_cookie", return_value="fake_sapisid")
_PATCH_AUTH = patch("ytm_player.services.auth.get_authorization", return_value="SAPISIDHASH fake")


def _make_cookie(name: str, value: str = "val", domain: str = ".youtube.com"):
    c = SimpleNamespace(name=name, value=value, domain=domain)
    return c


def _make_auth(tmp_path):
    return AuthManager(config_dir=tmp_path, auth_file=tmp_path / "headers_auth.json")


def _account(name):
    return {"accountName": name}


# ── Single account ────────────────────────────────────────────────────────────


def test_single_account_auto_selected(tmp_path):
    """With one valid account, it is selected automatically without prompting."""
    auth = _make_auth(tmp_path)
    cookies = [_make_cookie("SAPISID", "secret")]

    mock_ytm = MagicMock()
    mock_ytm.get_account_info.return_value = _account("Alice")

    with (
        _PATCH_SAPISID,
        _PATCH_AUTH,
        patch("ytm_player.services.auth.YTMusic", return_value=mock_ytm),
    ):
        result = auth._save_youtube_cookies(cookies)

    assert result is True
    saved = json.loads((tmp_path / "headers_auth.json").read_text())
    assert saved["x-goog-authuser"] == "0"


def test_single_account_at_index_1_is_found(tmp_path):
    """When only index 1 has a valid account, it is found and written."""
    auth = _make_auth(tmp_path)
    cookies = [_make_cookie("SAPISID", "secret")]

    def _ytmusic_factory(path):
        saved = json.loads(open(path).read())
        idx = int(saved["x-goog-authuser"])
        m = MagicMock()
        m.get_account_info.return_value = _account("Bob") if idx == 1 else {}
        return m

    with (
        _PATCH_SAPISID,
        _PATCH_AUTH,
        patch("ytm_player.services.auth.YTMusic", side_effect=_ytmusic_factory),
    ):
        result = auth._save_youtube_cookies(cookies)

    assert result is True
    saved = json.loads((tmp_path / "headers_auth.json").read_text())
    assert saved["x-goog-authuser"] == "1"


# ── No valid accounts ─────────────────────────────────────────────────────────


def test_no_valid_accounts_returns_false(tmp_path):
    """If no account index returns an accountName, return False."""
    auth = _make_auth(tmp_path)
    cookies = [_make_cookie("SAPISID", "secret")]

    mock_ytm = MagicMock()
    mock_ytm.get_account_info.return_value = {}  # no accountName

    with (
        _PATCH_SAPISID,
        _PATCH_AUTH,
        patch("ytm_player.services.auth.YTMusic", return_value=mock_ytm),
    ):
        result = auth._save_youtube_cookies(cookies)

    assert result is False


def test_account_info_exception_returns_false(tmp_path):
    """If get_account_info raises for every index, return False."""
    auth = _make_auth(tmp_path)
    cookies = [_make_cookie("SAPISID", "secret")]

    mock_ytm = MagicMock()
    mock_ytm.get_account_info.side_effect = Exception("network error")

    with (
        _PATCH_SAPISID,
        _PATCH_AUTH,
        patch("ytm_player.services.auth.YTMusic", return_value=mock_ytm),
    ):
        result = auth._save_youtube_cookies(cookies)

    assert result is False


# ── Multiple accounts ─────────────────────────────────────────────────────────


def test_multiple_accounts_prompts_user_and_picks_choice(tmp_path, monkeypatch):
    """With two valid accounts, user is prompted and their choice is saved."""
    auth = _make_auth(tmp_path)
    cookies = [_make_cookie("SAPISID", "s0"), _make_cookie("SAPISID1", "s1")]

    accounts = {0: _account("Alice"), 1: _account("Bob")}

    def _ytmusic_factory(path):
        saved = json.loads(open(path).read())
        idx = int(saved["x-goog-authuser"])
        m = MagicMock()
        m.get_account_info.return_value = accounts.get(idx, {})
        return m

    # User selects account 2 (Bob, index 1)
    monkeypatch.setattr("builtins.input", lambda _: "2")

    with (
        _PATCH_SAPISID,
        _PATCH_AUTH,
        patch("ytm_player.services.auth.YTMusic", side_effect=_ytmusic_factory),
    ):
        result = auth._save_youtube_cookies(cookies, interactive=True)

    assert result is True
    saved = json.loads((tmp_path / "headers_auth.json").read_text())
    assert saved["x-goog-authuser"] == "1"


def test_multiple_accounts_user_picks_first(tmp_path, monkeypatch):
    """User picks account 1 (Alice, index 0)."""
    auth = _make_auth(tmp_path)
    cookies = [_make_cookie("SAPISID", "s0"), _make_cookie("SAPISID1", "s1")]

    accounts = {0: _account("Alice"), 1: _account("Bob")}

    def _ytmusic_factory(path):
        saved = json.loads(open(path).read())
        idx = int(saved["x-goog-authuser"])
        m = MagicMock()
        m.get_account_info.return_value = accounts.get(idx, {})
        return m

    monkeypatch.setattr("builtins.input", lambda _: "1")

    with (
        _PATCH_SAPISID,
        _PATCH_AUTH,
        patch("ytm_player.services.auth.YTMusic", side_effect=_ytmusic_factory),
    ):
        result = auth._save_youtube_cookies(cookies, interactive=True)

    assert result is True
    saved = json.loads((tmp_path / "headers_auth.json").read_text())
    assert saved["x-goog-authuser"] == "0"


def test_multiple_accounts_retries_on_invalid_input(tmp_path, monkeypatch):
    """Invalid input is rejected; valid input on retry is accepted."""
    auth = _make_auth(tmp_path)
    cookies = [_make_cookie("SAPISID", "s0"), _make_cookie("SAPISID1", "s1")]

    accounts = {0: _account("Alice"), 1: _account("Bob")}

    def _ytmusic_factory(path):
        saved = json.loads(open(path).read())
        idx = int(saved["x-goog-authuser"])
        m = MagicMock()
        m.get_account_info.return_value = accounts.get(idx, {})
        return m

    responses = iter(["0", "99", "bad", "2"])
    monkeypatch.setattr("builtins.input", lambda _: next(responses))

    with (
        _PATCH_SAPISID,
        _PATCH_AUTH,
        patch("ytm_player.services.auth.YTMusic", side_effect=_ytmusic_factory),
    ):
        result = auth._save_youtube_cookies(cookies, interactive=True)

    assert result is True
    saved = json.loads((tmp_path / "headers_auth.json").read_text())
    assert saved["x-goog-authuser"] == "1"


# ── Index probing ─────────────────────────────────────────────────────────────


def test_always_probes_all_five_indices(tmp_path):
    """All five indices (0-4) are always probed regardless of cookie names."""
    auth = _make_auth(tmp_path)
    cookies = [_make_cookie("SAPISID", "secret")]

    probed = []

    def _ytmusic_factory(path):
        saved = json.loads(open(path).read())
        probed.append(int(saved["x-goog-authuser"]))
        m = MagicMock()
        # Only index 2 is a valid account — the rest return empty.
        m.get_account_info.return_value = _account("Charlie") if probed[-1] == 2 else {}
        return m

    with (
        _PATCH_SAPISID,
        _PATCH_AUTH,
        patch("ytm_player.services.auth.YTMusic", side_effect=_ytmusic_factory),
    ):
        result = auth._save_youtube_cookies(cookies)

    assert probed == [0, 1, 2, 3, 4]
    assert result is True
    saved = json.loads((tmp_path / "headers_auth.json").read_text())
    assert saved["x-goog-authuser"] == "2"


# ── Non-interactive multi-account (auto-refresh) ──────────────────────────────


def test_auto_refresh_preserves_existing_preferred_index(tmp_path):
    """Auto-refresh with multiple accounts honors the previously saved x-goog-authuser."""
    auth = _make_auth(tmp_path)
    cookies = [_make_cookie("SAPISID", "s0"), _make_cookie("SAPISID1", "s1")]

    # Simulate a prior auth file recording that the user chose index 1 (Bob).
    prior_headers = {"x-goog-authuser": "1", "cookie": "prior-cookie"}
    (tmp_path / "headers_auth.json").write_text(json.dumps(prior_headers), encoding="utf-8")

    accounts = {0: _account("Alice"), 1: _account("Bob")}

    def _ytmusic_factory(path):
        saved = json.loads(open(path).read())
        idx = int(saved["x-goog-authuser"])
        m = MagicMock()
        m.get_account_info.return_value = accounts.get(idx, {})
        return m

    with (
        _PATCH_SAPISID,
        _PATCH_AUTH,
        patch("ytm_player.services.auth.YTMusic", side_effect=_ytmusic_factory),
    ):
        result = auth._save_youtube_cookies(cookies)  # non-interactive (default)

    assert result is True
    saved = json.loads((tmp_path / "headers_auth.json").read_text())
    assert saved["x-goog-authuser"] == "1"  # Bob preserved


def test_auto_refresh_falls_back_to_first_valid_when_no_prior_auth(tmp_path):
    """Auto-refresh with no prior auth file falls back to the first valid account."""
    auth = _make_auth(tmp_path)
    cookies = [_make_cookie("SAPISID", "s0"), _make_cookie("SAPISID1", "s1")]

    accounts = {0: _account("Alice"), 1: _account("Bob")}

    def _ytmusic_factory(path):
        saved = json.loads(open(path).read())
        idx = int(saved["x-goog-authuser"])
        m = MagicMock()
        m.get_account_info.return_value = accounts.get(idx, {})
        return m

    with (
        _PATCH_SAPISID,
        _PATCH_AUTH,
        patch("ytm_player.services.auth.YTMusic", side_effect=_ytmusic_factory),
    ):
        result = auth._save_youtube_cookies(cookies)  # non-interactive, no prior file

    assert result is True
    saved = json.loads((tmp_path / "headers_auth.json").read_text())
    assert saved["x-goog-authuser"] == "0"  # Alice (first valid account)
