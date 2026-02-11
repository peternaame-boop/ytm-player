"""Library page — Spotify-style sidebar + inline track view."""

from __future__ import annotations

import logging
import time
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Click
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, Label, ListItem, ListView, Static

from ytm_player.config.keymap import Action
from ytm_player.config.settings import get_settings
from ytm_player.ui.widgets.track_table import TrackTable
from ytm_player.utils.formatting import copy_to_clipboard, normalize_tracks, truncate

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Individual library panel (sidebar)
# ---------------------------------------------------------------------------


class LibraryPanel(Widget):
    """A single panel in the library view showing a list of items.

    Supports in-panel filtering (``/``), loading state, and item activation.
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
        # Double-click tracking (only used when instant_select is False).
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
        # Start with loading indicator visible, list hidden.
        self._set_loading_visible(True)

    def _set_loading_visible(self, visible: bool) -> None:
        """Toggle between loading indicator and list view."""
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

    def _rebuild_list(self, items: list[dict[str, Any]]) -> None:
        """Clear and repopulate the ListView with *items*."""
        list_view = self.query_one(ListView)
        list_view.clear()

        for item in items:
            label = self._format_item(item)
            list_view.append(ListItem(Label(label)))

        count_label = self.query_one(".panel-count", Static)
        total = len(self._items)
        shown = len(items)
        if shown == total:
            count_label.update(f"{total} item{'s' if total != 1 else ''}")
        else:
            count_label.update(f"{shown}/{total}")

    def _format_item(self, item: dict[str, Any]) -> str:
        """Build a display label for a library item."""
        title = item.get("title", item.get("name", "Unknown"))

        # For playlists: show track count.
        count = item.get("count")
        if count is not None:
            return truncate(f"{title} ({count} tracks)", 60)

        return truncate(title, 60)

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def show_filter(self) -> None:
        """Show the in-panel filter input and focus it."""
        try:
            filter_input = self.query_one(f"#{self.id}-filter", Input)
            filter_input.add_class("visible")
            filter_input.value = ""
            filter_input.focus()
            self.filter_visible = True
        except Exception:
            logger.debug("Failed to show filter input in library panel", exc_info=True)

    def hide_filter(self) -> None:
        """Hide the filter input and restore all items."""
        try:
            filter_input = self.query_one(f"#{self.id}-filter", Input)
            filter_input.remove_class("visible")
            self.filter_visible = False
            self._filtered_items = list(self._items)
            self._rebuild_list(self._filtered_items)
        except Exception:
            logger.debug("Failed to hide filter input in library panel", exc_info=True)

    def on_input_changed(self, event: Input.Changed) -> None:
        """Apply filter as user types."""
        if not event.input.id or not event.input.id.endswith("-filter"):
            return

        query = event.value.strip().lower()
        if not query:
            self._filtered_items = list(self._items)
        else:
            self._filtered_items = [
                item for item in self._items
                if query in self._format_item(item).lower()
            ]
        self._rebuild_list(self._filtered_items)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Close filter on Enter and focus the list."""
        if event.input.id and event.input.id.endswith("-filter"):
            filter_input = self.query_one(f"#{self.id}-filter", Input)
            filter_input.remove_class("visible")
            self.filter_visible = False
            # Focus the list view.
            list_view = self.query_one(ListView)
            list_view.focus()

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle keyboard Enter / list activation."""
        if self._instant_select:
            # Instant mode: always fire, whether keyboard or click.
            idx = event.list_view.index
            if idx is not None and 0 <= idx < len(self._filtered_items):
                self.post_message(
                    self.ItemSelected(self._filtered_items[idx], self.id or "")
                )
            return

        # Legacy double-click mode.
        if self._click_activated:
            self._click_activated = False
            return

        idx = event.list_view.index
        if idx is not None and 0 <= idx < len(self._filtered_items):
            self.post_message(
                self.ItemSelected(self._filtered_items[idx], self.id or "")
            )

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
        """Handle left-click and right-click."""
        if event.button == 3:
            # ── Right-click: context menu ──
            event.stop()
            idx = self._find_clicked_item_index(event)
            if idx is not None and 0 <= idx < len(self._filtered_items):
                self.post_message(
                    self.ItemRightClicked(self._filtered_items[idx], self.id or "")
                )
            else:
                self.post_message(
                    self.ItemRightClicked(None, self.id or "")
                )
            return

        if event.button != 1:
            return

        if self._instant_select:
            # Instant mode: single click fires selection via ListView.Selected.
            # No need for double-click tracking — just let it through.
            return

        # ── Legacy double-click mode ──
        idx = self._find_clicked_item_index(event)
        if idx is None:
            return

        now = time.monotonic()

        if (
            self._last_click_index == idx
            and (now - self._last_click_time) < 0.4
        ):
            self._last_click_time = 0.0
            self._last_click_index = None
            self._click_activated = True
            if 0 <= idx < len(self._filtered_items):
                self.post_message(
                    self.ItemSelected(self._filtered_items[idx], self.id or "")
                )
            return

        self._last_click_time = now
        self._last_click_index = idx
        self._click_activated = True


# ---------------------------------------------------------------------------
# Main library page
# ---------------------------------------------------------------------------


class LibraryPage(Widget):
    """Spotify-style library: playlist sidebar on left, tracks on right."""

    DEFAULT_CSS = """
    LibraryPage {
        height: 1fr;
        width: 1fr;
    }

    LibraryPage > Horizontal {
        height: 1fr;
        width: 1fr;
    }

    #playlists {
        width: 30;
    }

    #content-area {
        height: 1fr;
        width: 1fr;
    }

    #content-header {
        height: auto;
        max-height: 5;
        padding: 1 2;
    }

    .content-title {
        text-style: bold;
    }

    .content-subtitle {
        color: $text-muted;
    }

    #empty-state {
        width: 1fr;
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
    }

    #loading-state {
        width: 1fr;
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
    }

    #library-tracks {
        height: 1fr;
    }
    """

    is_loading: reactive[bool] = reactive(True)

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._active_playlist_id: str | None = None
        self._active_focus: str = "sidebar"  # "sidebar" or "tracks"

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield LibraryPanel("Playlists", id="playlists", instant_select=True)
            with Vertical(id="content-area"):
                yield Vertical(id="content-header")
                yield Static("Select a playlist", id="empty-state")
                yield Static("Loading...", id="loading-state")
                yield TrackTable(show_album=True, id="library-tracks")

    def on_mount(self) -> None:
        settings = get_settings()
        sidebar_width = settings.ui.sidebar_width
        try:
            self.query_one("#playlists").styles.width = sidebar_width
        except Exception:
            logger.debug("Failed to set sidebar width from settings", exc_info=True)

        # Initially hide loading and tracks, show empty state.
        self.query_one("#loading-state").display = False
        self.query_one("#library-tracks").display = False
        self.query_one("#content-header").display = False

        self.run_worker(self._load_library(), name="load-library", exclusive=True)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    async def _load_library(self) -> None:
        """Fetch playlists and populate the sidebar."""
        self.is_loading = True

        try:
            playlists = await self.app.ytmusic.get_library_playlists(limit=50)

            playlists_panel = self.query_one("#playlists", LibraryPanel)
            if isinstance(playlists, list):
                playlists_panel.load_items(playlists)
            else:
                logger.error("Failed to load playlists: %s", playlists)
                playlists_panel.load_items([])

        except Exception:
            logger.exception("Failed to load library data")
        finally:
            self.is_loading = False

    async def refresh_library(self) -> None:
        """Public method to reload playlists."""
        await self._load_library()

    # ------------------------------------------------------------------
    # Playlist selection → load tracks inline
    # ------------------------------------------------------------------

    async def on_library_panel_item_selected(
        self, event: LibraryPanel.ItemSelected
    ) -> None:
        """Fetch and display selected playlist's tracks inline."""
        item = event.item_data
        playlist_id = item.get("playlistId") or item.get("browseId")
        if not playlist_id:
            return

        self._active_playlist_id = playlist_id

        # Show loading state.
        self.query_one("#empty-state").display = False
        self.query_one("#library-tracks").display = False
        self.query_one("#content-header").display = False
        loading = self.query_one("#loading-state")
        loading.display = True

        try:
            data = await self.app.ytmusic.get_playlist(playlist_id)

            # If user selected a different playlist while we were loading, discard.
            if self._active_playlist_id != playlist_id:
                return

            title = data.get("title", "Unknown Playlist")
            author = data.get("author", {})
            owner = author.get("name", "Unknown") if isinstance(author, dict) else str(author)
            tracks = data.get("tracks", [])
            track_count = data.get("trackCount", len(tracks))

            # Update header.
            header = self.query_one("#content-header", Vertical)
            await header.remove_children()
            header.display = True
            await header.mount(Label(title, classes="content-title"))
            subtitle = f"{owner} \u00b7 {track_count} track{'s' if track_count != 1 else ''}"
            await header.mount(Label(subtitle, classes="content-subtitle"))

            # Load tracks into the table.
            loading.display = False
            table = self.query_one("#library-tracks", TrackTable)
            table.display = True
            table.load_tracks(normalize_tracks(tracks))

        except Exception:
            logger.exception("Failed to load playlist %s", playlist_id)
            loading.display = False
            self.query_one("#empty-state").display = True
            empty = self.query_one("#empty-state", Static)
            empty.update("Failed to load playlist")

    # ------------------------------------------------------------------
    # Track selection → play
    # ------------------------------------------------------------------

    async def on_track_table_track_selected(
        self, event: TrackTable.TrackSelected
    ) -> None:
        """Queue all tracks and start playback from the selected one."""
        event.stop()
        table = self.query_one("#library-tracks", TrackTable)
        tracks = table.tracks
        idx = event.index

        self.app.queue.clear()
        self.app.queue.add_multiple(tracks)
        self.app.queue.jump_to(idx)
        await self.app.play_track(event.track)

    # ------------------------------------------------------------------
    # Right-click → context menu
    # ------------------------------------------------------------------

    def on_library_panel_item_right_clicked(
        self, event: LibraryPanel.ItemRightClicked
    ) -> None:
        """Open context menu or create-new flow on right-click."""
        item = event.item_data

        if item is not None:
            self._open_item_context_menu(item)
        else:
            self._prompt_create_playlist()

    def _open_item_context_menu(self, item: dict[str, Any]) -> None:
        """Push ActionsPopup for the right-clicked playlist."""
        from ytm_player.ui.popups.actions import ActionsPopup

        def _handle_action(action_id: str | None) -> None:
            if action_id is None:
                return

            if action_id in ("play_all", "shuffle_play"):
                self.app.run_worker(
                    self.on_library_panel_item_selected(
                        LibraryPanel.ItemSelected(item, "playlists")
                    )
                )
            elif action_id == "add_to_queue":
                self.app.notify("Added to queue", timeout=2)
            elif action_id == "delete":
                self.app.run_worker(self._delete_playlist(item))
            elif action_id == "copy_link":
                self._copy_item_link(item)

        self.app.push_screen(ActionsPopup(item, item_type="playlist"), _handle_action)

    def _prompt_create_playlist(self) -> None:
        """Show an input screen to create a new playlist."""
        from ytm_player.ui.popups.input_popup import InputPopup

        def _on_name(name: str | None) -> None:
            if name and name.strip():
                self.app.run_worker(self._create_playlist(name.strip()))

        self.app.push_screen(
            InputPopup("New Playlist", placeholder="Playlist name..."), _on_name
        )

    async def _create_playlist(self, name: str) -> None:
        """Create a new playlist and refresh the sidebar."""
        try:
            playlist_id = await self.app.ytmusic.create_playlist(name)
            if playlist_id:
                self.app.notify(f"Created '{name}'", timeout=2)
                await self._reload_playlists()
            else:
                self.app.notify("Failed to create playlist", severity="error", timeout=3)
        except Exception:
            logger.exception("Failed to create playlist %r", name)
            self.app.notify("Failed to create playlist", severity="error", timeout=3)

    async def _delete_playlist(self, item: dict[str, Any]) -> None:
        """Delete a playlist and refresh the sidebar."""
        playlist_id = item.get("playlistId") or item.get("browseId", "")
        title = item.get("title", "playlist")
        if not playlist_id:
            self.app.notify("Cannot determine playlist ID", severity="error", timeout=3)
            return

        try:
            success = await self.app.ytmusic.delete_playlist(playlist_id)
            if success:
                self.app.notify(f"Deleted '{title}'", timeout=2)
                # If the deleted playlist was the active one, clear content.
                if self._active_playlist_id == playlist_id:
                    self._active_playlist_id = None
                    self.query_one("#content-header").display = False
                    self.query_one("#library-tracks").display = False
                    empty = self.query_one("#empty-state", Static)
                    empty.update("Select a playlist")
                    empty.display = True
                await self._reload_playlists()
            else:
                self.app.notify("Failed to delete playlist", severity="error", timeout=3)
        except Exception:
            logger.exception("Failed to delete playlist %r", playlist_id)
            self.app.notify("Failed to delete playlist", severity="error", timeout=3)

    async def _reload_playlists(self) -> None:
        """Reload the playlists sidebar."""
        panel = self.query_one("#playlists", LibraryPanel)
        try:
            items = await self.app.ytmusic.get_library_playlists(limit=50)
            panel.load_items(items if isinstance(items, list) else [])
        except Exception:
            logger.exception("Failed to reload playlists")

    def _copy_item_link(self, item: dict[str, Any]) -> None:
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

    # ------------------------------------------------------------------
    # Vim-style action handler
    # ------------------------------------------------------------------

    async def handle_action(self, action: Action, count: int = 1) -> None:
        """Process vim-style navigation actions dispatched from the app."""
        match action:
            case Action.FOCUS_NEXT | Action.FOCUS_PREV:
                self._toggle_focus()
                return

            case Action.FILTER:
                # Show filter only when sidebar is focused.
                if self._active_focus == "sidebar":
                    panel = self.query_one("#playlists", LibraryPanel)
                    panel.show_filter()
                return

            case Action.DELETE_ITEM:
                self._confirm_delete_highlighted()
                return

        # Delegate to the focused panel.
        if self._active_focus == "sidebar":
            await self._handle_sidebar_action(action, count)
        else:
            await self._handle_tracks_action(action, count)

    def _toggle_focus(self) -> None:
        """Toggle focus between sidebar and track table."""
        if self._active_focus == "sidebar":
            # Only switch if tracks are visible.
            try:
                table = self.query_one("#library-tracks", TrackTable)
                if table.display:
                    self._active_focus = "tracks"
                    table.focus()
                    return
            except Exception:
                logger.debug("Failed to focus track table in library", exc_info=True)
        self._active_focus = "sidebar"
        try:
            list_view = self.query_one("#playlists-list", ListView)
            list_view.focus()
        except Exception:
            logger.debug("Failed to focus playlists list in library", exc_info=True)

    async def _handle_sidebar_action(self, action: Action, count: int) -> None:
        """Dispatch action to the sidebar ListView."""
        try:
            list_view = self.query_one("#playlists-list", ListView)
        except Exception:
            logger.debug("Failed to query playlists list for sidebar action", exc_info=True)
            return

        match action:
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
                list_view.action_first()
            case Action.GO_BOTTOM:
                list_view.action_last()
            case Action.SELECT:
                list_view.action_select_cursor()

    async def _handle_tracks_action(self, action: Action, count: int) -> None:
        """Dispatch action to the track table."""
        try:
            table = self.query_one("#library-tracks", TrackTable)
            if table.display:
                await table.handle_action(action, count)
        except Exception:
            logger.debug("Failed to dispatch action to library track table", exc_info=True)

    # ------------------------------------------------------------------
    # Delete with confirmation
    # ------------------------------------------------------------------

    def _get_highlighted_item(self) -> dict[str, Any] | None:
        """Return the highlighted playlist item, or None."""
        try:
            panel = self.query_one("#playlists", LibraryPanel)
            list_view = panel.query_one(ListView)
            idx = list_view.index
            if idx is not None and 0 <= idx < len(panel._filtered_items):
                return panel._filtered_items[idx]
        except Exception:
            logger.debug("Failed to get highlighted item from playlists panel", exc_info=True)
        return None

    def _confirm_delete_highlighted(self) -> None:
        """Show a confirmation popup for the highlighted playlist, then delete."""
        from ytm_player.ui.popups.confirm_popup import ConfirmPopup

        item = self._get_highlighted_item()
        if item is None:
            self.app.notify("No item selected", severity="warning", timeout=2)
            return

        title = item.get("title", item.get("name", "this item"))
        msg = f"Delete playlist '{title}'?"

        def _on_confirm(confirmed: bool) -> None:
            if confirmed:
                self.app.run_worker(self._delete_playlist(item))

        self.app.push_screen(ConfirmPopup(msg), _on_confirm)
