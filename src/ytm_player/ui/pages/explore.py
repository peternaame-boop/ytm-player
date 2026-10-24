"""Explore laid out like music.youtube.com/explore, and the page a mood or genre chip opens.

Explore shows three buttons (New releases, Charts, Moods & genres), shelves of
new albums, trending songs and new music videos, then every mood and genre as
a chip. A chip opens a grid of that mood's playlists.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, cast

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ItemGrid, VerticalScroll
from textual.widget import Widget
from textual.widgets import Label, Static

from ytm_player.config.keymap import Action
from ytm_player.ui.icons import icon
from ytm_player.ui.pages.home import CARD_WIDTH, HomeCard, Shelf, relayout_soon

if TYPE_CHECKING:
    from ytm_player.app._base import YTMHostBase

logger = logging.getLogger(__name__)

FEED_TTL_SECONDS = 600
CHIP_WIDTH = 24

# Browse page tabs the top buttons open. Moods & genres scrolls this page instead.
_RELEASES_TAB = 2
_CHARTS_TAB = 1

# The last Explore feed, so coming back is instant. (monotonic time, feed)
_feed_cache: tuple[float, dict[str, Any]] | None = None


def explore_shelves(feed: dict[str, Any]) -> list[tuple[str, list[dict[str, Any]]]]:
    """The shelves the web Explore page shows, in its order, without empty ones."""

    def items(key: str) -> list[dict[str, Any]]:
        value = feed.get(key) or []
        return list(value.get("items") or []) if isinstance(value, dict) else list(value)

    shelves = [
        ("New albums & singles", items("new_releases")),
        ("Trending", items("trending")),
        ("New music videos", items("new_videos")),
    ]
    return [(title, entries) for title, entries in shelves if entries]


async def _navigate(app: Any, page: str, **kwargs: Any) -> None:
    await cast("YTMHostBase", app).navigate_to(page, **kwargs)


class ExplorePage(Widget):
    """The Explore feed: top buttons, shelves of cover cards, mood and genre chips."""

    DEFAULT_CSS = f"""
    ExplorePage {{ width: 1fr; height: 1fr; }}
    ExplorePage > #explore-scroll {{ padding: 1 3; }}
    ExplorePage .explore-status {{ color: $text-muted; padding: 2 0; }}
    ExplorePage #explore-buttons {{ height: 3; margin-bottom: 2; }}
    ExplorePage .explore-button {{ width: 1fr; height: 3; margin-right: 2; padding: 1 2;
        background: $boost; text-style: bold; }}
    ExplorePage .explore-button:hover {{ background: $primary 30%; }}
    ExplorePage .explore-title {{ text-style: bold; margin-bottom: 1; }}
    ExplorePage #explore-moods {{ height: auto; grid-gutter: 1 2; }}
    ExplorePage .mood-chip {{ width: {CHIP_WIDTH}; height: 3; padding: 1 1; background: $boost;
        border-left: solid $primary; text-wrap: nowrap; text-overflow: ellipsis; }}
    ExplorePage .mood-chip:hover {{ background: $primary 30%; }}
    """

    BINDINGS = [Binding("r", "reload", "Reload", show=False)]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="explore-scroll"):
            with Horizontal(id="explore-buttons"):
                yield Static(
                    f"{icon('new_releases')}  New releases",
                    id="explore-releases",
                    classes="explore-button",
                )
                yield Static(
                    f"{icon('charts')}  Charts", id="explore-charts", classes="explore-button"
                )
                yield Static(
                    f"{icon('moods')}  Moods & genres",
                    id="explore-moods-btn",
                    classes="explore-button",
                )
            yield Label("Loading Explore…", classes="explore-status")

    def on_mount(self) -> None:
        self.run_worker(self._load(), group="explore", exclusive=True)

    def action_reload(self) -> None:
        self.run_worker(self._load(fresh=True), group="explore", exclusive=True)

    async def _load(self, fresh: bool = False) -> None:
        global _feed_cache
        feed: dict[str, Any] | None = None
        if not fresh and _feed_cache and time.monotonic() - _feed_cache[0] < FEED_TTL_SECONDS:
            feed = _feed_cache[1]
        if feed is None:
            ytmusic = cast("YTMHostBase", self.app).ytmusic
            feed = await ytmusic.get_explore() if ytmusic else None
            if feed:
                _feed_cache = (time.monotonic(), feed)
        scroll = self.query_one("#explore-scroll", VerticalScroll)
        await scroll.query(".explore-status, Shelf, .explore-title, #explore-moods").remove()
        if not feed:
            await scroll.mount(
                Label("Couldn't load Explore. Press r to try again.", classes="explore-status")
            )
            return
        widgets: list[Widget] = [Shelf(title, items) for title, items in explore_shelves(feed)]
        moods = [m for m in feed.get("moods_and_genres") or [] if m.get("params")]
        if moods:
            widgets.append(
                Label("Moods & genres", id="explore-moods-title", classes="explore-title")
            )
            chips = [Static(m.get("title", ""), classes="mood-chip") for m in moods]
            for chip, mood in zip(chips, moods):
                chip.mood = mood  # type: ignore[attr-defined]
            widgets.append(ItemGrid(*chips, id="explore-moods", min_column_width=CHIP_WIDTH))
        await scroll.mount(*widgets)
        relayout_soon(self)
        warm = getattr(self.app, "warm_streams", None)
        if warm is not None:
            warm([t.get("videoId", "") for _, items in explore_shelves(feed) for t in items])

    def on_click(self, event: events.Click) -> None:
        widget = event.widget
        if widget is None or event.button != 1:
            return
        mood = getattr(widget, "mood", None)
        if mood is not None:
            event.stop()
            target = ("mood", {"title": mood.get("title", ""), "params": mood["params"]})
        elif widget.id == "explore-releases":
            event.stop()
            target = ("browse", {"active_tab": _RELEASES_TAB})
        elif widget.id == "explore-charts":
            event.stop()
            target = ("browse", {"active_tab": _CHARTS_TAB})
        elif widget.id == "explore-moods-btn":
            event.stop()
            if self.query("#explore-moods-title"):
                self.query_one("#explore-moods-title").scroll_visible(top=True, animate=False)
            return
        else:
            return
        # Navigating removes this page. Awaiting that from its own handler
        # deadlocks, so the navigation runs as a worker on the app.
        page, kwargs = target
        self.app.run_worker(
            _navigate(self.app, page, **kwargs), group="explore-open", exclusive=True
        )

    async def handle_action(self, action: Action, count: int = 1) -> None:
        scroll = self.query_one("#explore-scroll", VerticalScroll)
        match action:
            case Action.MOVE_DOWN:
                scroll.scroll_relative(y=4 * count, animate=False)
            case Action.MOVE_UP:
                scroll.scroll_relative(y=-4 * count, animate=False)
            case Action.GO_TOP:
                scroll.scroll_home(animate=False)
            case Action.GO_BOTTOM:
                scroll.scroll_end(animate=False)

    def get_nav_state(self) -> dict[str, Any]:
        return {}


class MoodPage(Widget):
    """The playlists of one mood or genre, as a grid of cover cards."""

    DEFAULT_CSS = """
    MoodPage { width: 1fr; height: 1fr; }
    MoodPage > #mood-scroll { padding: 1 3; }
    MoodPage .mood-title { text-style: bold; margin-bottom: 1; }
    MoodPage .mood-status { color: $text-muted; }
    MoodPage #mood-grid { height: auto; grid-gutter: 1 2; }
    """

    def __init__(self, *, title: str = "", params: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.mood_title = title
        self.params = params

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="mood-scroll"):
            yield Label(self.mood_title, classes="mood-title")
            yield Label("Loading playlists…", classes="mood-status")
            yield ItemGrid(id="mood-grid", min_column_width=CARD_WIDTH)

    def on_mount(self) -> None:
        self.run_worker(self._load(), group="mood", exclusive=True)

    async def _load(self) -> None:
        ytmusic = cast("YTMHostBase", self.app).ytmusic
        playlists = await ytmusic.get_mood_playlists(self.params) if ytmusic else None
        status = self.query_one(".mood-status", Label)
        if not playlists:
            status.update(
                "Couldn't load these playlists." if playlists is None else "No playlists."
            )
            return
        status.display = False
        await self.query_one("#mood-grid", ItemGrid).mount(*[HomeCard(p) for p in playlists])
        relayout_soon(self)

    async def handle_action(self, action: Action, count: int = 1) -> None:
        scroll = self.query_one("#mood-scroll", VerticalScroll)
        match action:
            case Action.MOVE_DOWN:
                scroll.scroll_relative(y=4 * count, animate=False)
            case Action.MOVE_UP:
                scroll.scroll_relative(y=-4 * count, animate=False)

    def get_nav_state(self) -> dict[str, Any]:
        return {"title": self.mood_title, "params": self.params}
