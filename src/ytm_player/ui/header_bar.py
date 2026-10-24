"""Top header bar with sidebar toggle buttons."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.events import Click
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from ytm_player.config.settings import get_settings
from ytm_player.ui.icons import icon
from ytm_player.utils.usage import UsageSampler, format_usage

# Seconds between refreshes of the CPU and memory readout.
USAGE_REFRESH_SECONDS = 2.0


class HeaderBar(Widget):
    """Top bar with toggle buttons for playlist and lyrics sidebars."""

    class TogglePlaylistSidebar(Message):
        """Emitted when the playlist sidebar toggle is clicked."""

    class ToggleLyricsSidebar(Message):
        """Emitted when the lyrics sidebar toggle is clicked."""

    class SearchRequested(Message):
        """The top bar's search box was clicked."""

    class BackRequested(Message):
        """Emitted when the back button is clicked."""

    class ForwardRequested(Message):
        """Emitted when the forward button is clicked."""

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
    HeaderBar #header-search {
        width: 1fr;
        max-width: 60;
        height: 1;
        margin: 0 2;
        padding: 0 2;
        background: $boost;
        color: $text-muted;
    }
    HeaderBar #header-search:hover {
        color: $text;
        background: $primary 20%;
    }
    HeaderBar #header-spacer {
        width: 1fr;
        height: 1;
    }
    HeaderBar #header-usage {
        width: auto;
        height: 1;
        padding: 0 1;
        color: $primary;
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
    HeaderBar #back-btn {
        display: none;
    }
    HeaderBar #back-btn.visible {
        display: block;
    }
    HeaderBar #forward-btn {
        display: none;
    }
    HeaderBar #forward-btn.visible {
        display: block;
    }
    """

    is_playlist_on: reactive[bool] = reactive(False)
    is_lyrics_on: reactive[bool] = reactive(False)
    is_lyrics_dimmed: reactive[bool] = reactive(False)

    def compose(self) -> ComposeResult:
        with Horizontal(id="header-inner"):
            yield Static(f"{icon('menu')} Playlists", id="toggle-playlist", classes="hb-toggle")
            yield Static(f"{icon('back')} Back", id="back-btn", classes="hb-toggle")
            yield Static(f"Forward {icon('forward')}", id="forward-btn", classes="hb-toggle")
            yield Static(
                f"{icon('search')}  Search songs, albums, artists, podcasts", id="header-search"
            )
            yield Static(id="header-spacer")
            if get_settings().ui.show_usage:
                yield Static(id="header-usage")
            yield Static(f"{icon('lyrics')} Lyrics", id="toggle-lyrics", classes="hb-toggle")

    def on_mount(self) -> None:
        if get_settings().ui.show_usage:
            self._usage = UsageSampler()
            # A sample needs some elapsed time behind it to mean anything.
            self.set_timer(0.5, self.refresh_usage)
            self.set_interval(USAGE_REFRESH_SECONDS, self.refresh_usage)

    def refresh_usage(self) -> None:
        """Show CPU use since the last refresh and current memory."""
        try:
            label = self.query_one("#header-usage", Static)
        except Exception:
            return
        label.update(format_usage(self._usage.sample()))

    def on_click(self, event: Click) -> None:
        """Route clicks on toggle buttons to the correct message."""
        target = event.widget
        if target is None:
            return
        if target.id == "toggle-playlist":
            event.stop()
            self.post_message(self.TogglePlaylistSidebar())
        elif target.id == "toggle-lyrics":
            event.stop()
            self.post_message(self.ToggleLyricsSidebar())
        elif target.id == "header-search":
            event.stop()
            self.post_message(self.SearchRequested())
        elif target.id == "back-btn":
            event.stop()
            self.post_message(self.BackRequested())
        elif target.id == "forward-btn":
            event.stop()
            self.post_message(self.ForwardRequested())

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

    def set_back_visible(self, visible: bool) -> None:
        """Show/hide the back button. Called when the nav stack changes."""
        try:
            btn = self.query_one("#back-btn", Static)
            if visible:
                btn.add_class("visible")
            else:
                btn.remove_class("visible")
        except Exception:
            pass

    def set_forward_visible(self, visible: bool) -> None:
        """Show/hide the forward button. Called when the forward stack changes."""
        try:
            btn = self.query_one("#forward-btn", Static)
            if visible:
                btn.add_class("visible")
            else:
                btn.remove_class("visible")
        except Exception:
            pass
