"""Stations page — browse / search / play internet radio.

Distinct from YouTube Music's algorithmic "Start Radio" feature. Powered
by radio-browser.info; UUIDs map to live HTTP audio streams that mpv
plays directly (no yt-dlp resolution needed).

Layout:

    ┌── tabs (Top voted | Top clicked | Favorites | Search) ─────────────┐
    │ filter input (visible in Search tab only)                          │
    │ DataTable: Name | Country | Codec | Bitrate | Tags | ★             │
    │ footer: "N stations · enter to play · f to favorite"               │
    └────────────────────────────────────────────────────────────────────┘

Vim keys via TrackPage.handle_action: j/k/g g/G to navigate; enter to
play; f to toggle favorite. The page filters in-memory once the listing
is loaded — no per-keystroke API hits.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import DataTable, Input, Label, Static

from ytm_player.config.keymap import Action
from ytm_player.services.radio_browser import RadioBrowser, Station
from ytm_player.services.station_favorites import StationFavorites

if TYPE_CHECKING:
    from ytm_player.app._base import YTMHostBase

logger = logging.getLogger(__name__)


_TABS: tuple[tuple[str, str], ...] = (
    ("top_voted", "Top Voted"),
    ("top_clicked", "Most Played"),
    ("favorites", "Favorites"),
    ("search", "Search"),
)

_DEFAULT_LIMIT = 100


class StationsPage(Widget):
    """Internet radio station browser."""

    DEFAULT_CSS = """
    StationsPage {
        layout: vertical;
        width: 1fr;
        height: 1fr;
    }
    .stations-header {
        height: 3;
        padding: 0 1;
        background: $surface;
    }
    .stations-tab-row {
        height: 1;
        width: 1fr;
    }
    .stations-tab {
        width: auto;
        padding: 0 2;
        color: $text-muted;
    }
    .stations-tab.active {
        color: $primary;
        text-style: bold;
    }
    .stations-title {
        text-style: bold;
        color: $primary;
        height: 1;
    }
    #station-search-input {
        height: 1;
        margin: 0;
        display: none;
    }
    #station-search-input.visible {
        display: block;
    }
    #station-table {
        width: 1fr;
        height: 1fr;
    }
    .stations-loading {
        width: 1fr;
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
    }
    .stations-footer {
        dock: bottom;
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }
    """

    # Active tab key from _TABS.
    active_tab: reactive[str] = reactive("top_voted")

    def __init__(self, *, cursor_row: int | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._restore_cursor_row = cursor_row
        self._stations: list[Station] = []  # currently displayed rows
        self._search_q: str = ""
        self._favorites = StationFavorites()
        self._browser = RadioBrowser()

    # ── compose ───────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Vertical(classes="stations-header"):
            yield Label("Stations", classes="stations-title")
            with Horizontal(classes="stations-tab-row", id="stations-tabs"):
                for tab_id, label in _TABS:
                    klass = "stations-tab active" if tab_id == "top_voted" else "stations-tab"
                    yield Static(label, id=f"tab-{tab_id}", classes=klass)
            yield Input(placeholder="Search stations…", id="station-search-input")
        yield Label("Loading…", id="stations-loading", classes="stations-loading")
        yield DataTable(id="station-table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="stations-footer", classes="stations-footer")

    def on_mount(self) -> None:
        table = self.query_one("#station-table", DataTable)
        table.add_columns("Name", "Country", "Codec", "kbps", "Tags", "")
        table.display = False
        self.run_worker(self._load_active_tab(), exclusive=True, group="stations-load")

    # ── tab routing ───────────────────────────────────────────────────

    def watch_active_tab(self, tab: str) -> None:
        for tab_id, _ in _TABS:
            try:
                w = self.query_one(f"#tab-{tab_id}", Static)
            except Exception:
                continue
            if tab_id == tab:
                w.add_class("active")
            else:
                w.remove_class("active")
        try:
            search = self.query_one("#station-search-input", Input)
        except Exception:
            search = None
        if search is not None:
            if tab == "search":
                search.add_class("visible")
                search.focus()
            else:
                search.remove_class("visible")
        self.run_worker(self._load_active_tab(), exclusive=True, group="stations-load")

    async def _load_active_tab(self) -> None:
        import asyncio

        loading = self.query_one("#stations-loading", Label)
        table = self.query_one("#station-table", DataTable)
        loading.update("Loading…")
        loading.display = True
        table.display = False

        tab = self.active_tab
        try:
            if tab == "favorites":
                stations = self._favorites.list()
            elif tab == "search":
                if not self._search_q:
                    stations = []
                else:
                    stations = await asyncio.to_thread(
                        self._browser.search, name=self._search_q, limit=_DEFAULT_LIMIT
                    )
            elif tab == "top_clicked":
                stations = await asyncio.to_thread(self._browser.top_clicked, _DEFAULT_LIMIT)
            else:
                stations = await asyncio.to_thread(self._browser.top_voted, _DEFAULT_LIMIT)
        except Exception:
            logger.exception("Failed to load stations for tab %r", tab)
            stations = []

        self._populate(stations)

    def _populate(self, stations: list[Station]) -> None:
        self._stations = list(stations)
        table = self.query_one("#station-table", DataTable)
        loading = self.query_one("#stations-loading", Label)
        footer = self.query_one("#stations-footer", Static)
        table.clear()
        for s in self._stations:
            star = "★" if self._favorites.is_favorite(s.uuid) else ""
            tag_blurb = ", ".join(s.tags[:3])
            row = (
                Text(s.name or "—", overflow="ellipsis"),
                s.country_code or s.country or "",
                s.codec.lower() if s.codec else "",
                str(s.bitrate) if s.bitrate else "",
                Text(tag_blurb, overflow="ellipsis"),
                star,
            )
            table.add_row(*row, key=s.uuid)
        if not self._stations:
            loading.update(self._empty_message())
            loading.display = True
            table.display = False
            footer.update("")
            return
        loading.display = False
        table.display = True
        footer.update(
            f"{len(self._stations)} stations · enter to play · f to favorite · tab to cycle list"
        )
        row = self._restore_cursor_row
        self._restore_cursor_row = None
        if row is not None and 0 <= row < table.row_count:
            table.move_cursor(row=row)

    def _empty_message(self) -> str:
        match self.active_tab:
            case "favorites":
                return "No favorite stations yet — star (f) any station to add it."
            case "search":
                if not self._search_q:
                    return "Type to search across ~30,000 stations."
                return f"No stations matched “{self._search_q}”."
            case "top_clicked":
                return "Couldn't load top-played stations — check network."
            case _:
                return "Couldn't load top-voted stations — check network."

    # ── input handlers ────────────────────────────────────────────────

    def on_static_click(self, event: Any) -> None:
        # ListView-style click on tab labels.
        widget_id = getattr(event.widget, "id", None) or ""
        if widget_id.startswith("tab-"):
            event.stop()
            new_tab = widget_id[len("tab-") :]
            if any(t == new_tab for t, _ in _TABS):
                self.active_tab = new_tab

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "station-search-input":
            return
        self._search_q = event.value.strip()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "station-search-input":
            return
        self.run_worker(self._load_active_tab(), exclusive=True, group="stations-load")

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        await self._play_selected(event.row_key.value or "")

    async def _play_selected(self, uuid: str) -> None:
        station = next((s for s in self._stations if s.uuid == uuid), None)
        if station is None:
            return
        host = cast("YTMHostBase", self.app)
        try:
            await host.play_station(station)
        except Exception:
            logger.exception("Failed to play station %s", station.name)
            self.app.notify(f"Could not play {station.name}", severity="error", timeout=4)

    # ── handle_action: vim navigation dispatch from the app ───────────

    async def handle_action(self, action: Action, count: int = 1) -> None:
        table = self.query_one("#station-table", DataTable)
        match action:
            case Action.MOVE_DOWN:
                for _ in range(max(1, count)):
                    table.action_cursor_down()
            case Action.MOVE_UP:
                for _ in range(max(1, count)):
                    table.action_cursor_up()
            case Action.PAGE_DOWN:
                table.action_page_down()
            case Action.PAGE_UP:
                table.action_page_up()
            case Action.GO_TOP:
                table.action_scroll_top()
                if table.row_count:
                    table.move_cursor(row=0)
            case Action.GO_BOTTOM:
                table.action_scroll_bottom()
                if table.row_count:
                    table.move_cursor(row=table.row_count - 1)
            case Action.SELECT:
                if table.row_count == 0:
                    return
                row_key = table.coordinate_to_cell_key((table.cursor_row, 0)).row_key
                await self._play_selected(row_key.value or "")
            case Action.LIKE_TOGGLE:
                # Reuse `l` for favorite-toggle within the stations page.
                self._toggle_favorite_at_cursor()
            case Action.FILTER:
                # Filter on stations page jumps to the Search tab and focuses input.
                self.active_tab = "search"
            case Action.TOGGLE_SEARCH_MODE:
                self.active_tab = "search"
            case _:
                pass

    def _toggle_favorite_at_cursor(self) -> None:
        table = self.query_one("#station-table", DataTable)
        if table.row_count == 0:
            return
        try:
            row_key = table.coordinate_to_cell_key((table.cursor_row, 0)).row_key
        except Exception:
            return
        uuid = row_key.value or ""
        station = next((s for s in self._stations if s.uuid == uuid), None)
        if station is None:
            return
        now_fav = self._favorites.toggle(station)
        # Update the star cell in-place; on the Favorites tab also drop the row.
        try:
            table.update_cell(row_key, table.columns[-1].key, "★" if now_fav else "")
        except Exception:
            logger.debug("Failed to update favorite cell in-place", exc_info=True)
        if self.active_tab == "favorites" and not now_fav:
            self.run_worker(self._load_active_tab(), exclusive=True, group="stations-load")
        self.app.notify(f"{'Added' if now_fav else 'Removed'} favorite: {station.name}", timeout=2)

    # ── nav state preservation ────────────────────────────────────────

    def get_nav_state(self) -> dict[str, Any]:
        state: dict[str, Any] = {"active_tab": self.active_tab, "search_q": self._search_q}
        try:
            table = self.query_one("#station-table", DataTable)
            if table.cursor_row is not None and table.cursor_row > 0:
                state["cursor_row"] = table.cursor_row
        except Exception:
            pass
        return state
