"""Playlist picker popup for adding tracks to a playlist."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListView, ListItem, Static
from textual.worker import Worker, WorkerState

logger = logging.getLogger(__name__)

RECENT_PLAYLISTS_FILE = Path.home() / ".config" / "ytm-player" / "recent_playlists.json"
MAX_RECENT = 20


def _load_recent_ids() -> list[str]:
    """Load recently-used playlist IDs from disk."""
    try:
        if RECENT_PLAYLISTS_FILE.exists():
            data = json.loads(RECENT_PLAYLISTS_FILE.read_text())
            if isinstance(data, list):
                return data[:MAX_RECENT]
    except Exception:
        logger.debug("Could not load recent playlists", exc_info=True)
    return []


def _save_recent_ids(ids: list[str]) -> None:
    """Persist recently-used playlist IDs to disk."""
    try:
        RECENT_PLAYLISTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        RECENT_PLAYLISTS_FILE.write_text(json.dumps(ids[:MAX_RECENT]))
    except Exception:
        logger.debug("Could not save recent playlists", exc_info=True)


def _record_recent(playlist_id: str) -> None:
    """Move *playlist_id* to the front of the recent list and persist."""
    recent = _load_recent_ids()
    if playlist_id in recent:
        recent.remove(playlist_id)
    recent.insert(0, playlist_id)
    _save_recent_ids(recent)


class _PlaylistItem(ListItem):
    """A single playlist entry in the picker list."""

    def __init__(self, playlist_id: str, title: str, count: str = "") -> None:
        super().__init__()
        self.playlist_id = playlist_id
        self._title = title
        self._count = count

    def compose(self) -> ComposeResult:
        text = self._title
        if self._count:
            text = f"{self._title}  ({self._count})"
        yield Label(text)


class _CreateNewItem(ListItem):
    """Sentinel entry for creating a new playlist."""

    def __init__(self) -> None:
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Label("[+] Create New Playlist")


class PlaylistPicker(ModalScreen[str | None]):
    """Select a playlist to add track(s) to.

    Returns the playlist ID on success, or ``None`` if cancelled.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Close", show=False),
    ]

    DEFAULT_CSS = """
    PlaylistPicker {
        align: center middle;
    }

    PlaylistPicker > Vertical {
        width: 50;
        max-height: 80%;
        background: $surface;
        border: round $primary;
        padding: 1 2;
    }

    PlaylistPicker #picker-title {
        text-align: center;
        text-style: bold;
        width: 100%;
        color: $text;
    }

    PlaylistPicker #picker-status {
        text-align: center;
        width: 100%;
        color: $text-muted;
        margin-bottom: 1;
    }

    PlaylistPicker #filter-input {
        margin-bottom: 1;
    }

    PlaylistPicker ListView {
        height: auto;
        max-height: 18;
        background: $surface;
    }

    PlaylistPicker ListItem {
        padding: 0 1;
        height: 1;
    }

    PlaylistPicker ListItem:hover {
        background: $accent 30%;
    }

    PlaylistPicker ListView:focus > ListItem.--highlight {
        background: $primary 40%;
    }

    PlaylistPicker #new-playlist-input {
        display: none;
    }

    PlaylistPicker #new-playlist-input.visible {
        display: block;
        margin-top: 1;
    }
    """

    def __init__(self, video_ids: list[str]) -> None:
        super().__init__()
        self.video_ids = video_ids
        self._playlists: list[dict[str, Any]] = []
        self._creating_new: bool = False

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Add to Playlist", id="picker-title")
            yield Static("Loading playlists...", id="picker-status")
            yield Input(placeholder="Filter playlists...", id="filter-input")
            yield ListView(id="playlist-list")
            yield Input(
                placeholder="New playlist name...",
                id="new-playlist-input",
            )

    def on_mount(self) -> None:
        self.query_one("#filter-input", Input).display = False
        self._load_playlists()

    # ── Data loading ────────────────────────────────────────────────

    def _load_playlists(self) -> None:
        """Kick off an async worker to fetch user playlists."""
        self.run_worker(self._fetch_playlists(), name="fetch_playlists")

    async def _fetch_playlists(self) -> list[dict[str, Any]]:
        try:
            playlists = await self.app.ytmusic.get_library_playlists(limit=50)
            return playlists
        except Exception:
            logger.exception("Failed to fetch library playlists")
            return []

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name != "fetch_playlists":
            return

        if event.state == WorkerState.SUCCESS:
            self._playlists = event.worker.result or []
            self._sort_by_recent()
            self._populate_list()
        elif event.state == WorkerState.ERROR:
            status = self.query_one("#picker-status", Static)
            status.update("Failed to load playlists")

    def _sort_by_recent(self) -> None:
        """Sort playlists so recently-used ones appear first."""
        recent_ids = _load_recent_ids()
        if not recent_ids:
            return

        recent_set = set(recent_ids)
        recent_order = {pid: i for i, pid in enumerate(recent_ids)}

        def sort_key(pl: dict) -> tuple[int, int]:
            pid = pl.get("playlistId", pl.get("id", ""))
            if pid in recent_set:
                return (0, recent_order[pid])
            return (1, 0)

        self._playlists.sort(key=sort_key)

    def _populate_list(self, filter_text: str = "") -> None:
        """Rebuild the ListView with current playlists, optionally filtered."""
        status = self.query_one("#picker-status", Static)
        list_view = self.query_one("#playlist-list", ListView)
        filter_input = self.query_one("#filter-input", Input)

        list_view.clear()

        # Always show "Create New" at the top.
        list_view.append(_CreateNewItem())

        if not self._playlists:
            status.update("No playlists found")
            filter_input.display = False
        else:
            count = len(self._playlists)
            status.update(f"{count} playlist{'s' if count != 1 else ''}")
            filter_input.display = True

        query = filter_text.strip().lower()

        for pl in self._playlists:
            title = pl.get("title", "Untitled")
            if query and query not in title.lower():
                continue
            playlist_id = pl.get("playlistId", pl.get("id", ""))
            track_count = pl.get("count", "")
            if track_count:
                track_count = str(track_count)
            list_view.append(_PlaylistItem(playlist_id, title, track_count))

        list_view.focus()

    # ── Filtering ───────────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter-input":
            self._populate_list(event.value)

    # ── Selection ───────────────────────────────────────────────────

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        item = event.item

        if isinstance(item, _CreateNewItem):
            self._show_create_input()
            return

        if isinstance(item, _PlaylistItem):
            self._add_to_playlist(item.playlist_id, item._title)

    def _show_create_input(self) -> None:
        """Toggle the new-playlist name input."""
        self._creating_new = True
        new_input = self.query_one("#new-playlist-input", Input)
        new_input.add_class("visible")
        new_input.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "new-playlist-input":
            name = event.value.strip()
            if not name:
                self.notify("Playlist name cannot be empty", severity="warning")
                return
            self.run_worker(
                self._create_and_add(name),
                name="create_playlist",
            )

    async def _create_and_add(self, name: str) -> None:
        """Create a new playlist, then add the tracks to it."""
        status = self.query_one("#picker-status", Static)
        status.update(f"Creating '{name}'...")

        try:
            playlist_id = await self.app.ytmusic.create_playlist(name)
            if not playlist_id:
                self.notify("Failed to create playlist", severity="error")
                status.update("Creation failed")
                return

            status.update(f"Adding tracks to '{name}'...")
            await self.app.ytmusic.add_playlist_items(playlist_id, self.video_ids)

            _record_recent(playlist_id)

            track_word = "track" if len(self.video_ids) == 1 else "tracks"
            self.notify(
                f"Added {len(self.video_ids)} {track_word} to '{name}'",
                severity="information",
            )
            self.dismiss(playlist_id)

        except Exception:
            logger.exception("Failed to create playlist and add tracks")
            self.notify("Failed to create playlist", severity="error")
            status.update("Error")

    def _add_to_playlist(self, playlist_id: str, title: str) -> None:
        """Add tracks to an existing playlist."""
        self.run_worker(
            self._do_add(playlist_id, title),
            name="add_to_playlist",
        )

    async def _do_add(self, playlist_id: str, title: str) -> None:
        status = self.query_one("#picker-status", Static)
        status.update(f"Adding to '{title}'...")

        try:
            await self.app.ytmusic.add_playlist_items(playlist_id, self.video_ids)

            _record_recent(playlist_id)

            track_word = "track" if len(self.video_ids) == 1 else "tracks"
            self.notify(
                f"Added {len(self.video_ids)} {track_word} to '{title}'",
                severity="information",
            )
            self.dismiss(playlist_id)

        except Exception:
            logger.exception("Failed to add tracks to playlist %r", playlist_id)
            self.notify(f"Failed to add to '{title}'", severity="error")
            status.update("Error")

    # ── Cancel ──────────────────────────────────────────────────────

    def action_cancel(self) -> None:
        """Close the picker without selecting anything."""
        if self._creating_new:
            # First press: hide the create input and return to the list.
            self._creating_new = False
            new_input = self.query_one("#new-playlist-input", Input)
            new_input.remove_class("visible")
            new_input.value = ""
            self.query_one("#playlist-list", ListView).focus()
        else:
            self.dismiss(None)
