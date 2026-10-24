"""A sidebar playlist opens with the web app's header: cover, title, details, Play, Shuffle, Radio.

Clicked through Pilot. Before, the header was one text row with a red "[▶ Start Radio]" and no cover.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from textual.app import App, ComposeResult

from ytm_player.ui.pages.library import LibraryPage
from ytm_player.ui.widgets import cover
from ytm_player.ui.widgets.cover import Cover

TRACKS = [
    {"videoId": f"v{i}", "title": f"T{i}", "artists": [{"name": "A", "id": "UC1"}],
     "album": {"name": "Al", "id": "MPRE"}, "duration_seconds": 100, "thumbnails": []}
    for i in range(3)
]  # fmt: skip


class _YTMusic:
    async def get_playlist(self, playlist_id, limit=None, order=None):
        return {
            "id": playlist_id,
            "title": "Hip-Hop & Rap",
            "trackCount": len(TRACKS),
            "tracks": TRACKS,
            "author": {"name": "Shadow Sinz"},
            "description": "",
            "privacy": "PRIVATE",
            "thumbnails": [{"url": "small", "width": 120}, {"url": "big", "width": 544}],
        }


class _Host(App[None]):
    def __init__(self, locked: bool = False) -> None:
        super().__init__()
        self.ytmusic = _YTMusic()
        self.prefs: dict[str, bool] = {"PL1": locked}
        self.shuffle_prefs = SimpleNamespace(
            get=lambda pid: self.prefs.get(pid, False),
            set=lambda pid, value: self.prefs.__setitem__(pid, value),
        )
        self.queue = SimpleNamespace(current_track=None, current_context_id=None)
        self.player = SimpleNamespace(current_track=None)
        self._replace_queue_and_play = AsyncMock()
        self._start_playlist_radio = AsyncMock()

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        yield LibraryPage(playlist_id="PL1", id="library")


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cover, "fetch_thumbnail", lambda url: None)


async def _hero(pilot) -> None:
    for _ in range(30):
        await pilot.pause()
        if pilot.app.query("#play-all-btn"):
            return
    raise AssertionError("the playlist header never appeared")


async def test_the_header_shows_the_large_cover_and_the_web_buttons():
    app = _Host()
    async with app.run_test(size=(140, 40)) as pilot:
        await _hero(pilot)
        page = app.query_one(LibraryPage)
        assert page.query_one(Cover).url == "big"
        assert page.query_one(Cover).size == (30, 15)
        buttons = ["play-all-btn", "shuffle-all-btn", "start-radio-btn", "shuffle-lock-btn"]
        for button in buttons:
            assert page.query_one(f"#{button}").outer_size.height == 3
        assert str(page.query_one(".context-title").render()) == "Hip-Hop & Rap"


@pytest.mark.parametrize(
    ("button", "shuffle"), [("play-all-btn", False), ("shuffle-all-btn", True)]
)
async def test_play_and_shuffle_play_the_whole_playlist(button: str, shuffle: bool):
    app = _Host()
    async with app.run_test(size=(140, 40)) as pilot:
        await _hero(pilot)
        await pilot.click(f"#{button}")
        for _ in range(5):
            await pilot.pause()
        app._replace_queue_and_play.assert_awaited_once()
        args, kwargs = app._replace_queue_and_play.await_args
        assert [t["video_id"] for t in args[0]] == ["v0", "v1", "v2"]
        assert kwargs == {"entity_id": "PL1", "start_index": 0, "shuffle": shuffle}


async def test_radio_starts_the_playlist_radio():
    app = _Host()
    async with app.run_test(size=(140, 40)) as pilot:
        await _hero(pilot)
        await pilot.click("#start-radio-btn")
        for _ in range(5):
            await pilot.pause()
        app._start_playlist_radio.assert_awaited_once()


async def test_shuffle_lock_toggles_from_its_button():
    app = _Host()
    app.queue.shuffle_enabled = False
    async with app.run_test(size=(140, 40)) as pilot:
        await _hero(pilot)
        await pilot.click("#shuffle-lock-btn")
        await pilot.pause()
        lock = app.query_one("#shuffle-lock-btn")
        assert str(lock.render()) == "Shuffle lock: ON" and lock.has_class("locked")
        assert app.prefs["PL1"] is True


async def test_an_edit_keeps_the_header_and_shows_the_new_title():
    app = _Host()
    async with app.run_test(size=(140, 40)) as pilot:
        await _hero(pilot)
        await app.query_one(LibraryPage).refresh_header("Renamed", "new notes", "PUBLIC")
        await pilot.pause()
        page = app.query_one(LibraryPage)
        assert str(page.query_one(".context-title").render()) == "Renamed"
        assert str(page.query_one(".context-description").render()) == "new notes"
        assert page.query_one(Cover).url == "big"
