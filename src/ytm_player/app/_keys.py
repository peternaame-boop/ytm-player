"""Key handling and action dispatch mixin for YTMPlayerApp."""

from __future__ import annotations

import logging

from textual.events import Key

from ytm_player.config import Action, MatchResult
from ytm_player.ui.playback_bar import PlaybackBar
from ytm_player.ui.sidebars.lyrics_sidebar import LyricsSidebar

logger = logging.getLogger(__name__)

_MAX_KEY_COUNT = 1000


class KeyHandlingMixin:
    """Keyboard input processing and action dispatch."""

    async def on_key(self, event: Key) -> None:
        """Process keyboard input through the KeyMap system.

        Supports vim-style count prefixes (e.g. "5j" to move down 5 rows)
        and multi-key sequences (e.g. "g g" to go to top).
        """
        # Don't intercept keys when a modal screen is active -- let the
        # modal's own widgets (Input, ListView, etc.) handle them.
        if self.screen.is_modal:
            return

        # Don't intercept keys when an Input or TextArea is focused -- let
        # the widget handle normal text entry.
        from textual.widgets import Input, TextArea

        focused = self.focused
        if isinstance(focused, (Input, TextArea)):
            return

        key = self._normalize_key(event)

        # Digit handling: accumulate count prefix if no keys buffered yet.
        if key.isdigit() and not self._key_buffer:
            self._count_buffer += key
            event.prevent_default()
            return

        self._key_buffer.append(key)
        sequence = tuple(self._key_buffer)

        result, action = self.keymap.match(sequence)

        if result == MatchResult.EXACT:
            count = int(self._count_buffer) if self._count_buffer else 1
            count = min(count, _MAX_KEY_COUNT)  # Safety cap.
            self._key_buffer.clear()
            self._count_buffer = ""
            event.prevent_default()
            event.stop()
            await self._handle_action(action, count)

        elif result == MatchResult.PENDING:
            # Waiting for more keys in the sequence.
            event.prevent_default()
            event.stop()

        else:
            # No match -- reset buffers.
            self._key_buffer.clear()
            self._count_buffer = ""

    @staticmethod
    def _normalize_key(event: Key) -> str:
        """Convert a Textual Key event into the string format used by KeyMap.

        Textual key names like 'ctrl+r' become 'C-r', 'shift+tab' becomes
        'S-tab', etc.
        """
        key = event.key

        # Textual uses names like "ctrl+x", "shift+tab", "alt+v".
        if key.startswith("ctrl+"):
            return f"C-{key[5:]}"
        if key.startswith("shift+"):
            return f"S-{key[6:]}"
        if key.startswith("alt+"):
            return f"M-{key[4:]}"

        # Map Textual's special key names to our keymap names.
        key_map = {
            "up": "up",
            "down": "down",
            "left": "left",
            "right": "right",
            "home": "home",
            "end": "end",
            "pageup": "page_up",
            "pagedown": "page_down",
            "page_up": "page_up",
            "page_down": "page_down",
            "backspace": "backspace",
            "delete": "delete",
            "tab": "tab",
            "enter": "enter",
            "return": "enter",
            "escape": "escape",
            "plus": "+",
            "minus": "-",
            "equals": "=",
            "question_mark": "?",
            "slash": "/",
        }

        return key_map.get(key, key)

    async def _handle_action(self, action: Action | None, count: int = 1) -> None:
        """Dispatch a resolved action to the appropriate handler."""
        if action is None:
            return

        match action:
            # -- Playback controls --
            case Action.PLAY_PAUSE:
                await self._toggle_play_pause()

            case Action.NEXT_TRACK:
                await self._play_next()

            case Action.PREVIOUS_TRACK:
                await self._play_previous()

            case Action.PLAY_RANDOM:
                track = self.queue.play_random()
                if track:
                    await self.play_track(track)

            case Action.VOLUME_UP:
                if self.player:
                    await self.player.change_volume(5 * count)

            case Action.VOLUME_DOWN:
                if self.player:
                    await self.player.change_volume(-5 * count)

            case Action.MUTE:
                if self.player:
                    await self.player.mute()

            case Action.SEEK_FORWARD:
                if self.player:
                    await self.player.seek(self.settings.playback.seek_step * count)

            case Action.SEEK_BACKWARD:
                if self.player:
                    await self.player.seek(-self.settings.playback.seek_step * count)

            case Action.SEEK_START:
                if self.player:
                    await self.player.seek_start()

            case Action.CYCLE_REPEAT:
                mode = self.queue.cycle_repeat()
                bar = self.query_one("#playback-bar", PlaybackBar)
                bar.update_repeat(mode)
                self.notify(f"Repeat: {mode.value}", timeout=2)

            case Action.TOGGLE_SHUFFLE:
                self.queue.toggle_shuffle()
                bar = self.query_one("#playback-bar", PlaybackBar)
                bar.update_shuffle(self.queue.shuffle_enabled)
                state = "on" if self.queue.shuffle_enabled else "off"
                self.notify(f"Shuffle: {state}", timeout=2)

            # -- Page navigation --
            case Action.LIBRARY:
                await self.navigate_to("library")
            case Action.SEARCH:
                await self.navigate_to("search")
            case Action.QUEUE:
                await self.navigate_to("queue")
            case Action.LYRICS:
                self._toggle_lyrics_sidebar()
            case Action.TOGGLE_SIDEBAR:
                self._toggle_playlist_sidebar()
            case Action.TOGGLE_TRANSLITERATION:
                try:
                    self.query_one("#lyrics-sidebar", LyricsSidebar).toggle_transliteration()
                except Exception:
                    pass
            case Action.BROWSE:
                await self.navigate_to("browse")
            case Action.HELP:
                await self.navigate_to("help")
            case Action.LIKED_SONGS:
                await self.navigate_to("liked_songs")
            case Action.RECENTLY_PLAYED:
                await self.navigate_to("recently_played")
            case Action.CURRENT_CONTEXT:
                await self.navigate_to("context")

            case Action.GO_BACK:
                await self.navigate_to("back")

            case Action.CLOSE_POPUP:
                # Dismiss active popup if any; otherwise ignore.
                pass

            case Action.QUIT:
                self._clean_exit = True
                self.exit()

            # -- Add to playlist (quick shortcut for current track) --
            case Action.ADD_TO_PLAYLIST:
                await self._open_add_to_playlist()

            # -- Track actions (opens popup, handles result) --
            case Action.TRACK_ACTIONS:
                await self._open_track_actions()

            # -- Navigation actions delegated to the current page --
            case (
                Action.MOVE_DOWN
                | Action.MOVE_UP
                | Action.PAGE_DOWN
                | Action.PAGE_UP
                | Action.GO_TOP
                | Action.GO_BOTTOM
                | Action.SELECT
                | Action.FOCUS_NEXT
                | Action.FOCUS_PREV
                | Action.CONTEXT_ACTIONS
                | Action.SELECTED_ACTIONS
                | Action.ADD_TO_QUEUE
                | Action.DELETE_ITEM
                | Action.FILTER
                | Action.SORT_TITLE
                | Action.SORT_ARTIST
                | Action.SORT_ALBUM
                | Action.SORT_DURATION
                | Action.SORT_DATE
                | Action.REVERSE_SORT
                | Action.JUMP_TO_CURRENT
                | Action.TOGGLE_SEARCH_MODE
            ):
                page = self._get_current_page()
                if page and hasattr(page, "handle_action"):
                    await page.handle_action(action, count)

            case _:
                logger.debug("Unhandled action: %s", action)
