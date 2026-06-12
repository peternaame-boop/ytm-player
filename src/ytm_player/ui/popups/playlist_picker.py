"""Playlist picker popup for adding tracks to a playlist."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView, Static
from textual.worker import Worker, WorkerState

from ytm_player.config.paths import RECENT_PLAYLISTS_FILE
from ytm_player.ui.popups.confirm_popup import ConfirmPopup
from ytm_player.ui.popups.create_playlist_popup import CreatePlaylistPopup

if TYPE_CHECKING:
    from ytm_player.app._base import YTMHostBase

logger = logging.getLogger(__name__)
MAX_RECENT = 20


def _load_recent_ids() -> list[str]:
    """Load recently-used playlist IDs from disk."""
    try:
        if RECENT_PLAYLISTS_FILE.exists():
            data = json.loads(RECENT_PLAYLISTS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data[:MAX_RECENT]
    except Exception:
        logger.debug("Could not load recent playlists", exc_info=True)
    return []


def _save_recent_ids(ids: list[str]) -> None:
    """Persist recently-used playlist IDs to disk."""
    try:
        RECENT_PLAYLISTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        RECENT_PLAYLISTS_FILE.write_text(json.dumps(ids[:MAX_RECENT]), encoding="utf-8")
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

    """

    def __init__(self, video_ids: list[str], tracks: list[dict[str, Any]] | None = None) -> None:
        super().__init__()
        self.video_ids = video_ids
        self.tracks = tracks or []
        self._playlists: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Add to Playlist", id="picker-title")
            yield Static("Loading playlists...", id="picker-status")
            yield Input(placeholder="Filter playlists...", id="filter-input")
            yield ListView(id="playlist-list")

    def on_mount(self) -> None:
        self.query_one("#filter-input", Input).display = False
        self._load_playlists()

    # ── Data loading ────────────────────────────────────────────────

    def _load_playlists(self) -> None:
        """Kick off an async worker to fetch user playlists."""
        self.run_worker(self._fetch_playlists(), name="fetch_playlists")

    async def _fetch_playlists(self) -> list[dict[str, Any]]:
        try:
            ytmusic = cast("YTMHostBase", self.app).ytmusic
            assert ytmusic is not None
            playlists = await ytmusic.get_library_playlists(limit=50)
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
            self.app.push_screen(CreatePlaylistPopup(), self._on_create_result)
            return

        if isinstance(item, _PlaylistItem):
            self._add_to_playlist(item.playlist_id, item._title)

    def _on_create_result(self, result: tuple[str, str, str] | None) -> None:
        """Handle the result from CreatePlaylistPopup."""
        if result is None:
            return
        name, description, privacy = result
        self.run_worker(
            self._create_and_add(name, description, privacy),
            name="create_playlist",
        )

    async def _create_and_add(
        self, name: str, description: str = "", privacy: str = "PRIVATE"
    ) -> None:
        """Create a new playlist, then add the tracks to it."""
        status = self.query_one("#picker-status", Static)
        status.update(f"Creating '{name}'...")

        try:
            ytmusic = cast("YTMHostBase", self.app).ytmusic
            assert ytmusic is not None
            playlist_id = await ytmusic.create_playlist(
                name, description=description, privacy=privacy
            )
            if not playlist_id:
                self.notify("Failed to create playlist", severity="error")
                status.update("Creation failed")
                return

            status.update(f"Adding tracks to '{name}'...")
            from ytm_player.services.ytmusic import mutation_failure_suffix

            result = await ytmusic.add_playlist_items(playlist_id, self.video_ids)
            if result != "success":
                self.notify(
                    f"Created '{name}' but couldn't add tracks — {mutation_failure_suffix(result)}",
                    severity="warning",
                )
                status.update("Add failed")
                return

            _record_recent(playlist_id)

            # Full sidebar refresh — the new playlist doesn't exist in the
            # cached items yet, so update_item_count would silently be a no-op.
            try:
                from ytm_player.ui.sidebars.playlist_sidebar import PlaylistSidebar

                ps = self.app.query_one("#playlist-sidebar", PlaylistSidebar)
                await ps.refresh_playlists()
            except Exception:
                logger.exception("Sidebar refresh failed after create")

            # Update library page header if the target playlist is currently open.
            try:
                from ytm_player.ui.pages.library import LibraryPage
                from ytm_player.utils.formatting import strip_vl_prefix

                host = cast("YTMHostBase", self.app)
                current_pid = host._current_page_kwargs.get("playlist_id", "")
                if host._current_page == "library" and strip_vl_prefix(
                    current_pid
                ) == strip_vl_prefix(playlist_id):
                    library = self.app.query_one(LibraryPage)
                    library.update_track_count(+len(self.video_ids))
            except Exception:
                logger.exception("Library track count update failed")

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

    def _tracks_for_append(self, set_video_ids: dict[str, str]) -> list[dict[str, Any]]:
        """Build the track dicts to append to the open playlist's table.

        ``self.tracks`` are usually already-normalized dicts (the playing or
        focused track). Re-running ``normalize_tracks()`` on those would drop
        ``thumbnail_url`` — it reads the raw ``thumbnails`` key that normalized
        dicts no longer carry — so only raw dicts are normalized here.

        *set_video_ids* maps videoId -> server-assigned setVideoId from the add
        response; appended rows are stamped with it so "Remove from Playlist"
        works immediately. Rows still missing a setVideoId are flagged so the
        remove action can explain a reload is needed.
        """
        from ytm_player.utils.formatting import normalize_tracks

        out: list[dict[str, Any]] = []
        for src in self.tracks:
            if "video_id" in src:
                track = dict(src)
            else:
                normed = normalize_tracks([src])
                if not normed:
                    continue
                track = normed[0]
            svid = set_video_ids.get(track.get("video_id", ""))
            if svid:
                track["setVideoId"] = svid
            elif not track.get("setVideoId"):
                track["_needs_reload_for_removal"] = True
            out.append(track)
        return out

    def _add_to_playlist(self, playlist_id: str, title: str, duplicates: bool = False) -> None:
        """Add tracks to an existing playlist."""
        self.run_worker(
            self._do_add(playlist_id, title, duplicates=duplicates),
            name="add_to_playlist",
        )

    async def _do_add(self, playlist_id: str, title: str, duplicates: bool = False) -> None:
        status = self.query_one("#picker-status", Static)
        status.update(f"Adding to '{title}'...")

        try:
            from ytm_player.services.ytmusic import mutation_failure_suffix

            ytmusic = cast("YTMHostBase", self.app).ytmusic
            assert ytmusic is not None
            result = await ytmusic.add_playlist_items(
                playlist_id, self.video_ids, duplicates=duplicates
            )
            if result == "duplicate":
                status.update("Already in playlist")
                track_word = "track is" if len(self.video_ids) == 1 else "tracks are"

                def _on_duplicate_confirm(confirmed: bool | None) -> None:
                    if confirmed:
                        self._add_to_playlist(playlist_id, title, duplicates=True)
                    else:
                        self.dismiss(None)

                self.app.push_screen(
                    ConfirmPopup(
                        f"This {track_word} already in '{title}'.\nAdd anyway?",
                        confirm_label="Add anyway",
                        cancel_label="Cancel",
                    ),
                    _on_duplicate_confirm,
                )
                return
            if result != "success":
                self.notify(
                    f"Couldn't add to '{title}' — {mutation_failure_suffix(result)}",
                    severity="error",
                )
                status.update("Error")
                return

            _record_recent(playlist_id)

            # Optimistic sidebar count update — before next library reload.
            try:
                from ytm_player.ui.sidebars.playlist_sidebar import LibraryPanel

                panel = self.app.query_one("#ps-playlists", LibraryPanel)
                panel.update_item_count(playlist_id, +len(self.video_ids))
            except Exception:
                logger.exception("Sidebar count update failed")

            # Append tracks to the table and update header if playlist is open.
            try:
                from ytm_player.ui.pages.library import LibraryPage
                from ytm_player.ui.widgets.track_table import TrackTable
                from ytm_player.utils.formatting import strip_vl_prefix

                host = cast("YTMHostBase", self.app)
                current_pid = host._current_page_kwargs.get("playlist_id", "")
                if host._current_page == "library" and strip_vl_prefix(
                    current_pid
                ) == strip_vl_prefix(playlist_id):
                    library = self.app.query_one(LibraryPage)
                    if self.tracks:
                        table = library.query_one("#library-tracks", TrackTable)
                        table.append_tracks(
                            self._tracks_for_append(ytmusic.last_added_set_video_ids)
                        )
                        library.update_track_count()
                    else:
                        library.update_track_count(+len(self.video_ids))
            except Exception:
                logger.exception("Library track/count update failed")

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
        self.dismiss(None)
