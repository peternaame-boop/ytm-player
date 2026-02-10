"""Yes/No confirmation popup."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmPopup(ModalScreen[bool]):
    """Modal confirmation dialog.

    Returns ``True`` if the user confirms, ``False`` if cancelled.
    """

    BINDINGS = [
        Binding("escape", "dismiss(False)", "Cancel", show=False),
        Binding("n", "dismiss(False)", "No", show=False),
        Binding("y", "confirm", "Yes", show=False),
    ]

    DEFAULT_CSS = """
    ConfirmPopup {
        align: center middle;
    }

    ConfirmPopup > Vertical {
        width: 50;
        height: auto;
        background: $surface;
        border: round $primary;
        padding: 1 2;
    }

    ConfirmPopup #confirm-message {
        text-align: center;
        width: 100%;
        margin-bottom: 1;
        color: $text;
    }

    ConfirmPopup Horizontal {
        align: center middle;
        height: 3;
    }

    ConfirmPopup Button {
        margin: 0 1;
        min-width: 10;
    }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._message, id="confirm-message")
            with Horizontal():
                yield Button("Yes", variant="error", id="confirm-yes")
                yield Button("No", variant="default", id="confirm-no")

    def on_mount(self) -> None:
        self.query_one("#confirm-no", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")

    def action_confirm(self) -> None:
        self.dismiss(True)
