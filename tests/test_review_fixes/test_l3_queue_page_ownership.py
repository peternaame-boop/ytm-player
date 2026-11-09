"""A page mounted from inside a playback request must not keep that request (A1).

"Play all" and Discovery mix open the Queue page while their request is
still running. The page's message loop used to copy that request's token for
life, so after any newer request (Next) the page's own plays were dropped as
stale nested calls: the queue pointer moved and nothing played.

Real Textual app, real QueuePage, real mixins; fake player and API.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from textual.app import App, ComposeResult
from textual.containers import Container

from tests.test_app.test_playback import _fresh_playback_host, _stream_info
from ytm_player.app._navigation import NavigationMixin
from ytm_player.app._ownership import owns_request, request_scope
from ytm_player.app._playback import PlaybackMixin
from ytm_player.app._track_actions import TrackActionsMixin
from ytm_player.services.queue import QueueManager
from ytm_player.ui.pages.queue import QueuePage
from ytm_player.ui.widgets.track_table import TrackTable


def _raw(video_id: str) -> dict:
    return {"videoId": video_id, "title": video_id, "artists": [{"name": "A", "id": "a"}]}


def _track(video_id: str) -> dict:
    return {"video_id": video_id, "title": video_id, "duration": 180}


class _Host(TrackActionsMixin, PlaybackMixin, NavigationMixin, App[None]):
    """The real playback, track-action and navigation mixins over a bare app."""

    def __init__(self) -> None:
        super().__init__()
        state = vars(_fresh_playback_host())
        # Keep Textual's own notify / run_worker / query_one / timers.
        for name in ("notify", "call_later", "run_worker", "set_timer", "query_one"):
            state.pop(name)
        self.__dict__.update(state)
        self.queue = QueueManager()
        self.settings.playback.autoplay = False
        self.stream_resolver.resolve = AsyncMock(side_effect=_stream_info)
        self._log_listen_for = AsyncMock()
        self._log_current_listen = AsyncMock()
        self.shuffle_prefs = {}
        self.ytmusic = MagicMock()
        self._play_request = 0
        self._current_page = ""
        self._current_page_kwargs = {}
        self._nav_stack = []
        self._forward_stack = []
        self._page_state_cache = {}
        self._active_library_playlist_id = None
        self._sidebar_per_page = {}
        self._sidebar_default = True
        self._lyrics_sidebar_open = False
        self._context_seq = 0
        self._active_pane = "content"
        self._nav_idle = asyncio.Event()
        self._nav_idle.set()
        self._nav_pending = None

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        yield Container(id="main-content")

    def _apply_playlist_sidebar(self, visible: bool) -> None:
        pass

    def _apply_lyrics_sidebar(self, visible: bool) -> None:
        pass

    def loaded(self) -> list[str]:
        """Video ids handed to the player, in order."""
        return [call.args[1]["video_id"] for call in self.player.play.await_args_list]


async def _play_all(app: _Host) -> None:
    """Sidebar playlist menu -> Play all (app/_track_actions.py)."""
    app.ytmusic.get_playlist = AsyncMock(
        return_value={"tracks": [_raw(f"T{i}") for i in range(6)], "trackCount": 6}
    )
    worker = app.run_worker(
        app._play_entity_and_navigate(
            "play_all", {"playlistId": "PL", "title": "Mix"}, "playlist", sidebar=True
        )
    )
    await worker.wait()


async def _discovery_mix(app: _Host) -> None:
    """Discovery mix (app/_playback.py), reached through the same navigation."""
    app.ytmusic.get_discovery_mix = AsyncMock(return_value=([_track("S0")], "history"))
    app.ytmusic.get_radio = AsyncMock(return_value=[_track(f"T{i}") for i in range(1, 6)])
    worker = app.run_worker(app._start_discovery_mix())
    await worker.wait()


async def _select_queue_row(pilot: Any, app: _Host, row: int) -> None:
    """Enter on a Queue page row, the way a user plays it."""
    table = app.query_one("#queue-table", TrackTable)
    table.focus()
    table.move_cursor(row=row)
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()
    await pilot.pause()


@pytest.mark.parametrize("start", [_play_all, _discovery_mix], ids=["play-all", "discovery-mix"])
async def test_queue_page_opened_by_a_request_still_plays_selected_rows_after_next(start):
    app = _Host()
    async with app.run_test(size=(120, 40)) as pilot:
        await start(app)
        await pilot.pause()
        assert app.current_page_name == "queue"
        assert isinstance(app._get_current_page(), QueuePage)
        first = app.queue.tracks[0]["video_id"]
        assert app.loaded() == [first]

        await app._play_next()  # any newer request: key, bar, media key, `ytm next`
        second = app.queue.tracks[1]["video_id"]
        assert app.loaded() == [first, second]

        await _select_queue_row(pilot, app, 4)

        chosen = app.queue.tracks[4]["video_id"]
        assert app.queue.current_track["video_id"] == chosen
        # Master moved the queue pointer and played nothing.
        assert app.loaded() == [first, second, chosen]


async def test_queue_page_opened_outside_a_request_plays_selected_rows_after_next():
    """Control: the page was always fine when a key opened it."""
    app = _Host()
    async with app.run_test(size=(120, 40)) as pilot:
        app.queue.add_multiple([_track(f"T{i}") for i in range(6)])
        app.queue.jump_to(0)
        await app.play_track(app.queue.current_track)
        await app.navigate_to("queue")
        await pilot.pause()

        await app._play_next()
        await _select_queue_row(pilot, app, 4)

        assert app.loaded() == ["T0", "T1", "T4"]


@pytest.mark.parametrize("page_fails", [False, True], ids=["mounted", "page-build-fails"])
async def test_navigation_gives_the_caller_its_request_back(monkeypatch, page_fails):
    """The mount runs outside the request; the request itself goes on after it."""
    app = _Host()
    async with app.run_test(size=(120, 40)) as pilot:
        if page_fails:

            def _broken(page_name: str, **kwargs: Any) -> QueuePage:
                raise RuntimeError("page failed to build")

            monkeypatch.setattr(app, "_create_page", _broken)
        app._play_request = 1
        with request_scope(app, 1):
            try:
                await app.navigate_to("queue")
            except RuntimeError:
                assert page_fails
            await pilot.pause()
            assert owns_request(app)
            # Still bound to request 1: a newer request supersedes the caller.
            app._play_request = 2
            assert not owns_request(app)
