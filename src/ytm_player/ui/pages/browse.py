"""Browse page — moods, genres, charts, recommendations, and new releases."""

from __future__ import annotations

import logging
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Click
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView, Static

from ytm_player.config.keymap import Action
from ytm_player.ui.widgets.track_table import TrackTable
from ytm_player.utils.formatting import extract_artist, get_video_id, truncate

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tab bar
# ---------------------------------------------------------------------------

_TABS = ("For You", "Moods & Genres", "Charts", "New Releases")


class BrowseTabBar(Widget):
    """A horizontal tab selector for the Browse page sections."""

    DEFAULT_CSS = """
    BrowseTabBar {
        height: 3;
        width: 1fr;
        padding: 0 1;
    }

    BrowseTabBar Horizontal {
        height: 3;
        align: left middle;
    }

    BrowseTabBar .tab-item {
        padding: 0 2;
        height: 1;
        content-align: center middle;
        color: $text-muted;
    }

    BrowseTabBar .tab-item.active {
        text-style: bold;
        color: $text;
        border-bottom: solid $primary;
    }
    """

    active_tab: reactive[int] = reactive(0)

    class TabChanged(Message):
        """Emitted when the user switches to a different tab."""

        def __init__(self, index: int, label: str) -> None:
            super().__init__()
            self.index = index
            self.label = label

    def compose(self) -> ComposeResult:
        with Horizontal():
            for i, label in enumerate(_TABS):
                classes = "tab-item active" if i == 0 else "tab-item"
                yield Static(f" {label} ", id=f"tab-{i}", classes=classes)

    def on_click(self, event: Click) -> None:
        """Handle click on a tab label."""
        # Walk up from the click target to find the tab Static.
        node = event.widget
        while node is not None and not (isinstance(node, Static) and "tab-item" in node.classes):
            node = node.parent
        if node is None:
            return
        for i in range(len(_TABS)):
            if node.id == f"tab-{i}":
                self.switch_to(i)
                return

    def switch_to(self, index: int) -> None:
        """Activate the tab at *index*."""
        if index == self.active_tab:
            return
        # Update CSS classes.
        for i in range(len(_TABS)):
            tab = self.query_one(f"#tab-{i}", Static)
            if i == index:
                tab.add_class("active")
            else:
                tab.remove_class("active")
        self.active_tab = index
        self.post_message(self.TabChanged(index, _TABS[index]))


# ---------------------------------------------------------------------------
# Content sections
# ---------------------------------------------------------------------------


