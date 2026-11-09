"""T7: closing the Spotify import popup cancels only the popup's own workers.

Textual's ``DOMNode.workers`` is the app-wide worker manager, so cancelling
everything it holds also cancelled unrelated app work (page loads, lyrics,
history, playback) whenever the popup was closed with Escape or a backdrop click.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, Static
from textual.worker import WorkerCancelled, WorkerState

import ytm_player.services.spotify_import as spotify_service
from ytm_player.ui.popups.spotify_import import SpotifyImportPopup

_URL = "https://open.spotify.com/playlist/abc123"


class _Host(App[None]):
    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        yield Static("host")


async def test_closing_the_popup_cancels_its_import_but_not_other_app_work(monkeypatch) -> None:
    fetch_started = threading.Event()
    release_fetch = threading.Event()

    def _blocking_fetch(url: str) -> tuple[str, list[dict]]:
        fetch_started.set()
        release_fetch.wait(5)
        return "Playlist", []

    monkeypatch.setattr(spotify_service, "extract_spotify_tracks", _blocking_fetch)

    app = _Host()
    async with app.run_test() as pilot:
        unrelated_gate = asyncio.Event()

        async def _unrelated_work() -> str:
            await unrelated_gate.wait()
            return "done"

        unrelated = app.run_worker(_unrelated_work(), name="unrelated")
        popup = SpotifyImportPopup()
        await app.push_screen(popup)
        await pilot.pause()
        try:
            popup.query_one("#si-url-input", Input).value = _URL
            await pilot.press("enter")
            assert await asyncio.to_thread(fetch_started.wait, 5)
            own = [worker for worker in app.workers if worker.node is popup]
            assert [worker.name for worker in own] == ["spotify_import"]
            assert unrelated.state == WorkerState.RUNNING

            await pilot.press("escape")
            await pilot.pause()

            assert app.screen is not popup
            with pytest.raises(WorkerCancelled):
                await asyncio.wait_for(own[0].wait(), 5)
            assert not unrelated.is_cancelled
            assert unrelated.state == WorkerState.RUNNING
        finally:
            release_fetch.set()

        unrelated_gate.set()
        assert await asyncio.wait_for(unrelated.wait(), 5) == "done"
