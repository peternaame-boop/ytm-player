"""Tests for KeyHandlingMixin._normalize_key and count buffer behavior.

These cover:
- Modifier translation (ctrl+x → C-x, shift+tab → S-tab, alt+v → M-v)
- Special-key remap (pageup → page_up, return → enter)
- Passthrough for unmodified printable keys
- Cap on count buffer (1000)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from textual.events import Key

from ytm_player.app._keys import _MAX_KEY_COUNT, KeyHandlingMixin
from ytm_player.config import Action
from ytm_player.config.keymap import KeyMap
from ytm_player.services.queue import QueueManager


def _make_event(key: str) -> MagicMock:
    """Build a minimal mock that quacks like textual.events.Key."""
    e = MagicMock()
    e.key = key
    return e


class TestNormalizeKey:
    def test_ctrl_modifier(self):
        assert KeyHandlingMixin._normalize_key(_make_event("ctrl+r")) == "C-r"

    def test_shift_modifier(self):
        assert KeyHandlingMixin._normalize_key(_make_event("shift+tab")) == "S-tab"

    def test_alt_modifier(self):
        assert KeyHandlingMixin._normalize_key(_make_event("alt+v")) == "M-v"

    def test_pageup_remap(self):
        assert KeyHandlingMixin._normalize_key(_make_event("pageup")) == "page_up"

    def test_return_aliases_enter(self):
        assert KeyHandlingMixin._normalize_key(_make_event("return")) == "enter"

    def test_question_mark_remap(self):
        assert KeyHandlingMixin._normalize_key(_make_event("question_mark")) == "?"

    def test_symbolic_punctuation_uses_character(self):
        """Regression for #143: ">" "<" "^" reach the app as symbolic names."""
        for key, char in [
            ("greater_than_sign", ">"),
            ("less_than_sign", "<"),
            ("circumflex_accent", "^"),
        ]:
            assert KeyHandlingMixin._normalize_key(Key(key, char)) == char

    def test_space_keeps_its_name(self):
        assert KeyHandlingMixin._normalize_key(Key("space", " ")) == "space"

    def test_modifier_wins_over_character(self):
        assert KeyHandlingMixin._normalize_key(Key("ctrl+r", None)) == "C-r"

    def test_unmodified_passthrough(self):
        assert KeyHandlingMixin._normalize_key(_make_event("j")) == "j"

    def test_arrow_keys_passthrough(self):
        assert KeyHandlingMixin._normalize_key(_make_event("up")) == "up"


def _key_host() -> MagicMock:
    """A host running the real on_key over the default keymap."""
    host = MagicMock()
    host.screen.is_modal = False
    host.focused = None
    host.keymap = KeyMap()
    host.keymap._load_defaults()
    host._key_buffer = []
    host._count_buffer = ""
    host._normalize_key = KeyHandlingMixin._normalize_key
    host._handle_action = AsyncMock()
    return host


class TestModifierOnlyKeys:
    """Regression for #178: kitty also sends a key event for the Shift press."""

    async def test_g_shift_l_opens_the_lyrics(self):
        host = _key_host()

        for event in (Key("g", "g"), Key("left_shift", None), Key("L", "L")):
            await KeyHandlingMixin.on_key(host, event)

        host._handle_action.assert_awaited_once_with(Action.LYRICS, 1)

    async def test_count_survives_a_shift_press(self):
        host = _key_host()

        for event in (Key("1", "1"), Key("5", "5"), Key("right_shift", None), Key("J", "J")):
            await KeyHandlingMixin.on_key(host, event)

        host._handle_action.assert_awaited_once_with(Action.REORDER_DOWN, 15)


class TestKeyCountCap:
    def test_max_count_constant_is_1000(self):
        """Sanity: regression guard if someone changes the cap silently."""
        assert _MAX_KEY_COUNT == 1000


def _shuffle_host(*, locked: bool = False) -> MagicMock:
    """A host with a real queue in a playlist context; the rest is mocked."""
    host = MagicMock()
    host.queue = QueueManager()
    host.queue.add_multiple(
        [{"video_id": f"v{i}", "title": f"T{i}", "artist": "A", "duration": 60} for i in range(3)]
    )
    host.queue.jump_to(0)
    host.queue.set_context("PL1")
    host.shuffle_prefs.get.return_value = locked
    return host


class TestToggleShuffleAction:
    async def test_toggle_refreshes_the_queue_page(self):
        host = _shuffle_host()

        await KeyHandlingMixin._handle_action(host, Action.TOGGLE_SHUFFLE)

        assert host.queue.shuffle_enabled
        host._refresh_queue_page.assert_called_once()

    async def test_locked_context_toggles_nothing(self):
        host = _shuffle_host(locked=True)

        await KeyHandlingMixin._handle_action(host, Action.TOGGLE_SHUFFLE)

        assert not host.queue.shuffle_enabled
        host._refresh_queue_page.assert_not_called()
        assert host.notify.call_args.kwargs.get("severity") == "warning"


class TestDiscoveryMixAction:
    async def test_runs_in_a_worker_group_of_its_own(self):
        host = MagicMock()

        await KeyHandlingMixin._handle_action(host, Action.DISCOVERY_MIX)

        host.run_worker.assert_called_once_with(
            host._start_discovery_mix.return_value, group="discovery-mix", exclusive=True
        )
