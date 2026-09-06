"""Recently Played page.

Three tabs:

- **All** (default) — local history first, followed by additional account
  history. One row per track: a track played both here and elsewhere keeps
  its local position and time, with details the local row lacks filled in
  from the account row. Two groups, not one timeline — the account feed only
  says roughly when a track was played.
- **Local** — play history from the local SQLite database (everything played
  inside this app), most recent first.
- **YT Music** — the account's play history from any device, via the
  unofficial ytmusicapi ``get_history()``, in the server's order. Requires
  authentication.
"""

from __future__ import annotations

import asyncio
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
from ytm_player.utils.formatting import get_video_id, normalize_tracks

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
_YTM_LOAD_FAILED_MSG = "Couldn't load YT Music history. Check the log for details."

# Tab indices, in display order.
_TAB_ALL = 0
_TAB_LOCAL = 1
_TAB_YTM = 2
_TABS = (_TAB_ALL, _TAB_LOCAL, _TAB_YTM)
_DEFAULT_TAB = _TAB_ALL
_TAB_IDS = {_TAB_ALL: "recent-tab-all", _TAB_LOCAL: "recent-tab-local", _TAB_YTM: "recent-tab-ytm"}
# One line under the tab row saying what the active tab shows.
_TAB_DESCRIPTIONS = {
    _TAB_ALL: "Local history first, followed by additional account history — grouped, not one timeline",
    _TAB_LOCAL: "Tracks played in this app, most recent first",
    _TAB_YTM: "Your account's play history from any device, in YouTube Music's order",
}

# Cap the number of tracks rendered PER SOURCE. The local history query
# already limits to 100 (rendering thousands of rows overloads the TUI); the
# YT Music ``get_history()`` endpoint returns ~200 rows and isn't paginated.
# The account cache keeps the whole normalized feed; each view applies the
# cap when it derives its rows, so All shows up to 100 local rows plus up to
# 100 further account rows.
_MAX_TRACKS = 100


def _enrich(track: dict, extra: dict) -> dict:
    """A copy of *track* with the fields it lacks filled in from *extra*.

    A field counts as lacking when it is absent, ``None`` or ``""``; the
    local row's own values, ``played_at`` included, are never replaced, and
    an empty value in *extra* never replaces anything either.
    """
    merged = dict(track)
    for key, value in extra.items():
        if value in (None, "") or track.get(key) not in (None, ""):
            continue
        merged[key] = value
    return merged


def _compose_all(local: list[dict], account: list[dict]) -> list[dict]:
    """Local rows first, in their own order, then account-only rows in server order.

    One row per video ID. A track in both keeps the local row -- its position
    and ``played_at`` -- with the fields the local row lacks (artist and album
    IDs, thumbnail) filled in from the account row. Deduplication happens
    before the account-only cap, so that cap never counts rows the local
    block already shows.
    """
    by_id: dict[str, dict] = {}
    for track in account:
        vid = get_video_id(track)
        if vid and vid not in by_id:
            by_id[vid] = track
    rows: list[dict] = []
    seen: set[str] = set()
    for track in local[:_MAX_TRACKS]:
        vid = get_video_id(track)
        if not vid or vid in seen:
            continue
        seen.add(vid)
        extra = by_id.get(vid)
        if extra is not None:
            track = _enrich(track, extra)
        rows.append(track)
    account_only = [track for vid, track in by_id.items() if vid not in seen]
    return rows + account_only[:_MAX_TRACKS]


