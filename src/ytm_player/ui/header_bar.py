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
        background: $accent 30%;
    }
    HeaderBar .hb-toggle.active {
        color: $primary;
        text-style: bold;
    }
    HeaderBar .hb-toggle.dimmed {
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
        if target.id == "toggle-playlist":  # type: ignore[reportOptionalMemberAccess]
            event.stop()
            self.post_message(self.TogglePlaylistSidebar())
        elif target.id == "toggle-lyrics":  # type: ignore[reportOptionalMemberAccess]
            event.stop()
            self.post_message(self.ToggleLyricsSidebar())

    # ── State updates ────────────────────────────────────────────────

    def set_playlist_state(self, is_open: bool) -> None:
        self.is_playlist_on = is_open
        try:
            btn = self.query_one("#toggle-playlist", Static)
            if is_open:
                btn.add_class("active")
            else:
                btn.remove_class("active")
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
            btn.remove_class("active", "dimmed")
            if self.is_lyrics_dimmed:
                btn.add_class("dimmed")
            elif self.is_lyrics_on:
                btn.add_class("active")
        except Exception:
            pass
