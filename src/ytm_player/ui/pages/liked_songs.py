"""Liked Songs page showing the user's liked music."""

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


class LikedSongsPage(Widget):
    """Displays the user's Liked Music playlist."""

    DEFAULT_CSS = """
    LikedSongsPage {
        layout: vertical;
        width: 1fr;
        height: 1fr;
    }
    .liked-header {
        height: auto;
        max-height: 3;
        padding: 1 2;
        background: $surface;
    }
    .liked-header-row {
        height: auto;
        width: 1fr;
    }
    .liked-header-row Label {
        width: auto;
    }
    .liked-header-title {
        text-style: bold;
        color: $primary;
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
    .liked-footer {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        dock: bottom;
    }
    .liked-loading {
        width: 1fr;
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
    }
    .track-filter {
        dock: bottom;
        display: none;
    }
    .track-filter.visible {
        display: block;
    }
    """

    track_count: reactive[int] = reactive(0)

    def __init__(self, *, cursor_row: int | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._restore_cursor_row = cursor_row

    def compose(self) -> ComposeResult:
        with Vertical(id="liked-header", classes="liked-header"):
            with Horizontal(classes="liked-header-row"):
                yield Label("Liked Songs", classes="liked-header-title")
                yield Static("[▶ Start Radio]", id="start-radio-btn", markup=True)
        yield Label("Loading liked songs...", id="liked-loading", classes="liked-loading")
        yield TrackTable(show_album=False, id="liked-table")
        yield Static("", id="liked-footer", classes="liked-footer")
        yield Input(placeholder="/ Filter tracks...", id="track-filter", classes="track-filter")

    def on_mount(self) -> None:
        self.query_one("#liked-table", TrackTable).display = False
        self.run_worker(self._load_liked_songs(), group="liked-load")

    # First batch size for progressive loading.
    _FIRST_BATCH = 300

    async def _load_liked_songs(self) -> None:
        ytmusic = self.app.ytmusic  # type: ignore[attr-defined]
        if not ytmusic:
            self.query_one("#liked-loading", Label).update("YouTube Music not connected.")
            return

        try:
            raw_tracks = await ytmusic.get_liked_songs(limit=self._FIRST_BATCH)
            tracks = normalize_tracks(raw_tracks)
        except Exception:
            logger.exception("Failed to load liked songs")
            tracks = []

        self._display_tracks(tracks)

        # Kick off background fetch if the first batch was full (likely more tracks).
        if len(tracks) >= self._FIRST_BATCH:
            self._update_footer(loading_more=True)
            self.run_worker(self._fetch_remaining_liked(), group="liked-remaining")

    def _display_tracks(self, tracks: list[dict]) -> None:
        table = self.query_one("#liked-table", TrackTable)
        loading = self.query_one("#liked-loading", Label)

        if not tracks:
            table.display = False
            loading.update("No liked songs found.")
            loading.display = True
            return

        loading.display = False
        table.display = True
        table.load_tracks(tracks)

        self.track_count = len(tracks)
        self._update_footer()

        # Restore cursor position from navigation state.
        row = self._restore_cursor_row
        self._restore_cursor_row = None
        if row is not None and 0 <= row < table.row_count:
            table.move_cursor(row=row)

    def _update_footer(self, loading_more: bool = False) -> None:
        try:
            table = self.query_one("#liked-table", TrackTable)
            footer = self.query_one("#liked-footer", Static)
            total = table.track_count
            text = f"{total} liked songs"
            if loading_more:
                text += " (loading more…)"
            footer.update(text)
        except Exception:
            pass

    async def _fetch_remaining_liked(self) -> None:
        """Background fetch for liked songs beyond the first batch."""
        from ytm_player.services.ytmusic import YTMusicService

        ytmusic = self.app.ytmusic  # type: ignore[attr-defined]
        if not ytmusic:
            return
        try:
            remaining_raw = await ytmusic.get_liked_songs(
                limit=None, timeout=YTMusicService._LARGE_PLAYLIST_TIMEOUT
            )
        except Exception:
            logger.debug("Background fetch for remaining liked songs failed", exc_info=True)
            self._update_footer()
            return

        table = self.query_one("#liked-table", TrackTable)
        already_have = table.track_count

        remaining_raw = remaining_raw[already_have:]
        if not remaining_raw:
            self._update_footer()
            return

        remaining = normalize_tracks(remaining_raw)
        table.append_tracks(remaining)
        self.track_count = table.track_count
        self._update_footer()

    def get_nav_state(self) -> dict[str, Any]:
        """Return state to preserve when navigating away."""
        state: dict[str, Any] = {}
        try:
            table = self.query_one("#liked-table", TrackTable)
            if table.cursor_row is not None and table.cursor_row > 0:
                state["cursor_row"] = table.cursor_row
        except Exception:
            pass
        return state

    async def on_track_table_track_selected(self, event: TrackTable.TrackSelected) -> None:
        event.stop()
        table = self.query_one("#liked-table", TrackTable)
        tracks = table.tracks
        host = cast("YTMHostBase", self.app)
        host.queue.clear()
        host.queue.add_multiple(tracks)
        host.queue.jump_to_real(event.index)
        self._apply_shuffle_pref(host.queue)
        await host.play_track(event.track)

    _CONTEXT_ID = "__LIKED_SONGS__"

    def _apply_shuffle_pref(self, queue: Any) -> None:
        """Set queue context to the Liked Songs sentinel and restore shuffle pref."""
        queue.set_context(self._CONTEXT_ID)
        prefs = self.app.shuffle_prefs  # type: ignore[attr-defined]
        saved = prefs.get(self._CONTEXT_ID)
        if saved is not None and queue.shuffle_enabled != saved:
            queue.toggle_shuffle()

    async def handle_action(self, action: Action, count: int = 1) -> None:
        table = self.query_one("#liked-table", TrackTable)

        match action:
            case Action.ADD_TO_QUEUE:
                track = table.selected_track
                if track:
                    host = cast("YTMHostBase", self.app)
                    host.queue.add(track)
                    self.app.notify("Added to queue", timeout=2)
            case Action.TRACK_ACTIONS:
                track = table.selected_track
                if track:
                    host = cast("YTMHostBase", self.app)
                    host._open_actions_for_track(track)
            case _:
                await table.handle_action(action, count)

    def on_click(self, event: Click) -> None:
        if event.widget is not None and event.widget.id == "start-radio-btn":
            event.stop()
            self.run_worker(self._start_radio(), name="start_radio", exclusive=True)

    async def _start_radio(self) -> None:
        import random

        table = self.query_one("#liked-table", TrackTable)
        tracks = table.tracks
        if not tracks:
            return
        seeds = random.sample(tracks, min(5, len(tracks)))
        seed_list = "\n".join(f"  • {s.get('title', 'Unknown')}" for s in seeds)
        host = cast("YTMHostBase", self.app)
        await host._fetch_and_play_radio(seeds, label=f"Radio: Liked Songs\n{seed_list}")

    def on_track_table_filter_requested(self, event: TrackTable.FilterRequested) -> None:
        event.stop()
        try:
            f = self.query_one("#track-filter", Input)
            f.value = ""
            f.add_class("visible")
            f.focus()
        except Exception:
            pass

    def on_track_table_filter_closed(self, event: TrackTable.FilterClosed) -> None:
        event.stop()
        try:
            f = self.query_one("#track-filter", Input)
            f.remove_class("visible")
            self.query_one("#liked-table", TrackTable).focus()
        except Exception:
            pass

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "track-filter":
            self.query_one("#liked-table", TrackTable).apply_filter(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "track-filter":
            try:
                f = self.query_one("#track-filter", Input)
                f.remove_class("visible")
                self.query_one("#liked-table", TrackTable).focus()
            except Exception:
                pass

    def on_key(self, event: object) -> None:
        from textual.events import Key

        if not isinstance(event, Key):
            return
        if event.key == "escape":
            try:
                f = self.query_one("#track-filter", Input)
                if f.has_class("visible"):
                    event.stop()
                    event.prevent_default()
                    self.query_one("#liked-table", TrackTable).clear_filter()
                    f.remove_class("visible")
                    self.query_one("#liked-table", TrackTable).focus()
            except Exception:
                pass
