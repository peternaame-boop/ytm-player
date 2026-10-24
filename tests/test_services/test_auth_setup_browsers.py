"""`ytm setup` tries every browser with sign-in cookies, not only the first.

Regression: a stale Firefox profile sorts before Zen in the browser order. Setup
read Firefox, whose cookies are signed out, and dropped straight to manual header
paste without ever trying Zen, where the accounts lived.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from ytm_player.services.auth import AuthManager


def _manager(tmp_path, monkeypatch, candidates):
    manager = AuthManager(config_dir=tmp_path, auth_file=tmp_path / "auth.json")
    monkeypatch.setattr(manager, "_browser_candidates", lambda skip=frozenset(): iter(candidates))
    monkeypatch.setattr(manager, "_setup_manual", lambda: False)
    return manager


def test_setup_falls_through_a_signed_out_browser_to_the_next(tmp_path, monkeypatch):
    firefox = ("firefox", ["ff-cookie"], ["ff-jar"])
    zen = ("zen", ["zen-cookie"], ["zen-jar"])
    manager = _manager(tmp_path, monkeypatch, [firefox, zen])
    save = MagicMock(side_effect=[False, True])
    monkeypatch.setattr(manager, "_save_youtube_cookies", save)

    assert manager.setup_interactive() is True
    assert [call.args[0] for call in save.call_args_list] == [["ff-cookie"], ["zen-cookie"]]


def test_setup_stops_at_the_first_browser_that_works(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch, [("zen", ["c"], ["j"]), ("brave", ["b"], ["j"])])
    save = MagicMock(return_value=True)
    monkeypatch.setattr(manager, "_save_youtube_cookies", save)

    assert manager.setup_interactive() is True
    assert save.call_count == 1


def test_setup_reaches_manual_paste_when_every_browser_fails(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch, [("firefox", ["f"], ["j"]), ("zen", ["z"], ["j"])])
    monkeypatch.setattr(manager, "_save_youtube_cookies", MagicMock(return_value=False))
    manual = MagicMock(return_value=True)
    monkeypatch.setattr(manager, "_setup_manual", manual)

    assert manager.setup_interactive() is True
    manual.assert_called_once()


def test_setup_manual_when_no_browser_has_cookies(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch, [])
    manual = MagicMock(return_value=True)
    monkeypatch.setattr(manager, "_setup_manual", manual)
    save = MagicMock()
    monkeypatch.setattr(manager, "_save_youtube_cookies", save)

    assert manager.setup_interactive() is True
    save.assert_not_called()
    manual.assert_called_once()


def test_setup_survives_an_error_in_one_browser(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch, [("firefox", ["f"], ["j"]), ("zen", ["z"], ["j"])])
    save = MagicMock(side_effect=[RuntimeError("locked db"), True])
    monkeypatch.setattr(manager, "_save_youtube_cookies", save)

    assert manager.setup_interactive() is True
    assert save.call_count == 2


def test_a_signed_out_session_probes_as_no_account(tmp_path, monkeypatch):
    """ytmusicapi raises KeyError when the account menu carries no account
    header. That means "no account here", so the cursor moves to the next slot."""
    from ytm_player.services import auth as auth_mod

    fake = MagicMock()
    fake.get_account_info.side_effect = KeyError("header")
    monkeypatch.setattr(auth_mod, "YTMusic", lambda _path: fake)

    assert auth_mod._probe_account(str(tmp_path / "auth.json"), 0) is None
