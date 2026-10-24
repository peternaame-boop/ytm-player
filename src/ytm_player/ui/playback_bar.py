"""Always-visible playback status bar and interactive footer."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from rich.cells import cell_len
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.events import Click, MouseScrollDown, MouseScrollUp
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from ytm_player.services.queue import RepeatMode
from ytm_player.ui.icons import icon, volume_icon
from ytm_player.ui.theme import get_theme
from ytm_player.ui.widgets.album_art import AlbumArt
from ytm_player.ui.widgets.cover import Cover, has_real_images, track_thumbnail
from ytm_player.ui.widgets.progress_bar import SeekLine
from ytm_player.utils.formatting import extract_artist, format_duration, truncate

if TYPE_CHECKING:
    from ytm_player.app._base import YTMHostBase

logger = logging.getLogger(__name__)

# Playback state symbols.
# Fewest free columns on the artist line worth showing the selection in.
_MIN_SELECTION_ROOM = 12

# Icons come from ui/icons.py. Repeat and shuffle are monochrome: state shows
# in colour, grey off and theme green on.


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
    # The focused item in a list, shown on the artist line when it fits.
    selection: reactive[str] = reactive("")

    def render(self) -> Text:
        theme = get_theme()
        result = Text()

        # State icon
        if self.is_playing and not self.is_paused:
            result.append(f" {icon('play')} ", style=f"bold {theme.primary}")
        elif self.is_paused:
            result.append(f" {icon('pause')} ", style=theme.muted_text)
        else:
            result.append(f" {icon('stop')} ", style=theme.muted_text)

        if self.title:
            # Two lines, as on the web: the title, then artist and album.
            width = max(10, self.size.width - 4)
            title_w = width
            artist_w = min(len(self.artist), width // 2) if self.album else width
            album_w = width - artist_w - 3

            # FSI...PDI isolates each fragment so RTL titles don't pull
            # adjacent widgets (volume, repeat, shuffle) into the RTL BiDi
            # context.  Apply isolate AFTER truncate (PDI must not be cut).
            from ytm_player.utils.bidi import isolate_bidi, reorder_rtl_line

            result.append(
                isolate_bidi(truncate(reorder_rtl_line(self.title), title_w)),
                style=f"bold {theme.foreground}",
            )
            result.append("\n   ")
            if self.artist:
                result.append(
                    isolate_bidi(truncate(reorder_rtl_line(self.artist), artist_w)),
                    style=theme.secondary,
                )
            if self.album:
                result.append(" \u2022 ", style=theme.muted_text)
                result.append(
                    isolate_bidi(truncate(reorder_rtl_line(self.album), max(0, album_w))),
                    style=theme.muted_text,
                )
        else:
            result.append("No track playing", style=theme.muted_text)

        self._append_selection(result, theme)
        return result

    def _append_selection(self, result: Text, theme: Any) -> None:
        """Add the focused item to the second line, dimmed, if it fits and is not the playing track."""
        selection = self.selection.strip()
        if not selection or (self.title and selection.startswith(self.title)):
            return
        used = (
            result.cell_len
            if "\n" not in result.plain
            else cell_len(result.plain.split("\n", 1)[1])
        )
        room = self.size.width - used - 5
        if room < _MIN_SELECTION_ROOM:
            return
        if "\n" not in result.plain:
            result.append("\n   ")
        result.append("  \u203a  ", style=theme.muted_text)
        result.append(truncate(selection, room), style=f"dim {theme.foreground}")


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
        return Text(f"{volume_icon(self.volume)} {self.volume:>3}%", style=get_theme().muted_text)

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
        min-width: 3;
        content-align: center middle;
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
            return Text(icon("repeat"), style=f"bold {theme.primary}")
        elif self.repeat_mode == RepeatMode.ONE:
            return Text(icon("repeat_one"), style=f"bold {theme.primary}")
        return Text(icon("repeat_off"), style=theme.muted_text)

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
        min-width: 3;
        content-align: center middle;
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
            return Text(icon("shuffle"), style=f"bold {theme.primary}")
        return Text(icon("shuffle_off"), style=theme.muted_text)

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


class _RateButton(Widget):
    """Thumbs up or down for the playing track, as on the web app.

    Thumbs up adds the track to Liked songs. Thumbs down dislikes it and skips
    to the next track. A second click clears the rating.
    """

    DEFAULT_CSS = """
    _RateButton {
        height: 1;
        width: 3;
        margin: 0 1;
        content-align: center middle;
    }
    _RateButton:hover {
        background: $accent 30%;
    }
    """

    # likeStatus value: "LIKE", "DISLIKE", "INDIFFERENT", or "" (unknown).
    like_status: reactive[str] = reactive("")

    def __init__(self, rating: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._rating = rating
        self._icon = "thumb_up" if rating == "LIKE" else "thumb_down"

    def render(self) -> Text:
        theme = get_theme()
        if self.like_status == self._rating:
            return Text(f" {icon(self._icon)} ", style=theme.primary)
        return Text(f" {icon(self._icon + '_off')} ", style=theme.muted_text)

    def on_click(self, event: Click) -> None:
        event.stop()
        app = cast("YTMHostBase", self.app)
        try:
            self.run_worker(app._rate_current(self._rating), group="rate", exclusive=True)
        except Exception:
            logger.debug("Failed to rate from the player bar", exc_info=True)


class _BarIconButton(Widget):
    """A one-icon button in the player bar."""

    DEFAULT_CSS = """
    _BarIconButton {
        height: 1;
        width: 3;
        content-align: center middle;
        color: $text-muted;
    }
    _BarIconButton:hover {
        background: $accent 30%;
        color: $text;
    }
    """

    glyph: reactive[str] = reactive("")

    def __init__(self, glyph: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.glyph = glyph

    def render(self) -> Text:
        return Text(f" {self.glyph} ")


# ── Main playback bar ─────────────────────────────────────────────


class _TransportButton(Static):
    """Previous, play/pause or next, at the left end of the bar as on the web."""

    DEFAULT_CSS = """
    _TransportButton {
        width: 5;
        height: 3;
        content-align: center middle;
    }
    _TransportButton:hover {
        background: $boost;
    }
    _TransportButton.-play {
        background: $foreground;
        color: $background;
        text-style: bold;
    }
    _TransportButton.-play:hover {
        background: $primary;
    }
    """

    def __init__(self, label: str, action: str, **kwargs: Any) -> None:
        super().__init__(label, **kwargs)
        self._action = action

    async def on_click(self, event: Click) -> None:
        event.stop()
        app = cast("YTMHostBase", self.app)
        if self._action == "play_pause":
            await app._toggle_play_pause()
        elif self._action == "prev":
            await app._play_previous()
        elif self._action == "next":
            await app._play_next()


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
        height: 7;
        background: $playback-bar-bg;
    }
    /* Under the two-row seek line: three groups, as the web player bar lays
       them out. Transport and time hug the left and volume and modes hug the
       right, each sized to its content. The track takes the middle, 4 cells
       from each side. Every item inside a group has the same 2-cell gap. */
    /* One blank row above and below the 3 control rows, so the controls sit
       exactly midway between the seek line and the bar's bottom edge. */
    PlaybackBar #pb-outer {
        height: 1fr;
        width: 1fr;
        padding: 1 2;
        align-vertical: middle;
    }
    PlaybackBar #pb-left {
        width: auto;
        height: 3;
        align: left middle;
    }
    PlaybackBar #pb-transport {
        width: 15;
        height: 3;
        margin-right: 2;
    }
    PlaybackBar #pb-right > * {
        margin-left: 2;
    }
    PlaybackBar #pb-dislike {
        margin: 0 0 0 2;
    }
    PlaybackBar #pb-heart, PlaybackBar #pb-more {
        margin: 0 0 0 1;
    }
    PlaybackBar #pb-time {
        width: auto;
        height: 3;
        content-align: left middle;
        color: $text-muted;
    }
    PlaybackBar #pb-center {
        width: 1fr;
        height: 3;
        margin: 0 4;
        align: center middle;
    }
    /* Cover and title read as one block: no gap, title on the middle row. */
    PlaybackBar #pb-art {
        width: 10;
        height: 3;
        margin-right: 1;
    }
    PlaybackBar Cover#pb-art {
        width: 6;
    }
    PlaybackBar #pb-track-info {
        width: 1fr;
        height: 2;
        margin-top: 1;
    }
    /* Like, volume, repeat and shuffle sit on the same middle row as the
       play button, the time and the title. */
    PlaybackBar #pb-heart, PlaybackBar #pb-dislike, PlaybackBar #pb-more,
    PlaybackBar #pb-right > * {
        height: 3;
        content-align: center middle;
    }
    PlaybackBar #pb-right {
        width: auto;
        height: 3;
        align: right middle;
    }
    """

    def compose(self) -> ComposeResult:
        from ytm_player.config.settings import get_settings

        settings = get_settings()
        # As on the web: the seek line runs along the top edge, then one row of
        # controls in three groups (transport and time, track, volume and modes).
        yield SeekLine(id="pb-progress")
        with Horizontal(id="pb-outer"):
            with Horizontal(id="pb-left"):
                with Horizontal(id="pb-transport"):
                    yield _TransportButton(icon("prev"), "prev", id="pb-prev")
                    yield _TransportButton(
                        icon("play"), "play_pause", id="pb-play", classes="-play"
                    )
                    yield _TransportButton(icon("next"), "next", id="pb-next")
                yield Static("0:00 / 0:00", id="pb-time")
            with Horizontal(id="pb-center"):
                art: Widget = Cover(id="pb-art") if has_real_images() else AlbumArt(id="pb-art")
                if not settings.ui.album_art:
                    art.display = False
                yield art
                yield _TrackInfo(id="pb-track-info")
                yield _RateButton("DISLIKE", id="pb-dislike")
                yield _RateButton("LIKE", id="pb-heart")
                yield _BarIconButton(icon("more"), id="pb-more")
            with Horizontal(id="pb-right"):
                yield _VolumeDisplay(id="pb-volume")
                yield _RepeatButton(id="pb-repeat")
                yield _ShuffleButton(id="pb-shuffle")
                yield _BarIconButton(icon("expand"), id="pb-expand")

    def on_click(self, event: Click) -> None:
        """A left click on the cover or title opens the now-playing view. A right-click opens track actions."""
        if event.button == 1:
            target = event.widget
            ids = {w.id for w in (target.ancestors_with_self if target else [])}
            if "pb-more" in ids:
                # The web app's ⋮ menu: the same actions as a right-click.
                event.stop()
                self._post_track_actions()
                return
            if ids & {"pb-art", "pb-track-info", "pb-expand"}:
                event.stop()
                self.app.run_worker(self._toggle_now_playing(), group="now-playing", exclusive=True)
            return
        if event.button != 3:
            return
        self._post_track_actions()

    def _post_track_actions(self) -> None:
        """Ask for the track actions popup of the playing track."""
        app = cast("YTMHostBase", self.app)
        track = None
        if app.player is not None and app.player.current_track:
            track = app.player.current_track
        elif app.queue.current_track:
            track = app.queue.current_track
        if track:
            self.post_message(self.TrackRightClicked(track))

    async def _toggle_now_playing(self) -> None:
        app = cast("YTMHostBase", self.app)
        if getattr(app, "_current_page", None) == "now_playing":
            await app.navigate_to("back")
        else:
            await app.navigate_to("now_playing")

    # ── Public update methods ────────────────────────────────────────

    def set_selection(self, text: str) -> None:
        """Show the focused list item on the track info's second line, when it fits."""
        self.query_one("#pb-track-info", _TrackInfo).selection = text or ""

    def update_track(self, track: dict | None) -> None:
        """Update displayed track information."""
        info = self.query_one("#pb-track-info", _TrackInfo)
        art = cast("AlbumArt", self.query_one("#pb-art"))

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
        art.set_track(track_thumbnail(track))

    def update_playback_state(self, *, is_playing: bool, is_paused: bool) -> None:
        """Update play/pause state indicators."""
        info = self.query_one("#pb-track-info", _TrackInfo)
        info.is_playing = is_playing
        info.is_paused = is_paused
        self.query_one("#pb-play", _TransportButton).update(
            icon("pause") if is_playing and not is_paused else icon("play")
        )

    def update_position(self, position: float, duration: float | None = None) -> None:
        """Update the progress bar position."""
        progress = self.query_one("#pb-progress", SeekLine)
        progress.update_position(position, duration)
        total = progress.duration
        self.query_one("#pb-time", Static).update(
            f"{format_duration(int(position))} / {format_duration(int(total))}"
        )

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
        """Show the track's likeStatus on the thumbs.

        Accepts 'LIKE', 'DISLIKE', 'INDIFFERENT', None, or an unknown
        string. Each thumb is lit only for its own rating.
        """
        for button in self.query(_RateButton):
            button.like_status = (status or "").upper()

    def set_now_playing_open(self, is_open: bool) -> None:
        """Point the expand button down while the now-playing view is open, up otherwise."""
        try:
            self.query_one("#pb-expand", _BarIconButton).glyph = icon(
                "collapse" if is_open else "expand"
            )
        except Exception:
            logger.debug("Expand button not ready", exc_info=True)


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
                | "home"
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
        "home",
        "library",
        "search",
        "browse",
        "queue",
        "recently_played",
        "help",
    }

    def compose(self) -> ComposeResult:
        with Horizontal(id="footer-inner"):
            # Playback controls live in the player bar above.
            # Page navigation.
            yield _FooterButton("Home", "home", id="footer-home")
            yield _FooterButton("Library", "library", id="footer-library")
            yield _FooterButton("Search", "search", id="footer-search")
            yield _FooterButton("Browse", "browse", id="footer-browse")
            yield _FooterButton("Queue", "queue", id="footer-queue")
            yield _FooterButton("Recent", "recently_played", id="footer-recently_played")
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
