"""Searchable keybinding reference page."""

from __future__ import annotations

import logging
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import DataTable, Input, Label

from ytm_player.config.keymap import Action, get_keymap

logger = logging.getLogger(__name__)


# ── Action metadata ──────────────────────────────────────────────────

ACTION_DESCRIPTIONS: dict[Action, str] = {
    # Playback
    Action.PLAY_PAUSE: "Toggle play/pause",
    Action.NEXT_TRACK: "Skip to next track",
    Action.PREVIOUS_TRACK: "Go to previous track",
    Action.PLAY_RANDOM: "Play a random track from queue",
    Action.CYCLE_REPEAT: "Cycle repeat mode (Off/All/One)",
    Action.TOGGLE_SHUFFLE: "Toggle shuffle on/off",
    Action.VOLUME_UP: "Increase volume",
    Action.VOLUME_DOWN: "Decrease volume",
    Action.MUTE: "Toggle mute",
    Action.SEEK_FORWARD: "Seek forward",
    Action.SEEK_BACKWARD: "Seek backward",
    Action.SEEK_START: "Seek to beginning of track",
    # Navigation
    Action.MOVE_DOWN: "Move cursor down",
    Action.MOVE_UP: "Move cursor up",
    Action.PAGE_DOWN: "Scroll page down",
    Action.PAGE_UP: "Scroll page up",
    Action.GO_TOP: "Jump to top of list",
    Action.GO_BOTTOM: "Jump to bottom of list",
    Action.SELECT: "Select / confirm",
    Action.FOCUS_NEXT: "Focus next panel",
    Action.FOCUS_PREV: "Focus previous panel",
    Action.GO_BACK: "Go back / close page",
    Action.CLOSE_POPUP: "Close popup / cancel",
    # Pages
    Action.LIBRARY: "Go to library",
    Action.SEARCH: "Go to search",
    Action.BROWSE: "Go to browse",
    Action.LIKED_SONGS: "Go to liked songs",
    Action.RECENTLY_PLAYED: "Go to recently played",
    Action.LYRICS: "Show lyrics",
    Action.CURRENT_CONTEXT: "Go to current context",
    Action.JUMP_TO_CURRENT: "Jump to currently playing track",
    Action.QUEUE: "Show playback queue",
    Action.HELP: "Show this help page",
    # Actions
    Action.DELETE_ITEM: "Delete / remove item",
    Action.TRACK_ACTIONS: "Show track actions menu",
    Action.CONTEXT_ACTIONS: "Show context actions menu",
    Action.SELECTED_ACTIONS: "Actions for selected items",
    Action.ADD_TO_QUEUE: "Add track to queue",
    Action.ADD_TO_PLAYLIST: "Add track to a playlist",
    Action.FILTER: "Filter / search within list",
    # Sorting
    Action.SORT_TITLE: "Sort by title",
    Action.SORT_ARTIST: "Sort by artist",
    Action.SORT_ALBUM: "Sort by album",
    Action.SORT_DURATION: "Sort by duration",
    Action.SORT_DATE: "Sort by date",
    Action.REVERSE_SORT: "Reverse sort order",
    # Search
    Action.TOGGLE_SEARCH_MODE: "Toggle search mode",
}

ACTION_CATEGORIES: dict[str, list[Action]] = {
    "Playback": [
        Action.PLAY_PAUSE,
        Action.NEXT_TRACK,
        Action.PREVIOUS_TRACK,
        Action.PLAY_RANDOM,
        Action.CYCLE_REPEAT,
        Action.TOGGLE_SHUFFLE,
        Action.VOLUME_UP,
        Action.VOLUME_DOWN,
        Action.MUTE,
        Action.SEEK_FORWARD,
        Action.SEEK_BACKWARD,
        Action.SEEK_START,
    ],
    "Navigation": [
        Action.MOVE_DOWN,
        Action.MOVE_UP,
        Action.PAGE_DOWN,
        Action.PAGE_UP,
        Action.GO_TOP,
        Action.GO_BOTTOM,
        Action.SELECT,
        Action.FOCUS_NEXT,
        Action.FOCUS_PREV,
        Action.GO_BACK,
        Action.CLOSE_POPUP,
    ],
    "Pages": [
        Action.LIBRARY,
        Action.SEARCH,
        Action.BROWSE,
        Action.LIKED_SONGS,
        Action.RECENTLY_PLAYED,
        Action.LYRICS,
        Action.CURRENT_CONTEXT,
        Action.JUMP_TO_CURRENT,
        Action.QUEUE,
        Action.HELP,
    ],
    "Actions": [
        Action.DELETE_ITEM,
        Action.TRACK_ACTIONS,
        Action.CONTEXT_ACTIONS,
        Action.SELECTED_ACTIONS,
        Action.ADD_TO_QUEUE,
        Action.ADD_TO_PLAYLIST,
        Action.FILTER,
    ],
    "Sorting": [
        Action.SORT_TITLE,
        Action.SORT_ARTIST,
        Action.SORT_ALBUM,
        Action.SORT_DURATION,
        Action.SORT_DATE,
        Action.REVERSE_SORT,
    ],
    "Search": [
        Action.TOGGLE_SEARCH_MODE,
    ],
}


