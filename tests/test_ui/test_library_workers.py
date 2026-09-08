"""LibraryPage's workers: a playlist load and its tail fetch share a group of
their own, so a header action cannot cut a long playlist short, and a reload
supersedes the tail fetch of the load it replaces."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from textual.app import App, ComposeResult
from textual.widgets import Label

from ytm_player.ui.pages.library import LibraryPage
from ytm_player.ui.widgets.track_table import TrackTable

FIRST_BATCH = 3
TOTAL = 8


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
    """A playlist whose tail fetch waits on a gate the test opens."""

    def __init__(self) -> None:
        self.tail_started = asyncio.Event()
        self.tail_gate = asyncio.Event()
        self.tail_calls = 0

    async def get_playlist(self, playlist_id, limit=None, order=None):
        return {
            "id": playlist_id,
            "title": "Big Playlist",
            "trackCount": TOTAL,
            "tracks": _tracks(FIRST_BATCH),
            "owner": {"name": "me"},
            "description": "",
            "privacy": "PRIVATE",
        }

    async def get_playlist_remaining(self, playlist_id, already_have, order=None):
        self.tail_calls += 1
        self.tail_started.set()
        await self.tail_gate.wait()
        return _tracks(TOTAL - FIRST_BATCH, offset=FIRST_BATCH)


class _Host(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.ytmusic = _FakeYTMusic()
        self.shuffle_prefs = SimpleNamespace(get=lambda pid: False, set=lambda *a: None)
        self.queue = SimpleNamespace(current_track=None)
        self.player = SimpleNamespace(current_track=None)
        self.radio_started = False

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        yield LibraryPage(playlist_id="PL1", id="library")

    async def _start_playlist_radio(self, data) -> None:
        self.radio_started = True


class _FakeClick:
    def __init__(self, widget) -> None:
        self.widget = widget

    def stop(self) -> None:
        pass


def _labels(page: LibraryPage) -> list[str]:
    return [str(label.render()) for label in page.query(Label)]


async def test_start_radio_leaves_the_tail_fetch_running() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        page = app.query_one(LibraryPage)
        await asyncio.wait_for(app.ytmusic.tail_started.wait(), 5)
        table = page.query_one("#library-tracks", TrackTable)
        assert table.row_count == FIRST_BATCH
        assert any("loading" in text for text in _labels(page))

        page.on_click(_FakeClick(page.query_one("#start-radio-btn")))
        await pilot.pause()

        app.ytmusic.tail_gate.set()
        await asyncio.wait_for(app.workers.wait_for_complete(), 5)
        await pilot.pause()

        assert app.radio_started is True
        assert table.row_count == TOTAL
        assert not any("loading" in text for text in _labels(page))


async def test_a_reload_supersedes_the_previous_load_and_its_tail_fetch() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        page = app.query_one(LibraryPage)
        await asyncio.wait_for(app.ytmusic.tail_started.wait(), 5)
        table = page.query_one("#library-tracks", TrackTable)

        app.ytmusic.tail_started.clear()
        page.reload("PL1")
        await asyncio.wait_for(app.ytmusic.tail_started.wait(), 5)  # the reload's own tail
        app.ytmusic.tail_gate.set()
        await asyncio.wait_for(app.workers.wait_for_complete(), 5)
        await pilot.pause()

        # The first load's tail was cancelled: only the reload's tail was appended.
        assert app.ytmusic.tail_calls == 2
        assert [t["video_id"] for t in table.tracks] == [f"v{i}" for i in range(TOTAL)]


def test_reload_runs_the_load_in_the_page_load_group() -> None:
    page = LibraryPage(playlist_id="PL1")
    page.run_worker = MagicMock()

    page.reload("PL1")

    (work,), kwargs = page.run_worker.call_args
    work.close()  # the coroutine is not awaited here
    assert kwargs == {"name": "load-playlist", "group": LibraryPage.LOAD_GROUP, "exclusive": True}
    assert LibraryPage.LOAD_GROUP not in ("", "default")
