"""Explore laid out like the web app, and the mood pages its chips open. Clicked through Pilot."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from textual.app import App, ComposeResult

from ytm_player.services.ytmusic import _two_row_cards
from ytm_player.ui.pages import explore
from ytm_player.ui.pages.explore import ExplorePage, MoodPage
from ytm_player.ui.pages.home import HomeCard
from ytm_player.ui.widgets import cover

ART = [{"url": "u", "width": 226}]
FEED = {
    "new_releases": [{"title": "INSANO", "type": "Album", "browseId": "MPRE1",
                      "artists": [{"name": "Kid Cudi"}], "thumbnails": ART}],
    "trending": {"items": [{"title": "Hit", "videoId": "v1", "artists": [{"name": "A"}],
                            "thumbnails": ART}]},
    "new_videos": [],
    "moods_and_genres": [{"title": "Chill", "params": "p-chill"}, {"title": "Rock", "params": "p-rock"}],
}  # fmt: skip
MOOD = [{"resultType": "playlist", "title": "Chill Vibes", "browseId": "VLPL1",
         "description": "Playlist", "thumbnails": ART}]  # fmt: skip


class _Host(App[None]):
    def __init__(self, page: str = "explore", feed: dict | None = FEED) -> None:
        super().__init__()
        self.page = page
        self.ytmusic = MagicMock()
        self.ytmusic.get_explore = AsyncMock(return_value=feed)
        self.ytmusic.get_mood_playlists = AsyncMock(return_value=MOOD)
        self.navigate_to = AsyncMock()
        self._replace_queue_and_play = AsyncMock()

    def compose(self) -> ComposeResult:
        if self.page == "explore":
            yield ExplorePage(id="page-explore")
        else:
            yield MoodPage(id="page-mood", title="Chill", params="p-chill")


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cover, "fetch_thumbnail", lambda url: None)
    monkeypatch.setattr(explore, "_feed_cache", None)


async def _ready(pilot, selector: str) -> None:
    for _ in range(40):
        await pilot.pause()
        if pilot.app.query(selector):
            await pilot.pause(0.1)
            return
    raise AssertionError(f"{selector} never appeared")


async def test_explore_shows_the_web_shelves_and_mood_chips():
    app = _Host()
    async with app.run_test(size=(140, 60)) as pilot:
        await _ready(pilot, HomeCard)
        shelves = [str(s.query_one(".shelf-title").render()) for s in app.query("Shelf")]
        assert shelves == ["New albums & singles", "Trending"]  # the empty shelf is left out
        assert [str(c.render()) for c in app.query(".mood-chip")] == ["Chill", "Rock"]


async def test_a_mood_chip_opens_its_playlists():
    app = _Host()
    async with app.run_test(size=(140, 60)) as pilot:
        await _ready(pilot, ".mood-chip")
        chip = app.query(".mood-chip").first()
        chip.scroll_visible(animate=False)
        await pilot.pause(0.1)
        await pilot.click(chip)
        for _ in range(5):
            await pilot.pause()
        app.navigate_to.assert_awaited_with("mood", title="Chill", params="p-chill")


@pytest.mark.parametrize(("button", "tab"), [("#explore-releases", 2), ("#explore-charts", 1)])
async def test_top_buttons_open_the_browse_tabs(button: str, tab: int):
    app = _Host()
    async with app.run_test(size=(140, 60)) as pilot:
        await _ready(pilot, HomeCard)
        await pilot.click(button)
        for _ in range(5):
            await pilot.pause()
        app.navigate_to.assert_awaited_with("browse", active_tab=tab)


async def test_an_album_card_opens_the_album():
    app = _Host()
    async with app.run_test(size=(140, 60)) as pilot:
        await _ready(pilot, HomeCard)
        await pilot.click(HomeCard)
        for _ in range(5):
            await pilot.pause()
        app.navigate_to.assert_awaited_with("context", context_type="album", context_id="MPRE1")


async def test_a_failed_feed_says_so():
    app = _Host(feed=None)
    async with app.run_test(size=(140, 60)) as pilot:
        await _ready(pilot, ".explore-status")
        for _ in range(10):
            await pilot.pause()
        assert "Couldn't load Explore" in str(app.query_one(".explore-status").render())


async def test_the_mood_page_shows_playlist_cards_that_open():
    app = _Host(page="mood")
    async with app.run_test(size=(140, 40)) as pilot:
        await _ready(pilot, HomeCard)
        assert app.query_one(MoodPage).get_nav_state() == {"title": "Chill", "params": "p-chill"}
        await pilot.click(HomeCard)
        for _ in range(5):
            await pilot.pause()
        app.navigate_to.assert_awaited_with("context", context_type="playlist", context_id="VLPL1")


def test_mood_cards_parse_from_the_raw_page_even_with_a_plain_text_author():
    """Regression: ytmusicapi's get_mood_playlists raised KeyError on an author with no link."""
    raw = {"contents": [{"gridRenderer": {"items": [
        {"musicTwoRowItemRenderer": {
            "title": {"runs": [{"text": "EKDUM"}]},
            "subtitle": {"runs": [{"text": "Joseph"}]},
            "navigationEndpoint": {"browseEndpoint": {"browseId": "VLRD1"}},
            "thumbnailRenderer": {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": ART}}},
        }}]}}]}  # fmt: skip
    assert _two_row_cards(raw) == [
        {
            "resultType": "playlist",
            "title": "EKDUM",
            "description": "Joseph",
            "browseId": "VLRD1",
            "thumbnails": ART,
        }  # fmt: skip
    ]
