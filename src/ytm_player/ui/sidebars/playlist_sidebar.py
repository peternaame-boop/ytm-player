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
from ytm_player.utils.formatting import copy_to_clipboard, truncate

logger = logging.getLogger(__name__)

# Bounce animation speed (seconds per character shift).
_BOUNCE_INTERVAL = 0.25
# Pause at each end before reversing direction (seconds).
_BOUNCE_PAUSE = 1.5


class _BouncingLabel(Static):
    """A label that bounces horizontally when the text overflows.

    Call ``start_bounce(width)`` to begin the animation and
    ``stop_bounce()`` to reset to the truncated static view.
    """

    DEFAULT_CSS = """
    _BouncingLabel {
        height: 1;
        overflow: hidden;
    }
    """

    def __init__(self, full_text: str, **kwargs: Any) -> None:
        super().__init__(truncate(full_text, 60), **kwargs)
        self._full_text = full_text
        self._offset: int = 0
        self._direction: int = 1  # 1 = moving left, -1 = moving right
        self._visible_width: int = 0
        self._timer: Any = None
        self._pause_remaining: float = 0.0

    def start_bounce(self, visible_width: int) -> None:
        """Start bouncing if the text overflows the visible width."""
        # Account for padding (1 char each side from ListItem).
        self._visible_width = max(visible_width - 4, 10)
        if len(self._full_text) <= self._visible_width:
            return
        self._offset = 0
        self._direction = 1
        self._pause_remaining = _BOUNCE_PAUSE
        self.update(self._full_text[: self._visible_width])
        if self._timer is None:
            self._timer = self.set_interval(_BOUNCE_INTERVAL, self._tick)

    def stop_bounce(self) -> None:
        """Stop bouncing and reset to truncated text."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._offset = 0
        self.update(truncate(self._full_text, 60))

    def _tick(self) -> None:
        """Advance the bounce animation by one step."""
        if self._pause_remaining > 0:
            self._pause_remaining -= _BOUNCE_INTERVAL
            return

        max_offset = len(self._full_text) - self._visible_width
        self._offset += self._direction
        if self._offset >= max_offset:
            self._offset = max_offset
            self._direction = -1
            self._pause_remaining = _BOUNCE_PAUSE
        elif self._offset <= 0:
            self._offset = 0
            self._direction = 1
            self._pause_remaining = _BOUNCE_PAUSE

        visible = self._full_text[self._offset : self._offset + self._visible_width]
        self.update(visible)


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
        lbl = _BouncingLabel(full_text)
        self._bouncing_labels.insert(0, lbl)
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

    def _rebuild_list(self, items: list[dict[str, Any]]) -> None:
        list_view = self.query_one(ListView)
        list_view.clear()
        self._bouncing_labels: list[_BouncingLabel] = []
        for item in items:
            full_text = self._format_item(item)
            lbl = _BouncingLabel(full_text)
            self._bouncing_labels.append(lbl)
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

    # -- Highlight bounce --

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Start bouncing the highlighted item's label if it overflows."""
        # Stop all bouncing labels first.
        for lbl in getattr(self, "_bouncing_labels", []):
            lbl.stop_bounce()
        # Start bouncing the newly highlighted one.
        idx = event.list_view.index
        labels = getattr(self, "_bouncing_labels", [])
        if idx is not None and 0 <= idx < len(labels):
            try:
                sidebar_width: float = 30
                parent = self.parent
                if parent is not None:
                    width = parent.styles.width
                    if width is not None:
                        sidebar_width = width.value
            except Exception:
                sidebar_width = 30
            labels[idx].start_bounce(int(sidebar_width))

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

    #ps-separator {
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
