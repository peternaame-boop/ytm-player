"""Context action menu popup for tracks, albums, artists, and playlists."""

from __future__ import annotations

import logging
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListView, ListItem, Static

logger = logging.getLogger(__name__)

# ── Action definitions per item type ────────────────────────────────

TRACK_ACTIONS: list[tuple[str, str]] = [
    ("play", "Play"),
    ("play_next", "Play Next"),
    ("add_to_queue", "Add to Queue"),
    ("start_radio", "Start Radio"),
    ("go_to_artist", "Go to Artist"),
    ("go_to_album", "Go to Album"),
    ("add_to_playlist", "Add to Playlist"),
    ("toggle_like", "Like"),
    ("copy_link", "Copy Link"),
]

ALBUM_ACTIONS: list[tuple[str, str]] = [
    ("play_all", "Play All"),
    ("shuffle_play", "Shuffle Play"),
    ("add_to_library", "Add to Library"),
    ("add_to_queue", "Add to Queue"),
    ("go_to_artist", "Go to Artist"),
    ("copy_link", "Copy Link"),
]

ARTIST_ACTIONS: list[tuple[str, str]] = [
    ("play_top_songs", "Play Top Songs"),
    ("start_radio", "Start Radio"),
    ("toggle_subscribe", "Subscribe"),
    ("view_albums", "View Albums"),
    ("view_similar", "View Similar Artists"),
    ("copy_link", "Copy Link"),
]

PLAYLIST_ACTIONS: list[tuple[str, str]] = [
    ("play_all", "Play All"),
    ("shuffle_play", "Shuffle Play"),
    ("add_to_queue", "Add to Queue"),
    ("copy_link", "Copy Link"),
    ("delete", "Delete Playlist"),
]

_ACTIONS_BY_TYPE: dict[str, list[tuple[str, str]]] = {
    "track": TRACK_ACTIONS,
    "album": ALBUM_ACTIONS,
    "artist": ARTIST_ACTIONS,
    "playlist": PLAYLIST_ACTIONS,
}


def _build_actions(item: dict[str, Any], item_type: str) -> list[tuple[str, str]]:
    """Return the action list for *item_type*, adjusting labels dynamically."""
    base = list(_ACTIONS_BY_TYPE.get(item_type, TRACK_ACTIONS))
    result: list[tuple[str, str]] = []

    for action_id, label in base:
        # Swap "Like" / "Unlike" depending on the item's current rating.
        if action_id == "toggle_like":
            is_liked = item.get("likeStatus") == "LIKE" or item.get("liked", False)
            label = "Unlike" if is_liked else "Like"

        # Swap "Subscribe" / "Unsubscribe" for artists.
        if action_id == "toggle_subscribe":
            is_subscribed = item.get("subscribed", False)
            label = "Unsubscribe" if is_subscribed else "Subscribe"

        # Only show "Go to Artist" when artist info is available.
        if action_id == "go_to_artist":
            artists = item.get("artists") or []
            if not artists and not item.get("artist"):
                continue

        # Only show "Go to Album" when album info is available.
        if action_id == "go_to_album":
            if not item.get("album_id") and not item.get("album", {}).get("id"):
                continue

        result.append((action_id, label))

    return result


class _ActionItem(ListItem):
    """A single action entry in the list."""

    def __init__(self, action_id: str, label: str) -> None:
        super().__init__()
        self.action_id = action_id
        self._label = label

    def compose(self) -> ComposeResult:
        yield Label(self._label)


class ActionsPopup(ModalScreen[str | None]):
    """Context menu showing available actions for a track/album/artist/playlist.

    Returns the selected action string, or ``None`` if dismissed.
    """

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Close", show=False),
        Binding("j,down", "cursor_down", "Down", show=False),
        Binding("k,up", "cursor_up", "Up", show=False),
    ]

    DEFAULT_CSS = """
    ActionsPopup {
        align: center middle;
    }

    ActionsPopup > Vertical {
        width: 40;
        max-height: 80%;
        background: $surface;
        border: round $primary;
        padding: 1 2;
    }

    ActionsPopup #actions-title {
        text-align: center;
        text-style: bold;
        width: 100%;
        margin-bottom: 1;
        color: $text;
    }

    ActionsPopup ListView {
        height: auto;
        max-height: 20;
        background: $surface;
    }

    ActionsPopup ListItem {
        padding: 0 1;
        height: 1;
    }

    ActionsPopup ListItem:hover {
        background: $accent 30%;
    }

    ActionsPopup ListView:focus > ListItem.--highlight {
        background: $primary 40%;
    }
    """

    def __init__(self, item: dict[str, Any], item_type: str = "track") -> None:
        super().__init__()
        self.item = item
        self.item_type = item_type
        self._actions = _build_actions(item, item_type)

    @property
    def _title_text(self) -> str:
        """Derive a short title from the item for display."""
        name = (
            self.item.get("title")
            or self.item.get("name")
            or self.item.get("artist")
            or self.item_type.capitalize()
        )
        if len(name) > 34:
            name = name[:31] + "..."
        return name

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._title_text, id="actions-title")
            yield ListView(
                *[_ActionItem(aid, label) for aid, label in self._actions],
                id="actions-list",
            )

    def on_mount(self) -> None:
        list_view = self.query_one("#actions-list", ListView)
        list_view.focus()

    # ── Navigation helpers ──────────────────────────────────────────

    def action_cursor_down(self) -> None:
        self.query_one("#actions-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#actions-list", ListView).action_cursor_up()

    # ── Selection ───────────────────────────────────────────────────

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Return the selected action string and close."""
        event.stop()
        item = event.item
        if isinstance(item, _ActionItem):
            self.dismiss(item.action_id)

    def key_enter(self) -> None:
        """Fallback: select the highlighted item on Enter."""
        list_view = self.query_one("#actions-list", ListView)
        if list_view.highlighted_child is not None:
            item = list_view.highlighted_child
            if isinstance(item, _ActionItem):
                self.dismiss(item.action_id)
