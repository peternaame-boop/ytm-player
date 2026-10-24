"""Back and forward work by Alt+Left and Alt+Right, also while a text box has focus."""

from __future__ import annotations

from unittest.mock import AsyncMock

from textual.app import App, ComposeResult
from textual.widgets import Input, Static

from ytm_player.app._keys import KeyHandlingMixin
from ytm_player.config import Action
from ytm_player.config.keymap import get_keymap


class _Host(KeyHandlingMixin, App):
    def __init__(self) -> None:
        super().__init__()
        self.keymap = get_keymap()
        self._key_buffer: list[str] = []
        self._count_buffer = ""
        self._handle_action = AsyncMock()

    def compose(self) -> ComposeResult:
        yield Static("page", id="page")
        yield Input(id="box")

    def actions(self) -> list[Action]:
        return [call.args[0] for call in self._handle_action.await_args_list]


def test_the_default_keymap_binds_alt_left_and_alt_right():
    keymap = get_keymap()
    assert ("M-left",) in keymap.get_keys_for_action(Action.GO_BACK)
    assert ("M-right",) in keymap.get_keys_for_action(Action.GO_FORWARD)


async def test_alt_arrows_go_back_and_forward_on_a_page():
    app = _Host()
    async with app.run_test() as pilot:
        app.set_focus(None)
        await pilot.press("alt+left", "alt+right", "backspace")
        assert app.actions() == [Action.GO_BACK, Action.GO_FORWARD, Action.GO_BACK]


async def test_alt_arrows_still_navigate_from_a_text_box_but_backspace_edits():
    app = _Host()
    async with app.run_test() as pilot:
        box = app.query_one("#box", Input)
        box.focus()
        await pilot.press("a", "b", "backspace")
        assert box.value == "a"
        assert app.actions() == []
        await pilot.press("alt+left", "alt+right")
        assert app.actions() == [Action.GO_BACK, Action.GO_FORWARD]
        assert box.value == "a"