def _format_action_name(action: Action) -> str:
    """Convert an Action enum value to a human-readable name."""
    return action.value.replace("_", " ").title()


class HelpPage(Widget):
    """Searchable keybinding reference.

    Generates a grouped table from the active KeyMap, showing each action's
    name, keybinding(s), and description. Supports filtering with `/`.
    """

    DEFAULT_CSS = """
    HelpPage {
        layout: vertical;
        width: 1fr;
        height: 1fr;
    }
    .help-header {
        height: auto;
        max-height: 2;
        padding: 1 2;
        text-style: bold;
    }
    .help-filter {
        height: auto;
        max-height: 3;
        padding: 0 2;
        display: none;
    }
    .help-table {
        height: 1fr;
        width: 1fr;
    }
    .help-table > .datatable--cursor {
        background: #2a2a2a;
    }
    """

    filter_visible: reactive[bool] = reactive(False)
    filter_text: reactive[str] = reactive("")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._all_rows: list[tuple[str, str, str, str]] = []  # (category, name, keys, desc)

    def compose(self) -> ComposeResult:
        yield Label("[b]Keybindings[/b]", markup=True, classes="help-header")
        yield Input(
            placeholder="Filter keybindings...",
            id="help-filter-input",
            classes="help-filter",
        )
        yield DataTable(
            cursor_type="row",
            zebra_stripes=True,
            id="help-table",
            classes="help-table",
        )

    def on_mount(self) -> None:
        table = self.query_one("#help-table", DataTable)
        table.add_column("Action", width=None, key="action")
        table.add_column("Key", width=16, key="key")
        table.add_column("Description", width=None, key="description")

        self._build_rows()
        self._populate_table()

    def _build_rows(self) -> None:
        """Generate the full row list from the keymap, grouped by category."""
        keymap = get_keymap()
        self._all_rows = []

        for category, actions in ACTION_CATEGORIES.items():
            for action in actions:
                name = _format_action_name(action)
                description = ACTION_DESCRIPTIONS.get(action, "")
                key_seqs = keymap.get_keys_for_action(action)
                keys_str = (
                    " / ".join(keymap.format_key(seq) for seq in key_seqs)
                    if key_seqs
                    else "(unbound)"
                )
                self._all_rows.append((category, name, keys_str, description))

    def _populate_table(self, filter_text: str = "") -> None:
        """Fill the DataTable, optionally filtering rows."""
        table = self.query_one("#help-table", DataTable)
        table.clear()

        needle = filter_text.lower().strip()
        current_category = ""

        for category, name, keys, desc in self._all_rows:
            # Apply filter if active.
            if needle:
                haystack = f"{category} {name} {keys} {desc}".lower()
                if needle not in haystack:
                    continue

            # Insert a category header row when the category changes.
            if category != current_category:
                current_category = category
                table.add_row(
                    f"[b]{category}[/b]", "", "",
                    key=f"header_{category}",
                )

            table.add_row(name, keys, desc)

    # ── Filter management ─────────────────────────────────────────────

    def watch_filter_visible(self, visible: bool) -> None:
        try:
            filter_input = self.query_one("#help-filter-input", Input)
            filter_input.display = visible
            if visible:
                filter_input.focus()
            else:
                filter_input.value = ""
                self.filter_text = ""
        except Exception:
            pass

    def watch_filter_text(self, text: str) -> None:
        self._populate_table(text)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "help-filter-input":
            self.filter_text = event.value

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "help-filter-input":
            # Close filter and keep the result.
            self.filter_visible = False
            self.query_one("#help-table", DataTable).focus()

    # ── Action handling ───────────────────────────────────────────────

    async def handle_action(self, action: Action, count: int = 1) -> None:
        """Process vim-style navigation actions."""
        # If the filter input is focused, let it handle keys normally.
        if self.filter_visible:
            if action == Action.CLOSE_POPUP:
                self.filter_visible = False
            return

        table = self.query_one("#help-table", DataTable)

        match action:
            case Action.GO_BACK:
                await self.app.navigate_to("back")  # type: ignore[attr-defined]

            case Action.FILTER:
                self.filter_visible = True

            case Action.MOVE_DOWN:
                for _ in range(count):
                    table.action_cursor_down()

            case Action.MOVE_UP:
                for _ in range(count):
                    table.action_cursor_up()

            case Action.PAGE_DOWN:
                table.action_scroll_down()

            case Action.PAGE_UP:
                table.action_scroll_up()

            case Action.GO_TOP:
                if table.row_count > 0:
                    table.move_cursor(row=0)

            case Action.GO_BOTTOM:
                if table.row_count > 0:
                    table.move_cursor(row=table.row_count - 1)

            case Action.CLOSE_POPUP:
                if self.filter_text:
                    self.filter_text = ""
                    self._populate_table()
                else:
                    await self.app.navigate_to("back")  # type: ignore[attr-defined]
