"""Persistent playlist sidebar — visible across all views."""

from __future__ import annotations

import logging
import time
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.events import Click
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, Label, ListItem, ListView, Rule, Static

from ytm_player.config.settings import get_settings
from ytm_player.ui.selection_info_bar import SelectionChanged
from ytm_player.utils.formatting import copy_to_clipboard, truncate

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LibraryPanel (moved from library.py)
# ---------------------------------------------------------------------------


class LibraryPanel(Widget):
    """A panel showing a list of library items with filtering and selection.

    When ``instant_select`` is True, single-click and Enter both fire
    ``ItemSelected`` immediately (no double-click gate).
    """

    DEFAULT_CSS = """
    LibraryPanel {
        height: 1fr;
        border: solid $secondary;
        padding: 0 1;
    }

    LibraryPanel .panel-title {
        text-style: bold;
        color: $text;
        height: 1;
        padding: 0 0 0 0;
    }

    LibraryPanel .panel-count {
        color: $text-muted;
        dock: bottom;
        height: 1;
    }

    LibraryPanel ListView {
        height: 1fr;
        width: 1fr;
    }

    LibraryPanel ListView ListItem:hover {
        background: $accent 30%;
    }

    LibraryPanel.truncate-items ListView ListItem {
        height: 1;
    }

    LibraryPanel.truncate-items ListView ListItem Static {
        height: 1;
    }

    LibraryPanel .panel-loading {
        height: 1fr;
        width: 1fr;
        content-align: center middle;
        color: $text-muted;
    }

    LibraryPanel .panel-filter {
        dock: bottom;
        height: 1;
        display: none;
    }

    LibraryPanel .panel-filter.visible {
        display: block;
    }
    """

    is_loading: reactive[bool] = reactive(False)
    filter_visible: reactive[bool] = reactive(False)

    class ItemSelected(Message):
        """Emitted when an item in this panel is activated."""

        def __init__(self, item_data: dict[str, Any], panel_id: str) -> None:
            super().__init__()
            self.item_data = item_data
            self.panel_id = panel_id

    class ItemDoubleClicked(Message):
        """Emitted when an item is double-clicked in instant_select mode."""

        def __init__(self, item_data: dict[str, Any], panel_id: str) -> None:
            super().__init__()
            self.item_data = item_data
            self.panel_id = panel_id

    class ItemRightClicked(Message):
        """Emitted when an item (or empty space) is right-clicked."""

        def __init__(self, item_data: dict[str, Any] | None, panel_id: str) -> None:
            super().__init__()
            self.item_data = item_data
            self.panel_id = panel_id

    def __init__(
        self,
        title: str,
        *,
        instant_select: bool = False,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._title = title
        self._instant_select = instant_select
        self._items: list[dict[str, Any]] = []
        self._filtered_items: list[dict[str, Any]] = []
        self._last_click_time: float = 0.0
        self._last_click_index: int | None = None
        self._click_activated: bool = False

    def compose(self) -> ComposeResult:
        yield Label(self._title, classes="panel-title")
        yield Static("Loading...", classes="panel-loading")
        yield ListView(id=f"{self.id}-list")
        yield Static("", classes="panel-count")
        yield Input(
            placeholder="Filter...",
            id=f"{self.id}-filter",
            classes="panel-filter",
        )

    def on_mount(self) -> None:
        self._set_loading_visible(True)
        # Apply [ui] sidebar_overflow setting via CSS class.
        try:
            from ytm_player.config.settings import get_settings

            if get_settings().ui.sidebar_overflow == "truncate":
                self.add_class("truncate-items")
        except Exception:
            logger.debug("Failed to apply sidebar_overflow class", exc_info=True)

    def _set_loading_visible(self, visible: bool) -> None:
        try:
            loading = self.query_one(".panel-loading", Static)
            list_view = self.query_one(ListView)
            loading.display = visible
            list_view.display = not visible
        except Exception:
            logger.debug("Failed to toggle loading visibility in library panel", exc_info=True)

    def load_items(self, items: list[dict[str, Any]]) -> None:
        """Replace panel contents with *items* and hide loading indicator."""
        self._items = list(items)
        self._filtered_items = list(items)
        self._rebuild_list(self._filtered_items)
        self._set_loading_visible(False)
        self.is_loading = False

    def prepend_item(self, item: dict[str, Any]) -> None:
        """Optimistically insert *item* at the top of the panel."""
        self._items.insert(0, item)
        self._filtered_items.insert(0, item)
        full_text = self._format_item(item)
        lbl = Static(self._render_text(full_text))
        list_view = self.query_one(ListView)
        list_view.insert(0, [ListItem(lbl)])
        count_label = self.query_one(".panel-count", Static)
        total = len(self._items)
        shown = len(self._filtered_items)
        if shown == total:
            count_label.update(f"{total} item{'s' if total != 1 else ''}")
        else:
            count_label.update(f"{shown}/{total}")

    def remove_item(self, playlist_id: str) -> None:
        """Optimistically remove the item with *playlist_id* from the panel."""

        def matches(item: dict[str, Any]) -> bool:
            pid = item.get("playlistId") or item.get("browseId", "")
            return pid == playlist_id or pid == f"VL{playlist_id}"

        self._items = [i for i in self._items if not matches(i)]
        self._filtered_items = [i for i in self._filtered_items if not matches(i)]
        self._rebuild_list(self._filtered_items)

    def update_item_count(self, playlist_id: str, delta: int) -> None:
        """Optimistically update the cached track count for a playlist.

        Matches against both 'playlistId' and 'browseId' keys. Tolerates a
        VL-prefix mismatch in either direction. If the cached count is None
        (unknown), leaves it None — never fabricates.
        """

        def matches(item: dict[str, Any]) -> bool:
            pid = item.get("playlistId") or item.get("browseId", "") or ""
            target = playlist_id
            return (
                pid == target
                or pid == f"VL{target}"
                or f"VL{pid}" == target
                or pid == target.removeprefix("VL")
            )

        target_item = next((i for i in self._items if matches(i)), None)
        if target_item is None:
            return

        current = target_item.get("count")
        if current is None:
            return  # don't fabricate; wait for next library reload
        target_item["count"] = max(0, int(current) + delta)

        # Rebuild the list so the visible label reflects the new count.
        # Wrapped in try/except because tests instantiate via __new__ without
        # mounting the ListView; production code always has it mounted.
        try:
            self._rebuild_list(self._filtered_items)
        except Exception:
            logger.exception("update_item_count: _rebuild_list failed")

    def _rebuild_list(self, items: list[dict[str, Any]]) -> None:
        list_view = self.query_one(ListView)
        list_view.clear()
        for item in items:
            full_text = self._format_item(item)
            lbl = Static(self._render_text(full_text))
            list_view.append(ListItem(lbl))
        count_label = self.query_one(".panel-count", Static)
        total = len(self._items)
        shown = len(items)
        if shown == total:
            count_label.update(f"{total} item{'s' if total != 1 else ''}")
        else:
            count_label.update(f"{shown}/{total}")

    def _format_item(self, item: dict[str, Any]) -> str:
        title = item.get("title", item.get("name", "Unknown"))
        count = item.get("count")
        if count is not None:
            return f"{title} ({count} tracks)"
        return title

    def _render_text(self, full_text: str) -> str:
        """Apply the configured overflow rule to *full_text*.

        - "truncate" (default): hard-cut to 60 chars with `…` suffix on overflow.
          The `truncate-items` CSS class additionally clips the row to height 1
          so even truncated-but-still-too-wide text won't wrap visually.
        - "wrap": pass the text through unchanged; Textual's Static widget will
          wrap to multiple lines naturally.
        """
        try:
            from ytm_player.config.settings import get_settings

            mode = get_settings().ui.sidebar_overflow
        except Exception:
            mode = "truncate"
        if mode == "wrap":
            return full_text
        return truncate(full_text, 60)

    # -- Filtering --

    def show_filter(self) -> None:
        try:
            filter_input = self.query_one(f"#{self.id}-filter", Input)
            filter_input.add_class("visible")
            filter_input.value = ""
            filter_input.focus()
            self.filter_visible = True
        except Exception:
            logger.debug("Failed to show filter input in library panel", exc_info=True)

    def hide_filter(self) -> None:
        try:
            filter_input = self.query_one(f"#{self.id}-filter", Input)
            filter_input.remove_class("visible")
            self.filter_visible = False
            self._filtered_items = list(self._items)
            self._rebuild_list(self._filtered_items)
        except Exception:
            logger.debug("Failed to hide filter input in library panel", exc_info=True)

    def on_input_changed(self, event: Input.Changed) -> None:
        if not event.input.id or not event.input.id.endswith("-filter"):
            return
        query = event.value.strip().lower()
        if not query:
            self._filtered_items = list(self._items)
        else:
            self._filtered_items = [
                item for item in self._items if query in self._format_item(item).lower()
            ]
        self._rebuild_list(self._filtered_items)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id and event.input.id.endswith("-filter"):
            filter_input = self.query_one(f"#{self.id}-filter", Input)
            filter_input.remove_class("visible")
            self.filter_visible = False
            list_view = self.query_one(ListView)
            list_view.focus()

    # -- Highlight --

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Post SelectionChanged so SelectionInfoBar can display the full title.

        Only posts when this panel's ListView actually has focus — otherwise
        a freshly-mounted ListView fires Highlighted at index 0 on init and
        stomps a different focused widget's selection in the info bar.
        """
        try:
            list_view = event.list_view
            if not list_view.has_focus:
                return
            sel_idx = list_view.index
            if sel_idx is not None and 0 <= sel_idx < len(self._filtered_items):
                item = self._filtered_items[sel_idx]
                title = item.get("title", item.get("name", ""))
                self.post_message(SelectionChanged(title))
            else:
                self.post_message(SelectionChanged(""))
        except Exception:
            self.post_message(SelectionChanged(""))

    def on_focus(self) -> None:
        """When the panel gains focus, push the current highlight into the info bar."""
        try:
            list_view = self.query_one(ListView)
            sel_idx = list_view.index
            if sel_idx is not None and 0 <= sel_idx < len(self._filtered_items):
                item = self._filtered_items[sel_idx]
                title = item.get("title", item.get("name", ""))
                self.post_message(SelectionChanged(title))
            else:
                self.post_message(SelectionChanged(""))
        except Exception:
            self.post_message(SelectionChanged(""))

    # -- Selection --

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if self._instant_select:
            idx = event.list_view.index
            if idx is not None and 0 <= idx < len(self._filtered_items):
                self.post_message(self.ItemSelected(self._filtered_items[idx], self.id or ""))
            return
        if self._click_activated:
            self._click_activated = False
            return
        idx = event.list_view.index
        if idx is not None and 0 <= idx < len(self._filtered_items):
            self.post_message(self.ItemSelected(self._filtered_items[idx], self.id or ""))

    def _find_clicked_item_index(self, event: Click) -> int | None:
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
        if event.button == 3:
            event.stop()
            idx = self._find_clicked_item_index(event)
            if idx is not None and 0 <= idx < len(self._filtered_items):
                self.post_message(self.ItemRightClicked(self._filtered_items[idx], self.id or ""))
            else:
                self.post_message(self.ItemRightClicked(None, self.id or ""))
            return
        if event.button != 1:
            return
        if self._instant_select:
            idx = self._find_clicked_item_index(event)
            if idx is not None and 0 <= idx < len(self._filtered_items):
                now = time.monotonic()
                if self._last_click_index == idx and (now - self._last_click_time) < 0.4:
                    self._last_click_time = 0.0
                    self._last_click_index = None
                    event.stop()
                    self.post_message(
                        self.ItemDoubleClicked(self._filtered_items[idx], self.id or "")
                    )
                    return
                self._last_click_time = now
                self._last_click_index = idx
            return
        idx = self._find_clicked_item_index(event)
        if idx is None:
            return
        now = time.monotonic()
        if self._last_click_index == idx and (now - self._last_click_time) < 0.4:
            self._last_click_time = 0.0
            self._last_click_index = None
            self._click_activated = True
            if 0 <= idx < len(self._filtered_items):
                self.post_message(self.ItemSelected(self._filtered_items[idx], self.id or ""))
            return
        self._last_click_time = now
        self._last_click_index = idx
        self._click_activated = True


# ---------------------------------------------------------------------------
# PlaylistSidebar — wraps pinned-nav + LibraryPanel
# ---------------------------------------------------------------------------


class PlaylistSidebar(Widget):
    """Persistent playlist sidebar visible across all views."""

    DEFAULT_CSS = """
    PlaylistSidebar {
        width: 30;
        height: 1fr;
        border-right: solid $border;
    }

    PlaylistSidebar.hidden {
        display: none;
    }

    #ps-pinned-nav {
        height: auto;
        padding: 0 1;
    }

    .ps-pinned-item {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }

    .ps-pinned-item:hover {
        background: $accent 30%;
        color: $text;
    }

    .ps-pinned-item.active {
        color: $primary;
        text-style: bold;
    }

    #ps-separator,
    #ps-separator-bottom {
        margin: 0 1;
        color: $border;
    }

    #ps-playlists {
        width: 1fr;
    }
    """

    class PlaylistSelected(Message):
        """A playlist was selected in the sidebar."""

        def __init__(self, item_data: dict[str, Any]) -> None:
            super().__init__()
            self.item_data = item_data

    class PlaylistDoubleClicked(Message):
        """A playlist was double-clicked in the sidebar."""

        def __init__(self, item_data: dict[str, Any]) -> None:
            super().__init__()
            self.item_data = item_data

    class PlaylistRightClicked(Message):
        """A playlist was right-clicked in the sidebar."""

        def __init__(self, item_data: dict[str, Any] | None) -> None:
            super().__init__()
            self.item_data = item_data

    class NavItemClicked(Message):
        """A pinned nav item (Liked Songs / Recently Played) was clicked."""

        def __init__(self, nav_id: str) -> None:
            super().__init__()
            self.nav_id = nav_id

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._loaded: bool = False

    def compose(self) -> ComposeResult:
        with Vertical(id="ps-pinned-nav"):
            yield Static("\u2665 Liked Songs", id="ps-nav-liked", classes="ps-pinned-item")
            yield Static("\u23f1 Recently Played", id="ps-nav-recent", classes="ps-pinned-item")
            yield Static("\u266b Discovery Mix", id="ps-nav-discovery", classes="ps-pinned-item")
        yield Rule(id="ps-separator")
        yield LibraryPanel("Playlists", id="ps-playlists", instant_select=True)
        yield Rule(id="ps-separator-bottom")

    def on_mount(self) -> None:
        settings = get_settings()
        self.styles.width = settings.ui.sidebar_width

    async def ensure_loaded(self) -> None:
        """Load playlists if not already loaded. Gated by auth readiness."""
        if self._loaded:
            return
        ytmusic = getattr(self.app, "ytmusic", None)
        if ytmusic is None:
            return
        self._loaded = True
        try:
            playlists = await ytmusic.get_library_playlists(limit=50)
            panel = self.query_one("#ps-playlists", LibraryPanel)
            if isinstance(playlists, list):
                # Filter out "Liked Music" — it's already a pinned nav item.
                playlists = [
                    p for p in playlists if (p.get("playlistId") or p.get("browseId", "")) != "LM"
                ]
                panel.load_items(playlists)
            else:
                logger.error("Failed to load playlists: %s", playlists)
                panel.load_items([])
        except Exception:
            logger.exception("Failed to load library playlists in sidebar")

    async def refresh_playlists(self) -> None:
        """Force-reload playlists."""
        self._loaded = False
        await self.ensure_loaded()

    def auto_select_playlist(self, playlist_id: str) -> None:
        """Highlight a specific playlist in the panel."""
        panel = self.query_one("#ps-playlists", LibraryPanel)
        for item in panel._items:
            pid = item.get("playlistId") or item.get("browseId")
            if pid == playlist_id:
                self.post_message(self.PlaylistSelected(item))
                break

    # -- Bubble LibraryPanel messages as PlaylistSidebar messages --

    def on_library_panel_item_selected(self, event: LibraryPanel.ItemSelected) -> None:
        event.stop()
        self.post_message(self.PlaylistSelected(event.item_data))

    def on_library_panel_item_double_clicked(self, event: LibraryPanel.ItemDoubleClicked) -> None:
        event.stop()
        self.post_message(self.PlaylistDoubleClicked(event.item_data))

    def on_library_panel_item_right_clicked(self, event: LibraryPanel.ItemRightClicked) -> None:
        event.stop()
        self.post_message(self.PlaylistRightClicked(event.item_data))

    # -- Pinned nav clicks --

    def on_click(self, event: Click) -> None:
        target = event.widget
        if target is None:
            return
        if target.id == "ps-nav-liked":
            event.stop()
            self.post_message(self.NavItemClicked("liked_songs"))
        elif target.id == "ps-nav-recent":
            event.stop()
            self.post_message(self.NavItemClicked("recently_played"))
        elif target.id == "ps-nav-discovery":
            event.stop()
            self.post_message(self.NavItemClicked("discovery_mix"))

    # -- Public helpers for sidebar actions --

    def handle_sidebar_action(self, action: str, count: int = 1) -> None:
        """Dispatch vim-style actions to the sidebar ListView."""
        from ytm_player.config.keymap import Action

        try:
            list_view = self.query_one("#ps-playlists-list", ListView)
        except Exception:
            return

        match Action(action) if isinstance(action, str) else action:
            case Action.MOVE_DOWN:
                for _ in range(count):
                    list_view.action_cursor_down()
            case Action.MOVE_UP:
                for _ in range(count):
                    list_view.action_cursor_up()
            case Action.PAGE_DOWN:
                list_view.action_scroll_down()
            case Action.PAGE_UP:
                list_view.action_scroll_up()
            case Action.GO_TOP:
                if len(list_view.children) > 0:
                    list_view.index = 0
            case Action.GO_BOTTOM:
                if len(list_view.children) > 0:
                    list_view.index = len(list_view.children) - 1
            case Action.SELECT:
                list_view.action_select_cursor()
            case Action.FILTER:
                panel = self.query_one("#ps-playlists", LibraryPanel)
                panel.show_filter()

    def get_highlighted_item(self) -> dict[str, Any] | None:
        """Return the currently highlighted playlist item."""
        try:
            panel = self.query_one("#ps-playlists", LibraryPanel)
            list_view = panel.query_one(ListView)
            idx = list_view.index
            if idx is not None and 0 <= idx < len(panel._filtered_items):
                return panel._filtered_items[idx]
        except Exception:
            logger.debug("Failed to get highlighted item from sidebar", exc_info=True)
        return None

    def copy_item_link(self, item: dict[str, Any]) -> None:
        """Copy a YouTube Music playlist link to clipboard."""
        pid = item.get("playlistId") or item.get("browseId", "")
        if not pid:
            self.app.notify("No link available", severity="warning", timeout=2)
            return
        link = f"https://music.youtube.com/playlist?list={pid}"
        if copy_to_clipboard(link):
            self.app.notify("Link copied", timeout=2)
        else:
            self.app.notify(link, timeout=5)
