"""The web app's now-playing view: large cover beside Up next, opened from the player bar."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from textual.app import App, ComposeResult

from ytm_player.services.queue import QueueManager
from ytm_player.ui import playback_bar
from ytm_player.ui.pages.now_playing import NowPlayingPage, UpNextRow, UpNextText
from ytm_player.ui.playback_bar import PlaybackBar
from ytm_player.ui.widgets import cover
from ytm_player.ui.widgets.cover import Cover


def _track(video_id: str, title: str) -> dict:
    return {"video_id": video_id, "title": title, "artist": "Kid Cudi", "artists": [{"name": "Kid Cudi"}],
            "album": "", "album_id": None, "duration": 120, "is_video": False,
            "thumbnail_url": "https://lh3.googleusercontent.com/x=w120-h120-l90-rj"}  # fmt: skip


class _FakePlayer:
    current_track: dict | None = None

    def on(self, *_args: Any) -> None: ...

    def off(self, *_args: Any) -> None: ...


class _Host(App):
    def __init__(self, page: str) -> None:
        super().__init__()
        self.page = page
        self.queue = QueueManager()
        self.queue.add_multiple(
            [_track("a", "BLUE SKY"), _track("b", "Opiate"), _track("c", "Ladders")]
        )
        self.player = _FakePlayer()
        self.player.current_track = self.queue.tracks[0]
        self.navigate_to = AsyncMock()
        self._current_page = "home"

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        for name in ("selected-item", "playback-bar-bg", "progress-filled", "progress-empty",
                     "active-tab", "inactive-tab", "lyrics-played", "lyrics-current",
                     "lyrics-upcoming"):  # fmt: skip
            variables.setdefault(name, "#333333")
        return variables

    def compose(self) -> ComposeResult:
        if self.page == "now_playing":
            yield NowPlayingPage(id="page-now_playing")
        else:
            yield PlaybackBar(id="playback-bar")


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cover, "fetch_thumbnail", lambda url: None)


async def test_the_view_shows_the_playing_track_large_beside_up_next():
    app = _Host("now_playing")
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause(0.2)
        assert str(app.query_one("#np-title").render()) == "BLUE SKY"
        assert str(app.query_one("#np-artist").render()) == "Kid Cudi"
        big = app.query_one("#np-cover", Cover)
        assert big.url.endswith("=w544-h544-l90-rj") and big.size == (60, 30)
        rows = list(app.query(UpNextRow))
        assert [r.query_one(UpNextText).title for r in rows] == ["BLUE SKY", "Opiate", "Ladders"]
        assert rows[0].has_class("-current") and not rows[1].has_class("-current")


async def test_the_view_follows_a_track_change():
    app = _Host("now_playing")
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause(0.2)
        app.player.current_track = app.queue.tracks[1]
        await pilot.pause(0.8)
        assert str(app.query_one("#np-title").render()) == "Opiate"


@pytest.mark.parametrize(
    ("current", "expected"), [("home", "now_playing"), ("now_playing", "back")]
)
async def test_clicking_the_bar_title_opens_the_view_and_clicking_again_goes_back(
    monkeypatch, current: str, expected: str
):
    monkeypatch.setattr(playback_bar, "has_real_images", lambda: True)
    app = _Host("bar")
    app._current_page = current
    async with app.run_test(size=(120, 8)) as pilot:
        app.query_one(PlaybackBar).update_track(app.queue.tracks[0])
        await pilot.pause()
        await pilot.click("#pb-track-info")
        for _ in range(3):
            await pilot.pause()
        app.navigate_to.assert_awaited_once_with(expected)


async def test_clicking_the_bar_cover_opens_the_view(monkeypatch):
    monkeypatch.setattr(playback_bar, "has_real_images", lambda: True)
    app = _Host("bar")
    async with app.run_test(size=(120, 8)) as pilot:
        await pilot.click("#pb-art")
        for _ in range(3):
            await pilot.pause()
        app.navigate_to.assert_awaited_once_with("now_playing")


async def test_the_cover_shrinks_so_title_and_artist_stay_on_screen():
    app = _Host("now_playing")
    async with app.run_test(size=(160, 26)) as pilot:
        await pilot.pause(0.3)
        big = app.query_one("#np-cover", Cover)
        artist = app.query_one("#np-artist")
        assert big.size.height < 30 and big.size.width == 2 * big.size.height
        assert artist.region.bottom <= app.query_one(NowPlayingPage).region.bottom


async def test_an_up_next_row_shows_cover_title_artist_and_duration():
    app = _Host("now_playing")
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause(0.2)
        row = app.query(UpNextRow).first()
        assert row.query_one("Cover").size == (4, 2)
        title_line, artist_line = str(row.query_one(UpNextText).render()).split("\n")
        assert title_line.startswith("BLUE SKY") and title_line.endswith("   2:00")
        assert artist_line.startswith("Kid Cudi")
        assert row.outer_size.height == 3, "two lines and a divider"


async def test_clicking_an_up_next_row_plays_it():
    from unittest.mock import AsyncMock

    app = _Host("now_playing")
    app.play_track = AsyncMock()
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause(0.2)
        await pilot.click(list(app.query(UpNextRow))[2])
        for _ in range(3):
            await pilot.pause()
        app.play_track.assert_awaited_once()
        assert app.play_track.await_args.args[0]["title"] == "Ladders"


async def test_up_next_has_no_scroll_bar_so_its_dividers_float():
    from ytm_player.app._app import YTMPlayerApp
    from ytm_player.ui.pages.now_playing import UpNextList

    class _AppCssHost(_Host):
        CSS = YTMPlayerApp.CSS  # its * rule is what overrode the list before

    app = _AppCssHost("now_playing")
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause(0.2)
        assert app.query_one(UpNextList).styles.scrollbar_size_vertical == 0


async def test_up_next_is_centred_at_most_90_percent_of_its_half():
    from ytm_player.ui.pages.now_playing import UpNextList

    app = _Host("now_playing")
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause(0.2)
        half = app.query_one("#np-right").region
        listing = app.query_one(UpNextList).region
        assert listing.width <= round(half.width * 0.9)
        left_gap, right_gap = listing.x - half.x, half.right - listing.right
        assert abs(left_gap - right_gap) <= 1 and left_gap > 0


async def test_the_pane_has_the_web_apps_three_tabs_and_starts_on_up_next():
    app = _Host("now_playing")
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause(0.2)
        tabs = [str(t.render()) for t in app.query(".np-tab")]
        assert tabs == ["Up next", "Lyrics", "Related"]
        assert app.query_one("#np-tab-up-next").has_class("-active")
        assert app.query_one("#np-switch").current == "np-queue"


async def test_clicking_lyrics_shows_the_centred_lyrics_in_the_pane():
    from ytm_player.ui.sidebars.lyrics_sidebar import LyricsSidebar

    app = _Host("now_playing")
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause(0.2)
        await pilot.click("#np-tab-lyrics")
        await pilot.pause()
        page = app.query_one(NowPlayingPage)
        assert page.active_tab == "lyrics"
        assert app.query_one("#np-switch").current == "np-lyrics"
        lyrics = app.query_one("#np-lyrics", LyricsSidebar)
        assert lyrics.has_class("in-pane") and lyrics.display
        assert not app.query_one("#np-head").display  # "Playing from" and Save
        page.toggle_lyrics_tab()
        assert page.active_tab == "up-next"


async def test_related_lists_songs_and_a_click_plays_one():
    from ytm_player.ui.pages.now_playing import RelatedRow

    app = _Host("now_playing")
    related = [{"videoId": "R1", "title": "Related One", "artists": [{"name": "Someone"}],
                "thumbnails": []}]  # fmt: skip
    app.ytmusic = type("Y", (), {"get_related_songs": AsyncMock(return_value=related)})()
    app.play_track = AsyncMock()
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause(0.2)
        await pilot.click("#np-tab-related")
        await pilot.pause(0.3)
        rows = list(app.query(RelatedRow))
        assert [r.track["title"] for r in rows] == ["Related One"]
        app.ytmusic.get_related_songs.assert_awaited_once()
        await pilot.click(rows[0])
        await pilot.pause(0.2)
        app.play_track.assert_awaited()
        assert app.play_track.await_args.args[0]["video_id"] == "R1"
