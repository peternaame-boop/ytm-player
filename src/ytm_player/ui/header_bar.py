"""Top header bar with sidebar toggle buttons."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.events import Click
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from ytm_player.ui.theme import get_theme


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
    }
    HeaderBar .hb-toggle:hover {
        background: $border;
    }
    """

    is_playlist_on: reactive[bool] = reactive(False)
    is_lyrics_on: reactive[bool] = reactive(False)
    is_lyrics_dimmed: reactive[bool] = reactive(False)

    def compose(self) -> ComposeResult:
        with Horizontal(id="header-inner"):
            yield Static(id="toggle-playlist", classes="hb-toggle")
            yield Static(id="header-spacer")
            yield Static(id="toggle-lyrics", classes="hb-toggle")

    def on_mount(self) -> None:
        self._update_playlist_label()
        self._update_lyrics_label()

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
        self._update_playlist_label()

    def set_lyrics_state(self, is_open: bool) -> None:
        self.is_lyrics_on = is_open
        self._update_lyrics_label()

    def set_lyrics_dimmed(self, dimmed: bool) -> None:
        self.is_lyrics_dimmed = dimmed
        self._update_lyrics_label()

    def _update_playlist_label(self) -> None:
        try:
            theme = get_theme()
            btn = self.query_one("#toggle-playlist", Static)
            label = "\u2630 Playlists"
            if self.is_playlist_on:
                btn.update(Text(label, style=f"bold {theme.primary}"))
            else:
                btn.update(Text(label, style=theme.muted_text))
        except Exception:
            pass

    def _update_lyrics_label(self) -> None:
        try:
            theme = get_theme()
            btn = self.query_one("#toggle-lyrics", Static)
            label = "\u266b Lyrics"
            if self.is_lyrics_dimmed:
                btn.update(Text(label, style="dim"))
            elif self.is_lyrics_on:
                btn.update(Text(label, style=f"bold {theme.primary}"))
            else:
                btn.update(Text(label, style=theme.muted_text))
        except Exception:
            pass
