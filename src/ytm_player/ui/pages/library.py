"""Library page — content-only track view (sidebar moved to PlaylistSidebar)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Click
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, Label, Static

from ytm_player.config.keymap import Action
from ytm_player.ui.widgets.track_table import TrackTable
from ytm_player.utils.formatting import normalize_tracks

if TYPE_CHECKING:
    from ytm_player.app._base import YTMHostBase

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

    .content-title-row {
        height: auto;
        width: 1fr;
    }
    .content-title-row Label {
        width: auto;
    }
    .content-title {
        text-style: bold;
    }
    .content-subtitle {
        color: $text-muted;
    }
    #start-radio-btn {
        width: auto;
        min-width: 14;
        height: 1;
        margin: 0 0 0 1;
        padding: 0 1;
        color: $primary;
    }
    #start-radio-btn:hover {
        background: $primary 30%;
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

    .track-filter {
        dock: bottom;
        display: none;
    }

    .track-filter.visible {
        display: block;
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
        yield Input(placeholder="/ Filter tracks...", id="track-filter", classes="track-filter")

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

    def on_remove(self) -> None:
        """Cancel background workers (e.g. _fetch_remaining) when page is removed."""
        for worker in self.workers:
            worker.cancel()

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

    # First batch size for progressive playlist loading.
    _FIRST_BATCH = 300

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
            ytmusic = cast("YTMHostBase", self.app).ytmusic
            assert ytmusic is not None
            data = await ytmusic.get_playlist(
                playlist_id, limit=self._FIRST_BATCH, order="recently_added"
            )

            # If user selected a different playlist while we were loading, discard.
            if self._active_playlist_id != playlist_id:
                return

            title = data.get("title", "Unknown Playlist")
            author = data.get("author", {})
            owner = author.get("name", "Unknown") if isinstance(author, dict) else str(author)
            raw_tracks = data.get("tracks", [])
            tracks = normalize_tracks(raw_tracks)
            track_count = len(tracks)
            total_count = data.get("trackCount") or track_count

            # Store data for radio button — ensure playlistId is set since
            # get_playlist() returns 'id' but _start_playlist_radio expects 'playlistId'.
            self._playlist_data = data
            self._playlist_data.setdefault("playlistId", playlist_id)

            # Update header.
            header = self.query_one("#content-header", Vertical)
            await header.remove_children()
            header.display = True
            title_row = Horizontal(classes="content-title-row")
            await header.mount(title_row)
            await title_row.mount(Label(title, classes="content-title"))
            await title_row.mount(Static("[▶ Start Radio]", id="start-radio-btn", markup=True))
            subtitle = f"{owner} \u00b7 {track_count} track{'s' if track_count != 1 else ''}"
            if total_count > track_count:
                subtitle += f" (loading {total_count} total\u2026)"
            self._subtitle_label = Label(subtitle, classes="content-subtitle")
            await header.mount(self._subtitle_label)
            unavailable = len(raw_tracks) - track_count
            if unavailable:
                await header.mount(
                    Label(f"{unavailable} unavailable tracks hidden", classes="content-subtitle")
                )

            # Load tracks into the table.
            loading.display = False
            table = self.query_one("#library-tracks", TrackTable)
            table.display = True
            table.load_tracks(tracks)

            # Restore cursor position or scroll to the currently-playing track.
            self._restore_track_cursor(table)

            # Kick off background fetch for remaining tracks.
            if total_count > len(raw_tracks):
                self.run_worker(
                    self._fetch_remaining(playlist_id, len(raw_tracks)),
                    name="fetch-remaining",
                )

        except Exception:
            logger.exception("Failed to load playlist %s", playlist_id)
            loading.display = False
            self.query_one("#empty-state").display = True
            empty = self.query_one("#empty-state", Static)
            empty.update("Failed to load playlist")

    async def _fetch_remaining(self, playlist_id: str, already_have: int) -> None:
        """Background fetch for tracks beyond the first batch."""
        ytmusic = cast("YTMHostBase", self.app).ytmusic
        assert ytmusic is not None
        remaining = await ytmusic.get_playlist_remaining(
            playlist_id, already_have, order="recently_added"
        )
        # Discard if user switched playlists while we were fetching.
        if self._active_playlist_id != playlist_id:
            return
        if not remaining:
            return
        tracks = normalize_tracks(remaining)
        try:
            table = self.query_one("#library-tracks", TrackTable)
            table.append_tracks(tracks)
            # Update subtitle with final count.
            total = len(table.tracks)
            if hasattr(self, "_subtitle_label"):
                self._subtitle_label.update(f"{total} track{'s' if total != 1 else ''}")
        except Exception:
            logger.debug("Failed to append remaining tracks in library", exc_info=True)

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
        player = cast("YTMHostBase", self.app).player
        playing_id = getattr(player, "_current_track", None) if player is not None else None
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
    # Track filter
    # ------------------------------------------------------------------

    def on_track_table_filter_requested(self, event: TrackTable.FilterRequested) -> None:
        try:
            f = self.query_one("#track-filter", Input)
            f.value = ""
            f.add_class("visible")
            f.focus()
        except Exception:
            pass

    def on_track_table_filter_closed(self, event: TrackTable.FilterClosed) -> None:
        try:
            f = self.query_one("#track-filter", Input)
            f.remove_class("visible")
            self.query_one("#library-tracks", TrackTable).focus()
        except Exception:
            pass

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "track-filter":
            self.query_one("#library-tracks", TrackTable).apply_filter(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "track-filter":
            f = self.query_one("#track-filter", Input)
            f.remove_class("visible")
            self.query_one("#library-tracks", TrackTable).focus()

    def on_key(self, event: object) -> None:
        """Handle Escape in filter input."""
        from textual.events import Key

        if not isinstance(event, Key):
            return
        if event.key == "escape":
            try:
                f = self.query_one("#track-filter", Input)
                if f.has_class("visible"):
                    event.stop()
                    event.prevent_default()
                    self.query_one("#library-tracks", TrackTable).clear_filter()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Header button clicks
    # ------------------------------------------------------------------

    def on_click(self, event: Click) -> None:
        """Handle clicks on header action buttons."""
        if event.widget.id == "start-radio-btn":
            event.stop()
            data = getattr(self, "_playlist_data", None)
            if data:
                self.run_worker(
                    self.app._start_playlist_radio(data),  # type: ignore[attr-defined]
                    name="start_radio",
                    exclusive=True,
                )

    # ------------------------------------------------------------------
    # Track selection → play
    # ------------------------------------------------------------------

    async def on_track_table_track_selected(self, event: TrackTable.TrackSelected) -> None:
        """Queue all tracks and start playback from the selected one."""
        event.stop()
        table = self.query_one("#library-tracks", TrackTable)
        tracks = table.tracks
        idx = event.index

        host = cast("YTMHostBase", self.app)
        host.queue.clear()
        host.queue.add_multiple(tracks)
        host.queue.jump_to_real(idx)
        if self._active_playlist_id:
            host._active_library_playlist_id = self._active_playlist_id
        await host.play_track(event.track)

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
