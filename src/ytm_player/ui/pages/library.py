"""Library page — content-only track view (sidebar moved to PlaylistSidebar)."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, ClassVar, cast

from textual.app import ComposeResult
from textual.containers import ItemGrid, Vertical, VerticalScroll
from textual.events import Click
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, Label, Static

from ytm_player.config.keymap import Action
from ytm_player.ui.icons import icon
from ytm_player.ui.pages.hero import HERO_CSS, build_hero
from ytm_player.ui.pages.home import CARD_WIDTH, HomeCard, relayout_soon
from ytm_player.ui.track_filter import TRACK_FILTER_CSS, TrackFilterHost
from ytm_player.ui.widgets.cover import best_thumbnail
from ytm_player.ui.widgets.track_table import TrackTable
from ytm_player.utils.formatting import build_playlist_subtitle, normalize_tracks

if TYPE_CHECKING:
    from ytm_player.app._base import YTMHostBase

logger = logging.getLogger(__name__)

GRID_TTL_SECONDS = 600
# The last playlist list the grid showed, so going back to Library is instant,
# as it is for Home. (monotonic time, playlists).
_grid_cache: tuple[float, list[dict[str, Any]]] | None = None


class LibraryPage(TrackFilterHost, Widget):
    """Library content area — displays tracks for the selected playlist.

    The playlist sidebar has been extracted to PlaylistSidebar (persistent).
    This page receives a ``playlist_id`` kwarg from navigation and loads
    the corresponding tracks inline.
    """

    _filter_table_id = "#library-tracks"

    DEFAULT_CSS = (
        """
    LibraryPage {
        height: 1fr;
        width: 1fr;
    }

    #content-header {
        height: auto;
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

    #library-grid-scroll {
        height: 1fr;
        padding: 1 3;
    }

    #library-grid-title {
        text-style: bold;
        height: 1;
        margin-bottom: 1;
    }

    #library-grid {
        height: auto;
        grid-gutter: 1 2;
    }
    """
        + TRACK_FILTER_CSS
        + HERO_CSS
    )

    is_loading: reactive[bool] = reactive(True)

    # Group of a playlist load and the tail fetch it starts: a new load
    # supersedes both, and nothing else on this page cancels them.
    LOAD_GROUP: ClassVar[str] = "library-load"

    def __init__(
        self,
        *,
        playlist_id: str | None = None,
        cursor_row: int | None = None,
        grid: bool = False,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._active_playlist_id: str | None = playlist_id
        # The web app's Library view: every playlist as a cover card.
        self._grid = grid and playlist_id is None
        self._restore_cursor_row: int | None = cursor_row
        self._cached_owner: str = "Unknown"
        self._cached_year: int | str | None = None
        self._cached_privacy: str = ""

    def compose(self) -> ComposeResult:
        yield Vertical(id="content-header")
        with VerticalScroll(id="library-grid-scroll"):
            yield Static("Your playlists", id="library-grid-title")
            yield ItemGrid(id="library-grid", min_column_width=CARD_WIDTH)
        yield Static("Select a playlist from the sidebar", id="empty-state")
        yield Static("Loading...", id="loading-state")
        yield TrackTable(show_album=True, id="library-tracks")
        yield Input(placeholder="/ Filter tracks...", id="track-filter", classes="track-filter")

    def on_mount(self) -> None:
        self.query_one("#loading-state").display = False
        self.query_one("#library-tracks").display = False
        self.query_one("#content-header").display = False
        self.query_one("#library-grid-scroll").display = self._grid
        if self._grid:
            self.query_one("#empty-state").display = False
            self.run_worker(self._load_grid(), group="library-grid", exclusive=True)
            return

        # Auto-load if a playlist_id was provided.
        if self._active_playlist_id:
            self.reload(self._active_playlist_id)
        elif not self._active_playlist_id:
            # Try the currently-playing playlist from the app.
            target_id = getattr(self.app, "_active_library_playlist_id", None)
            if target_id:
                self._active_playlist_id = target_id
                self.reload(target_id)

    def get_nav_state(self) -> dict[str, Any]:
        """Return state to preserve when navigating away."""
        state: dict[str, Any] = {}
        if self._grid:
            return {"grid": True}
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

    @staticmethod
    def invalidate_grid_cache() -> None:
        """Forget the cached playlists, after a playlist is added, removed or renamed."""
        global _grid_cache
        _grid_cache = None

    async def _load_grid(self) -> None:
        """Fill the Library grid with the account's playlists, as cover cards."""
        global _grid_cache
        grid = self.query_one("#library-grid", ItemGrid)
        cached = _grid_cache
        if cached and time.monotonic() - cached[0] < GRID_TTL_SECONDS:
            playlists = cached[1]
        else:
            await grid.mount(Static("Loading your playlists…", classes="home-status"))
            ytmusic = getattr(self.app, "ytmusic", None)
            try:
                playlists = await ytmusic.get_library_playlists(limit=None) if ytmusic else []
            except Exception:
                logger.exception("Failed to load library playlists for the grid")
                playlists = []
            if playlists:
                _grid_cache = (time.monotonic(), playlists)
        await grid.remove_children()
        if not playlists:
            await grid.mount(Static("No playlists yet.", classes="home-status"))
            return
        mount_started = time.monotonic()
        await grid.mount_all(HomeCard(p) for p in playlists)
        logger.debug(
            "library grid: %d cards mounted in %.2fs",
            len(playlists),
            time.monotonic() - mount_started,
        )
        relayout_soon(self)

    # First batch size for progressive playlist loading.
    _FIRST_BATCH = 100  # about 200 tracks in 1.7 s; the rest loads behind it

    def reload(self, playlist_id: str) -> None:
        """Load *playlist_id* as this page's worker.

        Supersedes the load in flight and its tail fetch, so the tracks a
        previous load was still fetching are never appended to this one.
        """
        self.run_worker(
            self.load_playlist(playlist_id),
            name="load-playlist",
            group=self.LOAD_GROUP,
            exclusive=True,
        )

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
            privacy = (data.get("privacy") or "").strip()
            year = data.get("year")
            raw_tracks = data.get("tracks", [])
            tracks = normalize_tracks(raw_tracks)
            track_count = len(tracks)
            total_count = data.get("trackCount") or track_count

            # Cache metadata for use in refresh_header / _fetch_remaining without extra API calls.
            self._cached_owner = owner
            self._cached_year = year
            self._cached_privacy = privacy

            # Store data for radio button — ensure playlistId is set since
            # get_playlist() returns 'id' but _start_playlist_radio expects 'playlistId'.
            self._playlist_data = data
            self._playlist_data.setdefault("playlistId", playlist_id)
            subtitle = build_playlist_subtitle(owner, privacy, year, track_count)
            if total_count > track_count:
                subtitle += f" (loading {total_count} total\u2026)"
            notes = []
            unavailable = len(raw_tracks) - track_count
            if unavailable:
                notes.append(f"{unavailable} unavailable tracks hidden")
            description = (data.get("description") or "").strip()
            await self._mount_header(title, description, subtitle, notes)

            # Load tracks into the table.
            loading.display = False
            table = self.query_one("#library-tracks", TrackTable)
            table.display = True
            table.load_tracks(tracks)

            # Restore cursor position or scroll to the currently-playing track.
            self._restore_track_cursor(table)

            # Land keyboard focus on the table so Tab / j / k have a starting
            # point once the page's data has rendered.
            table.focus()

            # Kick off background fetch for remaining tracks.
            if total_count > len(raw_tracks):
                self.run_worker(
                    self._fetch_remaining(playlist_id, len(raw_tracks)),
                    name="fetch-remaining",
                    group=self.LOAD_GROUP,
                )

        except Exception:
            logger.exception("Failed to load playlist %s", playlist_id)
            loading.display = False
            self.query_one("#empty-state").display = True
            empty = self.query_one("#empty-state", Static)
            empty.update("Failed to load playlist")

    async def refresh_header(self, title: str, description: str, privacy: str) -> None:
        """Update the playlist header in-place after an edit, without reloading tracks."""
        try:
            header = self.query_one("#content-header", Vertical)
            if not header.display:
                return
            table = self.query_one("#library-tracks", TrackTable)
            self._cached_privacy = privacy
            subtitle = build_playlist_subtitle(
                self._cached_owner, privacy, self._cached_year, len(table.tracks)
            )
            await self._mount_header(title, description, subtitle)
        except Exception:
            logger.exception("Failed to refresh library header after edit")

    async def _mount_header(
        self, title: str, description: str, subtitle: str, notes: list[str] | None = None
    ) -> None:
        """The web app's playlist header: cover, title, details, then Play, Shuffle and Radio."""
        header = self.query_one("#content-header", Vertical)
        await header.remove_children()
        header.display = True
        lines = [Label(description, classes="context-description")] if description else []
        self._subtitle_label = Label(subtitle, classes="context-subtitle")
        lines.append(self._subtitle_label)
        lines += [Label(note, classes="context-subtitle") for note in notes or []]
        playlist_id = self._active_playlist_id or ""
        locked = bool(cast("YTMHostBase", self.app).shuffle_prefs.get(playlist_id))
        actions = [
            Static(f"{icon('play')}  Play", id="play-all-btn"),
            Static(f"{icon('shuffle')}  Shuffle", id="shuffle-all-btn"),
            Static(f"{icon('radio')}  Radio", id="start-radio-btn"),
            Static(
                "Shuffle lock: ON" if locked else "Shuffle lock: off",
                id="shuffle-lock-btn",
                classes="locked" if locked else "",
            ),
        ]
        thumbnail = best_thumbnail(getattr(self, "_playlist_data", None) or {}, min_width=544)
        await header.mount(build_hero("Playlist", title, lines, actions, thumbnail))

    def update_track_count(self, delta: int = 0) -> None:
        """Update the subtitle label to reflect the current table track count.

        *delta* is added to the count — useful when the table hasn't been
        updated yet (e.g. after adding tracks via the API).
        """
        try:
            table = self.query_one("#library-tracks", TrackTable)
            track_count = len(table.tracks) + delta
            subtitle = build_playlist_subtitle(
                self._cached_owner, self._cached_privacy, self._cached_year, track_count
            )
            if hasattr(self, "_subtitle_label") and self._subtitle_label:
                self._subtitle_label.update(subtitle)
        except Exception:
            logger.exception("Failed to update library track count label")

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
            self.update_track_count()
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
    # Header button clicks
    # ------------------------------------------------------------------

    def on_click(self, event: Click) -> None:
        """Handle clicks on header action buttons."""
        widget = event.widget
        if widget is None:
            return
        if widget.id in ("play-all-btn", "shuffle-all-btn"):
            event.stop()
            shuffle = widget.id == "shuffle-all-btn"
            self.run_worker(self._play_all(shuffle=shuffle), group="play-all", exclusive=True)
        elif widget.id == "start-radio-btn":
            event.stop()
            data = getattr(self, "_playlist_data", None)
            if data:
                self.run_worker(
                    cast("YTMHostBase", self.app)._start_playlist_radio(data),
                    name="start_radio",
                    group="start-radio",
                    exclusive=True,
                )
        elif widget.id == "shuffle-lock-btn":
            event.stop()
            self._toggle_shuffle_lock()

    async def _play_all(self, *, shuffle: bool) -> None:
        """The header's Play and Shuffle buttons: play the whole playlist from the top."""
        tracks = self.query_one("#library-tracks", TrackTable).tracks
        host = cast("YTMHostBase", self.app)
        if self._active_playlist_id:
            host._active_library_playlist_id = self._active_playlist_id
        await host._replace_queue_and_play(
            list(tracks), entity_id=self._active_playlist_id, start_index=0, shuffle=shuffle
        )

    def _toggle_shuffle_lock(self) -> None:
        """Flip the per-playlist Shuffle lock and update the button label."""
        playlist_id = self._active_playlist_id
        if not playlist_id:
            return
        host = cast("YTMHostBase", self.app)
        currently_locked = bool(host.shuffle_prefs.get(playlist_id))
        new_locked = not currently_locked
        host.shuffle_prefs.set(playlist_id, new_locked)
        # Update button visuals.
        try:
            btn = self.query_one("#shuffle-lock-btn", Static)
            btn.update("Shuffle lock: ON" if new_locked else "Shuffle lock: off")
            if new_locked:
                btn.add_class("locked")
            else:
                btn.remove_class("locked")
        except Exception:
            logger.debug("Failed to update shuffle-lock-btn label", exc_info=True)
        # If we just locked AND the queue is currently this playlist, force
        # shuffle on; if we unlocked, leave shuffle as-is (lock is one-way
        # enforcement on entry, not a toggle of current state).
        if new_locked and host.queue.current_context_id == playlist_id:
            if not host.queue.shuffle_enabled:
                host.queue.toggle_shuffle()
                host._refresh_queue_page()
                try:
                    bar = host.query_one("#playback-bar")
                    bar.update_shuffle(host.queue.shuffle_enabled)  # type: ignore[attr-defined]
                except Exception:
                    pass
        # Refresh the playback-bar shuffle button enabled/disabled state.
        try:
            bar = host.query_one("#playback-bar")
            bar.refresh_shuffle_lock_state()  # type: ignore[attr-defined]
        except Exception:
            logger.debug("Failed to refresh playback-bar lock state", exc_info=True)

    # ------------------------------------------------------------------
    # Track selection → play
    # ------------------------------------------------------------------

    async def on_track_table_track_selected(self, event: TrackTable.TrackSelected) -> None:
        """Queue all tracks and start playback from the selected one."""
        event.stop()
        table = self.query_one("#library-tracks", TrackTable)
        host = cast("YTMHostBase", self.app)
        if self._active_playlist_id:
            host._active_library_playlist_id = self._active_playlist_id
        await host._replace_queue_and_play(
            table.tracks,
            entity_id=self._active_playlist_id,
            start_index=event.index,
            autoplay=False,
        )
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