class ForYouSection(Widget):
    """Personalised recommendation shelves from get_home()."""

    DEFAULT_CSS = """
    ForYouSection {
        height: 1fr;
        width: 1fr;
        padding: 0 1;
    }

    ForYouSection .shelf-title {
        text-style: bold;
        color: $text;
        padding: 1 0 0 0;
    }

    ForYouSection .shelf-items {
        height: auto;
        max-height: 6;
    }

    ForYouSection .loading {
        height: 1fr;
        width: 1fr;
        content-align: center middle;
        color: $text-muted;
    }
    """

    is_loading: reactive[bool] = reactive(True)

    class ItemSelected(Message):
        def __init__(self, item: dict[str, Any]) -> None:
            super().__init__()
            self.item = item

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._shelves: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield Static("Loading recommendations...", id="foryou-loading", classes="loading")
        yield Vertical(id="foryou-shelves")

    def on_unmount(self) -> None:
        """Release shelf data to prevent memory retention."""
        self._shelves.clear()

    async def load_data(self) -> None:
        """Fetch and display personalised home shelves."""
        self.is_loading = True
        try:
            self._shelves = await self.app.ytmusic.get_home()
        except Exception:
            logger.debug("Failed to load home recommendations", exc_info=True)
            self._show_error("Failed to load recommendations.")
            self.is_loading = False
            return

        try:
            await self._populate_shelves()
        except Exception:
            logger.debug("Failed to render home shelves", exc_info=True)
            # Clean up any partially-mounted widgets.
            try:
                container = self.query_one("#foryou-shelves", Vertical)
                await container.remove_children()
            except Exception:
                pass
            self._show_error("Failed to load recommendations.")
        finally:
            self.is_loading = False

    async def _populate_shelves(self) -> None:
        loading = self.query_one("#foryou-loading", Static)
        loading.display = False

        container = self.query_one("#foryou-shelves", Vertical)
        # Clear _shelf_items references from old ListViews before removing.
        for lv in container.query(ListView):
            if hasattr(lv, "_shelf_items"):
                lv._shelf_items = []  # type: ignore[attr-defined]
        await container.remove_children()

        if not self._shelves:
            await container.mount(Static("No recommendations available.", classes="loading"))
            return

        for shelf in self._shelves:
            title = shelf.get("title", "Recommendations")
            contents = shelf.get("contents", [])
            if not contents:
                continue

            try:
                await container.mount(Label(title, classes="shelf-title"))

                list_view = ListView(classes="shelf-items")
                await container.mount(list_view)

                for item in contents[:8]:
                    item_title = item.get("title", "Unknown")
                    subtitle_parts: list[str] = []
                    artist_str = extract_artist(item)
                    if artist_str and artist_str != "Unknown":
                        subtitle_parts.append(artist_str)
                    description = item.get("description", "")
                    if description:
                        subtitle_parts.append(str(description))
                    subtitle = " - ".join(subtitle_parts)
                    display = (
                        truncate(f"{item_title}  {subtitle}", 80) if subtitle else item_title
                    )

                    list_view.append(ListItem(Label(display)))

                # Store items on the list_view for later retrieval.
                list_view._shelf_items = contents[:8]  # type: ignore[attr-defined]
            except Exception:
                logger.debug("Failed to render shelf %r", title, exc_info=True)

    def _show_error(self, message: str) -> None:
        loading = self.query_one("#foryou-loading", Static)
        loading.update(message)
        loading.display = True

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle item selection within a shelf."""
        list_view = event.list_view
        items = getattr(list_view, "_shelf_items", [])
        idx = list_view.index
        if idx is not None and 0 <= idx < len(items):
            self.post_message(self.ItemSelected(items[idx]))


class MoodsGenresSection(Widget):
    """Grid of mood/genre categories from get_mood_categories()."""

    DEFAULT_CSS = """
    MoodsGenresSection {
        height: 1fr;
        width: 1fr;
        padding: 0 1;
    }

    MoodsGenresSection .loading {
        height: 1fr;
        width: 1fr;
        content-align: center middle;
        color: $text-muted;
    }

    MoodsGenresSection .category-title {
        text-style: bold;
        color: $text;
        padding: 1 0 0 0;
    }

    MoodsGenresSection ListView {
        height: auto;
        max-height: 8;
    }
    """

    is_loading: reactive[bool] = reactive(True)

    class CategorySelected(Message):
        def __init__(self, category: dict[str, Any]) -> None:
            super().__init__()
            self.category = category

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._categories: list[dict[str, Any]] = []
        self._all_items: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield Static("Loading moods & genres...", id="moods-loading", classes="loading")
        yield Vertical(id="moods-container")

    def on_unmount(self) -> None:
        """Release category data to prevent memory retention."""
        self._categories.clear()
        self._all_items.clear()

    async def load_data(self) -> None:
        """Fetch and display mood/genre categories."""
        self.is_loading = True
        try:
            self._categories = await self.app.ytmusic.get_mood_categories()
            await self._populate_categories()
        except Exception:
            logger.debug("Failed to load mood categories")
            self._show_error("Failed to load moods & genres.")
        finally:
            self.is_loading = False

    async def _populate_categories(self) -> None:
        loading = self.query_one("#moods-loading", Static)
        loading.display = False

        container = self.query_one("#moods-container", Vertical)
        # Clear _category_items references from old ListViews before removing.
        for lv in container.query(ListView):
            if hasattr(lv, "_category_items"):
                lv._category_items = []  # type: ignore[attr-defined]
        await container.remove_children()

        if not self._categories:
            await container.mount(Static("No categories available.", classes="loading"))
            return

        self._all_items = []

        for category_group in self._categories:
            group_title = category_group.get("title", "")
            items = category_group.get("categories", [])
            if not items:
                continue

            if group_title:
                await container.mount(Label(group_title, classes="category-title"))

            list_view = ListView()
            await container.mount(list_view)

            for item in items:
                title = item.get("title", "Unknown")
                list_view.append(ListItem(Label(title)))
                self._all_items.append(item)

            list_view._category_items = items  # type: ignore[attr-defined]

    def _show_error(self, message: str) -> None:
        loading = self.query_one("#moods-loading", Static)
        loading.update(message)
        loading.display = True

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle category selection."""
        list_view = event.list_view
        items = getattr(list_view, "_category_items", [])
        idx = list_view.index
        if idx is not None and 0 <= idx < len(items):
            self.post_message(self.CategorySelected(items[idx]))


