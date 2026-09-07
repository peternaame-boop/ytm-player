"""Always-visible playback status bar and interactive footer."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Click, MouseScrollDown, MouseScrollUp
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget

from ytm_player.services.queue import RepeatMode
from ytm_player.ui.theme import get_theme
from ytm_player.ui.widgets.album_art import AlbumArt
from ytm_player.ui.widgets.progress_bar import PlaybackProgress
from ytm_player.utils.formatting import extract_artist, truncate

if TYPE_CHECKING:
    from ytm_player.app._base import YTMHostBase

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
        theme = get_theme()
        result = Text()

        # State icon
        if self.is_playing and not self.is_paused:
            result.append(f" {_ICON_PLAYING} ", style=f"bold {theme.primary}")
        elif self.is_paused:
            result.append(f" {_ICON_PAUSED} ", style=f"bold {theme.warning}")
        else:
            result.append(f" {_ICON_STOPPED} ", style=theme.muted_text)

        if self.title:
            max_w = max(10, self.size.width - 30)
            title_w = min(len(self.title), max_w // 2)
            artist_w = min(len(self.artist), max_w // 3)
            album_w = max_w - title_w - artist_w - 8

            # FSI...PDI isolates each fragment so RTL titles don't pull
            # adjacent widgets (volume, repeat, shuffle) into the RTL BiDi
            # context.  Apply isolate AFTER truncate (PDI must not be cut).
            from ytm_player.utils.bidi import isolate_bidi, reorder_rtl_line

            result.append(
                isolate_bidi(truncate(reorder_rtl_line(self.title), title_w)),
                style=f"bold {theme.foreground}",
            )
            if self.artist:
                result.append(" \u2014 ", style=theme.muted_text)
                result.append(
                    isolate_bidi(truncate(reorder_rtl_line(self.artist), artist_w)),
                    style=theme.secondary,
                )
            if self.album:
                result.append(" \u2014 ", style=theme.muted_text)
                result.append(
                    isolate_bidi(truncate(reorder_rtl_line(self.album), max(0, album_w))),
                    style=theme.muted_text,
                )
        else:
            result.append("No track playing", style=theme.muted_text)

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
        return Text(f" {_ICON_VOLUME} {self.volume:>3}%", style=get_theme().secondary)

    async def on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        event.stop()
        app = cast("YTMHostBase", self.app)
        if app.player is not None:
            await app.player.change_volume(5)

    async def on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        event.stop()
        app = cast("YTMHostBase", self.app)
        if app.player is not None:
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
        background: $accent 30%;
    }
    """

    repeat_mode: reactive[RepeatMode] = reactive(RepeatMode.OFF)

    def render(self) -> Text:
        theme = get_theme()
        if self.repeat_mode == RepeatMode.ALL:
            return Text(f"{_ICON_REPEAT_ALL} all", style=f"bold {theme.primary}")
        elif self.repeat_mode == RepeatMode.ONE:
            return Text(f"{_ICON_REPEAT_ONE} one", style=f"bold {theme.warning}")
        return Text(f"{_ICON_REPEAT_OFF} off", style=theme.muted_text)

    async def on_click(self, event: Click) -> None:
        event.stop()
        app = cast("YTMHostBase", self.app)
        mode = app.queue.cycle_repeat()
        try:
            bar = app.query_one("#playback-bar", PlaybackBar)
            bar.update_repeat(mode)
            app.notify(f"Repeat: {mode.value}", timeout=2)
        except Exception:
            logger.debug("Failed to update repeat mode display on click", exc_info=True)


