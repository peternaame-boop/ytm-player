"""Search page — query YouTube Music and display categorized results."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, cast

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Click
from textual.message import Message
from textual.reactive import reactive
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Input, Label, ListItem, ListView, Static

from ytm_player.config.keymap import Action
from ytm_player.config.settings import get_settings
from ytm_player.ui.widgets.track_table import TrackTable
from ytm_player.utils.formatting import (
    copy_to_clipboard,
    extract_artist,
    get_video_id,
    normalize_tracks,
    truncate,
)

if TYPE_CHECKING:
    from ytm_player.app._base import YTMHostBase

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

    class ItemRightClicked(Message):
        """Emitted when an item in this panel is right-clicked."""

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
        self._right_click_pending = False

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
        title = item.get("title") or item.get("name") or item.get("artist") or "Unknown"

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
        if self._right_click_pending:
            self._right_click_pending = False
            return
        idx = event.list_view.index
        if idx is not None and 0 <= idx < len(self._items):
            panel_id = self.id or ""
            self.post_message(self.ItemSelected(self._items[idx], panel_id))

    def _find_clicked_item_index(self, event: Click) -> int | None:
        """Walk up from the click target to find the ListItem index, or None."""
        node = event.widget
        while node is not None and not isinstance(node, ListItem):
            if node is self:
                break
            node = node.parent
        if not isinstance(node, ListItem):
            return None
        list_view = self.query_one(ListView)
        try:
            return list(list_view.children).index(node)
        except ValueError:
            return None

    def on_click(self, event: Click) -> None:
        """Handle right-click to emit ItemRightClicked."""
        if event.button == 3:
            event.stop()
            self._right_click_pending = True
            idx = self._find_clicked_item_index(event)
            if idx is not None and 0 <= idx < len(self._items):
                self.post_message(self.ItemRightClicked(self._items[idx], self.id or ""))


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

    #search-mode:hover {
        background: $primary 30%;
        border: solid $primary;
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

    #songs-panel:focus-within {
        border: solid $accent;
    }

    #albums-panel:focus-within {
        border: solid $accent;
    }

    #artists-panel:focus-within {
        border: solid $accent;
    }

    #playlists-panel:focus-within {
        border: solid $accent;
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
        last_query: str | None = None,
        search_mode: str | None = None,
        search_results: dict[str, list[dict[str, Any]]] | None = None,
        cursor_row: int | None = None,
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
        # State restoration from navigation.
        self._restore_query = last_query
        self._restore_mode = search_mode
        self._restore_results = search_results
        self._restore_cursor_row = cursor_row
        self._restoring = False

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal(id="search-header"):
                yield Input(
                    placeholder="Search YouTube Music...",
                    id="search-input",
                )
                yield Static("[red]▶[/red] Music", id="search-mode")
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
        if self._restore_mode:
            self.search_mode = self._restore_mode
        else:
            settings = get_settings()
            self.search_mode = settings.search.default_mode

        # Update mode label to match.
        mode_label = self.query_one("#search-mode", Static)
        display = "[red]▶[/red] Music" if self.search_mode == "music" else "[red]▶[/red] All"
        mode_label.update(display)

        # Restore previous search results if navigating back.
        has_restored = bool(self._restore_results and any(self._restore_results.values()))
        if has_restored:
            self._restoring = True
            self._search_results = self._restore_results  # type: ignore[assignment]
            self._last_query = self._restore_query or ""
            search_input = self.query_one("#search-input", Input)
            search_input.value = self._last_query
            self._populate_results(self._search_results)
            # Restore cursor position in songs table.
            if self._restore_cursor_row is not None:
                try:
                    table = self.query_one("#songs-table", TrackTable)
                    if self._restore_cursor_row < table.row_count:
                        table.move_cursor(row=self._restore_cursor_row)
                except Exception:
                    pass
            self._restore_results = None
            self._restore_cursor_row = None

        # Auto-focus the search input ONLY on fresh entry. If we restored
        # results from the page-state cache, leave focus on whatever the
        # user might want to interact with (results table) so navigation
        # back doesn't steal focus.
        if not has_restored:
            try:
                search_input = self.query_one("#search-input", Input)
                search_input.focus()
            except Exception:
                logger.debug("Failed to focus search input on fresh entry", exc_info=True)

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

    def get_nav_state(self) -> dict[str, Any]:
        """Return state to preserve when navigating away."""
        state: dict[str, Any] = {}
        if self._last_query:
            state["last_query"] = self._last_query
        if self.search_mode != "music":
            state["search_mode"] = self.search_mode
        if any(self._search_results.values()):
            state["search_results"] = self._search_results
        try:
            table = self.query_one("#songs-table", TrackTable)
            if table.cursor_row is not None:
                state["cursor_row"] = table.cursor_row
        except Exception:
            pass
        return state

    # ------------------------------------------------------------------
    # Input handling
    # ------------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        """Debounce input changes to fetch suggestions."""
        if event.input.id != "search-input":
            return

        # Suppress suggestions triggered by restoring saved input value.
        if self._restoring:
            self._restoring = False
            return

        query = event.value.strip()

        # The displayed results are tied to ``_last_query``. Once the user
        # types something different, those rows are stale — clear them so
        # that pressing Escape (which focuses the songs-table when it has
        # rows) doesn't strand the user on results from a previous query.
        # Skip clearing while a search is in flight — the worker will
        # populate fresh results when it finishes, and resetting _last_query
        # mid-flight causes the "press Enter twice" bug.
        if query != self._last_query and any(self._search_results.values()) and not self.is_loading:
            self._clear_stale_results()

        if not query:
            self._hide_suggestions()
            self._show_recent_searches()
            return

        # Don't fetch suggestions while a search is already in flight —
        # the overlay would cover incoming results.
        if self.is_loading:
            return

        # Cancel any pending debounce.
        if self._debounce_timer is not None:
            self._debounce_timer.stop()

        settings = get_settings()
        if settings.search.predictive:
            self._debounce_timer = self.set_timer(0.3, lambda: self._fetch_suggestions(query))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Execute search when Enter is pressed."""
        if event.input.id != "search-input":
            return

        query = event.value.strip()
        if not query:
            return

        # Cancel any pending suggestion debounce so it doesn't re-show
        # the overlay after we hide it.
        if self._debounce_timer is not None:
            self._debounce_timer.stop()
            self._debounce_timer = None
        self._hide_suggestions()
        self._last_query = query
        self.run_worker(self._execute_search(query), name="search", exclusive=True)

    def on_key(self, event: object) -> None:
        """Handle Escape on the search input: hide suggestions + defocus."""
        from textual.events import Key

        if not isinstance(event, Key):
            return
        if event.key != "escape":
            return

        # Only act when the search input is focused OR the suggestions
        # dropdown is visible. Otherwise let Escape bubble normally.
        focused = self.app.focused
        input_focused = focused is not None and getattr(focused, "id", None) == "search-input"

        suggestions_visible = False
        try:
            overlay = self.query_one("#suggestion-overlay", SuggestionList)
            suggestions_visible = overlay.has_class("visible")
        except Exception:
            pass

        if not (input_focused or suggestions_visible):
            return

        event.stop()
        event.prevent_default()
        if suggestions_visible:
            self._hide_suggestions()
        # Move focus to the songs results table if it has rows; otherwise
        # blur entirely so vim-style nav keys reach the app.
        try:
            songs_table = self.query_one("#songs-table", TrackTable)
            if songs_table.row_count > 0:
                songs_table.focus()
            else:
                self.app.set_focus(None)
        except Exception:
            try:
                self.app.set_focus(None)
            except Exception:
                logger.debug("Failed to clear focus on Escape", exc_info=True)

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
            ytmusic = cast("YTMHostBase", self.app).ytmusic
            assert ytmusic is not None
            suggestions = await ytmusic.get_search_suggestions(query)
            # Don't show suggestions if a search was submitted while we
            # were fetching — the overlay would cover the incoming results.
            if self.is_loading:
                return
            overlay = self.query_one("#suggestion-overlay", SuggestionList)
            overlay.show_suggestions(suggestions)
        except Exception:
            logger.exception("Failed to load suggestions for %r", query)

    def _hide_suggestions(self) -> None:
        try:
            overlay = self.query_one("#suggestion-overlay", SuggestionList)
            overlay.hide()
        except Exception:
            logger.debug("Failed to hide search suggestions", exc_info=True)

    def _show_recent_searches(self) -> None:
        """Display recent search history when input is empty."""
        self.run_worker(self._load_recent_searches(), name="recent", exclusive=True)

    async def _load_recent_searches(self) -> None:
        """Load and display recent searches from history."""
        try:
            history = cast("YTMHostBase", self.app).history
            assert history is not None
            entries = await history.get_search_history(limit=10)
            if entries:
                suggestions = [entry["query"] for entry in entries]
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
                # Cancel pending debounce and suppress the on_input_changed
                # that fires from setting the input value programmatically.
                if self._debounce_timer is not None:
                    self._debounce_timer.stop()
                    self._debounce_timer = None
                self._restoring = True
                search_input = self.query_one("#search-input", Input)
                search_input.value = query
                self._hide_suggestions()
                self._last_query = query
                self.run_worker(self._execute_search(query), name="search", exclusive=True)

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
            self._last_query = query
            self._populate_results(results)

            # Log the search to history.
            total_count = sum(len(v) for v in results.values())
            try:
                history = cast("YTMHostBase", self.app).history
                assert history is not None
                await history.log_search(
                    query=query,
                    filter_mode=self.search_mode,
                    result_count=total_count,
                )
            except Exception:
                logger.debug("Failed to log search to history", exc_info=True)

            self._update_loading("")
        except asyncio.CancelledError:
            # Worker was cancelled (exclusive collision, navigation away,
            # focus-change side effect, etc). Clear the loading indicator
            # so the UI doesn't lie about an in-flight search, then re-raise
            # so the worker tears down properly.
            logger.debug("search: worker cancelled for query=%r", query)
            self._update_loading("")
            raise
        except Exception:
            logger.exception("Search failed for query=%r", query)
            self._update_loading("Search failed. Try again.")
        finally:
            self.is_loading = False

    async def _search_music(self, query: str) -> dict[str, list[dict[str, Any]]]:
        """Execute filtered searches for each category (music-only mode)."""
        results: dict[str, list[dict[str, Any]]] = {
            "songs": [],
            "albums": [],
            "artists": [],
            "playlists": [],
        }

        ytmusic = cast("YTMHostBase", self.app).ytmusic
        assert ytmusic is not None
        # Run all four searches concurrently.
        tasks = {
            category: ytmusic.search(query, filter=api_filter, limit=10)
            for category, api_filter in self._MUSIC_FILTERS.items()
        }

        gathered = await asyncio.gather(*tasks.values(), return_exceptions=True)

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

        ytmusic = cast("YTMHostBase", self.app).ytmusic
        assert ytmusic is not None
        raw = await ytmusic.search(query, filter=None, limit=40)

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
        songs_table.load_tracks(normalize_tracks(results.get("songs", [])))

        # Albums, Artists, Playlists go into SearchResultPanels.
        albums_panel = self.query_one("#albums-panel", SearchResultPanel)
        albums_panel.load_items(results.get("albums", []))

        artists_panel = self.query_one("#artists-panel", SearchResultPanel)
        artists_panel.load_items(results.get("artists", []))

        playlists_panel = self.query_one("#playlists-panel", SearchResultPanel)
        playlists_panel.load_items(results.get("playlists", []))

    def _clear_stale_results(self) -> None:
        """Empty all result panels.

        Called when the user types a query that no longer matches what's
        on screen, so a follow-up Escape doesn't focus the songs-table on
        results from a previous search.
        """
        self._search_results = {
            "songs": [],
            "albums": [],
            "artists": [],
            "playlists": [],
        }
        self._last_query = ""
        try:
            self._populate_results(self._search_results)
        except Exception:
            logger.debug("Failed to clear stale search results", exc_info=True)

    def _update_loading(self, text: str) -> None:
        try:
            label = self.query_one("#loading-msg", Static)
            label.update(text)
        except Exception:
            logger.debug("Failed to update search loading indicator", exc_info=True)

    # ------------------------------------------------------------------
    # Mode toggle
    # ------------------------------------------------------------------

    def on_click(self, event: Click) -> None:
        """Toggle search mode when the mode label is clicked."""
        widget = event.widget
        if widget is None:
            return
        # Match the #search-mode Static or any child of it.
        try:
            if (
                widget.id == "search-mode"
                or self.query_one("#search-mode", Static) in widget.ancestors
            ):
                event.stop()
                self._toggle_search_mode()
        except Exception:
            pass

    def _toggle_search_mode(self) -> None:
        """Switch between music-only and all-results mode."""
        if self.search_mode == "music":
            self.search_mode = "all"
        else:
            self.search_mode = "music"

        # Update the mode indicator.
        mode_label = self.query_one("#search-mode", Static)
        display = "[red]▶[/red] Music" if self.search_mode == "music" else "[red]▶[/red] All"
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
        """Play the selected song and populate the queue with search results."""
        event.stop()
        track = event.track
        video_id = get_video_id(track)
        if video_id:
            table = self.query_one("#songs-table", TrackTable)
            host = cast("YTMHostBase", self.app)
            host.queue.clear()
            host.queue.add_multiple(table.tracks)
            host.queue.jump_to_real(event.index)
            # Search results are ephemeral — clear context so a later
            # shuffle toggle isn't saved against whatever collection
            # was previously playing (TP-7).
            host.queue.set_context(None)
            await host.play_track(track)

    async def on_search_result_panel_item_selected(
        self, event: SearchResultPanel.ItemSelected
    ) -> None:
        """Navigate to the context page for the selected album/artist/playlist."""
        item = event.item_data
        panel_id = event.panel_id
        result_type = item.get("resultType", "")
        host = cast("YTMHostBase", self.app)

        if panel_id == "albums-panel" or result_type == "album":
            browse_id = item.get("browseId") or item.get("album_id")
            if browse_id:
                await host.navigate_to("context", context_type="album", context_id=browse_id)

        elif panel_id == "artists-panel" or result_type == "artist":
            browse_id = item.get("browseId") or item.get("artist_id")
            if browse_id:
                await host.navigate_to("context", context_type="artist", context_id=browse_id)

        elif panel_id == "playlists-panel" or result_type == "playlist":
            browse_id = item.get("browseId") or item.get("playlistId")
            if browse_id:
                await host.navigate_to("context", context_type="playlist", context_id=browse_id)

    def on_search_result_panel_item_right_clicked(
        self, event: SearchResultPanel.ItemRightClicked
    ) -> None:
        """Open context menu for right-clicked album/artist/playlist."""
        from ytm_player.ui.popups.actions import ActionsPopup

        item = event.item_data
        panel_id = event.panel_id
        host = cast("YTMHostBase", self.app)

        # Map panel ID to item type for ActionsPopup.
        type_map = {
            "albums-panel": "album",
            "artists-panel": "artist",
            "playlists-panel": "playlist",
        }
        item_type = type_map.get(panel_id, "track")

        def _handle_action(action_id: str | None) -> None:
            if action_id is None:
                return

            if action_id in ("play_all", "shuffle_play"):
                browse_id = (
                    item.get("browseId") or item.get("album_id") or item.get("playlistId") or ""
                )
                ctx_type = item_type if item_type in ("album", "playlist") else None
                if browse_id and ctx_type:
                    host.run_worker(
                        host.navigate_to("context", context_type=ctx_type, context_id=browse_id)
                    )

            elif action_id == "go_to_artist":
                artists = item.get("artists") or []
                if isinstance(artists, list) and artists:
                    artist_id = artists[0].get("id") or artists[0].get("browseId", "")
                elif item_type == "artist":
                    artist_id = item.get("browseId") or item.get("artist_id") or ""
                else:
                    artist_id = ""
                if artist_id:
                    host.run_worker(
                        host.navigate_to("context", context_type="artist", context_id=artist_id)
                    )

            elif action_id in ("play_top_songs", "start_radio", "view_albums", "view_similar"):
                browse_id = item.get("browseId") or item.get("artist_id") or ""
                if browse_id:
                    host.run_worker(
                        host.navigate_to("context", context_type="artist", context_id=browse_id)
                    )

            elif action_id == "copy_link":
                browse_id = (
                    item.get("browseId")
                    or item.get("album_id")
                    or item.get("playlistId")
                    or item.get("artist_id")
                    or ""
                )
                if browse_id:
                    link = f"https://music.youtube.com/browse/{browse_id}"
                    if copy_to_clipboard(link):
                        host.notify("Link copied", timeout=2)
                    else:
                        host.notify(link, timeout=5)

        host.push_screen(ActionsPopup(item, item_type=item_type), _handle_action)

    # ------------------------------------------------------------------
    # Vim-style action handler
    # ------------------------------------------------------------------

    async def handle_action(self, action: Action, count: int = 1) -> None:
        """Process vim-style navigation actions dispatched from the app."""
        match action:
            case (
                Action.MOVE_DOWN
                | Action.MOVE_UP
                | Action.PAGE_DOWN
                | Action.PAGE_UP
                | Action.GO_TOP
                | Action.GO_BOTTOM
                | Action.SELECT
            ):
                # Delegate to whichever focusable child has focus.
                focused = self.app.focused
                if focused is not None:
                    # If focused widget is or lives inside a TrackTable, delegate.
                    track_table = focused if isinstance(focused, TrackTable) else None
                    if track_table is None:
                        try:
                            track_table = focused.query_one(TrackTable)
                        except Exception:
                            logger.debug(
                                "Failed to find TrackTable for focused widget", exc_info=True
                            )
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