class ChartsSection(Widget):
    """Top charts from get_charts() displayed as a track table."""

    DEFAULT_CSS = """
    ChartsSection {
        height: 1fr;
        width: 1fr;
        padding: 0 1;
    }

    ChartsSection .loading {
        height: 1fr;
        width: 1fr;
        content-align: center middle;
        color: $text-muted;
    }

    ChartsSection .section-title {
        text-style: bold;
        color: $text;
        height: 1;
        padding: 0 0 1 0;
    }

    ChartsSection #charts-country {
        dock: top;
        height: 1;
        width: auto;
        color: $text-muted;
        padding: 0 0 0 1;
    }
    """

    is_loading: reactive[bool] = reactive(True)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._chart_data: dict[str, Any] = {}
        self._country: str = "ZZ"

    def on_unmount(self) -> None:
        """Release chart data to prevent memory retention."""
        self._chart_data.clear()

    def compose(self) -> ComposeResult:
        yield Static("Loading charts...", id="charts-loading", classes="loading")
        with Vertical(id="charts-content"):
            yield Static("Global Charts", classes="section-title")
            yield Static(f"Country: {self._country}", id="charts-country")
            yield TrackTable(
                show_album=True,
                show_index=True,
                id="charts-table",
            )

    def on_mount(self) -> None:
        # Hide content until data loads.
        try:
            self.query_one("#charts-content").display = False
        except Exception:
            logger.debug("Failed to hide charts content on mount", exc_info=True)

    async def load_data(self, country: str = "ZZ") -> None:
        """Fetch and display chart data for *country*."""
        self.is_loading = True
        self._country = country
        try:
            self._chart_data = await self.app.ytmusic.get_charts(country=country)
            self._populate_charts()
        except Exception:
            logger.debug("Failed to load charts for country=%r", country)
            self._show_error("Failed to load charts.")
        finally:
            self.is_loading = False

    def _populate_charts(self) -> None:
        loading = self.query_one("#charts-loading", Static)
        loading.display = False

        content = self.query_one("#charts-content")
        content.display = True

        # Update country label.
        country_label = self.query_one("#charts-country", Static)
        country_name = self._chart_data.get("country", self._country)
        country_label.update(f"Country: {country_name}")

        # Extract tracks from chart data.
        # ytmusicapi get_charts returns { "songs": { "items": [...] }, ... }
        songs_section = self._chart_data.get("songs", {})
        if isinstance(songs_section, dict):
            tracks = songs_section.get("items", [])
        elif isinstance(songs_section, list):
            tracks = songs_section
        else:
            tracks = []

        table = self.query_one("#charts-table", TrackTable)
        table.load_tracks(tracks)

    def _show_error(self, message: str) -> None:
        loading = self.query_one("#charts-loading", Static)
        loading.update(message)
        loading.display = True
        try:
            self.query_one("#charts-content").display = False
        except Exception:
            logger.debug("Failed to hide charts content on error", exc_info=True)


