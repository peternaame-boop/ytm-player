"""Yes/No confirmation popup."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Static

from ytm_player.ui.popups.base import BasePopup


class ConfirmPopup(BasePopup[bool]):
    """Modal confirmation dialog.

    Returns ``True`` if the user confirms, ``False`` if cancelled.
    """

    _CANCEL_RESULT: ClassVar[bool] = False

    BINDINGS = [
        Binding("n", "cancel", "No", show=False),
        Binding("y", "confirm", "Yes", show=False),
    ]

    DEFAULT_CSS = """
    ConfirmPopup {
        height: 100%;
    }

    ConfirmPopup > Vertical {
        width: 50;
        height: auto;
    }

    ConfirmPopup #confirm-message {
        text-align: center;
        width: 100%;
        margin-bottom: 1;
        color: $text;
    }

    ConfirmPopup Horizontal {
        align: center middle;
        height: auto;
        margin-top: 1;
    }

    ConfirmPopup Button {
        width: 1fr;
        margin: 0 1;
    }

    """

    def __init__(
        self,
        message: str,
        confirm_label: str = "Yes",
        cancel_label: str = "No",
    ) -> None:
        super().__init__()
        self._message = message
        self._confirm_label = confirm_label
        self._cancel_label = cancel_label

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._message, id="confirm-message", markup=False)
            with Horizontal():
                yield Button(self._cancel_label, variant="default", id="confirm-no")
                yield Button(self._confirm_label, variant="primary", id="confirm-yes")

    def on_mount(self) -> None:
        self.query_one("#confirm-no", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")

    def action_confirm(self) -> None:
        self.dismiss(True)
