"""Simple text-input popup that returns the entered string or None."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, Static

from ytm_player.ui.popups.base import BasePopup


class InputPopup(BasePopup[str | None]):
    """Modal prompt with a single text input.

    Returns the entered text on submit, or ``None`` if dismissed.
    """

    DEFAULT_CSS = """
    InputPopup > Vertical {
        width: 50;
        height: auto;
    }

    InputPopup #input-title {
        text-align: center;
        text-style: bold;
        width: 100%;
        margin-bottom: 1;
        color: $text;
    }

    InputPopup Input {
        width: 100%;
    }
    """

    def __init__(self, title: str, placeholder: str = "") -> None:
        super().__init__()
        self._title = title
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._title, id="input-title", markup=False)
            yield Input(placeholder=self._placeholder, id="input-field")

    def on_mount(self) -> None:
        self.query_one("#input-field", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if value:
            self.dismiss(value)
        else:
            self.dismiss(None)