class _ShuffleButton(Widget):
    """Clickable shuffle indicator.

    Disabled when the current playback context has Shuffle lock on (set
    from the Library page playlist header). Visually dimmed and ignores
    clicks; toggle the lock off in the playlist header to re-enable.
    """

    DEFAULT_CSS = """
    _ShuffleButton {
        height: 1;
        width: auto;
        min-width: 7;
        padding: 0 1;
    }
    _ShuffleButton:hover {
        background: $accent 30%;
    }
    _ShuffleButton.locked {
        text-style: dim;
    }
    _ShuffleButton.locked:hover {
        background: transparent;
    }
    """

    shuffle_on: reactive[bool] = reactive(False)
    locked: reactive[bool] = reactive(False)

    def render(self) -> Text:
        theme = get_theme()
        if self.shuffle_on:
            return Text(f"{_ICON_SHUFFLE_ON} on ", style=f"bold {theme.primary}")
        return Text(f"{_ICON_SHUFFLE_OFF} off", style=theme.muted_text)

    def watch_locked(self, locked: bool) -> None:
        """Apply / remove the .locked class so CSS can dim the widget."""
        if locked:
            self.add_class("locked")
        else:
            self.remove_class("locked")

    async def on_click(self, event: Click) -> None:
        event.stop()
        if self.locked:
            # Shuffle is locked on for this playlist — point the user at the
            # toggle in the playlist header instead of silently no-oping.
            try:
                self.app.notify(
                    "Shuffle is locked for this playlist — toggle Shuffle lock in the playlist header.",
                    severity="warning",
                    timeout=4,
                )
            except Exception:
                pass
            return
        app = cast("YTMHostBase", self.app)
        app.queue.toggle_shuffle()
        app._refresh_queue_page()
        enabled = app.queue.shuffle_enabled
        try:
            bar = app.query_one("#playback-bar", PlaybackBar)
            bar.update_shuffle(enabled)
            state = "on" if enabled else "off"
            app.notify(f"Shuffle: {state}", timeout=2)
        except Exception:
            logger.debug("Failed to update shuffle state display on click", exc_info=True)


# Heart icon for the like indicator. Same character, styled differently
# based on like state (filled accent when liked, muted when not).
_ICON_HEART = "\u2764"  # Heavy Black Heart


class _HeartButton(Widget):
    """Like-state indicator and click-toggle for the currently-playing track."""

    DEFAULT_CSS = """
    _HeartButton {
        height: 1;
        width: 3;
        margin: 0 1;
        content-align: center middle;
    }
    _HeartButton:hover {
        background: $accent 30%;
    }
    """

    # likeStatus value: "LIKE", "DISLIKE", "INDIFFERENT", or "" (unknown).
    like_status: reactive[str] = reactive("")

    def render(self) -> Text:
        theme = get_theme()
        if self.like_status == "LIKE":
            return Text(f" {_ICON_HEART} ", style=f"bold {theme.accent}")
        return Text(f" {_ICON_HEART} ", style=theme.muted_text)

    def on_click(self, event: Click) -> None:
        """Click toggles like via the app's _toggle_like_current."""
        event.stop()
        app = cast("YTMHostBase", self.app)
        try:
            self.run_worker(app._toggle_like_current(), exclusive=True)
        except Exception:
            logger.debug("Failed to toggle like from heart click", exc_info=True)


# ── Main playback bar ─────────────────────────────────────────────


