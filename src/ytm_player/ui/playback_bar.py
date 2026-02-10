"""Always-visible playback status bar and interactive footer."""

from __future__ import annotations

import logging

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Click, MouseScrollDown, MouseScrollUp
from textual.reactive import reactive
from textual.widget import Widget
from rich.text import Text

from ytm_player.services.queue import RepeatMode
from ytm_player.ui.widgets.album_art import AlbumArt
from ytm_player.ui.widgets.progress_bar import PlaybackProgress
from ytm_player.utils.formatting import extract_artist, format_duration, truncate

logger = logging.getLogger(__name__)

# Playback state symbols.
_ICON_PLAYING = "\u25b6"  # Black right-pointing triangle
_ICON_PAUSED = "\u23f8"  # Double vertical bar
_ICON_STOPPED = "\u25a0"  # Black square

_ICON_VOLUME = "\U0001f50a"  # Speaker high volume

_ICON_REPEAT_OFF = "\U0001f501"  # Repeat button
_ICON_REPEAT_ALL = "\U0001f501"  # Same icon, coloured differently
_ICON_REPEAT_ONE = "\U0001f502"  # Repeat single button

_ICON_SHUFFLE_OFF = "\U0001f500"  # Twisted arrows
_ICON_SHUFFLE_ON = "\U0001f500"  # Same, coloured differently


# ── Track info widget ─────────────────────────────────────────────


