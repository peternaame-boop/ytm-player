"""Search page — query YouTube Music and display categorized results.

Under the search box sit the web app's filter chips: All, Songs, Albums,
Artists and Community playlists. All shows the top result, songs and one shelf
per category. Any other chip shows only that category and fetches more of it,
songs as a full-width list and the rest as a grid of cover cards.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, ClassVar, cast

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, ItemGrid, Vertical, VerticalScroll
from textual.events import Click
from textual.message import Message
from textual.reactive import reactive
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Input, Label, ListItem, ListView, Static

from ytm_player.config.keymap import Action
from ytm_player.config.settings import get_settings
from ytm_player.ui.icons import icon
from ytm_player.ui.pages.home import (
    CARD_WIDTH,
    HomeCard,
    Shelf,
    card_subtitle,
    navigate_browse_item,
    relayout_soon,
)
from ytm_player.ui.theme import get_theme
from ytm_player.ui.widgets.cover import Cover, best_thumbnail
from ytm_player.ui.widgets.track_table import TrackTable
from ytm_player.utils.formatting import (
    copy_to_clipboard,
    extract_artist,
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
            idx = self._find_clicked_item_index(event)
            if idx is not None and 0 <= idx < len(self._items):
                # Arm the suppress flag only when the right-click hit a real
                # item: that click also fires a phantom ListView.Selected
                # which on_list_view_selected must swallow. A right-click on
                # empty space fires no Selected, so a lingering flag would
                # eat the next legitimate selection instead.
                self._right_click_pending = True
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


# ---------------------------------------------------------------------------
# Web layout: top result card and shelves of cover cards
# ---------------------------------------------------------------------------

# Shelf title and the panel id its right-click menu maps to, per category.
# The filter chips, as the web app labels them, in its order.
FILTER_CHIPS = (("all", "All"), ("songs", "Songs"), ("albums", "Albums"),
                ("artists", "Artists"), ("playlists", "Community playlists"))  # fmt: skip
# Results a chip other than All fetches for its category.
FILTERED_LIMIT = 40


class SearchChips(Horizontal):
    """All, Songs, Albums, Artists, Community playlists. The active chip is filled."""

    DEFAULT_CSS = """
    SearchChips { height: 2; padding: 0 1; display: none; }
    SearchChips.visible { display: block; }
    SearchChips > .search-chip { width: auto; height: 1; padding: 0 2; margin-right: 1;
        background: $boost; }
    SearchChips > .search-chip:hover { background: $primary 30%; }
    SearchChips > .search-chip.-active { background: $foreground; color: $background; }
    """

    def compose(self) -> ComposeResult:
        for key, label in FILTER_CHIPS:
            yield Static(label, id=f"chip-{key}", classes="search-chip")

    def on_click(self, event: Click) -> None:
        widget = event.widget
        if widget is None or not (widget.id or "").startswith("chip-") or event.button != 1:
            return
        event.stop()
        page = next((a for a in self.ancestors if isinstance(a, SearchPage)), None)
        if page is not None:
            page.show_filter((widget.id or "").removeprefix("chip-"))


class SpellingNote(Horizontal):
    """The web app's spelling note above the results.

    "Showing results for <corrected>  Search instead for <original>" when the
    server corrected the query, or "Did you mean: <corrected>" when it only
    suggests one. Changed words are in the primary colour. Each query is a link.
    """

    DEFAULT_CSS = """
    SpellingNote { height: 2; padding: 0 1; display: none; }
    SpellingNote.visible { display: block; }
    SpellingNote > Static { width: auto; height: 1; }
    SpellingNote > .spell-label { color: $text-muted; margin-right: 1; }
    SpellingNote > .spell-link:hover { text-style: underline; }
    SpellingNote > #spell-original { margin-right: 0; }
    SpellingNote > #spell-instead { margin-left: 3; }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.correction: dict[str, Any] | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="spell-lead", classes="spell-label")
        yield Static("", id="spell-corrected", classes="spell-link")
        yield Static("", id="spell-instead", classes="spell-label")
        yield Static("", id="spell-original", classes="spell-link")

    def show(self, correction: dict[str, Any] | None) -> None:
        """Show *correction* (see YTMusicService.get_search_correction), or hide on None."""
        self.correction = correction
        self.set_class(correction is not None, "visible")
        if correction is None:
            return
        primary = get_theme().primary
        corrected = Text()
        for text, changed in correction.get("runs") or [(correction["corrected"], True)]:
            corrected.append(text, style=primary if changed else "")
        showing = correction.get("kind") == "showing"
        self.query_one("#spell-lead", Static).update(
            "Showing results for" if showing else "Did you mean:"
        )
        self.query_one("#spell-corrected", Static).update(corrected)
        self.query_one("#spell-instead", Static).update("Search instead for" if showing else "")
        self.query_one("#spell-original", Static).update(
            correction.get("original", "") if showing else ""
        )
        for widget_id in ("#spell-instead", "#spell-original"):
            self.query_one(widget_id).display = showing

    def on_click(self, event: Click) -> None:
        widget = event.widget
        if widget is None or event.button != 1 or self.correction is None:
            return
        page = next((a for a in self.ancestors if isinstance(a, SearchPage)), None)
        if page is None:
            return
        if widget.id == "spell-corrected":
            event.stop()
            page.search_for(self.correction["corrected"])
        elif widget.id == "spell-original":
            event.stop()
            page.search_for(self.correction["original"], ignore_spelling=True)


