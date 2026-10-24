"""Album and playlist pages: the web-style hero header and its buttons, clicked through Pilot."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from textual.app import App, ComposeResult

from ytm_player.ui.pages.context import ContextPage
from ytm_player.ui.widgets import cover
from ytm_player.ui.widgets.cover import Cover


def _tracks(n: int) -> list[dict[str, Any]]:
    return [
        {"videoId": f"v{i}", "title": f"T{i}", "artists": [{"name": "A", "id": "UC1"}],
         "album": {"name": "Al", "id": "MPRE"}, "duration_seconds": 100, "thumbnails": []}
        for i in range(n)
    ]  # fmt: skip


THUMBS = [{"url": "small", "width": 60}, {"url": "big", "width": 544}]


class _FakeYTMusic:
    def __init__(self) -> None:
        self.added: list[str] = []

    async def get_album(self, album_id):
        return {"title": "Random Access Memories", "type": "Album", "year": "2013",
                "artists": [{"name": "Daft Punk"}], "tracks": _tracks(4), "thumbnails": THUMBS,
                "audioPlaylistId": "OLAK5uy"}  # fmt: skip

    async def get_playlist(self, playlist_id, limit=None, order=None):
        return {"id": playlist_id, "title": "Replay Mix", "trackCount": 3, "tracks": _tracks(3),
                "author": {"name": "YouTube Music"}, "owned": False, "thumbnails": THUMBS,
                "description": "Songs you love\nsecond line"}  # fmt: skip

    async def add_to_library(self, playlist_id):
        self.added.append(playlist_id)
        return "success"


class _Host(App[None]):
    def __init__(self, context_type: str, context_id: str) -> None:
        super().__init__()
        self.ytmusic = _FakeYTMusic()
        self.page_args = (context_type, context_id)
        self._replace_queue_and_play = AsyncMock()
        self.radio_started = False

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        yield ContextPage(*self.page_args, id="context")

    async def _start_playlist_radio(self, item) -> None:
        self.radio_started = True


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cover, "fetch_thumbnail", lambda url: None)


async def _loaded(app: _Host, pilot) -> None:
    await asyncio.wait_for(app.workers.wait_for_complete(), 5)
    for _ in range(10):
        await pilot.pause()
        if app.query("#play-all-btn"):
            return


async def test_album_hero_shows_cover_kind_title_details_and_actions():
    app = _Host("album", "MPREb_ram")
    async with app.run_test(size=(140, 45)) as pilot:
        await _loaded(app, pilot)
        hero = app.query_one(".context-hero")
        assert hero.query_one(Cover).url == "big"
        assert str(hero.query_one(".context-kind").render()) == "Album"
        assert str(hero.query_one(".context-title").render()) == "Random Access Memories"
        assert "Daft Punk • 2013 • 4 tracks" in str(hero.query_one(".context-subtitle").render())
        ids = [w.id for w in hero.query(".context-actions > Static")]
        assert ids == ["play-all-btn", "shuffle-all-btn", "add-to-library-btn"]
        assert hero.region.height >= 15, "the hero is clipped"


async def test_playlist_hero_has_radio_and_first_description_line():
    app = _Host("playlist", "PL1")
    async with app.run_test(size=(140, 45)) as pilot:
        await _loaded(app, pilot)
        hero = app.query_one(".context-hero")
        subtitles = [str(w.render()) for w in hero.query(".context-subtitle")]
        assert subtitles[0].startswith("YouTube Music • 3 tracks")
        assert subtitles[1] == "Songs you love"
        ids = [w.id for w in hero.query(".context-actions > Static")]
        assert ids == ["play-all-btn", "shuffle-all-btn", "add-to-library-btn", "start-radio-btn"]
        # Regression: an old one-row style kept Save and Radio shorter than Play.
        assert {w.region.height for w in hero.query(".context-actions > Static")} == {3}


async def test_click_play_plays_the_list_from_the_top():
    app = _Host("album", "MPREb_ram")
    async with app.run_test(size=(140, 45)) as pilot:
        await _loaded(app, pilot)
        await pilot.click("#play-all-btn")
        await asyncio.wait_for(app.workers.wait_for_complete(), 5)
        (tracks,), kwargs = app._replace_queue_and_play.await_args
        assert [t["video_id"] for t in tracks] == ["v0", "v1", "v2", "v3"]
        assert kwargs == {"entity_id": "MPREb_ram", "start_index": 0, "shuffle": False}


async def test_click_shuffle_plays_the_list_shuffled():
    app = _Host("playlist", "PL1")
    async with app.run_test(size=(140, 45)) as pilot:
        await _loaded(app, pilot)
        await pilot.click("#shuffle-all-btn")
        await asyncio.wait_for(app.workers.wait_for_complete(), 5)
        _, kwargs = app._replace_queue_and_play.await_args
        assert kwargs["shuffle"] is True


async def test_click_save_adds_to_library_and_relabels():
    app = _Host("album", "MPREb_ram")
    async with app.run_test(size=(140, 45)) as pilot:
        await _loaded(app, pilot)
        await pilot.click("#add-to-library-btn")
        await asyncio.wait_for(app.workers.wait_for_complete(), 5)
        await pilot.pause()
        assert app.ytmusic.added == ["OLAK5uy"]
        assert str(app.query_one("#add-to-library-btn").render()) == "✓  Saved"


async def test_click_radio_starts_radio():
    app = _Host("playlist", "PL1")
    async with app.run_test(size=(140, 45)) as pilot:
        await _loaded(app, pilot)
        await pilot.click("#start-radio-btn")
        await asyncio.wait_for(app.workers.wait_for_complete(), 5)
        assert app.radio_started
