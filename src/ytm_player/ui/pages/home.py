"""Home page laid out like music.youtube.com: shelves of cover cards.

Each shelf from ``get_home()`` becomes a titled row of cards with real cover
art (see ``ui/widgets/cover.py``). A card plays a song or opens an album,
playlist or artist page through ``navigate_browse_item``, the routing the
Browse page already uses.

Mood chips (Energize, Relax ...) sit above the shelves, as on the web, and
open that mood's playlists. A shelf of songs, such as Quick picks, is a grid
of song rows four high instead of cover cards.

Keys: j/k move between shelves, h/l or the arrow keys move along a shelf,
Enter opens the focused card. Each shelf also has ◀ ▶ buttons, and Shift+wheel
or a tilt wheel scrolls it sideways, as on the web.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, cast

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, HorizontalScroll, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Label, Static

from ytm_player.config.keymap import Action
from ytm_player.config.settings import get_settings
from ytm_player.ui.icons import icon
from ytm_player.ui.pages.browse import _clean_shelf_title, navigate_browse_item
from ytm_player.ui.widgets.cover import Cover, best_thumbnail

if TYPE_CHECKING:
    from ytm_player.app._base import YTMHostBase

logger = logging.getLogger(__name__)

CARD_WIDTH = 24
SCROLL_STEP = CARD_WIDTH * 3
FEED_TTL_SECONDS = 600
FIRST_SHELVES = 2
FIRST_PAINT_GAP = 0.05  # seconds, about three frames

# The last Home feed, so coming back to Home is instant, as on the web.
# (monotonic time, shelves). The r key always fetches a fresh feed.
_feed_cache: tuple[float, list[dict[str, Any]]] | None = None
_LOAD_FAILED = "Couldn't load your Home feed. Press r to try again."
# The mood chips change rarely, so one fetch serves the whole run.
_chips_cache: list[dict[str, Any]] | None = None


def relayout_soon(widget: Widget) -> None:
    """Run one full screen layout pass just after a burst of cards mounts.

    Cards mounted after their page sometimes stay unpainted in kitty until the
    user scrolls. A scroll fixes it by laying out the screen again, so do the
    same once the cards exist.
    """
    widget.set_timer(0.15, lambda: widget.screen.refresh(layout=True))


def card_subtitle(item: dict[str, Any]) -> str:
    """Second line under a card, the way the web shows it."""
    artists = ", ".join(a.get("name", "") for a in item.get("artists") or [] if a.get("name"))
    kind = (item.get("resultType") or item.get("type") or "").capitalize()
    if item.get("count") is not None:  # a library playlist
        author = item.get("author")
        owner = author[0].get("name", "") if isinstance(author, list) and author else ""
        count = str(item["count"]).replace(",", "")
        unit = "song" if count == "1" else "songs"
        return " • ".join(p for p in ("Playlist", owner, f"{count} {unit}") if p)
    if artists and kind in ("Album", "Single", "Ep"):
        return f"{kind} • {artists}"
    if artists:
        return artists
    for key in ("description", "subscribers", "year"):
        if item.get(key):
            return str(item[key])
    return kind


class ShelfRow(HorizontalScroll):
    """A sideways-scrolling row of cards.

    Textual already scrolls a horizontal container on Shift+wheel and on a
    tilt wheel, so this class only adds paging for the ◀ ▶ buttons.
    """

    DEFAULT_CSS = """
    ShelfRow { height: 17; scrollbar-size-horizontal: 1; }
    """

    def page(self, direction: int) -> None:
        self.scroll_to(x=max(0, self.scroll_x + direction * SCROLL_STEP), animate=False)


class HomeCard(Vertical, can_focus=True):
    """One cover card. Click or Enter opens it."""

    DEFAULT_CSS = f"""
    HomeCard {{ width: {CARD_WIDTH}; height: 16; margin-right: 2; }}
    HomeCard > Cover {{ width: {CARD_WIDTH - 2}; height: 11; }}
    HomeCard > .card-title {{ width: {CARD_WIDTH - 2}; height: 1; margin-top: 1; text-style: bold;
        text-wrap: nowrap; text-overflow: ellipsis; }}
    HomeCard > .card-sub {{ width: {CARD_WIDTH - 2}; height: 1; color: $text-muted;
        text-wrap: nowrap; text-overflow: ellipsis; }}
    HomeCard:hover > .card-title {{ text-style: bold underline; }}
    HomeCard:focus > .card-title {{ color: $accent; text-style: bold underline; }}
    """

    BINDINGS = [Binding("enter", "open", "Open", show=False)]

    def __init__(self, item: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.item = item

    def compose(self) -> ComposeResult:
        yield Cover(best_thumbnail(self.item))
        yield Label(self.item.get("title", ""), classes="card-title")
        yield Label(card_subtitle(self.item), classes="card-sub")

    def on_enter(self, event: events.Enter) -> None:
        self._start_dwell()

    def on_leave(self, event: events.Leave) -> None:
        self._stop_dwell()

    def on_focus(self, event: events.Focus) -> None:
        self._start_dwell()

    def on_blur(self, event: events.Blur) -> None:
        self._stop_dwell()

    def _start_dwell(self) -> None:
        """A song card the pointer or focus rests on gets its stream resolved before the click."""
        from ytm_player.app._playback import SPECULATE_DWELL

        video_id = self.item.get("videoId") or ""
        speculate = getattr(self.app, "_speculate", None)
        if video_id and speculate and getattr(self, "_dwell", None) is None:

            def fire() -> None:
                self._dwell = None
                speculate(video_id)

            self._dwell = self.set_timer(SPECULATE_DWELL, fire)

    def _stop_dwell(self) -> None:
        dwell = getattr(self, "_dwell", None)
        if dwell is not None:
            dwell.stop()
        self._dwell = None

    def on_click(self, event: events.Click) -> None:
        if event.button != 1:
            return  # a right-click is for the page's actions menu, where it has one
        event.stop()
        self.focus()
        self.action_open()

    def action_open(self) -> None:
        # Opening an album or playlist removes this page, and with it this card.
        # Awaiting that from the card's own handler deadlocks: the removal waits
        # for the handler to finish. So the work runs as a worker on the app.
        self.app.run_worker(_open_item(self.app, self.item), group="home-open", exclusive=True)


async def _open_item(app: Any, item: dict[str, Any]) -> None:
    try:
        await navigate_browse_item(cast("YTMHostBase", app), item)
    except Exception:
        logger.exception("Failed to open home item %s", item.get("title"))
        app.notify("Couldn't open that item.", severity="warning")


# Rows in one column of a song shelf, as the web app's Quick picks shows them.
SONG_ROWS = 4
SONG_CARD_WIDTH = 44


def is_song_shelf(items: list[dict[str, Any]]) -> bool:
    """True for a shelf of songs: every item plays a track that belongs to an album."""
    return len(items) >= SONG_ROWS and all(
        item.get("videoId") and item.get("album") and not item.get("browseId") for item in items
    )


class SongCard(HomeCard):
    """A song row in a song shelf: a small cover, title over artist. Click or Enter plays it."""

    DEFAULT_CSS = f"""
    SongCard {{ layout: horizontal; width: {SONG_CARD_WIDTH}; height: 2; margin: 0 2 1 0; }}
    SongCard > Cover {{ width: 4; height: 2; margin-right: 2; }}
    SongCard:hover {{ background: $boost; }}
    SongCard:focus {{ background: $boost; }}
    """

    def compose(self) -> ComposeResult:
        from ytm_player.ui.pages.now_playing import UpNextText

        yield Cover(best_thumbnail(self.item, min_width=60))
        artists = ", ".join(
            a.get("name", "") for a in self.item.get("artists") or [] if a.get("name")
        )
        yield UpNextText(self.item.get("title", ""), artists, "", False)


class MoodChips(HorizontalScroll):
    """The web app's mood chips above the Home shelves."""

    DEFAULT_CSS = """
    MoodChips { height: 2; margin-bottom: 1; scrollbar-size-horizontal: 0; }
    MoodChips > .home-chip { width: auto; height: 1; padding: 0 2; margin-right: 1;
        background: $boost; }
    MoodChips > .home-chip:hover { background: $primary 30%; }
    """

    def __init__(self, moods: list[dict[str, Any]], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.moods = moods

    def compose(self) -> ComposeResult:
        for mood in self.moods:
            chip = Static(mood.get("title", ""), classes="home-chip")
            chip.mood = mood  # type: ignore[attr-defined]
            yield chip

    def on_click(self, event: events.Click) -> None:
        mood = getattr(event.widget, "mood", None)
        if mood is None or event.button != 1:
            return
        event.stop()
        target = {"title": mood.get("title", ""), "params": mood["params"]}
        self.app.run_worker(
            cast("YTMHostBase", self.app).navigate_to("mood", **target), group="home-open"
        )


class Shelf(Vertical):
    """A titled row of cards with ◀ ▶ scroll buttons."""

    DEFAULT_CSS = f"""
    Shelf {{ height: auto; margin-bottom: 1; }}
    Shelf > .shelf-head {{ height: 1; margin-bottom: 1; }}
    Shelf .shelf-title {{ width: 1fr; text-style: bold; }}
    Shelf .shelf-arrow {{ width: 5; content-align: center middle; color: $text-muted; }}
    Shelf .shelf-arrow:hover {{ color: $text; background: $boost; }}
    Shelf .song-column {{ width: {SONG_CARD_WIDTH + 2}; height: auto; }}
    /* A song shelf is four rows of three lines, not a row of tall cards. */
    Shelf ShelfRow.-songs {{ height: {SONG_ROWS * 3 + 1}; }}
    """

    def __init__(self, title: str, items: list[dict[str, Any]], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.title = title
        self.items = items

    def compose(self) -> ComposeResult:
        with Horizontal(classes="shelf-head"):
            yield Label(self.title, classes="shelf-title")
            yield Static(icon("chevron_left"), classes="shelf-arrow shelf-prev")
            yield Static(icon("chevron_right"), classes="shelf-arrow shelf-next")
        songs = is_song_shelf(self.items)
        with ShelfRow(classes="shelf-row -songs" if songs else "shelf-row"):
            if songs:
                for start in range(0, len(self.items), SONG_ROWS):
                    with Vertical(classes="song-column"):
                        for item in self.items[start : start + SONG_ROWS]:
                            yield SongCard(item)
            else:
                for item in self.items:
                    yield HomeCard(item)

    def on_click(self, event: events.Click) -> None:
        widget = event.widget
        if widget is None:
            return
        if widget.has_class("shelf-prev"):
            event.stop()
            self.query_one(ShelfRow).page(-1)
        elif widget.has_class("shelf-next"):
            event.stop()
            self.query_one(ShelfRow).page(1)

    def cards(self) -> list[HomeCard]:
        return list(self.query(HomeCard))


class HomePage(Widget):
    """The Home feed as shelves of cover cards."""

    DEFAULT_CSS = """
    HomePage { width: 1fr; height: 1fr; }
    HomePage > #home-scroll { padding: 1 3; }
    HomePage .home-status { color: $text-muted; padding: 2 3; }
    """

    BINDINGS = [Binding("r", "reload", "Reload", show=False)]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._shelves: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="home-scroll"):
            yield Static("Loading your Home…", id="home-status", classes="home-status")

    def on_mount(self) -> None:
        self.run_worker(self._load(), exclusive=True, group="home-load")

    async def action_reload(self) -> None:
        self.run_worker(self._load(fresh=True), exclusive=True, group="home-load")

    async def _load(self, fresh: bool = False) -> None:
        global _feed_cache
        host = cast("YTMHostBase", self.app)
        limit = max(get_settings().ui.home_shelves, 6)
        cached = _feed_cache
        if not fresh and cached and time.monotonic() - cached[0] < FEED_TTL_SECONDS:
            shelves: list[dict[str, Any]] | None = cached[1]
        else:
            try:
                shelves = await host.ytmusic.get_home(limit=limit) if host.ytmusic else None
            except Exception:
                logger.exception("Failed to load the Home feed")
                shelves = None
            if shelves:
                _feed_cache = (time.monotonic(), shelves)
        scroll = self.query_one("#home-scroll", VerticalScroll)
        await scroll.remove_children()
        if not shelves:
            await scroll.mount(Static(_LOAD_FAILED, id="home-status", classes="home-status"))
            return
        self._shelves = [s for s in shelves if s.get("contents")]
        built = [
            Shelf(_clean_shelf_title(s.get("title", "")), [c for c in s["contents"] if c])
            for s in self._shelves
        ]
        # The first shelves fill the screen. Paint them before mounting the
        # rest, so a page switch shows Home at once instead of after every card.
        await scroll.mount_all(built[:FIRST_SHELVES])
        warm = getattr(self.app, "warm_streams", None)
        if warm is not None:
            top = self._shelves[:FIRST_SHELVES]
            warm([c.get("videoId", "") for s in top for c in s["contents"] if c])
        if built[FIRST_SHELVES:]:
            await asyncio.sleep(FIRST_PAINT_GAP)
            await scroll.mount_all(built[FIRST_SHELVES:])
        relayout_soon(self)
        self.run_worker(self._load_chips(), group="home-chips", exclusive=True)

    async def _load_chips(self) -> None:
        """Mount the mood chips above the shelves. Home shows without them if they fail."""
        global _chips_cache
        if _chips_cache is None:
            ytmusic = cast("YTMHostBase", self.app).ytmusic
            try:
                moods = await ytmusic.get_mood_chips() if ytmusic else []
            except Exception:
                logger.debug("Mood chips unavailable", exc_info=True)
                return
            if not isinstance(moods, list) or not moods:
                return
            _chips_cache = moods
        scroll = self.query_one("#home-scroll", VerticalScroll)
        if not self.query(MoodChips):
            await scroll.mount(MoodChips(_chips_cache, id="home-chips"), before=0)

    # ── keyboard ──────────────────────────────────────────────────

    def _focus_position(self) -> tuple[int, int] | None:
        focused = self.app.focused
        for si, shelf in enumerate(self.query(Shelf)):
            for ci, card in enumerate(shelf.cards()):
                if card is focused:
                    return si, ci
        return None

    def _focus_card(self, shelf_index: int, card_index: int) -> None:
        shelves = list(self.query(Shelf))
        if not shelves:
            return
        shelf = shelves[max(0, min(shelf_index, len(shelves) - 1))]
        cards = shelf.cards()
        if not cards:
            return
        card = cards[max(0, min(card_index, len(cards) - 1))]
        card.focus()
        card.scroll_visible(animate=False)

    def on_key(self, event: events.Key) -> None:
        moves = {"left": (0, -1), "right": (0, 1), "h": (0, -1), "l": (0, 1)}
        if event.key in moves and self._focus_position() is not None:
            event.stop()
            si, ci = cast("tuple[int, int]", self._focus_position())
            self._focus_card(si, ci + moves[event.key][1])

    async def handle_action(self, action: Action, count: int = 1) -> None:
        position = self._focus_position()
        si, ci = position if position is not None else (-1, 0)
        if action == Action.MOVE_DOWN:
            self._focus_card(si + count, 0 if position is None else ci)
        elif action == Action.MOVE_UP:
            self._focus_card(max(0, si - count), ci)
        elif action == Action.GO_TOP:
            self._focus_card(0, 0)
        elif action == Action.GO_BOTTOM:
            self._focus_card(len(self.query(Shelf)) - 1, 0)
        elif action == Action.SELECT and position is not None:
            focused = self.app.focused
            if isinstance(focused, HomeCard):
                focused.action_open()

    def get_nav_state(self) -> dict[str, Any]:
        return {}
