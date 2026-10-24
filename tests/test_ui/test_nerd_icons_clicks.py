"""With Nerd Font icons on, clicking the player bar's toggles swaps to the right icon."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from textual.app import App, ComposeResult

from ytm_player.services.queue import QueueManager
from ytm_player.ui import icons, playback_bar
from ytm_player.ui.header_bar import HeaderBar
from ytm_player.ui.playback_bar import PlaybackBar
from ytm_player.ui.widgets import cover

NERD = icons._NERD


class _Host(App):
    def __init__(self) -> None:
        super().__init__()
        self.queue = QueueManager()
        self._refresh_queue_page = lambda: None
        self._toggle_like_current = AsyncMock()

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        for name in ("playback-bar-bg", "progress-filled", "progress-empty", "active-tab",
                     "inactive-tab", "selected-item"):  # fmt: skip
            variables.setdefault(name, "#222222")
        return variables

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header-bar")
        yield PlaybackBar(id="playback-bar")


@pytest.fixture(autouse=True)
def _nerd_icons(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(icons, "_nerd_font_installed", lambda: True)
    icons.active_set.cache_clear()
    monkeypatch.setattr(cover, "fetch_thumbnail", lambda url: None)
    monkeypatch.setattr(playback_bar, "has_real_images", lambda: False)


def _text(app: App, selector: str) -> str:
    return str(app.query_one(selector).render())


async def test_clicking_repeat_cycles_off_all_one():
    app = _Host()
    async with app.run_test(size=(140, 12)) as pilot:
        assert _text(app, "#pb-repeat") == NERD["repeat_off"]
        seen = []
        for _ in range(3):
            await pilot.click("#pb-repeat")
            await pilot.pause()
            seen.append(_text(app, "#pb-repeat"))
        assert seen == [NERD["repeat"], NERD["repeat_one"], NERD["repeat_off"]]


async def test_clicking_shuffle_toggles_it():
    app = _Host()
    async with app.run_test(size=(140, 12)) as pilot:
        await pilot.click("#pb-shuffle")
        await pilot.pause()
        assert app.queue.shuffle_enabled
        assert _text(app, "#pb-shuffle") == NERD["shuffle"]
        await pilot.click("#pb-shuffle")
        await pilot.pause()
        assert not app.queue.shuffle_enabled


async def test_thumbs_click_and_like_state():
    app = _Host()
    app._rate_current = AsyncMock()
    async with app.run_test(size=(140, 12)) as pilot:
        assert NERD["thumb_up_off"] in _text(app, "#pb-heart")
        assert NERD["thumb_down_off"] in _text(app, "#pb-dislike")
        await pilot.click("#pb-heart")
        for _ in range(3):
            await pilot.pause()
        app._rate_current.assert_awaited_with("LIKE")
        app.query_one(PlaybackBar).update_like_status("LIKE")
        await pilot.pause()
        assert NERD["thumb_up"] in _text(app, "#pb-heart")
        assert NERD["thumb_down_off"] in _text(app, "#pb-dislike")
        app.query_one(PlaybackBar).update_like_status("DISLIKE")
        await pilot.pause()
        assert NERD["thumb_up_off"] in _text(app, "#pb-heart")
        assert NERD["thumb_down"] in _text(app, "#pb-dislike")


async def test_expand_opens_now_playing_and_flips_when_open():
    app = _Host()
    async with app.run_test(size=(140, 12)) as pilot:
        bar = app.query_one(PlaybackBar)
        app.navigate_to = AsyncMock()
        app._current_page = "home"
        await pilot.click("#pb-expand")
        for _ in range(3):
            await pilot.pause()
        app.navigate_to.assert_awaited_with("now_playing")
        bar.set_now_playing_open(True)
        assert NERD["collapse"] in _text(app, "#pb-expand")


@pytest.mark.parametrize(
    ("volume", "name"),
    [(0, "volume_off"), (20, "volume_low"), (50, "volume_medium"), (90, "volume_high")],
)
async def test_volume_icon_follows_the_level(volume: int, name: str):
    app = _Host()
    async with app.run_test(size=(140, 12)) as pilot:
        app.query_one(PlaybackBar).update_volume(volume)
        await pilot.pause()
        assert _text(app, "#pb-volume").startswith(NERD[name])


async def test_play_button_swaps_play_and_pause():
    app = _Host()
    async with app.run_test(size=(140, 12)) as pilot:
        bar = app.query_one(PlaybackBar)
        assert _text(app, "#pb-play") == NERD["play"]
        bar.update_playback_state(is_playing=True, is_paused=False)
        await pilot.pause()
        assert _text(app, "#pb-play") == NERD["pause"]


async def test_header_buttons_lead_with_their_icons():
    app = _Host()
    async with app.run_test(size=(140, 12)):
        assert _text(app, "#toggle-lyrics").startswith(NERD["lyrics"])
        assert _text(app, "#header-search").startswith(NERD["search"])
