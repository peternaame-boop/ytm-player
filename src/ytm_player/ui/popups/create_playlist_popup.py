"""Popup for creating a new playlist with name and privacy selection."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static

PRIVACY_OPTIONS: list[tuple[str, str]] = [
    ("Private", "PRIVATE"),
    ("Public", "PUBLIC"),
    ("Unlisted", "UNLISTED"),
]


class CreatePlaylistPopup(ModalScreen[tuple[str, str] | None]):
    """Modal prompt for creating a playlist.

    Returns ``(name, privacy)`` on submit, or ``None`` if dismissed.
    """

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Close", show=False),
    ]

    DEFAULT_CSS = """
    CreatePlaylistPopup {
        align: center middle;
    }

    CreatePlaylistPopup > Vertical {
        width: 50;
        height: auto;
        background: $surface;
        border: round $primary;
        padding: 1 2;
    }

    CreatePlaylistPopup #popup-title {
        text-align: center;
        text-style: bold;
        width: 100%;
        margin-bottom: 1;
        color: $text;
    }

    CreatePlaylistPopup Input {
        width: 100%;
        margin-bottom: 1;
    }

    CreatePlaylistPopup Select {
        width: 100%;
        margin-bottom: 1;
    }

    CreatePlaylistPopup #button-row {
        height: auto;
        align: right middle;
        margin-top: 1;
    }

    CreatePlaylistPopup Button {
        margin-left: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("New Playlist", id="popup-title")
            yield Input(placeholder="Playlist name...", id="input-name")
            yield Select(
                PRIVACY_OPTIONS,
                value="PRIVATE",
                id="select-privacy",
                allow_blank=False,
            )
            with Horizontal(id="button-row"):
                yield Button("Cancel", variant="default", id="btn-cancel")
                yield Button("Create", variant="primary", id="btn-create")

    def on_mount(self) -> None:
        self.query_one("#input-name", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-create":
            self._submit()
        else:
            self.dismiss(None)

    def _submit(self) -> None:
        name = self.query_one("#input-name", Input).value.strip()
        if not name:
            return
        privacy = str(self.query_one("#select-privacy", Select).value)
        self.dismiss((name, privacy))