_SHELVES = (
    ("albums", "Albums", "albums-panel"),
    ("artists", "Artists", "artists-panel"),
    ("playlists", "Community playlists", "playlists-panel"),
)
_PANEL_FOR_TYPE = {"album": "albums-panel", "single": "albums-panel", "ep": "albums-panel",
                   "artist": "artists-panel", "playlist": "playlists-panel"}  # fmt: skip


def as_card(item: dict[str, Any]) -> dict[str, Any]:
    """A search result shaped for a cover card: artists carry their name in ``artist``."""
    card = dict(item)
    if not card.get("title"):
        artist = (card.get("artists") or [{}])[0]
        card["title"] = card.get("artist") or artist.get("name", "")
        card.setdefault("browseId", artist.get("id"))
    if (card.get("resultType") or "").lower() == "playlist" and not card.get("description"):
        card["description"] = " • ".join(
            p for p in ("Playlist", card.get("author") or "", card.get("itemCount") or "") if p
        )
    if (card.get("resultType") or "").lower() == "artist" and card.get("subscribers"):
        card["description"] = f"Artist • {card['subscribers']} subscribers"
    return card


def top_result(raw: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The item an unfiltered search marks as its top result, shaped for a card."""
    for item in raw:
        if item.get("category") == "Top result":
            return as_card(item)
    return None


class TopResult(Horizontal, can_focus=True):
    """The web's "Top result" card: a cover, the name, what it is, and Play for a song or album."""

    DEFAULT_CSS = """
    TopResult { height: 13; width: 1fr; padding: 1 1 0 1; background: $boost; }
    TopResult > Cover { width: 22; height: 11; margin-right: 2; }
    TopResult .top-text { width: 1fr; height: auto; }
    TopResult .top-title { text-style: bold; margin-top: 3; }
    TopResult .top-sub { width: 1fr; color: $text-muted; }
    TopResult #top-play { width: auto; height: 3; padding: 1 2; margin-top: 1;
        background: $foreground; color: $background; text-style: bold; }
    TopResult #top-play:hover { background: $primary; }
    TopResult:focus .top-title { color: $accent; text-style: bold underline; }
    """

    def __init__(self, item: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.item = item

    def compose(self) -> ComposeResult:
        kind = (self.item.get("resultType") or "").lower()
        yield Cover(best_thumbnail(self.item, min_width=226))
        with Vertical(classes="top-text"):
            yield Label(self.item.get("title", ""), classes="top-title")
            yield Label(card_subtitle(self.item) if kind != "artist" else
                        self.item.get("description") or "Artist", classes="top-sub")  # fmt: skip
            if kind in ("song", "video") or (kind in ("album", "playlist") and self._playlist_id):
                yield Static(f"{icon('play')}  Play", id="top-play")

    @property
    def _playlist_id(self) -> str:
        return self.item.get("playlistId") or self.item.get("audioPlaylistId") or ""

    def on_click(self, event: Click) -> None:
        if event.button != 1:
            return
        event.stop()
        play = event.widget is not None and event.widget.id == "top-play"
        self.app.run_worker(self._open(play=play), group="search-open", exclusive=True)

    async def _open(self, *, play: bool) -> None:
        host = cast("YTMHostBase", self.app)
        kind = (self.item.get("resultType") or "").lower()
        if play and kind in ("album", "playlist"):
            await host._play_playlist(self._playlist_id, self.item.get("title", ""))
        else:
            await navigate_browse_item(host, self.item)


# ---------------------------------------------------------------------------
# Search page
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
        padding: 0 2;
    }

    #search-top {
        height: 15;
        margin-bottom: 1;
    }

    #top-result-box {
        width: 1fr;
        min-width: 40;
        margin-right: 2;
    }

    #songs-panel {
        width: 2fr;
        border: solid $secondary;
        padding: 0 1;
    }

    #search-shelves {
        height: auto;
    }

    .search-legacy-panel {
        display: none;
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

    #search-filtered {
        display: none;
        height: auto;
        padding: 0 1;
    }

    SearchPage.-only-songs #top-result-box,
    SearchPage.-only-songs #search-shelves {
        display: none;
    }

    SearchPage.-only-songs #search-top {
        height: 80vh;
    }

    SearchPage.-only-cards #search-top,
    SearchPage.-only-cards #search-shelves {
        display: none;
    }

    SearchPage.-only-cards #search-filtered {
        display: block;
    }

    """

    search_mode: reactive[str] = reactive("music")
    is_loading: reactive[bool] = reactive(False)

    # A search supersedes the previous search; a suggestions or recent-searches
    # fetch supersedes the previous one of those (they fill the same overlay).
    # Separate groups: typing while a search runs must not cancel the search.
    SEARCH_GROUP: ClassVar[str] = "search"
    SUGGEST_GROUP: ClassVar[str] = "search-suggest"

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
            "top": [],
        }
        self.search_filter = "all"
        # More results per (query, chip), fetched the first time a chip is picked.
        self._filtered_cache: dict[tuple[str, str, bool], list[dict[str, Any]]] = {}
        # True while the shown results are for the query as typed, after
        # "Search instead for" turned the server's spelling correction off.
        self._ignore_spelling = False
        # State restoration from navigation.
        self._restore_query = last_query
        self._restore_mode = search_mode
        self._restore_results = search_results
        self._restore_cursor_row = cursor_row
        self._restoring = False

    def compose(self) -> ComposeResult:
        mode_color = get_theme().primary
        with Vertical():
            with Horizontal(id="search-header"):
                yield Input(
                    placeholder="Search YouTube Music...",
                    id="search-input",
                )
                yield Static(f"[{mode_color}]▶[/{mode_color}] Music", id="search-mode")
            yield SuggestionList(id="suggestion-overlay")
            yield Static("", id="loading-msg", classes="loading-indicator")
            yield SpellingNote(id="spelling-note")
            yield SearchChips(id="search-chips")
            with VerticalScroll(id="search-results"):
                with Horizontal(id="search-top"):
                    with Vertical(id="top-result-box"):
                        yield Label("Top result", classes="panel-title")
                    with Vertical(id="songs-panel"):
                        yield Label("Songs", classes="panel-title")
                        yield TrackTable(
                            show_album=True,
                            show_index=True,
                            id="songs-table",
                        )
                yield Vertical(id="search-shelves")
                yield ItemGrid(id="search-filtered", min_column_width=CARD_WIDTH)
            # The text panels stay for their keyboard and menu handlers. The
            # shelves above show the same results as cover cards, as on the web.
            for name, pid in (("Albums", "albums"), ("Artists", "artists"),
                              ("Playlists", "playlists")):  # fmt: skip
                yield SearchResultPanel(name, id=f"{pid}-panel", classes="search-legacy-panel")

    def on_mount(self) -> None:
        if self._restore_mode:
            self.search_mode = self._restore_mode
        else:
            settings = get_settings()
            self.search_mode = settings.search.default_mode

        # Update mode label to match.
        mode_label = self.query_one("#search-mode", Static)
        mode_color = get_theme().primary
        display = (
            f"[{mode_color}]▶[/{mode_color}] Music"
            if self.search_mode == "music"
            else f"[{mode_color}]▶[/{mode_color}] All"
        )
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
            "top": [],
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
        self._start_search(query)

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
            group=self.SUGGEST_GROUP,
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
        self.run_worker(
            self._load_recent_searches(), name="recent", group=self.SUGGEST_GROUP, exclusive=True
        )

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
                search_input = self.query_one("#search-input", Input)
                # Setting .value only fires on_input_changed when the value
                # actually changes. If the picked suggestion equals the current
                # text, no Changed event arrives to reset _restoring — so only
                # arm it when the value will change, else it leaks and swallows
                # the user's next real keystroke.
                if search_input.value != query:
                    self._restoring = True
                    search_input.value = query
                else:
                    self._restoring = False
                self._hide_suggestions()
                self._last_query = query
                self._start_search(query)

    # ------------------------------------------------------------------
    # Search execution
    # ------------------------------------------------------------------

    def search_for(self, query: str, *, ignore_spelling: bool = False) -> None:
        """Put *query* in the search box and search it, as the spelling note's links do."""
        search_input = self.query_one("#search-input", Input)
        if search_input.value != query:
            # Same guard as a picked suggestion: the Changed event must not
            # open the suggestions overlay over the new results.
            self._restoring = True
            search_input.value = query
        self._hide_suggestions()
        self._last_query = query
        self._start_search(query, ignore_spelling=ignore_spelling)

    def _start_search(self, query: str, *, ignore_spelling: bool = False) -> None:
        """Run the search as this page's worker.

        A suggestions fetch still in flight is cancelled: landing after a
        fast search, it would cover the results with the overlay.
        *ignore_spelling* searches the query as typed. A new query typed or
        picked by the user always starts with the server's correction on.
        """
        self._ignore_spelling = ignore_spelling
        self.workers.cancel_group(self, self.SUGGEST_GROUP)
        self.run_worker(
            self._execute_search(query), name="search", group=self.SEARCH_GROUP, exclusive=True
        )

    async def _execute_search(self, query: str) -> None:
        """Run the search and populate all result panels."""
        self.is_loading = True
        self._update_loading("Searching...")

        try:
            search = self._search_music if self.search_mode == "music" else self._search_all
            results, correction = await asyncio.gather(search(query), self._fetch_correction(query))

            self._search_results = results
            self._last_query = query
            self._populate_results(results)
            self.query_one("#spelling-note", SpellingNote).show(correction)

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

    def _spelling_kwargs(self) -> dict[str, Any]:
        """Search keywords for the current query: ignore_spelling only when it is on."""
        return {"ignore_spelling": True} if self._ignore_spelling else {}

    async def _fetch_correction(self, query: str) -> dict[str, Any] | None:
        """The spelling note for *query*, fetched beside the results. None on any failure."""
        ytmusic = cast("YTMHostBase", self.app).ytmusic
        if ytmusic is None:
            return None
        try:
            return await ytmusic.get_search_correction(query, ignore_spelling=self._ignore_spelling)
        except Exception:
            logger.exception("Spelling note lookup failed for %r", query)
            return None

    async def _search_music(self, query: str) -> dict[str, list[dict[str, Any]]]:
        """Execute filtered searches for each category (music-only mode)."""
        results: dict[str, list[dict[str, Any]]] = {
            "songs": [],
            "albums": [],
            "artists": [],
            "playlists": [],
            "top": [],
        }

        ytmusic = cast("YTMHostBase", self.app).ytmusic
        assert ytmusic is not None
        # Run all four searches concurrently.
        tasks = {
            category: ytmusic.search(query, filter=api_filter, limit=10, **self._spelling_kwargs())
            for category, api_filter in self._MUSIC_FILTERS.items()
        }
        tasks["top"] = ytmusic.search(query, filter=None, limit=1, **self._spelling_kwargs())

        gathered = await asyncio.gather(*tasks.values(), return_exceptions=True)

        for category, result in zip(tasks.keys(), gathered):
            if isinstance(result, Exception):
                logger.error("Search for %s failed: %s", category, result)
            elif category == "top" and isinstance(result, list):
                top = top_result(result)
                results["top"] = [top] if top else []
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
            "top": [],
        }

        ytmusic = cast("YTMHostBase", self.app).ytmusic
        assert ytmusic is not None
        raw = await ytmusic.search(query, filter=None, limit=40, **self._spelling_kwargs())
        top = top_result(raw)
        results["top"] = [top] if top else []

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

    def show_filter(self, key: str) -> None:
        """Show the results of chip *key*: "all" or one category."""
        if key not in dict(FILTER_CHIPS):
            return
        self._mark_filter(key)
        if key == "all":
            self.query_one("#songs-table", TrackTable).load_tracks(
                normalize_tracks(self._search_results.get("songs", []))
            )
            return
        self.run_worker(
            self._load_filtered(self._last_query, key), group="search-filter", exclusive=True
        )

    def _mark_filter(self, key: str) -> None:
        """Light chip *key* and lay the page out for it."""
        self.search_filter = key
        for chip_key, _label in FILTER_CHIPS:
            self.query_one(f"#chip-{chip_key}").set_class(chip_key == key, "-active")
        self.set_class(key == "songs", "-only-songs")
        self.set_class(key not in ("all", "songs"), "-only-cards")

    async def _load_filtered(self, query: str, key: str) -> None:
        """Fetch more results of one category, then show them."""
        if not query:
            return
        cache_key = (query, key, self._ignore_spelling)
        items = self._filtered_cache.get(cache_key)
        if items is None:
            ytmusic = cast("YTMHostBase", self.app).ytmusic
            api_filter = self._MUSIC_FILTERS[key]
            try:
                items = (
                    await ytmusic.search(
                        query,
                        filter=api_filter,
                        limit=FILTERED_LIMIT,
                        **self._spelling_kwargs(),
                    )
                    if ytmusic
                    else []
                )
            except Exception:
                logger.exception("Filtered search failed for %r (%s)", query, key)
                items = []
            items = items or self._search_results.get(key, [])
            self._filtered_cache[cache_key] = items
        if self.search_filter != key or self._last_query != query:
            return
        if key == "songs":
            self.query_one("#songs-table", TrackTable).load_tracks(normalize_tracks(items))
            return
        grid = self.query_one("#search-filtered", ItemGrid)
        await grid.remove_children()
        if items:
            await grid.mount(*[HomeCard(as_card(item)) for item in items])
            relayout_soon(self)

    def _populate_results(self, results: dict[str, list[dict[str, Any]]]) -> None:
        """Push result data into each panel widget."""
        # A new search starts on the All chip. The chips show once there is something to filter.
        self._mark_filter("all")
        self.query_one("#search-chips", SearchChips).set_class(
            any(results.get(k) for k, _label in FILTER_CHIPS if k != "all"), "visible"
        )
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

        self.run_worker(self._mount_web_results(results), group="search-cards", exclusive=True)

    async def _mount_web_results(self, results: dict[str, list[dict[str, Any]]]) -> None:
        """The top result card and one shelf of cover cards per category."""
        box = self.query_one("#top-result-box", Vertical)
        await box.query(TopResult).remove()
        top = results.get("top") or []
        if top:
            await box.mount(TopResult(top[0]))
        shelves = self.query_one("#search-shelves", Vertical)
        await shelves.remove_children()
        new = [
            Shelf(title, [as_card(item) for item in results.get(key, [])], id=f"shelf-{key}")
            for key, title, _ in _SHELVES
            if results.get(key)
        ]
        if new:
            await shelves.mount(*new)
            relayout_soon(self)
        warm = getattr(self.app, "warm_streams", None)
        if warm is not None:
            warm([t.get("videoId", "") for t in top + results.get("songs", [])])

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
            "top": [],
        }
        self._last_query = ""
        try:
            self._populate_results(self._search_results)
            self.query_one("#spelling-note", SpellingNote).show(None)
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
        """Toggle search mode on the mode label. A right-click on a card opens its menu."""
        widget = event.widget
        if widget is None:
            return
        if event.button == 3:
            card = next((w for w in widget.ancestors_with_self if hasattr(w, "item")), None)
            item = getattr(card, "item", None) if card is not None else None
            if isinstance(item, dict):
                event.stop()
                kind = (item.get("resultType") or "").lower()
                panel = _PANEL_FOR_TYPE.get(kind, "")
                self.on_search_result_panel_item_right_clicked(
                    SearchResultPanel.ItemRightClicked(item, panel)
                )
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
        mode_color = get_theme().primary
        display = (
            f"[{mode_color}]▶[/{mode_color}] Music"
            if self.search_mode == "music"
            else f"[{mode_color}]▶[/{mode_color}] All"
        )
        mode_label.update(display)

        # Re-run the last search with the new mode if we have a query.
        if self._last_query:
            self._start_search(self._last_query, ignore_spelling=self._ignore_spelling)

    # ------------------------------------------------------------------
    # Track and item selection handlers
    # ------------------------------------------------------------------

    async def on_track_table_track_selected(self, event: TrackTable.TrackSelected) -> None:
        """Play the selected song and populate the queue with search results."""
        event.stop()
        table = self.query_one("#songs-table", TrackTable)
        host = cast("YTMHostBase", self.app)
        await host._replace_queue_and_play(
            table.tracks,
            entity_id=None,
            start_index=event.index,
            autoplay=False,
        )
        await host.play_track(event.track)

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
                host.run_worker(host._play_entity_and_navigate(action_id, item, item_type))
                return

            if action_id == "toggle_subscribe":
                browse_id = item.get("browseId") or item.get("artist_id") or ""
                if not browse_id:
                    host.notify("No ID available for this item.", severity="warning", timeout=2)
                else:
                    host.run_worker(host._toggle_artist_subscribe_simple(browse_id))
                return

            if action_id == "copy_link":
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
                return

            host.run_worker(host._dispatch_entity_action(action_id, item, item_type))

        host.push_screen(ActionsPopup(item, item_type=item_type, source="search"), _handle_action)

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
                | Action.MARK_TOGGLE
                | Action.MARK_RANGE
                | Action.MARK_CLEAR
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

            case Action.TOGGLE_SEARCH_MODE:
                self._toggle_search_mode()

            case Action.FILTER:
                # Focus the search input.
                search_input = self.query_one("#search-input", Input)
                search_input.focus()

            case Action.SEARCH:
                search_input = self.query_one("#search-input", Input)
                search_input.focus()
