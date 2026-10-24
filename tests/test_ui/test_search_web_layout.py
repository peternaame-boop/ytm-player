"""Search laid out like the web app: a top result card, the songs, then shelves of cover cards.

Clicked through Pilot. Before, albums, artists and playlists were text lists with no covers.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input

from ytm_player.ui.pages.home import HomeCard
from ytm_player.ui.pages.search import SearchPage, TopResult
from ytm_player.ui.widgets import cover

ART = [{"url": "https://lh3.googleusercontent.com/a=w120-h120-l90-rj", "width": 120}]
SONG = {"resultType": "song", "title": "BLUE SKY", "videoId": "v1", "duration_seconds": 200,
        "artists": [{"name": "Kid Cudi", "id": "UC1"}], "thumbnails": ART}  # fmt: skip
ALBUM = {"resultType": "album", "title": "INSANO", "type": "Album", "browseId": "MPRE1",
         "playlistId": "OLAK1", "artists": [{"name": "Kid Cudi", "id": "UC1"}], "thumbnails": ART}  # fmt: skip
ARTIST = {"resultType": "artist", "artist": "Kid Cudi", "browseId": "UC1",
          "subscribers": "6.2M", "thumbnails": ART}  # fmt: skip
PLAYLIST = {"resultType": "playlist", "title": "Cudi Essentials", "browseId": "VLPL1",
            "author": "YouTube Music", "itemCount": "50", "thumbnails": ART}  # fmt: skip
TOP_ARTIST = {"category": "Top result", "resultType": "artist", "subscribers": "6.2M",
              "artists": [{"name": "Kid Cudi", "id": "UC1"}], "thumbnails": ART}  # fmt: skip

BY_FILTER = {"songs": [SONG], "albums": [ALBUM], "artists": [ARTIST],
             "community_playlists": [PLAYLIST]}  # fmt: skip


class _Host(App[None]):
    def __init__(self, top: dict) -> None:
        super().__init__()
        self.top = top
        self.ytmusic = MagicMock()
        self.ytmusic.search = AsyncMock(side_effect=self._search)
        self.ytmusic.get_search_suggestions = AsyncMock(return_value=[])
        self.history = MagicMock()
        self.history.log_search = AsyncMock()
        self.history.get_search_history = AsyncMock(return_value=[])
        self.navigate_to = AsyncMock()
        self._replace_queue_and_play = AsyncMock()
        self._play_playlist = AsyncMock()
        self.push_screen = MagicMock()

    async def _search(self, query, filter=None, limit=10):
        return BY_FILTER[filter] if filter else [self.top, SONG]

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        yield SearchPage(id="search")


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cover, "fetch_thumbnail", lambda url: None)


async def _search(pilot, query: str = "kid cudi") -> None:
    pilot.app.query_one("#search-input", Input).value = query
    await pilot.press("enter")
    for _ in range(40):
        await pilot.pause()
        if pilot.app.query(HomeCard):
            await pilot.pause(0.1)
            return
    raise AssertionError("no result cards appeared")


async def test_results_show_a_top_result_card_and_cover_shelves():
    app = _Host(TOP_ARTIST)
    async with app.run_test(size=(160, 50)) as pilot:
        await _search(pilot)
        top = app.query_one(TopResult)
        assert str(top.query_one(".top-title").render()) == "Kid Cudi"
        assert str(top.query_one(".top-sub").render()) == "Artist • 6.2M subscribers"
        shelves = [str(s.query_one(".shelf-title").render()) for s in app.query("Shelf")]
        assert shelves == ["Albums", "Artists", "Community playlists"]
        titles = [str(c.query_one(".card-title").render()) for c in app.query(HomeCard)]
        assert titles == ["INSANO", "Kid Cudi", "Cudi Essentials"]
        assert not app.query_one("#albums-panel").display


async def test_clicking_a_top_artist_opens_the_artist_page():
    app = _Host(TOP_ARTIST)
    async with app.run_test(size=(160, 50)) as pilot:
        await _search(pilot)
        await pilot.click(TopResult)
        for _ in range(5):
            await pilot.pause()
        app.navigate_to.assert_awaited_with("context", context_type="artist", context_id="UC1")


async def test_play_on_a_top_song_plays_it():
    app = _Host(dict(SONG, category="Top result"))
    async with app.run_test(size=(160, 50)) as pilot:
        await _search(pilot, "blue sky")
        await pilot.click("#top-play")
        for _ in range(5):
            await pilot.pause()
        app._replace_queue_and_play.assert_awaited_once()
        assert app._replace_queue_and_play.await_args.args[0][0]["video_id"] == "v1"


async def test_play_on_a_top_album_plays_the_album():
    app = _Host(dict(ALBUM, category="Top result"))
    async with app.run_test(size=(160, 50)) as pilot:
        await _search(pilot, "insano")
        await pilot.click("#top-play")
        for _ in range(5):
            await pilot.pause()
        app._play_playlist.assert_awaited_once_with("OLAK1", "INSANO")


async def test_clicking_an_album_card_opens_the_album():
    app = _Host(TOP_ARTIST)
    async with app.run_test(size=(160, 50)) as pilot:
        await _search(pilot)
        await pilot.click("#shelf-albums HomeCard")
        for _ in range(5):
            await pilot.pause()
        app.navigate_to.assert_awaited_with("context", context_type="album", context_id="MPRE1")


async def test_right_click_on_a_card_opens_its_actions_menu_not_the_item():
    app = _Host(TOP_ARTIST)
    async with app.run_test(size=(160, 50)) as pilot:
        await _search(pilot)
        app.query_one("#shelf-playlists HomeCard").scroll_visible(animate=False)
        await pilot.pause(0.1)
        await pilot.click("#shelf-playlists HomeCard", button=3)
        await pilot.pause()
        app.navigate_to.assert_not_awaited()
        popup = app.push_screen.call_args.args[0]
        assert popup.item_type == "playlist"


async def test_song_thumbnails_ask_for_a_larger_copy():
    assert cover.best_thumbnail(SONG).endswith("=w226-h226-l90-rj")


async def test_filter_chips_show_after_a_search_and_start_on_all():
    app = _Host(TOP_ARTIST)
    async with app.run_test(size=(160, 50)) as pilot:
        await _search(pilot)
        chips = [str(c.render()) for c in app.query(".search-chip")]
        assert chips == ["All", "Songs", "Albums", "Artists", "Community playlists"]
        assert app.query_one("#search-chips").has_class("visible")
        assert app.query_one("#chip-all").has_class("-active")
        page = app.query_one(SearchPage)
        assert not page.has_class("-only-songs") and not page.has_class("-only-cards")


async def test_the_albums_chip_shows_only_albums_and_fetches_more():
    app = _Host(TOP_ARTIST)
    async with app.run_test(size=(160, 50)) as pilot:
        await _search(pilot)
        app.ytmusic.search.reset_mock()
        await pilot.click("#chip-albums")
        for _ in range(10):
            await pilot.pause()
        page = app.query_one(SearchPage)
        assert page.has_class("-only-cards") and page.search_filter == "albums"
        assert (
            not app.query_one("#search-top").display
            or not app.query_one("#search-top").region.height
        )
        grid = app.query_one("#search-filtered")
        assert [str(c.query_one(".card-title").render()) for c in grid.query(HomeCard)] == [
            "INSANO"
        ]
        app.ytmusic.search.assert_awaited_once_with("kid cudi", filter="albums", limit=40)


async def test_the_songs_chip_lists_songs_full_width_then_all_restores_the_page():
    app = _Host(TOP_ARTIST)
    async with app.run_test(size=(160, 50)) as pilot:
        await _search(pilot)
        await pilot.click("#chip-songs")
        for _ in range(10):
            await pilot.pause()
        page = app.query_one(SearchPage)
        assert page.has_class("-only-songs")
        assert not app.query_one("#top-result-box").region.height
        await pilot.click("#chip-all")
        await pilot.pause()
        assert not page.has_class("-only-songs") and not page.has_class("-only-cards")
        assert app.query(TopResult)
