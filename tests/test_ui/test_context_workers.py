"""ContextPage's workers: the data load and the fetches it chains share a
group of their own, so the header buttons' workers cannot cut a playlist's
tail or an artist's full song list short."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from textual.app import App, ComposeResult

from ytm_player.ui.pages.context import ContextPage
from ytm_player.ui.widgets.track_table import TrackTable

FIRST_BATCH = 3
TOTAL = 8
FULL_ARTIST_SONGS = 6


def _tracks(n: int, offset: int = 0) -> list[dict[str, Any]]:
    return [
        {
            "videoId": f"v{i + offset}",
            "title": f"T{i + offset}",
            "artists": [{"name": "A", "id": "UC1"}],
            "album": {"name": "Al", "id": "MPRE"},
            "duration_seconds": 100,
            "thumbnails": [],
        }
        for i in range(n)
    ]


class _FakeYTMusic:
    """A playlist whose tail fetch, and an artist whose full song list, wait on gates."""

    def __init__(self) -> None:
        self.tail_started = asyncio.Event()
        self.tail_gate = asyncio.Event()
        self.full_started = asyncio.Event()
        self.full_gate = asyncio.Event()
        self.added: list[str] = []

    async def get_playlist(self, playlist_id, limit=None, order=None):
        if playlist_id == "VLPLfull":
            self.full_started.set()
            await self.full_gate.wait()
            return {"trackCount": FULL_ARTIST_SONGS, "tracks": _tracks(FULL_ARTIST_SONGS)}
        return {
            "id": playlist_id,
            "title": "Big Playlist",
            "trackCount": TOTAL,
            "tracks": _tracks(FIRST_BATCH),
            "author": {"name": "me"},
            "owned": False,
        }

    async def get_playlist_remaining(self, playlist_id, already_have, order=None):
        self.tail_started.set()
        await self.tail_gate.wait()
        return _tracks(TOTAL - FIRST_BATCH, offset=FIRST_BATCH)

    async def get_artist(self, browse_id):
        return {
            "name": "Artist X",
            "songs": {"browseId": "VLPLfull", "results": _tracks(2)},
        }

    async def add_to_library(self, playlist_id):
        self.added.append(playlist_id)
        return "success"


class _Host(App[None]):
    def __init__(self, context_type: str, context_id: str) -> None:
        super().__init__()
        self.ytmusic = _FakeYTMusic()
        self.page_args = (context_type, context_id)
        self.radio_started = False

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        yield ContextPage(*self.page_args, id="context")

    async def _start_playlist_radio(self, item) -> None:
        self.radio_started = True


class _FakeClick:
    def __init__(self, widget) -> None:
        self.widget = widget

    def stop(self) -> None:
        pass


@pytest.fixture
def worker_kwargs(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, Any]]:
    """The keyword arguments of every worker the page starts, by worker name."""
    seen: dict[str, dict[str, Any]] = {}
    original = ContextPage.run_worker

    def recording(self, *args, **kwargs):
        seen[kwargs.get("name", "")] = kwargs
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ContextPage, "run_worker", recording)
    return seen


async def _settled_table(app: _Host, pilot) -> TrackTable:
    await asyncio.wait_for(app.workers.wait_for_complete(), 5)
    await pilot.pause()
    return app.query_one("#context-tracks", TrackTable)


async def test_start_radio_on_a_playlist_leaves_the_tail_fetch_running(worker_kwargs) -> None:
    app = _Host("playlist", "PL1")
    async with app.run_test() as pilot:
        page = app.query_one(ContextPage)
        await asyncio.wait_for(app.ytmusic.tail_started.wait(), 5)
        assert app.query_one("#context-tracks", TrackTable).row_count == FIRST_BATCH

        page.on_click(_FakeClick(page.query_one("#start-radio-btn")))
        await pilot.pause()

        app.ytmusic.tail_gate.set()
        table = await _settled_table(app, pilot)

        assert app.radio_started is True
        assert table.row_count == TOTAL
        assert worker_kwargs["fetch_context"] == {
            "name": "fetch_context",
            "group": ContextPage.LOAD_GROUP,
            "exclusive": True,
        }
        assert worker_kwargs["fetch_remaining"] == {
            "name": "fetch_remaining",
            "group": ContextPage.LOAD_GROUP,
        }
        assert worker_kwargs["start_radio"] == {
            "name": "start_radio",
            "group": "start-radio",
            "exclusive": True,
        }


async def test_add_to_library_on_a_playlist_leaves_the_tail_fetch_running(worker_kwargs) -> None:
    app = _Host("playlist", "PL1")
    async with app.run_test() as pilot:
        page = app.query_one(ContextPage)
        await asyncio.wait_for(app.ytmusic.tail_started.wait(), 5)

        page.on_click(_FakeClick(page.query_one("#add-to-library-btn")))
        await pilot.pause()

        app.ytmusic.tail_gate.set()
        table = await _settled_table(app, pilot)

        assert app.ytmusic.added == ["PL1"]
        assert table.row_count == TOTAL
        assert worker_kwargs["add_to_lib"] == {
            "name": "add_to_lib",
            "group": "add-to-library",
            "exclusive": True,
        }


async def test_an_artist_full_song_list_lands_in_the_load_group(worker_kwargs) -> None:
    app = _Host("artist", "UC_x")
    async with app.run_test() as pilot:
        await asyncio.wait_for(app.ytmusic.full_started.wait(), 5)
        assert app.query_one("#context-tracks", TrackTable).row_count == 2

        app.ytmusic.full_gate.set()
        table = await _settled_table(app, pilot)

        assert table.row_count == FULL_ARTIST_SONGS
        assert worker_kwargs["fetch-artist-songs"] == {
            "name": "fetch-artist-songs",
            "group": ContextPage.LOAD_GROUP,
            "exclusive": True,
        }
        assert ContextPage.LOAD_GROUP not in ("", "default")
