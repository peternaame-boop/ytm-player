"""Search page — query YouTube Music and display categorized results."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Input, Label, ListItem, ListView, Static

from ytm_player.config.keymap import Action
from ytm_player.config.settings import get_settings
from ytm_player.ui.widgets.track_table import TrackTable
from ytm_player.utils.formatting import extract_artist, truncate

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Search result panel (for Albums, Artists, Playlists)
# ---------------------------------------------------------------------------


class SearchResultPanel(Widget):
    """A single panel within the search results area.

    Displays a titled list of items. Each item stores its underlying data dict
    so callers can act on selection.
    """

    DEFAULT_CSS = """
    SearchResultPanel {
        height: 1fr;
        width: 1fr;
        border: solid $secondary;
        padding: 0 1;
    }

    SearchResultPanel .panel-title {
        text-style: bold;
        color: $text;
        padding: 0 0 1 0;
    }

    SearchResultPanel ListView {
        height: 1fr;
        width: 1fr;
    }

    SearchResultPanel .panel-empty {
        color: $text-muted;
        content-align: center middle;
        height: 1fr;
    }

    SearchResultPanel .panel-count {
        color: $text-muted;
        dock: bottom;
        height: 1;
        padding: 0 0 0 0;
    }
    """

    class ItemSelected(Message):
        """Emitted when an item in this panel is activated."""

        def __init__(self, item_data: dict[str, Any], panel_id: str) -> None:
            super().__init__()
            self.item_data = item_data
            self.panel_id = panel_id

    def __init__(
        self,
        title: str,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._title = title
        self._items: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield Label(self._title, classes="panel-title")
        yield ListView(id=f"{self.id}-list")
        yield Static("", classes="panel-count")

    def load_items(self, items: list[dict[str, Any]]) -> None:
        """Replace panel contents with *items*."""
        self._items = list(items)
        list_view = self.query_one(ListView)
        list_view.clear()

        if not items:
            count_label = self.query_one(".panel-count", Static)
            count_label.update("No results")
            return

        for item in self._items:
            label = self._format_item(item)
            list_view.append(ListItem(Label(label)))

        count_label = self.query_one(".panel-count", Static)
        count_label.update(f"{len(self._items)} result{'s' if len(self._items) != 1 else ''}")

    def _format_item(self, item: dict[str, Any]) -> str:
        """Build a human-readable label for a result item."""
        result_type = item.get("resultType", item.get("category", ""))
        title = item.get("title", item.get("name", "Unknown"))

        if result_type in ("album", "albums"):
            artist = extract_artist(item)
            year = item.get("year", "")
            suffix = f" - {artist}" if artist else ""
            suffix += f" ({year})" if year else ""
            return truncate(f"{title}{suffix}", 60)

        if result_type in ("artist", "artists"):
            subs = item.get("subscribers", "")
            suffix = f"  [{subs}]" if subs else ""
            return truncate(f"{title}{suffix}", 60)

        if result_type in ("playlist", "playlists"):
            author = item.get("author", "")
            count = item.get("itemCount", item.get("count", ""))
            suffix = f" by {author}" if author else ""
            suffix += f" ({count} tracks)" if count else ""
            return truncate(f"{title}{suffix}", 60)

        # Fallback
        return truncate(title, 60)


    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Forward item activation to parent."""
        idx = event.list_view.index
        if idx is not None and 0 <= idx < len(self._items):
            panel_id = self.id or ""
            self.post_message(self.ItemSelected(self._items[idx], panel_id))


# ---------------------------------------------------------------------------
# Suggestion overlay
# ---------------------------------------------------------------------------


