"""Home page: every click, key and scroll the page adds, driven through Pilot."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from textual import events
from textual.app import App, ComposeResult

from ytm_player.config.keymap import Action
from ytm_player.ui.pages import home
from ytm_player.ui.pages.home import HomeCard, HomePage, Shelf, ShelfRow
from ytm_player.ui.widgets import cover

SONG = {
    "title": "Instant Crush",
    "videoId": "vid123",
    "resultType": "song",
    "artists": [{"name": "Daft Punk"}],
    "thumbnails": [{"url": "u1", "width": 226}],
}
ALBUM = {
    "title": "Random Access Memories",
    "browseId": "MPREb_ram",
    "type": "Album",
    "artists": [{"name": "Daft Punk"}],
    "thumbnails": [{"url": "u2", "width": 226}],
}
PLAYLIST = {"title": "Replay Mix", "playlistId": "RDTMAK5uy", "description": "Mix",
            "thumbnails": [{"url": "u3", "width": 226}]}  # fmt: skip


def _feed(cards_per_shelf: int = 10) -> list[dict]:
    filler = [dict(SONG, title=f"Song {i}", videoId=f"v{i}") for i in range(cards_per_shelf - 3)]
    return [
        {"title": "Listen again", "contents": [SONG, ALBUM, PLAYLIST, *filler]},
        {
            "title": "Quick picks",
            "contents": [dict(SONG, title="Second shelf song", videoId="v2x")],
        },
        {"title": "Empty shelf", "contents": []},
    ]


class _Host(App):
    def __init__(self, feed: list[dict] | None) -> None:
        super().__init__()
        self.ytmusic = MagicMock()
        self.ytmusic.get_home = AsyncMock(return_value=feed)
        self.navigate_to = AsyncMock()
        self._replace_queue_and_play = AsyncMock()

    def compose(self) -> ComposeResult:
        yield HomePage(id="page")


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        home, "get_settings", lambda: SimpleNamespace(ui=SimpleNamespace(home_shelves=3))
    )
    monkeypatch.setattr(cover, "fetch_thumbnail", lambda url: None)
    monkeypatch.setattr(home, "_feed_cache", None)


async def _ready(pilot) -> None:
    for _ in range(20):
        await pilot.pause()
        if pilot.app.query(HomeCard):
            return


async def test_shelves_and_cards_render_and_empty_shelves_are_skipped():
    app = _Host(_feed())
    async with app.run_test(size=(160, 50)) as pilot:
        await _ready(pilot)
        shelves = list(app.query(Shelf))
        assert [s.title for s in shelves] == ["Listen again", "Quick picks"]
        assert len(shelves[0].cards()) == 10
        card = shelves[0].cards()[1]
        assert str(card.query_one(".card-title").render()) == "Random Access Memories"
        assert str(card.query_one(".card-sub").render()) == "Album • Daft Punk"


async def test_click_song_card_replaces_queue_and_plays():
    app = _Host(_feed())
    async with app.run_test(size=(160, 50)) as pilot:
        await _ready(pilot)
        await pilot.click(HomeCard)  # first card is the song
        await pilot.pause()
        app._replace_queue_and_play.assert_awaited_once()
        (tracks,), _ = app._replace_queue_and_play.await_args
        assert tracks[0]["video_id"] == "vid123"
        app.navigate_to.assert_not_awaited()


async def test_click_album_card_opens_album_page():
    app = _Host(_feed())
    async with app.run_test(size=(160, 50)) as pilot:
        await _ready(pilot)
        album_card = app.query(Shelf).first().cards()[1]
        await pilot.click(album_card)
        await pilot.pause()
        app.navigate_to.assert_awaited_with("context", context_type="album", context_id="MPREb_ram")


async def test_click_playlist_card_opens_playlist_page():
    app = _Host(_feed())
    async with app.run_test(size=(160, 50)) as pilot:
        await _ready(pilot)
        await pilot.click(app.query(Shelf).first().cards()[2])
        await pilot.pause()
        app.navigate_to.assert_awaited_with(
            "context", context_type="playlist", context_id="RDTMAK5uy"
        )


async def test_opening_a_card_that_replaces_the_page_does_not_hang():
    """Regression: the click handler awaited navigation that removed its own card."""
    from textual.containers import Container
    from textual.widgets import Static

    class _NavHost(_Host):
        def compose(self) -> ComposeResult:
            with Container(id="main-content"):
                yield HomePage(id="page")

        async def _navigate(self, *_args, **_kwargs) -> None:
            main = self.query_one("#main-content")
            await main.remove_children()  # what the real navigate_to does first
            await main.mount(Static("album page", id="album"))

    app = _NavHost(_feed())
    app.navigate_to = AsyncMock(side_effect=app._navigate)
    async with app.run_test(size=(160, 50)) as pilot:
        await _ready(pilot)
        await pilot.click(app.query(Shelf).first().cards()[1])
        for _ in range(20):
            await pilot.pause(0.05)
            if app.query("#album"):
                break
        assert app.query("#album"), "navigation never completed"
        assert not app.query(HomePage)


async def test_liked_music_card_opens_the_liked_songs_page():
    """Regression: the playlist page shows "No data found" for the virtual LM playlist."""
    liked = {"title": "Liked Music", "playlistId": "LM", "description": "Auto playlist",
             "thumbnails": [{"url": "u4", "width": 226}]}  # fmt: skip
    app = _Host([{"title": "Listen again", "contents": [liked]}])
    async with app.run_test(size=(160, 50)) as pilot:
        await _ready(pilot)
        await pilot.click(HomeCard)
        for _ in range(5):
            await pilot.pause()
        app.navigate_to.assert_awaited_once_with("liked_songs")


async def test_enter_on_focused_card_opens_it():
    app = _Host(_feed())
    async with app.run_test(size=(160, 50)) as pilot:
        await _ready(pilot)
        app.query(Shelf).first().cards()[1].focus()
        await pilot.press("enter")
        await pilot.pause()
        app.navigate_to.assert_awaited_with("context", context_type="album", context_id="MPREb_ram")


async def test_arrow_buttons_scroll_the_shelf():
    app = _Host(_feed(cards_per_shelf=14))
    async with app.run_test(size=(120, 50)) as pilot:
        await _ready(pilot)
        shelf = app.query(Shelf).first()
        row = shelf.query_one(ShelfRow)
        assert row.scroll_x == 0
        await pilot.click(shelf.query_one(".shelf-next"))
        await pilot.pause()
        assert row.scroll_x > 0
        moved = row.scroll_x
        await pilot.click(shelf.query_one(".shelf-prev"))
        await pilot.pause()
        assert row.scroll_x < moved


async def test_shift_wheel_and_tilt_wheel_scroll_sideways():
    app = _Host(_feed(cards_per_shelf=14))
    async with app.run_test(size=(120, 50)) as pilot:
        await _ready(pilot)
        row = app.query(ShelfRow).first()
        kw = dict(x=5, y=5, delta_x=0, delta_y=0, button=0, ctrl=False, meta=False)

        async def send(event_cls, shift: bool) -> float:
            row.post_message(event_cls(row, shift=shift, **kw))
            await pilot.pause(0.3)  # tilt-wheel scrolling animates
            return row.scroll_x

        after_shift = await send(events.MouseScrollDown, shift=True)
        assert after_shift > 0
        after_tilt = await send(events.MouseScrollRight, shift=False)
        assert after_tilt > after_shift
        assert await send(events.MouseScrollLeft, shift=False) < after_tilt
        assert await send(events.MouseScrollUp, shift=True) < after_shift


async def test_keyboard_moves_between_and_along_shelves():
    app = _Host(_feed())
    async with app.run_test(size=(160, 50)) as pilot:
        await _ready(pilot)
        page = app.query_one(HomePage)
        first_shelf = app.query(Shelf).first().cards()

        async def act(action: Action) -> None:
            await page.handle_action(action)
            await pilot.pause()

        await act(Action.MOVE_DOWN)
        assert app.focused is first_shelf[0]
        await pilot.press("right")
        assert app.focused is first_shelf[1]
        await pilot.press("l")
        assert app.focused is first_shelf[2]
        await pilot.press("left")
        assert app.focused is first_shelf[1]
        await act(Action.MOVE_DOWN)
        assert app.focused is app.query(Shelf).last().cards()[0]
        await act(Action.MOVE_UP)
        assert app.focused in first_shelf
        await act(Action.SELECT)
        await pilot.pause()
        assert app.navigate_to.await_count + app._replace_queue_and_play.await_count == 1


async def test_failed_load_shows_retry_and_r_reloads():
    app = _Host(None)
    async with app.run_test(size=(120, 40)) as pilot:
        for _ in range(10):
            await pilot.pause()
        status = app.query_one("#home-status")
        assert "Press r to try again" in str(status.render())
        app.ytmusic.get_home = AsyncMock(return_value=_feed())
        app.query_one(HomePage).focus()
        await pilot.press("r")
        await _ready(pilot)
        assert app.query(HomeCard)


async def test_coming_back_to_home_reuses_the_feed_and_r_refreshes_it():
    app = _Host(_feed())
    async with app.run_test(size=(160, 50)) as pilot:
        await _ready(pilot)
        assert app.ytmusic.get_home.await_count == 1
        page = app.query_one(HomePage)
        await page.remove()
        await app.mount(HomePage(id="page2"))
        await _ready(pilot)
        assert app.ytmusic.get_home.await_count == 1, "Home refetched instead of using the cache"
        app.query_one("#page2").query(HomeCard).first().focus()
        await pilot.pause()
        await pilot.press("r")
        for _ in range(10):
            await pilot.pause()
        assert app.ytmusic.get_home.await_count == 2


async def test_footer_home_button_navigates_home():
    from ytm_player.ui.playback_bar import FooterBar

    class _FooterHost(App):
        def __init__(self) -> None:
            super().__init__()
            self.navigate_to = AsyncMock()

        def compose(self) -> ComposeResult:
            yield FooterBar()

    app = _FooterHost()
    async with app.run_test(size=(160, 10)) as pilot:
        await pilot.click("#footer-home")
        await pilot.pause()
        app.navigate_to.assert_awaited_with("home")


def test_g_h_is_bound_to_home():
    from ytm_player.config.keymap import DEFAULT_BINDINGS

    assert Action("home") is Action.HOME
    assert DEFAULT_BINDINGS["home"] == ["g h"]


async def test_the_first_shelves_paint_before_the_rest_mount(monkeypatch):
    """A page switch showed Home only after all its cards mounted (0.6 s). The top shelves go first."""
    monkeypatch.setattr(home, "FIRST_PAINT_GAP", 0.5)
    feed = [{"title": f"Shelf {i}", "contents": [dict(SONG, videoId=f"s{i}")]} for i in range(5)]
    app = _Host(feed)
    async with app.run_test(size=(140, 40)) as pilot:
        await _ready(pilot)
        assert len(app.query(home.Shelf)) == 2
        await pilot.pause(0.7)
        assert len(app.query(home.Shelf)) == 5


async def test_a_song_card_the_pointer_rests_on_is_resolved_ahead(monkeypatch):
    from ytm_player.app import _playback

    monkeypatch.setattr(_playback, "SPECULATE_DWELL", 0.2)
    app = _Host(_feed())
    app._speculate = MagicMock()
    async with app.run_test(size=(140, 40)) as pilot:
        await _ready(pilot)
        await pilot.hover(HomeCard)
        await pilot.pause(0.4)
        app._speculate.assert_called_once_with("vid123")
        await pilot.hover("#page", offset=(130, 38))
        await pilot.hover(HomeCard)
        await pilot.pause(0.4)
        assert app._speculate.call_count == 2


async def test_passing_over_a_card_does_not_resolve_it(monkeypatch):
    from ytm_player.app import _playback

    monkeypatch.setattr(_playback, "SPECULATE_DWELL", 0.3)
    app = _Host(_feed())
    app._speculate = MagicMock()
    async with app.run_test(size=(140, 40)) as pilot:
        await _ready(pilot)
        await pilot.hover(HomeCard)
        await pilot.pause(0.05)
        await pilot.hover("#page", offset=(130, 38))
        await pilot.pause(0.5)
        app._speculate.assert_not_called()


async def test_home_warms_the_songs_on_its_first_shelves():
    app = _Host(_feed(cards_per_shelf=4))
    app.warm_streams = MagicMock()
    async with app.run_test(size=(140, 40)) as pilot:
        await _ready(pilot)
        ids = app.warm_streams.call_args.args[0]
        assert ids[0] == "vid123" and "v2x" in ids


def _song(i: int) -> dict:
    return {"title": f"Pick {i}", "videoId": f"q{i}", "album": {"name": "A", "id": "MPRE1"},
            "artists": [{"name": f"Artist {i}"}], "thumbnails": [{"url": f"t{i}", "width": 60}]}  # fmt: skip


async def test_a_shelf_of_songs_is_a_grid_four_rows_high(monkeypatch):
    from ytm_player.ui.pages.home import SONG_ROWS, SongCard

    feed = [{"title": "Quick picks", "contents": [_song(i) for i in range(9)]}]
    app = _Host(feed)
    async with app.run_test(size=(160, 50)) as pilot:
        await _ready(pilot)
        shelf = app.query_one(Shelf)
        columns = list(shelf.query(".song-column"))
        assert [len(c.query(SongCard)) for c in columns] == [SONG_ROWS, SONG_ROWS, 1]
        assert len(shelf.cards()) == 9, "song rows are cards for keys and clicks"
        assert shelf.query_one(ShelfRow).has_class("-songs")


async def test_clicking_a_song_row_opens_the_song(monkeypatch):
    from ytm_player.ui.pages.home import SongCard

    opened: list[str] = []

    async def fake_open(app, item):
        opened.append(item["videoId"])

    monkeypatch.setattr(home, "_open_item", fake_open)
    app = _Host([{"title": "Quick picks", "contents": [_song(i) for i in range(4)]}])
    async with app.run_test(size=(160, 50)) as pilot:
        await _ready(pilot)
        await pilot.click(list(app.query(SongCard))[2])
        for _ in range(3):
            await pilot.pause()
        assert opened == ["q2"]


async def test_mixed_shelves_keep_cover_cards():
    from ytm_player.ui.pages.home import SongCard, is_song_shelf

    assert not is_song_shelf([SONG, ALBUM, PLAYLIST, SONG])
    assert not is_song_shelf([_song(1), _song(2)]), "too short for a grid"
    app = _Host(_feed())
    async with app.run_test(size=(160, 50)) as pilot:
        await _ready(pilot)
        assert not app.query(SongCard)


async def test_mood_chips_sit_above_the_shelves_and_open_a_mood(monkeypatch):
    monkeypatch.setattr(home, "_chips_cache", None)
    app = _Host(_feed())
    app.ytmusic.get_mood_chips = AsyncMock(
        return_value=[{"title": "Energize", "params": "p1"}, {"title": "Relax", "params": "p2"}]
    )
    async with app.run_test(size=(160, 50)) as pilot:
        await _ready(pilot)
        for _ in range(10):
            await pilot.pause()
            if app.query(".home-chip"):
                break
        chips = list(app.query(".home-chip"))
        assert [str(c.render()) for c in chips] == ["Energize", "Relax"]
        scroll = app.query_one("#home-scroll")
        assert scroll.children[0].id == "home-chips"
        await pilot.click(chips[1])
        for _ in range(3):
            await pilot.pause()
        app.navigate_to.assert_awaited_with("mood", title="Relax", params="p2")


async def test_home_shows_without_chips_when_they_fail(monkeypatch):
    monkeypatch.setattr(home, "_chips_cache", None)
    app = _Host(_feed())
    app.ytmusic.get_mood_chips = AsyncMock(side_effect=RuntimeError("offline"))
    async with app.run_test(size=(160, 50)) as pilot:
        await _ready(pilot)
        for _ in range(5):
            await pilot.pause()
        assert not app.query(".home-chip")
        assert app.query(Shelf)
