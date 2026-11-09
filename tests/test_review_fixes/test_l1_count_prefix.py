"""A count prefix is ASCII digits only (review A16).

``str.isdigit()`` is true for "²" (the key left of 1 on an AZERTY
keyboard) and other Unicode digits that ``int()`` rejects, so the next
bound key raised ValueError inside the App's key handler.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from textual.events import Key

from ytm_player.app._keys import KeyHandlingMixin
from ytm_player.config import Action
from ytm_player.config.keymap import KeyMap


def _host() -> MagicMock:
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


@pytest.mark.parametrize(
    ("key", "character"),
    [
        ("superscript_two", "²"),
        ("superscript_three", "³"),
        ("superscript_one", "¹"),
        ("circled_digit_one", "①"),
    ],
)
async def test_a_non_ascii_digit_is_not_a_count_prefix(key, character):
    assert character.isdigit(), "the case only matters for characters isdigit() accepts"
    host = _host()

    await KeyHandlingMixin.on_key(host, Key(key, character))
    assert host._count_buffer == ""

    # The next bound key raised ValueError here on master.
    await KeyHandlingMixin.on_key(host, Key("j", "j"))

    host._handle_action.assert_awaited_once_with(Action.MOVE_DOWN, 1)
    assert host._key_buffer == []
    assert host._count_buffer == ""


async def test_a_non_ascii_digit_inside_a_count_drops_the_count_without_raising():
    host = _host()

    for event in (Key("5", "5"), Key("superscript_two", "²"), Key("j", "j")):
        await KeyHandlingMixin.on_key(host, event)

    # "²" is an unbound key: like any other one it resets the pending count.
    host._handle_action.assert_awaited_once_with(Action.MOVE_DOWN, 1)


async def test_ascii_digits_still_build_a_count():
    host = _host()

    for event in (Key("1", "1"), Key("2", "2"), Key("j", "j")):
        await KeyHandlingMixin.on_key(host, event)

    host._handle_action.assert_awaited_once_with(Action.MOVE_DOWN, 12)
