"""Tests for KeyMap load hardening: corrupt-file recovery and binding validation."""

from __future__ import annotations

import logging

from ytm_player.config.keymap import Action, KeyMap, MatchResult


def test_corrupt_keymap_falls_back_to_defaults(tmp_path, caplog):
    """A malformed keymap.toml must not crash the app: back up + use defaults."""
    path = tmp_path / "keymap.toml"
    path.write_text("this is not valid toml === [[[", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        km = KeyMap.load(path)

    # Usable defaults are loaded.
    assert km.bindings
    assert km.match(("space",)) == (MatchResult.EXACT, Action.PLAY_PAUSE)
    # The broken file is backed up and moved out of the way.
    assert path.with_suffix(".toml.bak").exists()
    assert not path.exists()
    assert any("keymap" in r.getMessage().lower() for r in caplog.records)


def test_invalid_binding_value_is_skipped(tmp_path, caplog):
    """A non-str/non-list binding value logs a warning and is skipped, no crash."""
    path = tmp_path / "keymap.toml"
    path.write_text("[pages]\nlibrary = 5\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        km = KeyMap.load(path)

    # No crash; the default library binding remains intact (line skipped).
    assert ("g", "l") in km.get_keys_for_action(Action.LIBRARY)
    assert any("library" in r.getMessage() for r in caplog.records)


def test_list_with_non_str_element_is_skipped(tmp_path, caplog):
    """A list containing a non-str element is not a valid binding — skip + warn."""
    path = tmp_path / "keymap.toml"
    path.write_text('[pages]\nlibrary = ["g x", 7]\n', encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        km = KeyMap.load(path)

    assert ("g", "l") in km.get_keys_for_action(Action.LIBRARY)
    assert any("library" in r.getMessage() for r in caplog.records)


def test_unknown_action_logs_warning(tmp_path, caplog):
    """An unknown action name is logged (not silently swallowed) and skipped."""
    path = tmp_path / "keymap.toml"
    path.write_text('[pages]\nnonexistent_action = "x"\n', encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        km = KeyMap.load(path)

    assert km.bindings  # defaults still present
    assert any("nonexistent_action" in r.getMessage() for r in caplog.records)


def test_valid_override_replaces_default(tmp_path):
    """A well-formed override still rebinds the action (no regression)."""
    path = tmp_path / "keymap.toml"
    path.write_text('[pages]\nlibrary = "g x"\n', encoding="utf-8")

    km = KeyMap.load(path)

    assert km.match(("g", "x")) == (MatchResult.EXACT, Action.LIBRARY)
    assert km.match(("g", "l")) == (MatchResult.NO_MATCH, None)


def test_missing_file_uses_defaults(tmp_path):
    """No keymap.toml on disk → defaults, no backup, no crash."""
    path = tmp_path / "keymap.toml"
    km = KeyMap.load(path)
    assert km.match(("space",)) == (MatchResult.EXACT, Action.PLAY_PAUSE)
    assert not path.with_suffix(".toml.bak").exists()


def test_play_next_default_binding():
    """PLAY_NEXT binds to X / C-x by default (adjacent to add_to_queue's Z / C-z)."""
    km = KeyMap()
    km._load_defaults()
    assert km.match(("X",)) == (MatchResult.EXACT, Action.PLAY_NEXT)
    assert km.match(("C-x",)) == (MatchResult.EXACT, Action.PLAY_NEXT)


def test_removed_actions_are_gone():
    """CONTEXT_ACTIONS / SELECTED_ACTIONS / CLOSE_POPUP were retired — no bindings."""
    km = KeyMap()
    km._load_defaults()
    # The Action members no longer exist.
    for name in ("CONTEXT_ACTIONS", "SELECTED_ACTIONS", "CLOSE_POPUP"):
        assert not hasattr(Action, name)
    # Their old default keys resolve to nothing now.
    assert km.match(("g", "A")) == (MatchResult.NO_MATCH, None)
    assert km.match(("g", "a")) == (MatchResult.NO_MATCH, None)
    assert km.match(("C-space",)) == (MatchResult.NO_MATCH, None)
    # Escape is bound again — to clearing marks, not to the retired CLOSE_POPUP.
    assert km.match(("escape",)) == (MatchResult.EXACT, Action.MARK_CLEAR)
