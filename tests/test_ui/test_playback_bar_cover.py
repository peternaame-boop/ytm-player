"""The playback bar shows a real cover when the terminal draws images, half-blocks otherwise."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from ytm_player.ui import playback_bar
from ytm_player.ui.playback_bar import PlaybackBar
from ytm_player.ui.widgets import cover
from ytm_player.ui.widgets.album_art import AlbumArt
from ytm_player.ui.widgets.cover import Cover

TRACK = {"title": "BLUE SKY", "artists": [{"name": "Kid Cudi"}], "thumbnail_url": "https://img/1"}


class _Host(App):
    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        for name in ("playback-bar-bg", "progress-filled", "progress-empty", "active-tab",
                     "inactive-tab", "selected-item"):  # fmt: skip
            variables.setdefault(name, "#222222")
        return variables

    def compose(self) -> ComposeResult:
        yield PlaybackBar(id="playback-bar")


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cover, "fetch_thumbnail", lambda url: None)


async def test_real_images_put_a_cover_in_the_bar_and_track_changes_update_it(monkeypatch):
    monkeypatch.setattr(playback_bar, "has_real_images", lambda: True)
    app = _Host()
    async with app.run_test(size=(120, 8)) as pilot:
        bar = app.query_one(PlaybackBar)
        art = app.query_one("#pb-art")
        assert isinstance(art, Cover)
        bar.update_track(TRACK)
        await pilot.pause()
        assert art.url == "https://img/1"
        bar.update_track(None)
        await pilot.pause()
        assert art.url == ""


async def test_no_real_images_keeps_the_half_block_art(monkeypatch):
    monkeypatch.setattr(playback_bar, "has_real_images", lambda: False)
    app = _Host()
    async with app.run_test(size=(120, 8)):
        art = app.query_one("#pb-art")
        assert isinstance(art, AlbumArt)
        app.query_one(PlaybackBar).update_track(TRACK)
        assert art.thumbnail_url == "https://img/1"


@pytest.mark.parametrize(
    ("button", "method"),
    [
        ("#pb-prev", "_play_previous"),
        ("#pb-play", "_toggle_play_pause"),
        ("#pb-next", "_play_next"),
    ],
)
async def test_the_bar_has_the_web_transport_buttons(button: str, method: str):
    from unittest.mock import AsyncMock

    app = _Host()
    for name in ("_play_previous", "_toggle_play_pause", "_play_next"):
        setattr(app, name, AsyncMock())
    async with app.run_test(size=(120, 8)) as pilot:
        assert app.query_one(button).outer_size.height == 3
        await pilot.click(button)
        await pilot.pause()
        getattr(app, method).assert_awaited_once()


async def test_the_play_button_shows_pause_while_a_track_plays():
    app = _Host()
    async with app.run_test(size=(120, 8)) as pilot:
        bar = app.query_one(PlaybackBar)
        bar.update_playback_state(is_playing=True, is_paused=False)
        await pilot.pause()
        assert str(app.query_one("#pb-play").render()) == "⏸"
        bar.update_playback_state(is_playing=True, is_paused=True)
        await pilot.pause()
        assert str(app.query_one("#pb-play").render()) == "▶"


async def test_the_thumbs_rate_the_playing_track():
    from unittest.mock import AsyncMock

    app = _Host()
    app._rate_current = AsyncMock()
    async with app.run_test(size=(120, 8)) as pilot:
        await pilot.click("#pb-heart")
        for _ in range(3):
            await pilot.pause()
        app._rate_current.assert_awaited_once_with("LIKE")
        await pilot.click("#pb-dislike")
        for _ in range(3):
            await pilot.pause()
        app._rate_current.assert_awaited_with("DISLIKE")


async def test_the_more_button_opens_the_playing_tracks_actions():
    from types import SimpleNamespace

    class _ActionsHost(_Host):
        def __init__(self) -> None:
            super().__init__()
            self.player = SimpleNamespace(current_track={"video_id": "abc", "title": "Song"})
            self.queue = SimpleNamespace(current_track=None)
            self.asked: list[str] = []

        def on_playback_bar_track_right_clicked(self, message: PlaybackBar.TrackRightClicked):
            self.asked.append(message.track["video_id"])

    app = _ActionsHost()
    async with app.run_test(size=(120, 8)) as pilot:
        await pilot.click("#pb-more")
        await pilot.pause()
        assert app.asked == ["abc"]