class RecentTab(Static):
    """A focusable tab label (All / Local / YT Music).

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
        max-height: 5;
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
    .recent-tab-desc {
        height: auto;
        color: $text-muted;
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
        height: auto;
        max-height: 2;
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
        active_tab: int = _DEFAULT_TAB,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._restore_cursor_row = cursor_row
        self._active_tab = active_tab if active_tab in _TABS else _DEFAULT_TAB
        # Set when a loader catches an expected disk-side / network
        # failure so ``_display_tracks`` can render the failure message
        # instead of the genuine empty-state copy.
        self._load_failed = False
        # Distinguishes empty YT Music states: auth missing, load failed,
        # or genuinely empty account history.
        self._ytm_auth_required = False
        self._ytm_load_failed = False
        # Per-page cache for the Local tab. The account feed is cached at the
        # app level (see _get_cache); All is derived from both, never cached.
        self._tab_cache: dict[int, list[dict]] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="recent-header", classes="recent-header"):
            with Horizontal(classes="recent-header-row"):
                yield Label("Recently Played", classes="recent-header-title")
                yield Static("│", classes="recent-tab-sep")
                for index, label in (
                    (_TAB_ALL, "All"),
                    (_TAB_LOCAL, "Local"),
                    (_TAB_YTM, "YT Music"),
                ):
                    cls = "recent-tab active" if self._active_tab == index else "recent-tab"
                    yield RecentTab(label, index, id=_TAB_IDS[index], classes=cls)
                yield Static("[▶ Start Radio]", id="start-radio-btn", markup=True)
            yield Static(
                _TAB_DESCRIPTIONS[self._active_tab], id="recent-tab-desc", classes="recent-tab-desc"
            )
        yield Label("Loading history...", id="recent-loading", classes="recent-loading")
        yield TrackTable(show_album=False, id="recent-table")
        yield Static("", id="recent-footer", classes="recent-footer")
        yield Input(placeholder="/ Filter tracks...", id="track-filter", classes="track-filter")

    def on_mount(self) -> None:
        self.query_one("#recent-table", TrackTable).display = False
        self._load_active_tab()

    def _load_active_tab(self) -> None:
        """Kick off the loader worker for the currently active tab."""
        loaders = {
            _TAB_ALL: self._load_all,
            _TAB_LOCAL: self._load_history,
            _TAB_YTM: self._load_ytm_history,
        }
        self.run_worker(loaders[self._active_tab](), group="recent-load", exclusive=True)

    # ── Source caches ────────────────────────────────────────────────
    # The Local cache is per-page (cheap SQLite reads). The account feed is
    # cached at the app level so it survives page navigation and can be
    # updated by playback reporting while the page is closed. All has no
    # cache of its own: it is derived from the two sources on every render.

    def _get_cache(self, index: int) -> list[dict] | None:
        if index == _TAB_YTM:
            return getattr(self.app, "_ytm_history", None)
        return self._tab_cache.get(index)

    def _set_cache(self, index: int, tracks: list[dict]) -> None:
        if index == _TAB_YTM:
            self.app._ytm_history = tracks  # type: ignore[attr-defined]
        else:
            self._tab_cache[index] = tracks

    def _clear_cache(self, index: int) -> None:
        if index == _TAB_ALL:
            self._clear_cache(_TAB_LOCAL)
            self._clear_cache(_TAB_YTM)
        elif index == _TAB_YTM:
            self.app._ytm_history = None  # type: ignore[attr-defined]
        else:
            self._tab_cache.pop(index, None)

    def _sources_cached(self, index: int) -> bool:
        if index == _TAB_ALL:
            return self._get_cache(_TAB_LOCAL) is not None and self._get_cache(_TAB_YTM) is not None
        return self._get_cache(index) is not None

    def _rows_for(self, index: int) -> list[dict]:
        """The rows a view shows, derived from the source caches with the view's limits."""
        if index == _TAB_ALL:
            return _compose_all(self._get_cache(_TAB_LOCAL) or [], self._get_cache(_TAB_YTM) or [])
        return (self._get_cache(index) or [])[:_MAX_TRACKS]

    # ── Loaders ──────────────────────────────────────────────────────

    async def _fetch_local(self) -> None:
        """Fill the Local cache from SQLite. Sets ``_load_failed``; renders nothing."""
        history = self.app.history  # type: ignore[attr-defined]
        if not history:
            self._load_failed = True
            return
        try:
            tracks = await history.get_recently_played(limit=_MAX_TRACKS)
        except (OSError, sqlite3.Error):
            # Local DB failure: file unreadable, disk full, DB locked,
            # schema mismatch, corrupt page, etc. Programming errors
            # (TypeError, AttributeError) are NOT caught here — they
            # must propagate so bugs surface in development per the
            # error-handling architecture in CLAUDE.md.
            logger.exception("Failed to load play history")
            self._load_failed = True
            return
        self._load_failed = False
        # Cache only successful loads: a failure cached as [] would show
        # "No play history yet" with no retry until remount.
        self._set_cache(_TAB_LOCAL, tracks)

    async def _fetch_ytm(self) -> None:
        """Fill the app-level account cache from ``get_history()``; renders nothing.

        The cache holds the whole normalized feed, unfiltered; views apply
        their own limits. Plays the account accepted while no feed was
        cached (see ``_add_to_ytm_history_cache`` on the app) are folded
        into the fetched feed by ``_merge_pending_account_plays``.
        """
        self._ytm_auth_required = False
        self._ytm_load_failed = False
        ytmusic = self.app.ytmusic  # type: ignore[attr-defined]
        if not ytmusic:
            self._ytm_auth_required = True
            return
        # Reports accepted from here on post-date the feed we are about to
        # receive; the sequence number tells them apart from earlier ones.
        fetch_seq = self._pending_account_seq()
        # ``get_history`` requires auth; the service logs and returns None on
        # any failure (expired session, network, server error) so we can tell
        # a genuine empty history from an error.
        raw = await ytmusic.get_history()
        if raw is None:
            self._ytm_load_failed = True
            return
        self._set_cache(
            _TAB_YTM, self._merge_pending_account_plays(normalize_tracks(raw), fetch_seq)
        )

    def _pending_account_seq(self) -> int:
        """The sequence number of the latest pending accepted play (0 = none yet)."""
        seq = getattr(self.app, "_ytm_history_pending_seq", 0)
        return seq if isinstance(seq, int) else 0

    def _merge_pending_account_plays(self, feed: list[dict], fetch_seq: int) -> list[dict]:
        """Fold the pending accepted plays into *feed*, consuming them.

        A play accepted after the fetch started (sequence number above
        *fetch_seq*) post-dates the feed, so it goes ahead of it even when
        the feed already lists the track. A play accepted before the fetch
        started is already reflected in the feed: its server position
        stands. Only when the feed does not list such a play yet does it go
        ahead of the feed, behind the newer plays.
        """
        pending = self._take_pending_account_plays()
        if not pending:
            return feed
        feed_ids = {get_video_id(t) for t in feed}
        ahead = [t for seq, t in pending if seq > fetch_seq]
        ahead += [t for seq, t in pending if seq <= fetch_seq and get_video_id(t) not in feed_ids]
        ahead_ids = {get_video_id(t) for t in ahead}
        return ahead + [t for t in feed if get_video_id(t) not in ahead_ids]

    def _take_pending_account_plays(self) -> list[tuple[int, dict]]:
        pending = getattr(self.app, "_ytm_history_pending", None)
        if not isinstance(pending, list) or not pending:
            return []
        taken = list(pending)
        pending.clear()
        return taken

    async def _load_history(self) -> None:
        """Load the local SQLite play history (Local tab)."""
        if not self.app.history:  # type: ignore[attr-defined]
            self.query_one("#recent-loading", Label).update("History not available.")
            return
        await self._fetch_local()
        if self._active_tab == _TAB_LOCAL:
            self._display_tracks(self._rows_for(_TAB_LOCAL))

    async def _load_ytm_history(self) -> None:
        """Load the account play history from YT Music (YT Music tab)."""
        # Reuse the app-level cache when present (populated on a prior visit)
        # so we don't refetch every time.
        if self._get_cache(_TAB_YTM) is None:
            await self._fetch_ytm()
        if self._active_tab == _TAB_YTM:
            self._display_tracks(self._rows_for(_TAB_YTM))

    async def _load_all(self) -> None:
        """Load whichever sources aren't cached yet, then render All from both."""
        fetches = []
        if self._get_cache(_TAB_LOCAL) is None:
            fetches.append(self._fetch_local())
        if self._get_cache(_TAB_YTM) is None:
            fetches.append(self._fetch_ytm())
        if fetches:
            await asyncio.gather(*fetches)
        if self._active_tab == _TAB_ALL:
            self._display_tracks(self._rows_for(_TAB_ALL))

    # ── Live updates ─────────────────────────────────────────────────

    def optimistic_add(self, index: int, track: dict) -> None:
        """Prepend a just-played track to a source's cache and re-render live.

        Dedups by ``video_id``: an existing row for the same track moves to
        the top. The Local cache stays capped at ``_MAX_TRACKS`` (its query
        limit); the account cache is unfiltered and capped on display. No-op
        until the source has a cache (the first visit fetches the real list).
        If the source, or All, is showing, it re-renders.
        """
        cache = self._get_cache(index)
        if cache is None:
            return
        video_id = get_video_id(track)
        updated = [dict(track)] + [t for t in cache if get_video_id(t) != video_id]
        if index == _TAB_LOCAL:
            updated = updated[:_MAX_TRACKS]
        self._set_cache(index, updated)
        self._refresh_tab_from_cache(index)

    def _refresh_tab_from_cache(self, index: int) -> None:
        """Re-render after a background change to source *index*'s cache.

        Re-renders when that source is showing, or when All is showing (it
        draws from both sources). Never steals focus: the change lands from
        playback timers, not user input, and the user may be typing in the
        filter. The cursor stays on the same track and the filter is
        reapplied.
        """
        if self._active_tab not in (index, _TAB_ALL):
            return
        if self._active_tab != _TAB_ALL and self._get_cache(index) is None:
            return
        rows = self._rows_for(self._active_tab)
        query = ""
        try:
            query = self.query_one("#track-filter", Input).value
        except Exception:
            logger.debug("Failed to read track filter on tab refresh", exc_info=True)
        try:
            table = self.query_one("#recent-table", TrackTable)
            # Keep the cursor on the SAME TRACK, not the same row number —
            # the prepend shifts every row down by one. Identity comes from
            # the highlighted VISIBLE row (mapped through any active sort),
            # not a backing-list index. The reload below resets the sort, so
            # the target row is that track's index in the new rows.
            cursored = table.selected_track
            keep_row = None
            if cursored is not None:
                for i, t in enumerate(rows):
                    if get_video_id(t) == get_video_id(cursored):
                        keep_row = i
                        break
            self._restore_cursor_row = keep_row
        except Exception:
            logger.debug("Failed to preserve cursor on tab refresh", exc_info=True)
        self._display_tracks(rows, focus=False)
        if query:
            try:
                self.query_one("#recent-table", TrackTable).apply_filter(query)
            except Exception:
                logger.debug("Failed to reapply track filter on tab refresh", exc_info=True)

    # ── Rendering ────────────────────────────────────────────────────

    def _display_tracks(self, tracks: list[dict], *, focus: bool = True) -> None:
        table = self.query_one("#recent-table", TrackTable)
        loading = self.query_one("#recent-loading", Label)

        if not tracks:
            table.load_tracks([])
            self.track_count = 0
            self.query_one("#recent-footer", Static).update("")
            table.display = False
            loading.update(self._empty_message())
            loading.display = True
            return

        loading.display = False
        table.display = True
        table.load_tracks(tracks)

        self.track_count = len(tracks)
        self.query_one("#recent-footer", Static).update(self._footer_text(len(tracks)))

        # Restore cursor position from navigation state.
        row = self._restore_cursor_row
        self._restore_cursor_row = None
        if row is not None and 0 <= row < table.row_count:
            table.move_cursor(row=row)

        # Land keyboard focus on the table so Tab / j / k have a starting
        # point — but never on background refreshes, which would steal focus
        # from the filter input mid-keystroke.
        if focus:
            table.focus()

    def _empty_message(self) -> str:
        """Empty-state copy for the ACTIVE tab; the other tabs' failures never leak in."""
        if self._active_tab == _TAB_YTM:
            if self._ytm_auth_required:
                return _YTM_AUTH_REQUIRED_MSG
            if self._ytm_load_failed:
                return _YTM_LOAD_FAILED_MSG
            return "No YT Music play history found."
        if self._active_tab == _TAB_LOCAL:
            return (
                _HISTORY_LOAD_FAILED_MSG
                if self._load_failed
                else "No play history yet. Start listening!"
            )
        # All: name every source that is missing instead of claiming an empty
        # history the user may well have.
        problems = []
        if self._load_failed:
            problems.append("Couldn't load local history")
        if self._ytm_auth_required:
            problems.append("sign in to YT Music to include your account history")
        elif self._ytm_load_failed:
            problems.append("couldn't load YT Music history")
        if not problems:
            return "No play history yet. Start listening!"
        message = "; ".join(problems) + "."
        if self._load_failed or self._ytm_load_failed:
            message += " Check the log at ~/.config/ytm-player/logs/ytm.log for details."
        return message

    def _footer_text(self, count: int) -> str:
        if self._active_tab == _TAB_LOCAL:
            return f"{count} recently played tracks (local)"
        if self._active_tab == _TAB_YTM:
            return f"{count} recently played tracks (YT Music)"
        # Partial results are said out loud, and first: a missing source is
        # not an empty one, and the notice must survive a narrow terminal.
        if self._load_failed:
            notice = "Local history couldn't be loaded; showing YT Music history only"
        elif self._ytm_auth_required:
            notice = (
                "Sign in to YT Music to include your account history; showing local history only"
            )
        elif self._ytm_load_failed:
            notice = "YT Music history couldn't be loaded; showing local history only"
        else:
            return f"{count} tracks — local history first, then additional account history"
        return f"{notice} — {count} tracks"

    def get_nav_state(self) -> dict[str, Any]:
        """Return state to preserve when navigating away."""
        state: dict[str, Any] = {}
        if self._active_tab != _DEFAULT_TAB:
            state["active_tab"] = self._active_tab
        try:
            table = self.query_one("#recent-table", TrackTable)
            if table.cursor_row is not None and table.cursor_row > 0:
                state["cursor_row"] = table.cursor_row
        except Exception:
            logger.debug("Failed to capture cursor for nav state", exc_info=True)
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
            case _:
                await table.handle_action(action, count)

    def _switch_tab(self, index: int) -> None:
        """Activate the tab at *index*, loading (or restoring) its tracks.

        Re-selecting the tab that's already active refreshes it (drops the
        cache and refetches) — handy for the YT Music tab, which otherwise
        keeps showing the cached snapshot from when it was first opened.
        Refreshing All refetches both sources.
        """
        if index == self._active_tab:
            self._reload_active_tab()
            return

        self._active_tab = index
        # Update tab label styling and the one-line description.
        for tab_index, widget_id in _TAB_IDS.items():
            self.query_one(f"#{widget_id}", Static).set_class(tab_index == index, "active")
        self.query_one("#recent-tab-desc", Static).update(_TAB_DESCRIPTIONS[index])
        self._reset_filter()

        if self._sources_cached(index):
            self._display_tracks(self._rows_for(index))
        else:
            self._show_loading()
            self._load_active_tab()

    def _reload_active_tab(self) -> None:
        """Drop the active tab's cache(s) and refetch."""
        self._clear_cache(self._active_tab)
        self._reset_filter()
        self._show_loading()
        self._load_active_tab()
        self.app.notify("Refreshing\u2026", timeout=1)

    def _reset_filter(self) -> None:
        try:
            f = self.query_one("#track-filter", Input)
            f.value = ""
            f.remove_class("visible")
        except Exception:
            logger.debug("Failed to reset track filter", exc_info=True)

    def _show_loading(self) -> None:
        self.query_one("#recent-table", TrackTable).display = False
        loading = self.query_one("#recent-loading", Label)
        loading.update("Loading history...")
        loading.display = True

    def on_click(self, event: Click) -> None:
        widget_id = event.widget.id if event.widget is not None else None
        if widget_id == "start-radio-btn":
            event.stop()
            self.run_worker(self._start_radio(), name="start_radio", exclusive=True)
            return
        for index, tab_id in _TAB_IDS.items():
            if widget_id == tab_id:
                event.stop()
                self._switch_tab(index)
                return

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
