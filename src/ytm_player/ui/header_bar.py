"""Top header bar with sidebar toggle buttons."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.events import Click
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class HeaderBar(Widget):
    """Top bar with toggle buttons for playlist and lyrics sidebars."""

    class TogglePlaylistSidebar(Message):
        """Emitted when the playlist sidebar toggle is clicked."""

    class ToggleLyricsSidebar(Message):
        """Emitted when the lyrics sidebar toggle is clicked."""

    DEFAULT_CSS = """
    HeaderBar {
        dock: top;
        height: 1;
        background: $playback-bar-bg;
    }
    HeaderBar #header-inner {
        height: 1;
        width: 1fr;
    }
    HeaderBar #header-spacer {
        width: 1fr;
        height: 1;
    }
    HeaderBar .hb-toggle {
        height: 1;
        width: auto;
        padding: 0 1;
        color: $text-muted;
    }
    HeaderBar .hb-toggle:hover {
        background: $border;
    }
    HeaderBar .hb-toggle.active {
        color: $primary;
        text-style: bold;
    }
    HeaderBar .hb-toggle.dimmed {
        color: $text-muted;
        text-style: dim;
    }
    """

    is_playlist_on: reactive[bool] = reactive(False)
    is_lyrics_on: reactive[bool] = reactive(False)
    is_lyrics_dimmed: reactive[bool] = reactive(False)

    def compose(self) -> ComposeResult:
        with Horizontal(id="header-inner"):
            yield Static("\u2630 Playlists", id="toggle-playlist", classes="hb-toggle")
            yield Static(id="header-spacer")
            yield Static("\u266b Lyrics", id="toggle-lyrics", classes="hb-toggle")

    def on_click(self, event: Click) -> None:
        """Route clicks on toggle buttons to the correct message."""
        target = event.widget
        if target.id == "toggle-playlist":
            event.stop()
            self.post_message(self.TogglePlaylistSidebar())
        elif target.id == "toggle-lyrics":
            event.stop()
            self.post_message(self.ToggleLyricsSidebar())

    # ── State updates ────────────────────────────────────────────────

    def set_playlist_state(self, is_open: bool) -> None:
        self.is_playlist_on = is_open
        try:
            self.query_one("#toggle-playlist", Static).set_class(is_open, "active")
        except Exception:
            pass

    def set_lyrics_state(self, is_open: bool) -> None:
        self.is_lyrics_on = is_open
        self._apply_lyrics_classes()

    def set_lyrics_dimmed(self, dimmed: bool) -> None:
        self.is_lyrics_dimmed = dimmed
        self._apply_lyrics_classes()

    def _apply_lyrics_classes(self) -> None:
        try:
            btn = self.query_one("#toggle-lyrics", Static)
            if self.is_lyrics_dimmed:
                btn.remove_class("active")
                btn.add_class("dimmed")
            elif self.is_lyrics_on:
                btn.remove_class("dimmed")
                btn.add_class("active")
            else:
                btn.remove_class("active", "dimmed")
        except Exception:
            pass
