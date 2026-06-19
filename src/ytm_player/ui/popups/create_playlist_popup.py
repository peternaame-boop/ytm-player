"""Popup for creating or editing a playlist with name and privacy selection."""

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


class CreatePlaylistPopup(ModalScreen[tuple[str, str, str] | None]):
    """Modal prompt for creating or editing a playlist.

    Pass ``initial_name``, ``initial_description``, and ``initial_privacy`` to
    pre-fill the fields for edit mode.  Set ``edit_mode=True`` to swap the
    title and submit-button labels to "Edit Playlist" / "Edit".

    Returns ``(name, description, privacy)`` on submit, or ``None`` if dismissed.
    """

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Close", show=False),
    ]

    DEFAULT_CSS = """
    CreatePlaylistPopup {
        align: center middle;
        height: 100%;
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

    CreatePlaylistPopup #input-description {
        height: 3;
    }

    CreatePlaylistPopup Select {
        width: 100%;
        margin-bottom: 1;
    }

    CreatePlaylistPopup #button-row {
        height: auto;
        align: center middle;
        margin-top: 1;
    }

    CreatePlaylistPopup Button {
        width: 1fr;
        margin: 0 1;
    }
    """

    def __init__(
        self,
        *,
        initial_name: str = "",
        initial_description: str = "",
        initial_privacy: str = "PRIVATE",
        edit_mode: bool = False,
    ) -> None:
        super().__init__()
        self._initial_name = initial_name
        self._initial_description = initial_description
        self._initial_privacy = initial_privacy
        self._edit_mode = edit_mode

    def compose(self) -> ComposeResult:
        title = "Edit Playlist" if self._edit_mode else "New Playlist"
        submit_label = "Edit" if self._edit_mode else "Create"
        with Vertical():
            yield Static(title, id="popup-title")
            yield Input(
                value=self._initial_name,
                placeholder="Playlist name...",
                id="input-name",
            )
            yield Input(
                value=self._initial_description,
                placeholder="Description (optional)...",
                id="input-description",
            )
            yield Select(
                PRIVACY_OPTIONS,
                value=self._initial_privacy,
                id="select-privacy",
                allow_blank=False,
            )
            with Horizontal(id="button-row"):
                yield Button("Cancel", variant="default", id="btn-cancel")
                yield Button(submit_label, variant="primary", id="btn-create")

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
        name_input = self.query_one("#input-name", Input)
        name = name_input.value.strip()
        if not name:
            self.notify("Playlist name cannot be empty", severity="warning", timeout=3)
            name_input.focus()
            return
        description = self.query_one("#input-description", Input).value.strip()
        privacy = str(self.query_one("#select-privacy", Select).value)
        self.dismiss((name, description, privacy))