class PlaybackBar(Widget):
    """Persistent playback bar showing track info, progress, and controls.

    Layout (2 lines + optional album art):
        Line 1: [art] > Song Title -- Artist Name -- Album       vol  repeat  shuffle
        Line 2: [art]  1:23 [===========>---------] 4:56
    """

    class TrackRightClicked(Message):
        """Emitted when the playback bar area is right-clicked."""

        def __init__(self, track: dict) -> None:
            super().__init__()
            self.track = track

    DEFAULT_CSS = """
    PlaybackBar {
        dock: bottom;
        height: 4;
        background: $playback-bar-bg;
        border-top: solid $border;
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
        from ytm_player.config.settings import get_settings

        settings = get_settings()
        with Horizontal(id="pb-outer"):
            art = AlbumArt(id="pb-art")
            if not settings.ui.album_art:
                art.display = False
            yield art
            with Vertical(id="pb-content"):
                with Horizontal(id="pb-top-row"):
                    yield _TrackInfo(id="pb-track-info")
                    yield _HeartButton(id="pb-heart")
                    yield _VolumeDisplay(id="pb-volume")
                    yield _RepeatButton(id="pb-repeat")
                    yield _ShuffleButton(id="pb-shuffle")
                with Horizontal(id="pb-bottom-row"):
                    yield PlaybackProgress(bar_style=settings.ui.progress_style, id="pb-progress")

    def on_click(self, event: Click) -> None:
        """Right-click on the playback bar opens track actions."""
        if event.button != 3:
            return
        app = cast("YTMHostBase", self.app)
        track = None
        if app.player is not None and app.player.current_track:
            track = app.player.current_track
        elif app.queue.current_track:
            track = app.queue.current_track
        if track:
            self.post_message(self.TrackRightClicked(track))

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
        rep.repeat_mode = mode

    def update_shuffle(self, enabled: bool) -> None:
        """Update the shuffle state display."""
        shuf = self.query_one("#pb-shuffle", _ShuffleButton)
        shuf.shuffle_on = enabled

    def refresh_shuffle_lock_state(self) -> None:
        """Re-read shuffle_prefs for the current queue context and dim/un-dim
        the shuffle button accordingly. Called from the Library page when the
        user toggles Shuffle lock or enters a different playlist."""
        try:
            app = cast("YTMHostBase", self.app)
            ctx = app.queue.current_context_id
            locked = bool(app.shuffle_prefs.get(ctx)) if ctx else False
            shuf = self.query_one("#pb-shuffle", _ShuffleButton)
            shuf.locked = locked
        except Exception:
            logger.debug("Failed to refresh shuffle lock state", exc_info=True)

    def update_like_status(self, status: str | None) -> None:
        """Update the heart icon based on the track's likeStatus.

        Accepts 'LIKE', 'DISLIKE', 'INDIFFERENT', None, or an unknown
        string. Anything other than 'LIKE' shows the muted (not-liked)
        state.
        """
        try:
            heart = self.query_one("#pb-heart", _HeartButton)
            heart.like_status = (status or "").upper()
        except Exception:
            logger.debug("Failed to update heart like_status display", exc_info=True)


# ── Interactive footer bar ────────────────────────────────────────


class _FooterButton(Widget):
    """A clickable footer button."""

    DEFAULT_CSS = """
    _FooterButton {
        height: 1;
        width: auto;
        padding: 0 1;
    }
    _FooterButton:hover {
        background: $accent 30%;
    }
    """

    is_active: reactive[bool] = reactive(False)
    is_dimmed: reactive[bool] = reactive(False)

    def __init__(self, label: str, action: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._label = label
        self._action = action

    def render(self) -> Text:
        theme = get_theme()
        if self.is_active:
            return Text(self._label, style=f"bold {theme.primary}")
        if self.is_dimmed:
            return Text(self._label, style="dim")
        return Text(self._label, style=theme.muted_text)

    async def on_click(self, event: Click) -> None:
        event.stop()
        app = cast("YTMHostBase", self.app)
        match self._action:
            case (
                "help"
                | "library"
                | "search"
                | "queue"
                | "browse"
                | "liked_songs"
                | "recently_played"
            ):
                await app.navigate_to(self._action)
            case "play_pause":
                await app._toggle_play_pause()
            case "prev":
                await app._play_previous()
            case "next":
                await app._play_next()
            case "spotify_import":
                from ytm_player.ui.popups.spotify_import import SpotifyImportPopup

                app.push_screen(SpotifyImportPopup())


class FooterBar(Widget):
    """Interactive footer with clickable navigation items."""

    DEFAULT_CSS = """
    FooterBar {
        dock: bottom;
        height: 1;
        background: $background;
    }
    FooterBar #footer-inner {
        height: 1;
        width: 1fr;
    }
    FooterBar #footer-help {
        dock: right;
    }
    """

    # Page actions that get an active-page highlight.
    _PAGE_ACTIONS = {
        "library",
        "search",
        "browse",
        "queue",
        "help",
    }

    def compose(self) -> ComposeResult:
        with Horizontal(id="footer-inner"):
            # Playback controls (icon-only).
            yield _FooterButton("\u23ee", "prev")
            yield _FooterButton("\u23ef", "play_pause")
            yield _FooterButton("\u23ed", "next")
            # Page navigation.
            yield _FooterButton("Library", "library", id="footer-library")
            yield _FooterButton("Search", "search", id="footer-search")
            yield _FooterButton("Browse", "browse", id="footer-browse")
            yield _FooterButton("Queue", "queue", id="footer-queue")
            # Spotify import.
            yield _FooterButton("Import", "spotify_import")
            # Help pushed to far right.
            yield _FooterButton("?", "help", id="footer-help")

    def set_active_page(self, page_name: str) -> None:
        """Highlight the footer button corresponding to the active page."""
        for action in self._PAGE_ACTIONS:
            try:
                btn = self.query_one(f"#footer-{action}", _FooterButton)
                btn.is_active = action == page_name
            except Exception:
                logger.debug(
                    "Failed to update footer button for action '%s'", action, exc_info=True
                )