class SuggestionList(Widget):
    """A dropdown-style overlay showing search suggestions below the input."""

    DEFAULT_CSS = """
    SuggestionList {
        layer: overlay;
        height: auto;
        max-height: 10;
        width: 1fr;
        display: none;
        background: $surface;
        border: solid $secondary;
        padding: 0 1;
    }

    SuggestionList.visible {
        display: block;
    }

    SuggestionList ListView {
        height: auto;
        max-height: 8;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._suggestions: list[str] = []

    def compose(self) -> ComposeResult:
        yield ListView(id="suggestion-list")

    def show_suggestions(self, suggestions: list[str]) -> None:
        """Populate and display suggestions."""
        self._suggestions = list(suggestions)
        list_view = self.query_one(ListView)
        list_view.clear()

        if not suggestions:
            self.remove_class("visible")
            return

        for text in suggestions:
            list_view.append(ListItem(Label(text)))
        self.add_class("visible")

    def hide(self) -> None:
        self.remove_class("visible")

    @property
    def suggestions(self) -> list[str]:
        return list(self._suggestions)


# ---------------------------------------------------------------------------
# Main search page
# ---------------------------------------------------------------------------


class SearchPage(Widget):
    """Full search page with input, suggestions, and categorized result panels.

    Layout:
        - Search input with mode toggle at the top
        - Suggestion dropdown when input is focused and non-empty
        - Songs panel (TrackTable) alongside Albums, Artists panels
        - Playlists panel spanning full width below
    """

    DEFAULT_CSS = """
    SearchPage {
        height: 1fr;
        width: 1fr;
    }

    #search-header {
        height: 3;
        padding: 0 1;
        align: center middle;
    }

    #search-input {
        width: 1fr;
        margin: 0 1 0 0;
    }

    #search-mode {
        width: auto;
        min-width: 12;
        content-align: center middle;
        border: solid $secondary;
        padding: 0 1;
    }

    #search-results {
        height: 1fr;
    }

    #songs-panel {
        width: 2fr;
        border: solid $secondary;
        padding: 0 1;
    }

    #albums-panel {
        width: 1fr;
    }

    #artists-panel {
        width: 1fr;
    }

    #playlists-panel {
        height: auto;
        max-height: 10;
        width: 1fr;
    }

    #suggestion-overlay {
        dock: top;
        margin: 3 1 0 1;
    }

    .loading-indicator {
        width: 1fr;
        height: 1;
        content-align: center middle;
        color: $text-muted;
    }

    #recent-searches {
        height: auto;
        max-height: 12;
        width: 1fr;
        padding: 0 1;
    }

    #recent-searches .panel-title {
        text-style: bold;
        color: $text-muted;
        padding: 0 0 1 0;
    }
    """

    search_mode: reactive[str] = reactive("music")
    is_loading: reactive[bool] = reactive(False)

    # Filters to pass to ytmusicapi for music-only mode.
    _MUSIC_FILTERS: dict[str, str] = {
        "songs": "songs",
        "albums": "albums",
        "artists": "artists",
        "playlists": "community_playlists",
    }

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._debounce_timer: Timer | None = None
        self._last_query: str = ""
        self._search_results: dict[str, list[dict[str, Any]]] = {
            "songs": [],
            "albums": [],
            "artists": [],
            "playlists": [],
        }

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal(id="search-header"):
                yield Input(
                    placeholder="Search YouTube Music...",
                    id="search-input",
                )
                yield Static("[Music]", id="search-mode")
            yield SuggestionList(id="suggestion-overlay")
            yield Static("", id="loading-msg", classes="loading-indicator")
            with Horizontal(id="search-results"):
                with Vertical(id="songs-panel"):
                    yield Label("Songs", classes="panel-title")
                    yield TrackTable(
                        show_album=True,
                        show_index=True,
                        id="songs-table",
                    )
                yield SearchResultPanel("Albums", id="albums-panel")
                yield SearchResultPanel("Artists", id="artists-panel")
            yield SearchResultPanel("Playlists", id="playlists-panel")

    def on_mount(self) -> None:
        settings = get_settings()
        self.search_mode = settings.search.default_mode

    def on_unmount(self) -> None:
        """Clean up timers and release data references."""
        if self._debounce_timer is not None:
            self._debounce_timer.stop()
            self._debounce_timer = None
        # Release potentially large search result data.
        self._search_results = {
            "songs": [],
            "albums": [],
            "artists": [],
            "playlists": [],
        }

    # ------------------------------------------------------------------
    # Input handling
    # ------------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        """Debounce input changes to fetch suggestions."""
        if event.input.id != "search-input":
            return

        query = event.value.strip()
        if not query:
            self._hide_suggestions()
            self._show_recent_searches()
            return

        # Cancel any pending debounce.
        if self._debounce_timer is not None:
            self._debounce_timer.stop()

        settings = get_settings()
        if settings.search.predictive:
            self._debounce_timer = self.set_timer(
                0.3, lambda: self._fetch_suggestions(query)
            )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Execute search when Enter is pressed."""
        if event.input.id != "search-input":
            return

        query = event.value.strip()
        if not query:
            return

        self._hide_suggestions()
        self._last_query = query
        self.run_worker(self._execute_search(query), name="search", exclusive=True)

    # ------------------------------------------------------------------
    # Suggestions
    # ------------------------------------------------------------------

    def _fetch_suggestions(self, query: str) -> None:
        """Kick off an async worker to fetch suggestions."""
        self.run_worker(
            self._load_suggestions(query),
            name="suggestions",
            exclusive=True,
        )

    async def _load_suggestions(self, query: str) -> None:
        """Fetch predictive suggestions from the API."""
        try:
            suggestions = await self.app.ytmusic.get_search_suggestions(query)
            overlay = self.query_one("#suggestion-overlay", SuggestionList)
            overlay.show_suggestions(suggestions)
        except Exception:
            logger.exception("Failed to load suggestions for %r", query)

    def _hide_suggestions(self) -> None:
        try:
            overlay = self.query_one("#suggestion-overlay", SuggestionList)
            overlay.hide()
        except Exception:
            pass

    def _show_recent_searches(self) -> None:
        """Display recent search history when input is empty."""
        self.run_worker(self._load_recent_searches(), name="recent", exclusive=True)

    async def _load_recent_searches(self) -> None:
        """Load and display recent searches from history."""
        try:
            history = await self.app.history.get_search_history(limit=10)
            if history:
                suggestions = [entry["query"] for entry in history]
                overlay = self.query_one("#suggestion-overlay", SuggestionList)
                overlay.show_suggestions(suggestions)
        except Exception:
            logger.debug("Could not load recent searches", exc_info=True)

    # ------------------------------------------------------------------
    # Suggestion selection
    # ------------------------------------------------------------------

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle suggestion item click — populate input and search."""
        list_view = event.list_view
        if list_view.id == "suggestion-list":
            overlay = self.query_one("#suggestion-overlay", SuggestionList)
            idx = list_view.index
            if idx is not None and 0 <= idx < len(overlay.suggestions):
                query = overlay.suggestions[idx]
                search_input = self.query_one("#search-input", Input)
                search_input.value = query
                self._hide_suggestions()
                self._last_query = query
                self.run_worker(
                    self._execute_search(query), name="search", exclusive=True
                )

    # ------------------------------------------------------------------
    # Search execution
    # ------------------------------------------------------------------

    async def _execute_search(self, query: str) -> None:
        """Run the search and populate all result panels."""
        self.is_loading = True
        self._update_loading("Searching...")

        try:
            if self.search_mode == "music":
                results = await self._search_music(query)
            else:
                results = await self._search_all(query)

            self._search_results = results
            self._populate_results(results)

            # Log the search to history.
            total_count = sum(len(v) for v in results.values())
            try:
                await self.app.history.log_search(
                    query=query,
                    filter_mode=self.search_mode,
                    result_count=total_count,
                )
            except Exception:
                logger.debug("Failed to log search to history", exc_info=True)

        except Exception:
            logger.exception("Search failed for query=%r", query)
            self._update_loading("Search failed. Try again.")
        finally:
            self.is_loading = False
            self._update_loading("")

    async def _search_music(self, query: str) -> dict[str, list[dict[str, Any]]]:
        """Execute filtered searches for each category (music-only mode)."""
        results: dict[str, list[dict[str, Any]]] = {
            "songs": [],
            "albums": [],
            "artists": [],
            "playlists": [],
        }

        # Run all four searches concurrently.
        tasks = {
            category: self.app.ytmusic.search(query, filter=api_filter, limit=10)
            for category, api_filter in self._MUSIC_FILTERS.items()
        }

        gathered = await asyncio.gather(
            *tasks.values(), return_exceptions=True
        )

        for category, result in zip(tasks.keys(), gathered):
            if isinstance(result, Exception):
                logger.error("Search for %s failed: %s", category, result)
            elif isinstance(result, list):
                results[category] = result

        return results

    async def _search_all(self, query: str) -> dict[str, list[dict[str, Any]]]:
        """Execute a single unfiltered search and categorize results."""
        results: dict[str, list[dict[str, Any]]] = {
            "songs": [],
            "albums": [],
            "artists": [],
            "playlists": [],
        }

        raw = await self.app.ytmusic.search(query, filter=None, limit=40)

        for item in raw:
            result_type = item.get("resultType", "")
            if result_type == "song" or result_type == "video":
                results["songs"].append(item)
            elif result_type == "album":
                results["albums"].append(item)
            elif result_type == "artist":
                results["artists"].append(item)
            elif result_type == "playlist":
                results["playlists"].append(item)

        return results

    # ------------------------------------------------------------------
    # Populate UI
    # ------------------------------------------------------------------

    def _populate_results(self, results: dict[str, list[dict[str, Any]]]) -> None:
        """Push result data into each panel widget."""
        # Songs go into the TrackTable.
        songs_table = self.query_one("#songs-table", TrackTable)
        songs_table.load_tracks(results.get("songs", []))

        # Albums, Artists, Playlists go into SearchResultPanels.
        albums_panel = self.query_one("#albums-panel", SearchResultPanel)
        albums_panel.load_items(results.get("albums", []))

        artists_panel = self.query_one("#artists-panel", SearchResultPanel)
        artists_panel.load_items(results.get("artists", []))

        playlists_panel = self.query_one("#playlists-panel", SearchResultPanel)
        playlists_panel.load_items(results.get("playlists", []))

    def _update_loading(self, text: str) -> None:
        try:
            label = self.query_one("#loading-msg", Static)
            label.update(text)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Mode toggle
    # ------------------------------------------------------------------

    def _toggle_search_mode(self) -> None:
        """Switch between music-only and all-results mode."""
        if self.search_mode == "music":
            self.search_mode = "all"
        else:
            self.search_mode = "music"

        # Update the mode indicator.
        mode_label = self.query_one("#search-mode", Static)
        display = "[Music]" if self.search_mode == "music" else "[All]"
        mode_label.update(display)

        # Re-run the last search with the new mode if we have a query.
        if self._last_query:
            self.run_worker(
                self._execute_search(self._last_query),
                name="search",
                exclusive=True,
            )

    # ------------------------------------------------------------------
    # Track and item selection handlers
    # ------------------------------------------------------------------

    async def on_track_table_track_selected(self, event: TrackTable.TrackSelected) -> None:
        """Play the selected song."""
        track = event.track
        video_id = track.get("videoId") or track.get("video_id")
        if video_id:
            await self.app.play_track(track)

    async def on_search_result_panel_item_selected(
        self, event: SearchResultPanel.ItemSelected
    ) -> None:
        """Navigate to the context page for the selected album/artist/playlist."""
        item = event.item_data
        panel_id = event.panel_id
        result_type = item.get("resultType", "")

        if panel_id == "albums-panel" or result_type == "album":
            browse_id = item.get("browseId") or item.get("album_id")
            if browse_id:
                await self.app.navigate_to("context", context_type="album", context_id=browse_id)

        elif panel_id == "artists-panel" or result_type == "artist":
            browse_id = item.get("browseId") or item.get("artist_id")
            if browse_id:
                await self.app.navigate_to("context", context_type="artist", context_id=browse_id)

        elif panel_id == "playlists-panel" or result_type == "playlist":
            browse_id = item.get("browseId") or item.get("playlistId")
            if browse_id:
                await self.app.navigate_to(
                    "context", context_type="playlist", context_id=browse_id
                )

    # ------------------------------------------------------------------
    # Vim-style action handler
    # ------------------------------------------------------------------

    async def handle_action(self, action: Action, count: int = 1) -> None:
        """Process vim-style navigation actions dispatched from the app."""
        match action:
            case Action.MOVE_DOWN | Action.MOVE_UP | Action.PAGE_DOWN | Action.PAGE_UP | Action.GO_TOP | Action.GO_BOTTOM | Action.SELECT:
                # Delegate to whichever focusable child has focus.
                focused = self.app.focused
                if focused is not None:
                    # If focused widget is or lives inside a TrackTable, delegate.
                    track_table = focused if isinstance(focused, TrackTable) else None
                    if track_table is None:
                        try:
                            track_table = focused.query_one(TrackTable)
                        except Exception:
                            pass
                    if track_table is not None:
                        await track_table.handle_action(action, count)
                        return

                    # For ListViews in result panels, handle basic navigation.
                    if isinstance(focused, ListView):
                        if action == Action.MOVE_DOWN:
                            focused.action_cursor_down()
                        elif action == Action.MOVE_UP:
                            focused.action_cursor_up()
                        elif action == Action.SELECT:
                            focused.action_select_cursor()

            case Action.FOCUS_NEXT:
                self.app.action_focus_next()

            case Action.FOCUS_PREV:
                self.app.action_focus_previous()

            case Action.TOGGLE_SEARCH_MODE:
                self._toggle_search_mode()

            case Action.FILTER:
                # Focus the search input.
                search_input = self.query_one("#search-input", Input)
                search_input.focus()

            case Action.SEARCH:
                search_input = self.query_one("#search-input", Input)
                search_input.focus()
