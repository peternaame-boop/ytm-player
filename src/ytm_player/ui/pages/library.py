"""Library page — browse the user's playlists, albums, and subscribed artists."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.events import Click
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, Label, ListItem, ListView, Static

from ytm_player.config.keymap import Action
from ytm_player.config.settings import get_settings
from ytm_player.utils.formatting import truncate

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Individual library panel
# ---------------------------------------------------------------------------


class LibraryPanel(Widget):
    """A single panel in the library view showing a list of items.

    Supports in-panel filtering (``/``), loading state, and item activation.
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
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._title = title
        self._items: list[dict[str, Any]] = []
        self._filtered_items: list[dict[str, Any]] = []
        # Double-click tracking: single click highlights, double-click navigates.
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
            pass

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

        # For albums: show artist.
        artists = item.get("artists")
        if isinstance(artists, list) and artists:
            artist_names = [
                a.get("name", "") if isinstance(a, dict) else str(a)
                for a in artists
            ]
            artist_str = ", ".join(n for n in artist_names if n)
            if artist_str:
                return truncate(f"{title} - {artist_str}", 60)

        artist = item.get("artist", "")
        if artist:
            return truncate(f"{title} - {artist}", 60)

        # For playlists: show track count.
        count = item.get("count")
        if count is not None:
            return truncate(f"{title} ({count} tracks)", 60)

        # For artists: show subscriber count.
        subs = item.get("subscribers", "")
        if subs:
            return truncate(f"{title}  [{subs}]", 60)

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
            pass

    def hide_filter(self) -> None:
        """Hide the filter input and restore all items."""
        try:
            filter_input = self.query_one(f"#{self.id}-filter", Input)
            filter_input.remove_class("visible")
            self.filter_visible = False
            self._filtered_items = list(self._items)
            self._rebuild_list(self._filtered_items)
        except Exception:
            pass

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
        """Emit ItemSelected only on keyboard activation (Enter), not single click.

        Single mouse clicks set ``_click_activated`` which suppresses
        navigation here.  Double-click posts ItemSelected directly from
        ``on_click``.
        """
        if self._click_activated:
            # Came from a mouse click — suppress navigation.
            self._click_activated = False
            return

        # Keyboard activation (Enter / vim select) — navigate.
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
        """Handle left-click (single=highlight, double=navigate) and right-click."""
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

        # ── Left-click: single=highlight only, double=navigate ──
        idx = self._find_clicked_item_index(event)
        if idx is None:
            return

        now = time.monotonic()

        if (
            self._last_click_index == idx
            and (now - self._last_click_time) < 0.4
        ):
            # Double-click on same item → navigate.
            self._last_click_time = 0.0
            self._last_click_index = None
            self._click_activated = True  # Suppress the upcoming Selected event.
            if 0 <= idx < len(self._filtered_items):
                self.post_message(
                    self.ItemSelected(self._filtered_items[idx], self.id or "")
                )
            return

        # Single click → just highlight; suppress the Selected that ListView fires.
        self._last_click_time = now
        self._last_click_index = idx
        self._click_activated = True


# ---------------------------------------------------------------------------
# Main library page
# ---------------------------------------------------------------------------