class _TrackInfo(Widget):
    """Displays the current track title, artist, and album on a single line."""

    DEFAULT_CSS = """
    _TrackInfo {
        height: 1;
        width: 1fr;
    }
    """

    title: reactive[str] = reactive("")
    artist: reactive[str] = reactive("")
    album: reactive[str] = reactive("")
    is_playing: reactive[bool] = reactive(False)
    is_paused: reactive[bool] = reactive(False)

    def render(self) -> Text:
        result = Text()

        # State icon
        if self.is_playing and not self.is_paused:
            result.append(f" {_ICON_PLAYING} ", style="bold #ff0000")
        elif self.is_paused:
            result.append(f" {_ICON_PAUSED} ", style="bold #f39c12")
        else:
            result.append(f" {_ICON_STOPPED} ", style="#999999")

        if self.title:
            max_w = max(10, self.size.width - 30)
            title_w = min(len(self.title), max_w // 2)
            artist_w = min(len(self.artist), max_w // 3)
            album_w = max_w - title_w - artist_w - 8

            result.append(truncate(self.title, title_w), style="bold white")
            if self.artist:
                result.append(" \u2014 ", style="#999999")
                result.append(truncate(self.artist, artist_w), style="#aaaaaa")
            if self.album:
                result.append(" \u2014 ", style="#999999")
                result.append(truncate(self.album, max(0, album_w)), style="#999999")
        else:
            result.append("No track playing", style="#999999")

        return result



# ── Interactive control widgets ───────────────────────────────────


class _VolumeDisplay(Widget):
    """Volume display — scroll to change volume."""

    DEFAULT_CSS = """
    _VolumeDisplay {
        height: 1;
        width: auto;
        min-width: 9;
    }
    """

    volume: reactive[int] = reactive(80)

    def render(self) -> Text:
        return Text(f" {_ICON_VOLUME} {self.volume:>3}%", style="#aaaaaa")

    async def on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        event.stop()
        app = self.app
        if hasattr(app, "player") and app.player:
            await app.player.change_volume(5)

    async def on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        event.stop()
        app = self.app
        if hasattr(app, "player") and app.player:
            await app.player.change_volume(-5)



class _RepeatButton(Widget):
    """Clickable repeat mode indicator."""

    DEFAULT_CSS = """
    _RepeatButton {
        height: 1;
        width: auto;
        min-width: 7;
        padding: 0 1;
    }
    _RepeatButton:hover {
        background: #333333;
    }
    """

    repeat_mode: reactive[str] = reactive("off")

    def render(self) -> Text:
        if self.repeat_mode == "all":
            return Text(f"{_ICON_REPEAT_ALL} all", style="bold #2ecc71")
        elif self.repeat_mode == "one":
            return Text(f"{_ICON_REPEAT_ONE} one", style="bold #f39c12")
        return Text(f"{_ICON_REPEAT_OFF} off", style="#999999")

    async def on_click(self, event: Click) -> None:
        event.stop()
        app = self.app
        if hasattr(app, "queue"):
            mode = app.queue.cycle_repeat()
            try:
                bar = app.query_one("#playback-bar", PlaybackBar)
                bar.update_repeat(mode)
                app.notify(f"Repeat: {mode.value}", timeout=2)
            except Exception:
                pass



class _ShuffleButton(Widget):
    """Clickable shuffle indicator."""

    DEFAULT_CSS = """
    _ShuffleButton {
        height: 1;
        width: auto;
        min-width: 7;
        padding: 0 1;
    }
    _ShuffleButton:hover {
        background: #333333;
    }
    """

    shuffle_on: reactive[bool] = reactive(False)

    def render(self) -> Text:
        if self.shuffle_on:
            return Text(f"{_ICON_SHUFFLE_ON} on ", style="bold #2ecc71")
        return Text(f"{_ICON_SHUFFLE_OFF} off", style="#999999")

    async def on_click(self, event: Click) -> None:
        event.stop()
        app = self.app
        if hasattr(app, "queue"):
            app.queue.toggle_shuffle()
            enabled = app.queue.shuffle_enabled
            try:
                bar = app.query_one("#playback-bar", PlaybackBar)
                bar.update_shuffle(enabled)
                state = "on" if enabled else "off"
                app.notify(f"Shuffle: {state}", timeout=2)
            except Exception:
                pass



# ── Main playback bar ─────────────────────────────────────────────


class PlaybackBar(Widget):
    """Persistent playback bar showing track info, progress, and controls.

    Layout (2 lines + optional album art):
        Line 1: [art] > Song Title -- Artist Name -- Album       vol  repeat  shuffle
        Line 2: [art]  1:23 [===========>---------] 4:56
    """

    DEFAULT_CSS = """
    PlaybackBar {
        dock: bottom;
        height: 4;
        background: #1a1a1a;
        border-top: solid #333333;
    }
    PlaybackBar #pb-outer {
        height: 100%;
        width: 1fr;
    }
    PlaybackBar #pb-art {
        width: 10;
        height: 3;
        margin: 0 1 0 0;
    }
    PlaybackBar #pb-content {
        width: 1fr;
        height: auto;
    }
    PlaybackBar #pb-top-row {
        height: 1;
        width: 1fr;
    }
    PlaybackBar #pb-bottom-row {
        height: 1;
        width: 1fr;
    }
    PlaybackBar #pb-track-info {
        width: 1fr;
    }
    PlaybackBar #pb-progress {
        width: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        with Horizontal(id="pb-outer"):
            yield AlbumArt(id="pb-art")
            with Vertical(id="pb-content"):
                with Horizontal(id="pb-top-row"):
                    yield _TrackInfo(id="pb-track-info")
                    yield _VolumeDisplay(id="pb-volume")
                    yield _RepeatButton(id="pb-repeat")
                    yield _ShuffleButton(id="pb-shuffle")
                with Horizontal(id="pb-bottom-row"):
                    yield PlaybackProgress(
                        bar_style="block",
                        filled_color="#ff0000",
                        empty_color="#404040",
                        time_color="#aaaaaa",
                        id="pb-progress",
                    )

    # ── Public update methods ────────────────────────────────────────

    def update_track(self, track: dict | None) -> None:
        """Update displayed track information."""
        info = self.query_one("#pb-track-info", _TrackInfo)
        art = self.query_one("#pb-art", AlbumArt)

        if track is None:
            info.title = ""
            info.artist = ""
            info.album = ""
            info.is_playing = False
            info.is_paused = False
            art.clear_track()
            return

        info.title = track.get("title", "")
        info.artist = extract_artist(track)
        info.album = track.get("album") or ""
        art.set_track(track.get("thumbnail_url", ""))

    def update_playback_state(self, *, is_playing: bool, is_paused: bool) -> None:
        """Update play/pause state indicators."""
        info = self.query_one("#pb-track-info", _TrackInfo)
        info.is_playing = is_playing
        info.is_paused = is_paused

    def update_position(self, position: float, duration: float | None = None) -> None:
        """Update the progress bar position."""
        progress = self.query_one("#pb-progress", PlaybackProgress)
        progress.update_position(position, duration)

    def update_volume(self, volume: int) -> None:
        """Update the volume display."""
        vol = self.query_one("#pb-volume", _VolumeDisplay)
        vol.volume = volume

    def update_repeat(self, mode: RepeatMode) -> None:
        """Update the repeat mode display."""
        rep = self.query_one("#pb-repeat", _RepeatButton)
        rep.repeat_mode = mode.value

    def update_shuffle(self, enabled: bool) -> None:
        """Update the shuffle state display."""
        shuf = self.query_one("#pb-shuffle", _ShuffleButton)
        shuf.shuffle_on = enabled



# ── Interactive footer bar ────────────────────────────────────────


class _FooterButton(Widget):
    """A clickable footer button."""

    DEFAULT_CSS = """
    _FooterButton {
        height: 1;
        width: auto;
        padding: 0 2;
    }
    _FooterButton:hover {
        background: #333333;
    }
    """

    is_active: reactive[bool] = reactive(False)

    def __init__(self, label: str, action: str, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._label = label
        self._action = action

    def render(self) -> Text:
        if self.is_active:
            return Text(self._label, style="bold #ff0000")
        return Text(self._label, style="#999999")

    async def on_click(self, event: Click) -> None:
        event.stop()
        app = self.app
        match self._action:
            case "help":
                await app.navigate_to("help")  # type: ignore[attr-defined]
            case "library":
                await app.navigate_to("library")  # type: ignore[attr-defined]
            case "search":
                await app.navigate_to("search")  # type: ignore[attr-defined]
            case "queue":
                await app.navigate_to("queue")  # type: ignore[attr-defined]
            case "play_pause":
                if hasattr(app, "player") and app.player:
                    await app.player.toggle_pause()
            case "prev":
                await app._play_previous()  # type: ignore[attr-defined]
            case "next":
                await app._play_next()  # type: ignore[attr-defined]
            case "spotify_import":
                from ytm_player.ui.popups.spotify_import import SpotifyImportPopup
                app.push_screen(SpotifyImportPopup())


class FooterBar(Widget):
    """Interactive footer with clickable navigation items."""

    DEFAULT_CSS = """
    FooterBar {
        dock: bottom;
        height: 1;
        background: #0f0f0f;
    }
    FooterBar #footer-inner {
        height: 1;
        width: 1fr;
    }
    """

    # Map of action names to page names for active indicator.
    _PAGE_ACTIONS = {"help", "library", "search", "queue"}

    def compose(self) -> ComposeResult:
        with Horizontal(id="footer-inner"):
            yield _FooterButton("[?] Help", "help", id="footer-help")
            yield _FooterButton("Library", "library", id="footer-library")
            yield _FooterButton("Search", "search", id="footer-search")
            yield _FooterButton("Queue", "queue", id="footer-queue")
            yield _FooterButton("\u23ee Prev", "prev")
            yield _FooterButton("\u23ef Play/Pause", "play_pause")
            yield _FooterButton("\u23ed Next", "next")
            yield _FooterButton("\U0001f500 Import", "spotify_import")

    def set_active_page(self, page_name: str) -> None:
        """Highlight the footer button corresponding to the active page."""
        for action in self._PAGE_ACTIONS:
            try:
                btn = self.query_one(f"#footer-{action}", _FooterButton)
                btn.is_active = (action == page_name)
            except Exception:
                pass
