"""Silent renewal tries every browser with sign-in cookies, not only the first.

Regression: a stale Firefox profile sorts before Zen in the browser order. Renewal
read Firefox, did not find the recorded account there, and gave up without ever
trying Zen, where the account lived.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from ytm_player.services.auth import AuthManager


def _manager(tmp_path, monkeypatch, first, rest):
    auth_file = tmp_path / "auth.json"
    auth_file.write_bytes(b'{"cookie": "SAPISID=x", "x-goog-authuser": "1"}')
    manager = AuthManager(config_dir=tmp_path, auth_file=auth_file)
    monkeypatch.setattr(manager, "_detect_browser", lambda: first)
    seen_skip = []

    def candidates(skip=frozenset()):
        seen_skip.append(skip)
        return iter(rest)

    monkeypatch.setattr(manager, "_browser_candidates", candidates)
    return manager, seen_skip


def test_refusal_in_the_first_browser_falls_through_to_the_next(tmp_path, monkeypatch):
    firefox = ("firefox", ["ff-cookie"], ["ff-jar"])
    zen = ("zen", ["zen-cookie"], ["zen-jar"])
    manager, seen_skip = _manager(tmp_path, monkeypatch, firefox, [zen])
    save = MagicMock(side_effect=[False, True])
    monkeypatch.setattr(manager, "_save_youtube_cookies", save)

    assert manager.try_auto_refresh() is True
    assert [call.args[0] for call in save.call_args_list] == [["ff-cookie"], ["zen-cookie"]]
    assert seen_skip == [frozenset({"firefox"})]


def test_stops_at_the_first_browser_that_works(tmp_path, monkeypatch):
    manager, _ = _manager(tmp_path, monkeypatch, ("zen", ["c"], ["j"]), [("brave", ["b"], ["j"])])
    save = MagicMock(return_value=True)
    monkeypatch.setattr(manager, "_save_youtube_cookies", save)

    assert manager.try_auto_refresh() is True
    assert save.call_count == 1


def test_every_browser_refusing_returns_false(tmp_path, monkeypatch):
    manager, _ = _manager(tmp_path, monkeypatch, ("firefox", ["f"], ["j"]), [("zen", ["z"], ["j"])])
    monkeypatch.setattr(manager, "_save_youtube_cookies", MagicMock(return_value=False))

    assert manager.try_auto_refresh() is False


def test_an_error_in_one_browser_does_not_stop_the_next(tmp_path, monkeypatch):
    manager, _ = _manager(tmp_path, monkeypatch, ("firefox", ["f"], ["j"]), [("zen", ["z"], ["j"])])
    save = MagicMock(side_effect=[RuntimeError("locked db"), True])
    monkeypatch.setattr(manager, "_save_youtube_cookies", save)

    assert manager.try_auto_refresh() is True
