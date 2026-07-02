"""Recently Played page.

Two tabs:
- **Local** — play history from the local SQLite database (everything played
  inside this app).
- **YT Music** — server-side play history from the account, via the
  unofficial ytmusicapi ``get_history()``. Requires authentication.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING, Any, cast

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Click
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, Label, Static

from ytm_player.config.keymap import Action
from ytm_player.ui.track_filter import TRACK_FILTER_CSS, TrackFilterHost
from ytm_player.ui.widgets.track_table import TrackTable
from ytm_player.utils.formatting import normalize_tracks

if TYPE_CHECKING:
    from ytm_player.app._base import YTMHostBase

logger = logging.getLogger(__name__)

# Shown when the local history DB read fails (file unreadable, locked,
# corrupt schema, etc.). Distinct from the genuine empty-state message
# below so users don't see "No play history yet" when actually a disk
# error happened.
_HISTORY_LOAD_FAILED_MSG = (
    "Couldn't load history. Check the log at ~/.config/ytm-player/logs/ytm.log for details."
)

# Shown on the YT Music tab when no authenticated session is available (the
# ytmusicapi ``get_history`` endpoint requires auth).
_YTM_AUTH_REQUIRED_MSG = "Sign in to YT Music to see your account play history."

# Tab indices.
_TAB_LOCAL = 0
_TAB_YTM = 1

# Cap the number of tracks rendered per tab. The local history query already
# limits to 100 (rendering thousands of rows overloads the TUI); the YT Music
# ``get_history()`` endpoint returns ~200 rows and isn't paginated, so we slice
# it to the same 100 to keep both tabs snappy.
_MAX_TRACKS = 100


class RecentTab(Static):
    """A focusable tab label (Local / YT Music).

    Made focusable so the app-wide ``Tab`` / ``Shift+Tab`` section traversal
    lands on each label in turn; ``Enter`` then switches to it (see
    ``RecentlyPlayedPage.handle_action``). Mirrors ``BrowseTab``.
    """

    can_focus = True

    def __init__(self, label: str, index: int, **kwargs: Any) -> None:
        super().__init__(label, **kwargs)
        self.tab_index = index


class RecentlyPlayedPage(TrackFilterHost, Widget):
    """Displays recently played tracks (local history + YT Music account)."""

    _filter_table_id = "#recent-table"

    DEFAULT_CSS = (
        """
    RecentlyPlayedPage {
        layout: vertical;
        width: 1fr;
        height: 1fr;
    }
    .recent-header {
        height: auto;
        max-height: 3;
        padding: 1 2;
        background: $surface;
    }
    .recent-header-row {
        height: auto;
        width: 1fr;
    }
    .recent-header-row Label {
        width: auto;
    }
    .recent-header-title {
        text-style: bold;
        color: $primary;
    }
    .recent-tab-sep {
        width: auto;
        height: 1;
        margin: 0 0 0 2;
        color: $text-muted 50%;
    }
    .recent-tab {
        width: auto;
        height: 1;
        margin: 0 0 0 1;
        padding: 0 1;
        color: $text-muted;
    }
    .recent-tab:hover {
        color: $text;
        background: $primary 20%;
    }
    .recent-tab.active {
        color: $background;
        background: $primary;
        text-style: bold;
    }
    .recent-tab:focus {
        color: $text;
        background: $primary 40%;
        text-style: bold;
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
    .recent-footer {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        dock: bottom;
    }
    .recent-loading {
        width: 1fr;
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
    }
    """
        + TRACK_FILTER_CSS
    )

    track_count: reactive[int] = reactive(0)
    _load_failed: bool

    def __init__(
        self,
        *,
        cursor_row: int | None = None,
        active_tab: int = _TAB_LOCAL,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._restore_cursor_row = cursor_row
        self._active_tab = active_tab if active_tab in (_TAB_LOCAL, _TAB_YTM) else _TAB_LOCAL
        # Set when a loader catches an expected disk-side / network
        # failure so ``_display_tracks`` can render the failure message
        # instead of the genuine empty-state copy.
        self._load_failed = False
        # Distinguishes an empty YT Music tab caused by missing auth from a
        # genuinely empty account history.
        self._ytm_auth_required = False
        # Per-tab track cache so switching tabs doesn't refetch every time.
        self._tab_cache: dict[int, list[dict]] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="recent-header", classes="recent-header"):
            with Horizontal(classes="recent-header-row"):
                yield Label("Recently Played", classes="recent-header-title")
                yield Static("│", classes="recent-tab-sep")
                local_cls = "recent-tab active" if self._active_tab == _TAB_LOCAL else "recent-tab"
                ytm_cls = "recent-tab active" if self._active_tab == _TAB_YTM else "recent-tab"
                yield RecentTab("Local", _TAB_LOCAL, id="recent-tab-local", classes=local_cls)
                yield RecentTab("YT Music", _TAB_YTM, id="recent-tab-ytm", classes=ytm_cls)
                yield Static("[▶ Start Radio]", id="start-radio-btn", markup=True)
        yield Label("Loading history...", id="recent-loading", classes="recent-loading")
        yield TrackTable(show_album=False, id="recent-table")
        yield Static("", id="recent-footer", classes="recent-footer")
        yield Input(placeholder="/ Filter tracks...", id="track-filter", classes="track-filter")

    def on_mount(self) -> None:
        self.query_one("#recent-table", TrackTable).display = False
        self._load_active_tab()

    def _load_active_tab(self) -> None:
        """Kick off the loader worker for the currently active tab."""
        if self._active_tab == _TAB_YTM:
            self.run_worker(self._load_ytm_history(), group="recent-load", exclusive=True)
        else:
            self.run_worker(self._load_history(), group="recent-load", exclusive=True)

    async def _load_history(self) -> None:
        """Load the local SQLite play history (Local tab)."""
        self._ytm_auth_required = False
        history = self.app.history  # type: ignore[attr-defined]
        if not history:
            self.query_one("#recent-loading", Label).update("History not available.")
            return

        try:
            tracks = await history.get_recently_played(limit=_MAX_TRACKS)
            self._load_failed = False
        except (OSError, sqlite3.Error):
            # Local DB failure: file unreadable, disk full, DB locked,
            # schema mismatch, corrupt page, etc. Programming errors
            # (TypeError, AttributeError) are NOT caught here — they
            # must propagate so bugs surface in development per the
            # error-handling architecture in CLAUDE.md.
            logger.exception("Failed to load play history")
            tracks = []
            self._load_failed = True

        self._tab_cache[_TAB_LOCAL] = tracks
        if self._active_tab == _TAB_LOCAL:
            self._display_tracks(tracks)

    async def _load_ytm_history(self) -> None:
        """Load the account play history from YT Music (YT Music tab)."""
        self._load_failed = False
        self._ytm_auth_required = False
        ytmusic = self.app.ytmusic  # type: ignore[attr-defined]
        if not ytmusic:
            self._ytm_auth_required = True
            self._tab_cache[_TAB_YTM] = []
            if self._active_tab == _TAB_YTM:
                self._display_tracks([])
            return

        # ``get_history`` requires auth; the service wrapper logs and returns
        # [] on any failure (expired session, network, server error). The
        # endpoint isn't paginated and hands back ~200 rows in one shot, so we
        # cap it to _MAX_TRACKS to match the local tab and avoid overloading
        # the TUI.
        raw = await ytmusic.get_history()
        tracks = normalize_tracks(raw)[:_MAX_TRACKS]

        self._tab_cache[_TAB_YTM] = tracks
        if self._active_tab == _TAB_YTM:
            self._display_tracks(tracks)

    def _display_tracks(self, tracks: list[dict]) -> None:
        table = self.query_one("#recent-table", TrackTable)
        loading = self.query_one("#recent-loading", Label)

        if not tracks:
            table.display = False
            if self._load_failed:
                loading.update(_HISTORY_LOAD_FAILED_MSG)
            elif self._active_tab == _TAB_YTM:
                if self._ytm_auth_required:
                    loading.update(_YTM_AUTH_REQUIRED_MSG)
                else:
                    loading.update("No YT Music play history found.")
            else:
                loading.update("No play history yet. Start listening!")
            loading.display = True
            return

        loading.display = False
        table.display = True
        table.load_tracks(tracks)

        self.track_count = len(tracks)
        footer = self.query_one("#recent-footer", Static)
        source = "YT Music" if self._active_tab == _TAB_YTM else "local"
        footer.update(f"{len(tracks)} recently played tracks ({source})")

        # Restore cursor position from navigation state.
        row = self._restore_cursor_row
        self._restore_cursor_row = None
        if row is not None and 0 <= row < table.row_count:
            table.move_cursor(row=row)

        # Land keyboard focus on the table so Tab / j / k have a starting point.
        table.focus()

    def get_nav_state(self) -> dict[str, Any]:
        """Return state to preserve when navigating away."""
        state: dict[str, Any] = {}
        if self._active_tab != _TAB_LOCAL:
            state["active_tab"] = self._active_tab
        try:
            table = self.query_one("#recent-table", TrackTable)
            if table.cursor_row is not None and table.cursor_row > 0:
                state["cursor_row"] = table.cursor_row
        except Exception:
            pass
        return state

    _CONTEXT_ID = "__RECENTLY_PLAYED__"

    async def on_track_table_track_selected(self, event: TrackTable.TrackSelected) -> None:
        """Replace the queue with the history list and play the selection.

        Replacing (not appending) matches every other page — appending made
        repeated selections pile up duplicates in the live queue.
        """
        event.stop()
        table = self.query_one("#recent-table", TrackTable)
        host = cast("YTMHostBase", self.app)
        await host._replace_queue_and_play(
            table.tracks,
            entity_id=self._CONTEXT_ID,
            start_index=event.index,
            autoplay=False,
        )
        await host.play_track(event.track)

    async def handle_action(self, action: Action, count: int = 1) -> None:
        # When a tab label holds focus (via Tab / Shift+Tab traversal),
        # Enter switches to it and movement keys drop focus into the table.
        focused = self.app.focused
        if isinstance(focused, RecentTab):
            if action == Action.SELECT:
                self._switch_tab(focused.tab_index)
                return
            if action in (Action.MOVE_DOWN, Action.MOVE_UP):
                self.query_one("#recent-table", TrackTable).focus()
                return

        table = self.query_one("#recent-table", TrackTable)

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

    def _switch_tab(self, index: int) -> None:
        """Activate the tab at *index*, loading (or restoring) its tracks."""
        if index == self._active_tab:
            return
        self._active_tab = index

        # Update tab label styling.
        local_tab = self.query_one("#recent-tab-local", Static)
        ytm_tab = self.query_one("#recent-tab-ytm", Static)
        local_tab.set_class(index == _TAB_LOCAL, "active")
        ytm_tab.set_class(index == _TAB_YTM, "active")

        # Reset filter state when switching tabs.
        try:
            f = self.query_one("#track-filter", Input)
            f.value = ""
            f.remove_class("visible")
        except Exception:
            pass

        if index in self._tab_cache:
            self._display_tracks(self._tab_cache[index])
        else:
            table = self.query_one("#recent-table", TrackTable)
            table.display = False
            loading = self.query_one("#recent-loading", Label)
            loading.update("Loading history...")
            loading.display = True
            self._load_active_tab()

    def on_click(self, event: Click) -> None:
        widget_id = event.widget.id if event.widget is not None else None
        if widget_id == "start-radio-btn":
            event.stop()
            self.run_worker(self._start_radio(), name="start_radio", exclusive=True)
        elif widget_id == "recent-tab-local":
            event.stop()
            self._switch_tab(_TAB_LOCAL)
        elif widget_id == "recent-tab-ytm":
            event.stop()
            self._switch_tab(_TAB_YTM)

    async def _start_radio(self) -> None:
        import random

        table = self.query_one("#recent-table", TrackTable)
        tracks = table.tracks
        if not tracks:
            return
        seeds = random.sample(tracks, min(5, len(tracks)))
        host = cast("YTMHostBase", self.app)
        source = "YT Music" if self._active_tab == _TAB_YTM else "Recently Played"
        await host._fetch_and_play_radio(seeds, label=f"Radio: {source}")