class LibraryPage(Widget):
    """Three-panel library browser: Playlists, Albums, Artists.

    Loads data from the YouTube Music API on mount and populates each panel.
    """

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
        width: 40%;
    }

    #albums {
        width: 40%;
    }

    #artists {
        width: 20%;
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

    def compose(self) -> ComposeResult:
        # Apply panel widths from config.
        settings = get_settings()
        widths = settings.ui.library_panels  # [40, 40, 20]

        with Horizontal():
            yield LibraryPanel("Playlists", id="playlists")
            yield LibraryPanel("Albums", id="albums")
            yield LibraryPanel("Artists", id="artists")

    def on_mount(self) -> None:
        # Apply configured panel widths via CSS.
        settings = get_settings()
        widths = settings.ui.library_panels
        try:
            self.query_one("#playlists").styles.width = f"{widths[0]}%"
            self.query_one("#albums").styles.width = f"{widths[1]}%"
            self.query_one("#artists").styles.width = f"{widths[2]}%"
        except (IndexError, Exception):
            pass

        self.run_worker(self._load_library(), name="load-library", exclusive=True)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    async def _load_library(self) -> None:
        """Fetch playlists, albums, and artists concurrently."""
        self.is_loading = True

        try:
            playlists_task = self.app.ytmusic.get_library_playlists(limit=50)
            albums_task = self.app.ytmusic.get_library_albums(limit=50)
            artists_task = self.app.ytmusic.get_library_artists(limit=50)

            playlists, albums, artists = await asyncio.gather(
                playlists_task, albums_task, artists_task,
                return_exceptions=True,
            )

            # Populate each panel, handling exceptions per-category.
            playlists_panel = self.query_one("#playlists", LibraryPanel)
            if isinstance(playlists, list):
                playlists_panel.load_items(playlists)
            else:
                logger.error("Failed to load playlists: %s", playlists)
                playlists_panel.load_items([])

            albums_panel = self.query_one("#albums", LibraryPanel)
            if isinstance(albums, list):
                albums_panel.load_items(albums)
            else:
                logger.error("Failed to load albums: %s", albums)
                albums_panel.load_items([])

            artists_panel = self.query_one("#artists", LibraryPanel)
            if isinstance(artists, list):
                artists_panel.load_items(artists)
            else:
                logger.error("Failed to load artists: %s", artists)
                artists_panel.load_items([])

        except Exception:
            logger.exception("Failed to load library data")
        finally:
            self.is_loading = False

    async def refresh_library(self) -> None:
        """Public method to reload all library data."""
        await self._load_library()

    # ------------------------------------------------------------------
    # Item selection → navigation
    # ------------------------------------------------------------------

    async def on_library_panel_item_selected(
        self, event: LibraryPanel.ItemSelected
    ) -> None:
        """Navigate to the context page for the selected item."""
        item = event.item_data
        panel_id = event.panel_id

        if panel_id == "playlists":
            playlist_id = item.get("playlistId") or item.get("browseId")
            if playlist_id:
                await self.app.navigate_to(
                    "context", context_type="playlist", context_id=playlist_id
                )

        elif panel_id == "albums":
            album_id = item.get("browseId") or item.get("album_id")
            if album_id:
                await self.app.navigate_to(
                    "context", context_type="album", context_id=album_id
                )

        elif panel_id == "artists":
            artist_id = item.get("browseId") or item.get("artist_id")
            if artist_id:
                await self.app.navigate_to(
                    "context", context_type="artist", context_id=artist_id
                )

    # ------------------------------------------------------------------
    # Right-click → context menu
    # ------------------------------------------------------------------

    _PANEL_TO_ITEM_TYPE: dict[str, str] = {
        "playlists": "playlist",
        "albums": "album",
        "artists": "artist",
    }

    def on_library_panel_item_right_clicked(
        self, event: LibraryPanel.ItemRightClicked
    ) -> None:
        """Open context menu or create-new flow on right-click."""
        item = event.item_data
        panel_id = event.panel_id

        if item is not None:
            self._open_item_context_menu(item, panel_id)
        else:
            self._handle_empty_space_click(panel_id)

    def _open_item_context_menu(self, item: dict[str, Any], panel_id: str) -> None:
        """Push ActionsPopup for the right-clicked item."""
        from ytm_player.ui.popups.actions import ActionsPopup

        item_type = self._PANEL_TO_ITEM_TYPE.get(panel_id, "track")

        def _handle_action(action_id: str | None) -> None:
            if action_id is None:
                return

            if action_id in ("play_all", "shuffle_play"):
                # Navigate into the item — the context page handles playback.
                self.on_library_panel_item_selected(
                    LibraryPanel.ItemSelected(item, panel_id)
                )
            elif action_id == "add_to_queue":
                self.app.notify("Added to queue", timeout=2)
            elif action_id == "delete":
                asyncio.ensure_future(self._delete_playlist(item))
            elif action_id == "copy_link":
                self._copy_item_link(item, panel_id)
            elif action_id == "play_top_songs":
                # For artists — navigate into the artist page.
                self.on_library_panel_item_selected(
                    LibraryPanel.ItemSelected(item, panel_id)
                )
            elif action_id == "start_radio":
                self.app.notify("Starting radio...", timeout=2)
            elif action_id in ("toggle_subscribe", "add_to_library"):
                self.app.notify("Action triggered", timeout=2)
            elif action_id in ("view_albums", "view_similar"):
                self.on_library_panel_item_selected(
                    LibraryPanel.ItemSelected(item, panel_id)
                )
            elif action_id == "go_to_artist":
                artists = item.get("artists") or []
                if isinstance(artists, list) and artists:
                    artist = artists[0]
                    artist_id = artist.get("id") or artist.get("browseId", "")
                    if artist_id:
                        asyncio.ensure_future(self.app.navigate_to(
                            "context", context_type="artist", context_id=artist_id
                        ))

        self.app.push_screen(ActionsPopup(item, item_type=item_type), _handle_action)

    def _handle_empty_space_click(self, panel_id: str) -> None:
        """Handle right-click on empty area of a panel."""
        if panel_id == "playlists":
            self._prompt_create_playlist()
        else:
            self.app.notify(
                "Use Search to find and add albums/artists", timeout=3
            )

    def _prompt_create_playlist(self) -> None:
        """Show an input screen to create a new playlist."""
        from ytm_player.ui.popups.input_popup import InputPopup

        def _on_name(name: str | None) -> None:
            if name and name.strip():
                asyncio.ensure_future(self._create_playlist(name.strip()))

        self.app.push_screen(
            InputPopup("New Playlist", placeholder="Playlist name..."), _on_name
        )

    async def _create_playlist(self, name: str) -> None:
        """Create a new playlist and refresh the library."""
        try:
            playlist_id = await self.app.ytmusic.create_playlist(name)
            if playlist_id:
                self.app.notify(f"Created '{name}'", timeout=2)
                await self._reload_panel("playlists")
            else:
                self.app.notify("Failed to create playlist", severity="error", timeout=3)
        except Exception:
            logger.exception("Failed to create playlist %r", name)
            self.app.notify("Failed to create playlist", severity="error", timeout=3)

    async def _delete_playlist(self, item: dict[str, Any]) -> None:
        """Delete a playlist and refresh the panel."""
        playlist_id = item.get("playlistId") or item.get("browseId", "")
        title = item.get("title", "playlist")
        if not playlist_id:
            self.app.notify("Cannot determine playlist ID", severity="error", timeout=3)
            return

        try:
            success = await self.app.ytmusic.delete_playlist(playlist_id)
            if success:
                self.app.notify(f"Deleted '{title}'", timeout=2)
                await self._reload_panel("playlists")
            else:
                self.app.notify("Failed to delete playlist", severity="error", timeout=3)
        except Exception:
            logger.exception("Failed to delete playlist %r", playlist_id)
            self.app.notify("Failed to delete playlist", severity="error", timeout=3)

    async def _reload_panel(self, panel_id: str) -> None:
        """Reload a single panel's data."""
        panel = self.query_one(f"#{panel_id}", LibraryPanel)
        try:
            if panel_id == "playlists":
                items = await self.app.ytmusic.get_library_playlists(limit=50)
            elif panel_id == "albums":
                items = await self.app.ytmusic.get_library_albums(limit=50)
            elif panel_id == "artists":
                items = await self.app.ytmusic.get_library_artists(limit=50)
            else:
                return
            panel.load_items(items if isinstance(items, list) else [])
        except Exception:
            logger.exception("Failed to reload panel %r", panel_id)

    def _copy_item_link(self, item: dict[str, Any], panel_id: str) -> None:
        """Copy a YouTube Music link for the item to clipboard."""
        link = ""
        if panel_id == "playlists":
            pid = item.get("playlistId") or item.get("browseId", "")
            if pid:
                link = f"https://music.youtube.com/playlist?list={pid}"
        elif panel_id == "albums":
            bid = item.get("browseId") or item.get("album_id", "")
            if bid:
                link = f"https://music.youtube.com/browse/{bid}"
        elif panel_id == "artists":
            bid = item.get("browseId") or item.get("artist_id", "")
            if bid:
                link = f"https://music.youtube.com/channel/{bid}"

        if not link:
            self.app.notify("No link available", severity="warning", timeout=2)
            return

        try:
            import subprocess
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=link.encode(),
                check=True,
            )
            self.app.notify("Link copied", timeout=2)
        except Exception:
            self.app.notify(link, timeout=5)

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

            case Action.MOVE_UP:
                focused = self.app.focused
                if isinstance(focused, ListView):
                    for _ in range(count):
                        focused.action_cursor_up()

            case Action.PAGE_DOWN:
                focused = self.app.focused
                if isinstance(focused, ListView):
                    focused.action_scroll_down()

            case Action.PAGE_UP:
                focused = self.app.focused
                if isinstance(focused, ListView):
                    focused.action_scroll_up()

            case Action.GO_TOP:
                focused = self.app.focused
                if isinstance(focused, ListView):
                    focused.action_first()

            case Action.GO_BOTTOM:
                focused = self.app.focused
                if isinstance(focused, ListView):
                    focused.action_last()

            case Action.SELECT:
                focused = self.app.focused
                if isinstance(focused, ListView):
                    focused.action_select_cursor()

            case Action.FOCUS_NEXT:
                self.app.action_focus_next()

            case Action.FOCUS_PREV:
                self.app.action_focus_previous()

            case Action.FILTER:
                # Show in-panel filter for the focused panel.
                focused = self.app.focused
                panel = self._find_parent_panel(focused)
                if panel is not None:
                    panel.show_filter()

            case Action.DELETE_ITEM:
                self._confirm_delete_highlighted()

    # ------------------------------------------------------------------
    # Delete with confirmation
    # ------------------------------------------------------------------

    def _get_highlighted_item(self) -> tuple[dict[str, Any] | None, str]:
        """Return (item_data, panel_id) for the currently highlighted item."""
        focused = self.app.focused
        panel = self._find_parent_panel(focused)
        if panel is None:
            return None, ""
        list_view = panel.query_one(ListView)
        idx = list_view.index
        if idx is not None and 0 <= idx < len(panel._filtered_items):
            return panel._filtered_items[idx], panel.id or ""
        return None, panel.id or ""

    def _confirm_delete_highlighted(self) -> None:
        """Show a confirmation popup for the highlighted item, then delete."""
        from ytm_player.ui.popups.confirm_popup import ConfirmPopup

        item, panel_id = self._get_highlighted_item()
        if item is None:
            self.app.notify("No item selected", severity="warning", timeout=2)
            return

        title = item.get("title", item.get("name", "this item"))

        if panel_id == "playlists":
            msg = f"Delete playlist '{title}'?"
        elif panel_id == "albums":
            msg = f"Remove album '{title}' from library?"
        elif panel_id == "artists":
            msg = f"Unsubscribe from '{title}'?"
        else:
            return

        def _on_confirm(confirmed: bool) -> None:
            if confirmed:
                asyncio.ensure_future(self._execute_delete(item, panel_id))

        self.app.push_screen(ConfirmPopup(msg), _on_confirm)

    async def _execute_delete(self, item: dict[str, Any], panel_id: str) -> None:
        """Perform the actual deletion based on panel type."""
        title = item.get("title", item.get("name", "item"))

        try:
            if panel_id == "playlists":
                playlist_id = item.get("playlistId") or item.get("browseId", "")
                if not playlist_id:
                    self.app.notify("Cannot determine playlist ID", severity="error", timeout=3)
                    return
                success = await self.app.ytmusic.delete_playlist(playlist_id)
                label = "Deleted"

            elif panel_id == "albums":
                # rate_playlist(INDIFFERENT) removes from library.
                browse_id = item.get("browseId") or item.get("album_id", "")
                # The playlistId for an album is needed, not the browseId.
                playlist_id = item.get("playlistId") or browse_id
                if not playlist_id:
                    self.app.notify("Cannot determine album ID", severity="error", timeout=3)
                    return
                success = await self.app.ytmusic.remove_album_from_library(playlist_id)
                label = "Removed"

            elif panel_id == "artists":
                channel_id = item.get("browseId") or item.get("artist_id", "")
                if not channel_id:
                    self.app.notify("Cannot determine artist ID", severity="error", timeout=3)
                    return
                success = await self.app.ytmusic.unsubscribe_artist(channel_id)
                label = "Unsubscribed from"

            else:
                return

            if success:
                self.app.notify(f"{label} '{title}'", timeout=2)
                await self._reload_panel(panel_id)
            else:
                self.app.notify(f"Failed to remove '{title}'", severity="error", timeout=3)

        except Exception:
            logger.exception("Failed to delete item from panel %r", panel_id)
            self.app.notify(f"Failed to remove '{title}'", severity="error", timeout=3)

    def _find_parent_panel(self, widget: Widget | None) -> LibraryPanel | None:
        """Walk up the widget tree to find the enclosing LibraryPanel."""
        current = widget
        while current is not None:
            if isinstance(current, LibraryPanel):
                return current
            current = current.parent
        # Fall back to the first panel.
        try:
            return self.query_one("#playlists", LibraryPanel)
        except Exception:
            return None