class NewReleasesSection(Widget):
    """New album releases from get_new_releases()."""

    DEFAULT_CSS = """
    NewReleasesSection {
        height: 1fr;
        width: 1fr;
        padding: 0 1;
    }

    NewReleasesSection .loading {
        height: 1fr;
        width: 1fr;
        content-align: center middle;
        color: $text-muted;
    }

    NewReleasesSection .section-title {
        text-style: bold;
        color: $text;
        height: 1;
        padding: 0 0 1 0;
    }

    NewReleasesSection ListView {
        height: 1fr;
    }
    """

    is_loading: reactive[bool] = reactive(True)

    class AlbumSelected(Message):
        def __init__(self, album: dict[str, Any]) -> None:
            super().__init__()
            self.album = album

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._albums: list[dict[str, Any]] = []

    def on_unmount(self) -> None:
        """Release album data to prevent memory retention."""
        self._albums.clear()

    def compose(self) -> ComposeResult:
        yield Static("Loading new releases...", id="releases-loading", classes="loading")
        with Vertical(id="releases-content"):
            yield Label("New Releases", classes="section-title")
            yield ListView(id="releases-list")

    def on_mount(self) -> None:
        try:
            self.query_one("#releases-content").display = False
        except Exception:
            logger.debug("Failed to hide releases content on mount", exc_info=True)

    async def load_data(self) -> None:
        """Fetch and display new releases."""
        self.is_loading = True
        try:
            self._albums = await self.app.ytmusic.get_new_releases()
            self._populate_releases()
        except Exception:
            logger.debug("Failed to load new releases")
            self._show_error("Failed to load new releases.")
        finally:
            self.is_loading = False

    def _populate_releases(self) -> None:
        loading = self.query_one("#releases-loading", Static)
        loading.display = False

        content = self.query_one("#releases-content")
        content.display = True

        list_view = self.query_one("#releases-list", ListView)
        list_view.clear()

        for album in self._albums:
            title = album.get("title", "Unknown Album")
            artist_str = extract_artist(album)
            if artist_str == "Unknown":
                artist_str = ""
            album_type = album.get("type", "")
            year = album.get("year", "")

            parts = [title]
            if artist_str:
                parts.append(f"by {artist_str}")
            meta_parts: list[str] = []
            if album_type:
                meta_parts.append(album_type)
            if year:
                meta_parts.append(str(year))
            if meta_parts:
                parts.append(f"({', '.join(meta_parts)})")

            display = truncate(" ".join(parts), 80)
            list_view.append(ListItem(Label(display)))

    def _show_error(self, message: str) -> None:
        loading = self.query_one("#releases-loading", Static)
        loading.update(message)
        loading.display = True
        try:
            self.query_one("#releases-content").display = False
        except Exception:
            logger.debug("Failed to hide releases content on error", exc_info=True)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle album selection."""
        idx = event.list_view.index
        if idx is not None and 0 <= idx < len(self._albums):
            self.post_message(self.AlbumSelected(self._albums[idx]))


# ---------------------------------------------------------------------------
# Main browse page
# ---------------------------------------------------------------------------


class BrowsePage(Widget):
    """Tabbed browse page: For You, Moods & Genres, Charts, New Releases.

    Each tab lazily loads its data on first activation.
    """

    DEFAULT_CSS = """
    BrowsePage {
        height: 1fr;
        width: 1fr;
    }

    BrowsePage > Vertical {
        height: 1fr;
        width: 1fr;
    }

    #browse-content {
        height: 1fr;
        width: 1fr;
    }

    #browse-content > Widget {
        display: none;
    }

    #browse-content > Widget.active-section {
        display: block;
    }
    """

    active_tab: reactive[int] = reactive(0)

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._tabs_loaded: set[int] = set()

    def compose(self) -> ComposeResult:
        with Vertical():
            yield BrowseTabBar(id="browse-tabs")
            with Vertical(id="browse-content"):
                yield ForYouSection(id="section-foryou", classes="active-section")
                yield MoodsGenresSection(id="section-moods")
                yield ChartsSection(id="section-charts")
                yield NewReleasesSection(id="section-releases")

    def on_mount(self) -> None:
        # Load the default tab (For You).
        self._load_tab(0)

    # ------------------------------------------------------------------
    # Tab switching
    # ------------------------------------------------------------------

    def on_browse_tab_bar_tab_changed(self, event: BrowseTabBar.TabChanged) -> None:
        """Switch the visible content section and lazy-load data."""
        self._switch_section(event.index)

    def _switch_section(self, index: int) -> None:
        """Show the section at *index* and hide all others."""
        section_ids = [
            "section-foryou",
            "section-moods",
            "section-charts",
            "section-releases",
        ]

        for i, sid in enumerate(section_ids):
            try:
                section = self.query_one(f"#{sid}")
                if i == index:
                    section.add_class("active-section")
                else:
                    section.remove_class("active-section")
            except Exception:
                logger.debug("Failed to toggle browse section '%s'", sid, exc_info=True)

        self.active_tab = index
        self._load_tab(index)

    def _load_tab(self, index: int) -> None:
        """Lazy-load data for a tab if not already loaded."""
        if index in self._tabs_loaded:
            return
        self._tabs_loaded.add(index)

        match index:
            case 0:
                section = self.query_one("#section-foryou", ForYouSection)
                self.run_worker(section.load_data(), name="load-foryou", exclusive=True)
            case 1:
                section = self.query_one("#section-moods", MoodsGenresSection)
                self.run_worker(section.load_data(), name="load-moods", exclusive=True)
            case 2:
                section = self.query_one("#section-charts", ChartsSection)
                self.run_worker(section.load_data(), name="load-charts", exclusive=True)
            case 3:
                section = self.query_one("#section-releases", NewReleasesSection)
                self.run_worker(section.load_data(), name="load-releases", exclusive=True)

    # ------------------------------------------------------------------
    # Item selection handlers
    # ------------------------------------------------------------------

    async def on_for_you_section_item_selected(self, event: ForYouSection.ItemSelected) -> None:
        """Handle item selection from the For You shelves."""
        item = event.item
        await self._navigate_item(item)

    def on_moods_genres_section_category_selected(
        self, event: MoodsGenresSection.CategorySelected
    ) -> None:
        """Navigate to mood/genre playlist listing."""
        category = event.category
        params = category.get("params")
        if params:
            self.run_worker(
                self._load_mood_playlists(params),
                name="load-mood-playlists",
                exclusive=True,
            )

    async def _load_mood_playlists(self, category_params: str) -> None:
        """Fetch playlists for a mood/genre and navigate to the first one."""
        try:
            playlists = await self.app.ytmusic.get_mood_playlists(category_params)
            if playlists:
                # Navigate to the first playlist as a preview, or show a list.
                # For now, navigate to the first one.
                first = playlists[0] if isinstance(playlists, list) else None
                if first:
                    playlist_id = first.get("playlistId") or first.get("browseId")
                    if playlist_id:
                        await self.app.navigate_to(
                            "context", context_type="playlist", context_id=playlist_id
                        )
        except Exception:
            logger.debug("Failed to load mood playlists")
            self.app.notify("Failed to load mood playlists", severity="error")

    async def on_new_releases_section_album_selected(
        self, event: NewReleasesSection.AlbumSelected
    ) -> None:
        """Navigate to the selected album."""
        album = event.album
        album_id = album.get("browseId") or album.get("album_id")
        if album_id:
            await self.app.navigate_to("context", context_type="album", context_id=album_id)

    async def on_track_table_track_selected(self, event: TrackTable.TrackSelected) -> None:
        """Play the selected chart track."""
        await self.app.play_track(event.track)

    async def _navigate_item(self, item: dict[str, Any]) -> None:
        """Route an item to the appropriate context page or play it directly."""
        result_type = item.get("resultType", item.get("type", ""))
        video_id = get_video_id(item)
        browse_id = item.get("browseId")

        if result_type in ("song", "video", "flat_song") or video_id:
            await self.app.play_track(item)
        elif result_type in ("album", "single"):
            if browse_id:
                await self.app.navigate_to("context", context_type="album", context_id=browse_id)
        elif result_type == "artist":
            if browse_id:
                await self.app.navigate_to("context", context_type="artist", context_id=browse_id)
        elif result_type == "playlist":
            playlist_id = item.get("playlistId") or browse_id
            if playlist_id:
                await self.app.navigate_to(
                    "context", context_type="playlist", context_id=playlist_id
                )

    # ------------------------------------------------------------------
    # Vim-style action handler
    # ------------------------------------------------------------------

    async def handle_action(self, action: Action, count: int = 1) -> None:
        """Process vim-style navigation actions dispatched from the app."""
        match action:
            case Action.MOVE_DOWN:
                focused = self.app.focused
                if isinstance(focused, ListView):
                    for _ in range(count):
                        focused.action_cursor_down()
                elif isinstance(focused, TrackTable):
                    await focused.handle_action(action, count)

            case Action.MOVE_UP:
                focused = self.app.focused
                if isinstance(focused, ListView):
                    for _ in range(count):
                        focused.action_cursor_up()
                elif isinstance(focused, TrackTable):
                    await focused.handle_action(action, count)

            case Action.PAGE_DOWN:
                focused = self.app.focused
                if isinstance(focused, ListView):
                    focused.action_scroll_down()
                elif isinstance(focused, TrackTable):
                    await focused.handle_action(action, count)

            case Action.PAGE_UP:
                focused = self.app.focused
                if isinstance(focused, ListView):
                    focused.action_scroll_up()
                elif isinstance(focused, TrackTable):
                    await focused.handle_action(action, count)

            case Action.GO_TOP:
                focused = self.app.focused
                if isinstance(focused, ListView):
                    focused.action_first()
                elif isinstance(focused, TrackTable):
                    await focused.handle_action(action, count)

            case Action.GO_BOTTOM:
                focused = self.app.focused
                if isinstance(focused, ListView):
                    focused.action_last()
                elif isinstance(focused, TrackTable):
                    await focused.handle_action(action, count)

            case Action.SELECT:
                focused = self.app.focused
                if isinstance(focused, ListView):
                    focused.action_select_cursor()
                elif isinstance(focused, TrackTable):
                    await focused.handle_action(action, count)

            case Action.FOCUS_NEXT:
                # Move to next tab.
                tab_bar = self.query_one("#browse-tabs", BrowseTabBar)
                next_idx = (tab_bar.active_tab + 1) % len(_TABS)
                tab_bar.switch_to(next_idx)

            case Action.FOCUS_PREV:
                # Move to previous tab.
                tab_bar = self.query_one("#browse-tabs", BrowseTabBar)
                prev_idx = (tab_bar.active_tab - 1) % len(_TABS)
                tab_bar.switch_to(prev_idx)
