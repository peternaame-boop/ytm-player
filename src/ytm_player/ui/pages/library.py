"""Library page — content-only track view (sidebar moved to PlaylistSidebar)."""

from __future__ import annotations

import logging
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label, Static

from ytm_player.config.keymap import Action
from ytm_player.ui.widgets.track_table import TrackTable
from ytm_player.utils.formatting import normalize_tracks

logger = logging.getLogger(__name__)


class LibraryPage(Widget):
    """Library content area — displays tracks for the selected playlist.

    The playlist sidebar has been extracted to PlaylistSidebar (persistent).
    This page receives a ``playlist_id`` kwarg from navigation and loads
    the corresponding tracks inline.
    """

    DEFAULT_CSS = """
    LibraryPage {
        height: 1fr;
        width: 1fr;
    }

    #content-header {
        height: auto;
        max-height: 5;
        padding: 1 2;
    }

    .content-title {
        text-style: bold;
    }

    .content-subtitle {
        color: $text-muted;
    }

    #empty-state {
        width: 1fr;
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
    }

    #loading-state {
        width: 1fr;
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
    }

    #library-tracks {
        height: 1fr;
    }
    """

    is_loading: reactive[bool] = reactive(True)

    def __init__(
        self,
        *,
        playlist_id: str | None = None,
        cursor_row: int | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._active_playlist_id: str | None = playlist_id
        self._restore_cursor_row: int | None = cursor_row

    def compose(self) -> ComposeResult:
        yield Vertical(id="content-header")
        yield Static("Select a playlist from the sidebar", id="empty-state")
        yield Static("Loading...", id="loading-state")
        yield TrackTable(show_album=True, id="library-tracks")

    def on_mount(self) -> None:
        self.query_one("#loading-state").display = False
        self.query_one("#library-tracks").display = False
        self.query_one("#content-header").display = False

        # Auto-load if a playlist_id was provided.
        if self._active_playlist_id:
            self.run_worker(
                self.load_playlist(self._active_playlist_id),
                name="load-playlist",
                exclusive=True,
            )
        elif not self._active_playlist_id:
            # Try the currently-playing playlist from the app.
            target_id = getattr(self.app, "_active_library_playlist_id", None)
            if target_id:
                self._active_playlist_id = target_id
                self.run_worker(
                    self.load_playlist(target_id),
                    name="load-playlist",
                    exclusive=True,
                )

    def get_nav_state(self) -> dict[str, Any]:
        """Return state to preserve when navigating away."""
        state: dict[str, Any] = {}
        if self._active_playlist_id:
            state["playlist_id"] = self._active_playlist_id
        try:
            table = self.query_one("#library-tracks", TrackTable)
            if table.display and table.cursor_row is not None:
                state["cursor_row"] = table.cursor_row
        except Exception:
            pass
        return state

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    async def load_playlist(self, playlist_id: str) -> None:
        """Fetch and display a playlist's tracks."""
        self._active_playlist_id = playlist_id

        # Show loading state.
        self.query_one("#empty-state").display = False
        self.query_one("#library-tracks").display = False
        self.query_one("#content-header").display = False
        loading = self.query_one("#loading-state")
        loading.display = True

        try:
            data = await self.app.ytmusic.get_playlist(playlist_id, order="recently_added")

            # If user selected a different playlist while we were loading, discard.
            if self._active_playlist_id != playlist_id:
                return

            title = data.get("title", "Unknown Playlist")
            author = data.get("author", {})
            owner = author.get("name", "Unknown") if isinstance(author, dict) else str(author)
            tracks = data.get("tracks", [])
            track_count = data.get("trackCount", len(tracks))

            # Update header.
            header = self.query_one("#content-header", Vertical)
            await header.remove_children()
            header.display = True
            await header.mount(Label(title, classes="content-title"))
            subtitle = f"{owner} \u00b7 {track_count} track{'s' if track_count != 1 else ''}"
            await header.mount(Label(subtitle, classes="content-subtitle"))

            # Load tracks into the table.
            loading.display = False
            table = self.query_one("#library-tracks", TrackTable)
            table.display = True
            table.load_tracks(normalize_tracks(tracks))

            # Restore cursor position or scroll to the currently-playing track.
            self._restore_track_cursor(table)

        except Exception:
            logger.exception("Failed to load playlist %s", playlist_id)
            loading.display = False
            self.query_one("#empty-state").display = True
            empty = self.query_one("#empty-state", Static)
            empty.update("Failed to load playlist")

    def _restore_track_cursor(self, table: TrackTable) -> None:
        """Move cursor to the saved row, or to the currently-playing track."""
        if table.row_count == 0:
            return

        row = self._restore_cursor_row
        self._restore_cursor_row = None

        if row is not None and 0 <= row < table.row_count:
            table.move_cursor(row=row)
            return

        # Fall back to the currently-playing track.
        playing_id = getattr(self.app, "player", None) and getattr(
            self.app.player, "_current_track", None
        )
        if playing_id and isinstance(playing_id, dict):
            playing_id = playing_id.get("video_id")

        if not playing_id:
            queue = getattr(self.app, "queue", None)
            if queue:
                current = queue.current_track
                if current:
                    playing_id = current.get("video_id")

        if playing_id:
            for i, track in enumerate(table._tracks):
                if track.get("video_id") == playing_id:
                    table.move_cursor(row=i)
                    return

    # ------------------------------------------------------------------
    # Track selection → play
    # ------------------------------------------------------------------

    async def on_track_table_track_selected(self, event: TrackTable.TrackSelected) -> None:
        """Queue all tracks and start playback from the selected one."""
        event.stop()
        table = self.query_one("#library-tracks", TrackTable)
        tracks = table.tracks
        idx = event.index

        self.app.queue.clear()
        self.app.queue.add_multiple(tracks)
        self.app.queue.jump_to_real(idx)
        if self._active_playlist_id:
            self.app._active_library_playlist_id = self._active_playlist_id  # type: ignore[attr-defined]
        await self.app.play_track(event.track)

    # ------------------------------------------------------------------
    # Vim-style action handler
    # ------------------------------------------------------------------

    async def handle_action(self, action: Action, count: int = 1) -> None:
        """Process vim-style navigation actions dispatched from the app."""
        try:
            table = self.query_one("#library-tracks", TrackTable)
            if table.display:
                await table.handle_action(action, count)
        except Exception:
            logger.debug("Failed to dispatch action to library track table", exc_info=True)
