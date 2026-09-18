"""Tests for _save_youtube_cookies multi-account selection during `ytm setup`.

Silent (non-interactive) renewal is covered in test_auth_identity.py."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ytm_player.services.auth import AuthManager

_PATCH_SAPISID = patch("ytm_player.services.auth.sapisid_from_cookie", return_value="fake_sapisid")
_PATCH_AUTH = patch("ytm_player.services.auth.get_authorization", return_value="SAPISIDHASH fake")


def _make_cookie(name: str, value: str = "val", domain: str = ".youtube.com"):
    c = SimpleNamespace(name=name, value=value, domain=domain)
    return c


def _make_auth(tmp_path):
    return AuthManager(
        config_dir=tmp_path,
        auth_file=tmp_path / "headers_auth.json",
        stream_cookies_file=tmp_path / "stream_cookies.txt",
    )


def _account(name):
    return {"accountName": name}


# ── Single account ────────────────────────────────────────────────────────────


def test_single_account_auto_selected(tmp_path):
    """With one valid account, it is selected automatically without prompting."""
    auth = _make_auth(tmp_path)
    cookies = [_make_cookie("SAPISID", "secret")]

    def _ytmusic_factory(path):
        saved = json.loads(Path(path).read_text())
        m = MagicMock()
        m.get_account_info.return_value = (
            _account("Alice") if saved["x-goog-authuser"] == "0" else {}
        )
        return m

    with (
        _PATCH_SAPISID,
        _PATCH_AUTH,
        patch("ytm_player.services.auth.YTMusic", side_effect=_ytmusic_factory),
    ):
        result = auth._save_youtube_cookies(cookies, interactive=True)

    assert result is True
    saved = json.loads((tmp_path / "headers_auth.json").read_text())
    assert saved["x-goog-authuser"] == "0"


def test_single_account_at_index_1_is_found(tmp_path):
    """When only index 1 has a valid account, it is found and written."""
    auth = _make_auth(tmp_path)
    cookies = [_make_cookie("SAPISID", "secret")]

    def _ytmusic_factory(path):
        saved = json.loads(Path(path).read_text())
        idx = int(saved["x-goog-authuser"])
        m = MagicMock()
        m.get_account_info.return_value = _account("Bob") if idx == 1 else {}
        return m

    with (
        _PATCH_SAPISID,
        _PATCH_AUTH,
        patch("ytm_player.services.auth.YTMusic", side_effect=_ytmusic_factory),
    ):
        result = auth._save_youtube_cookies(cookies, interactive=True)

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
        result = auth._save_youtube_cookies(cookies, interactive=True)

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
        result = auth._save_youtube_cookies(cookies, interactive=True)

    assert result is False


# ── Multiple accounts ─────────────────────────────────────────────────────────


def test_multiple_accounts_prompts_user_and_picks_choice(tmp_path, monkeypatch):
    """With two valid accounts, user is prompted and their choice is saved."""
    auth = _make_auth(tmp_path)
    cookies = [_make_cookie("SAPISID", "s0"), _make_cookie("SAPISID1", "s1")]

    accounts = {0: _account("Alice"), 1: _account("Bob")}

    def _ytmusic_factory(path):
        saved = json.loads(Path(path).read_text())
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
        saved = json.loads(Path(path).read_text())
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
        saved = json.loads(Path(path).read_text())
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


def test_always_probes_all_ten_indices_by_default(tmp_path):
    """All ten indices (0-9) are probed by default regardless of cookie names."""
    auth = _make_auth(tmp_path)
    cookies = [_make_cookie("SAPISID", "secret")]

    probed = []

    def _ytmusic_factory(path):
        saved = json.loads(Path(path).read_text())
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
        result = auth._save_youtube_cookies(cookies, interactive=True)

    assert probed == list(range(10))
    assert result is True
    saved = json.loads((tmp_path / "headers_auth.json").read_text())
    assert saved["x-goog-authuser"] == "2"


def test_probes_custom_slots(tmp_path):
    """Passing slots limits or expands the probed indices."""
    auth = _make_auth(tmp_path)
    cookies = [_make_cookie("SAPISID", "secret")]

    probed = []

    def _ytmusic_factory(path):
        saved = json.loads(Path(path).read_text())
        probed.append(int(saved["x-goog-authuser"]))
        m = MagicMock()
        m.get_account_info.return_value = _account("Charlie") if probed[-1] == 2 else {}
        return m

    with (
        _PATCH_SAPISID,
        _PATCH_AUTH,
        patch("ytm_player.services.auth.YTMusic", side_effect=_ytmusic_factory),
    ):
        result = auth._save_youtube_cookies(cookies, interactive=True, slots=6)

    assert probed == list(range(6))
    assert result is True
    saved = json.loads((tmp_path / "headers_auth.json").read_text())
    assert saved["x-goog-authuser"] == "2"


def test_default_ten_slots_discovers_account_in_slot_7(tmp_path):
    """Account in slot 7 (slots 0-6 empty) is discovered and saved with x-goog-authuser: '7'."""
    auth = _make_auth(tmp_path)
    cookies = [_make_cookie("SAPISID", "secret")]

    probed = []

    def _ytmusic_factory(path):
        saved = json.loads(Path(path).read_text())
        probed.append(int(saved["x-goog-authuser"]))
        m = MagicMock()
        m.get_account_info.return_value = _account("Grace") if probed[-1] == 7 else {}
        return m

    with (
        _PATCH_SAPISID,
        _PATCH_AUTH,
        patch("ytm_player.services.auth.YTMusic", side_effect=_ytmusic_factory),
    ):
        result = auth._save_youtube_cookies(cookies, interactive=True)

    assert probed == list(range(10))
    assert result is True
    saved = json.loads((tmp_path / "headers_auth.json").read_text())
    assert saved["x-goog-authuser"] == "7"


def test_custom_slots_discovers_account_in_slot_12(tmp_path):
    """Custom slots=15 probes all 15 slots and discovers an account in slot 12."""
    auth = _make_auth(tmp_path)
    cookies = [_make_cookie("SAPISID", "secret")]

    probed = []

    def _ytmusic_factory(path):
        saved = json.loads(Path(path).read_text())
        probed.append(int(saved["x-goog-authuser"]))
        m = MagicMock()
        m.get_account_info.return_value = _account("Leo") if probed[-1] == 12 else {}
        return m

    with (
        _PATCH_SAPISID,
        _PATCH_AUTH,
        patch("ytm_player.services.auth.YTMusic", side_effect=_ytmusic_factory),
    ):
        result = auth._save_youtube_cookies(cookies, interactive=True, slots=15)

    assert probed == list(range(15))
    assert result is True
    saved = json.loads((tmp_path / "headers_auth.json").read_text())
    assert saved["x-goog-authuser"] == "12"


def test_multiple_accounts_selects_account_in_slot_7(tmp_path, monkeypatch):
    """When multiple accounts are found, user can select an account in slot 7."""
    auth = _make_auth(tmp_path)
    cookies = [_make_cookie("SAPISID", "secret")]

    accounts = {0: _account("Alice"), 7: _account("Grace")}

    def _ytmusic_factory(path):
        saved = json.loads(Path(path).read_text())
        idx = int(saved["x-goog-authuser"])
        m = MagicMock()
        m.get_account_info.return_value = accounts.get(idx, {})
        return m

    # User selects account 2 (Grace, in slot 7)
    monkeypatch.setattr("builtins.input", lambda _: "2")

    with (
        _PATCH_SAPISID,
        _PATCH_AUTH,
        patch("ytm_player.services.auth.YTMusic", side_effect=_ytmusic_factory),
    ):
        result = auth._save_youtube_cookies(cookies, interactive=True)

    assert result is True
    saved = json.loads((tmp_path / "headers_auth.json").read_text())
    assert saved["x-goog-authuser"] == "7"


# ── setup_interactive parameter forwarding ────────────────────────────────────


def test_setup_interactive_browser_forwards_slots_to_save_cookies(tmp_path):
    """setup_interactive(browser='firefox', slots=8) forwards slots=8 into _save_youtube_cookies."""
    auth = _make_auth(tmp_path)
    cookie = _make_cookie("SAPISID", "secret")

    with (
        patch("ytm_player.services.auth._extract_browser_jar", return_value=[cookie]),
        patch("ytm_player.services.auth._patch_yt_dlp_browsers"),
        patch.object(auth, "_save_youtube_cookies", return_value=True) as mock_save,
    ):
        result = auth.setup_interactive(browser="firefox", slots=8)

    assert result is True
    mock_save.assert_called_once_with([cookie], interactive=True, stream_jar=[cookie], slots=8)


def test_setup_interactive_browser_probes_custom_slots_end_to_end(tmp_path):
    """setup_interactive(browser='firefox', slots=8) end-to-end probes exactly 8 slots."""
    auth = _make_auth(tmp_path)
    cookie = _make_cookie("SAPISID", "secret")
    probed = []

    def _ytmusic_factory(path):
        saved = json.loads(Path(path).read_text())
        probed.append(int(saved["x-goog-authuser"]))
        m = MagicMock()
        m.get_account_info.return_value = _account("Bob") if probed[-1] == 3 else {}
        return m

    with (
        _PATCH_SAPISID,
        _PATCH_AUTH,
        patch("ytm_player.services.auth._extract_browser_jar", return_value=[cookie]),
        patch("ytm_player.services.auth._patch_yt_dlp_browsers"),
        patch("ytm_player.services.auth._build_stream_cookiejar", return_value=b"# cookies"),
        patch("ytm_player.services.auth.YTMusic", side_effect=_ytmusic_factory),
    ):
        result = auth.setup_interactive(browser="firefox", slots=8)

    assert result is True
    assert probed == list(range(8))
    saved = json.loads((tmp_path / "headers_auth.json").read_text())
    assert saved["x-goog-authuser"] == "3"


# ── Recovery hints ────────────────────────────────────────────────────────────


def test_setup_interactive_autodetect_forwards_slots(tmp_path):
    """setup_interactive() without browser auto-detects and forwards slots."""
    auth = _make_auth(tmp_path)
    cookie = _make_cookie("SAPISID", "secret")
    with (
        patch.object(auth, "_detect_browser", return_value=("firefox", [cookie], [cookie])),
        patch.object(auth, "_save_youtube_cookies", return_value=True) as mock_save,
    ):
        result = auth.setup_interactive(slots=12)

    assert result is True
    mock_save.assert_called_once_with([cookie], interactive=True, stream_jar=[cookie], slots=12)


def test_recovery_hint_zero_accounts_below_max_slots(tmp_path, capsys):
    """When no accounts found and slots < 30, suggest slots + 5."""
    auth = _make_auth(tmp_path)
    cookies = [_make_cookie("SAPISID", "secret")]
    mock_ytm = MagicMock()
    mock_ytm.get_account_info.return_value = {}

    with (
        _PATCH_SAPISID,
        _PATCH_AUTH,
        patch("ytm_player.services.auth.YTMusic", return_value=mock_ytm),
    ):
        result = auth._save_youtube_cookies(cookies, interactive=True, slots=10)

    assert result is False
    out = capsys.readouterr().out
    assert "No valid YouTube Music account found in slots 0-9." in out
    assert "retry with `ytm setup --slots 15`" in out


def test_recovery_hint_zero_accounts_singular_slot(tmp_path, capsys):
    """When slots=1, format says 'slot 0' instead of 'slots 0-0'."""
    auth = _make_auth(tmp_path)
    cookies = [_make_cookie("SAPISID", "secret")]
    mock_ytm = MagicMock()
    mock_ytm.get_account_info.return_value = {}

    with (
        _PATCH_SAPISID,
        _PATCH_AUTH,
        patch("ytm_player.services.auth.YTMusic", return_value=mock_ytm),
    ):
        result = auth._save_youtube_cookies(cookies, interactive=True, slots=1)

    assert result is False
    out = capsys.readouterr().out
    assert "No valid YouTube Music account found in slot 0." in out
    assert "retry with `ytm setup --slots 6`" in out


def test_recovery_hint_zero_accounts_boundary_slots(tmp_path, capsys):
    """When slots=28, suggested slots is capped at MAX_ACCOUNT_PROBE_SLOTS (30)."""
    auth = _make_auth(tmp_path)
    cookies = [_make_cookie("SAPISID", "secret")]
    mock_ytm = MagicMock()
    mock_ytm.get_account_info.return_value = {}

    with (
        _PATCH_SAPISID,
        _PATCH_AUTH,
        patch("ytm_player.services.auth.YTMusic", return_value=mock_ytm),
    ):
        result = auth._save_youtube_cookies(cookies, interactive=True, slots=28)

    assert result is False
    out = capsys.readouterr().out
    assert "retry with `ytm setup --slots 30`" in out


def test_recovery_hint_zero_accounts_at_max_slots(tmp_path, capsys):
    """When slots >= 30, advise manual setup instead of out-of-range slots."""
    auth = _make_auth(tmp_path)
    cookies = [_make_cookie("SAPISID", "secret")]
    mock_ytm = MagicMock()
    mock_ytm.get_account_info.return_value = {}

    with (
        _PATCH_SAPISID,
        _PATCH_AUTH,
        patch("ytm_player.services.auth.YTMusic", return_value=mock_ytm),
    ):
        result = auth._save_youtube_cookies(cookies, interactive=True, slots=30)

    assert result is False
    out = capsys.readouterr().out
    assert "retry with `ytm setup --manual`" in out


def test_recovery_hint_multiple_accounts_step_consistency(tmp_path, monkeypatch, capsys):
    """With custom slots=5, multiple accounts suggests slots 10 (consistent
    with zero-account formula).
    """
    auth = _make_auth(tmp_path)
    cookies = [_make_cookie("SAPISID", "s0")]
    accounts = {0: _account("Alice"), 1: _account("Bob")}

    def _ytmusic_factory(path):
        saved = json.loads(Path(path).read_text())
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
        result = auth._save_youtube_cookies(cookies, interactive=True, slots=5)

    assert result is True
    out = capsys.readouterr().out
    assert "retry with `ytm setup --slots 10`" in out


def test_recovery_hint_multiple_accounts_at_max_slots(tmp_path, monkeypatch, capsys):
    """When slots >= 30, multiple accounts advises manual setup."""
    auth = _make_auth(tmp_path)
    cookies = [_make_cookie("SAPISID", "s0")]
    accounts = {0: _account("Alice"), 1: _account("Bob")}

    def _ytmusic_factory(path):
        saved = json.loads(Path(path).read_text())
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
        result = auth._save_youtube_cookies(cookies, interactive=True, slots=30)

    assert result is True
    out = capsys.readouterr().out
    assert "retry with `ytm setup --manual`" in out
