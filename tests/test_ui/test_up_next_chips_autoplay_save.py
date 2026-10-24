"""Up next as on the web: Save, a radio's mix chips, and the Autoplay switch. Clicked through Pilot."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from textual.app import App, ComposeResult

from ytm_player.config.settings import Settings
from ytm_player.services.queue import QueueManager
from ytm_player.ui.pages.now_playing import (
    AutoplayRow,
    NowPlayingPage,
    QueueChips,
    UpNextList,
    UpNextText,
)
from ytm_player.ui.popups.playlist_picker import PlaylistPicker
from ytm_player.ui.widgets import cover


def _track(video_id: str, title: str) -> dict:
    return {"video_id": video_id, "title": title, "artist": "Beyoncé",
            "artists": [{"name": "Beyoncé"}], "album": "", "album_id": None, "duration": 200,
            "is_video": False, "thumbnail_url": ""}  # fmt: skip


CHIPS = [
    {"label": "All", "playlist_id": "RDAMVMv0", "params": "pa", "selected": True},
    {"label": "Familiar", "playlist_id": "RDATiYvv0", "params": "pf", "selected": False},
    {"label": "Deep cuts", "playlist_id": "RDATpXvv0", "params": "pd", "selected": False},
]
DEEP_CUTS = [{"videoId": "v0", "title": "Halo"}, {"videoId": "d1", "title": "Bye Bye"},
             {"videoId": "d2", "title": "Your Love"}]  # fmt: skip


class _FakePlayer:
    current_track: dict | None = None

    def on(self, *_args: Any) -> None: ...

    def off(self, *_args: Any) -> None: ...


class _Host(App):
    def __init__(self, *, radio: bool) -> None:
        super().__init__()
        self.queue = QueueManager()
        self.queue.add_multiple([_track("v0", "Halo"), _track("v1", "Sweet Dreams"),
                                 _track("v2", "Irreplaceable")])  # fmt: skip
        self.queue.jump_to(0)
        if radio:
            self.queue.radio_seeds = [_track("v0", "Halo")]
        else:
            self.queue.set_context("PLmine")
        self.player = _FakePlayer()
        self.player.current_track = self.queue.tracks[0]
        self.navigate_to = AsyncMock()
        self.ytmusic = MagicMock()
        self.ytmusic.get_queue_chips = AsyncMock(return_value=CHIPS)
        self.ytmusic.get_chip_tracks = AsyncMock(return_value=DEEP_CUTS)
        self.ytmusic.get_related_songs = AsyncMock(return_value=[])
        self.settings = Settings()
        self.settings.playback.autoplay = True
        self.settings.save = MagicMock()  # never write the real config.toml
        self._refresh_queue_page = MagicMock()
        self._prefetch_next_track = MagicMock()
        self._refill_queue = MagicMock()
        self.pushed: list[Any] = []

    def push_screen(self, screen: Any, *args: Any, **kwargs: Any) -> Any:
        self.pushed.append(screen)

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        for name in ("selected-item", "playback-bar-bg", "progress-filled", "progress-empty",
                     "active-tab", "inactive-tab", "lyrics-played", "lyrics-current",
                     "lyrics-upcoming"):  # fmt: skip
            variables.setdefault(name, "#333333")
        return variables

    def compose(self) -> ComposeResult:
        yield NowPlayingPage(id="page-now_playing")


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cover, "fetch_thumbnail", lambda url: None)


def _titles(app: App) -> list[str]:
    return [t.title for t in app.query(UpNextText)]


async def _settle(pilot, condition, tries: int = 40) -> None:
    for _ in range(tries):
        await pilot.pause(0.05)
        if condition():
            return
    raise AssertionError("condition never held")


async def test_a_radio_shows_its_mix_chips_with_all_picked():
    app = _Host(radio=True)
    async with app.run_test(size=(160, 45)) as pilot:
        chips = app.query_one(QueueChips)
        await _settle(pilot, lambda: chips.has_class("visible"))
        labels = [str(w.render()) for w in chips.query(".np-chip")]
        assert labels == ["All", "Familiar", "Deep cuts"]
        assert chips.query_one("#np-chip-0").has_class("-active")
        app.ytmusic.get_queue_chips.assert_awaited_once_with("v0")
        # A radio never ends, so there is no Autoplay switch.
        assert not app.query(AutoplayRow)


async def test_clicking_a_chip_swaps_the_tracks_after_the_playing_one():
    app = _Host(radio=True)
    async with app.run_test(size=(160, 45)) as pilot:
        chips = app.query_one(QueueChips)
        await _settle(pilot, lambda: chips.has_class("visible"))
        await pilot.click("#np-chip-2")
        await _settle(pilot, lambda: _titles(app) == ["Halo", "Bye Bye", "Your Love"])
        app.ytmusic.get_chip_tracks.assert_awaited_once_with("v0", "RDATpXvv0", "pd")
        assert chips.query_one("#np-chip-2").has_class("-active")
        assert not chips.query_one("#np-chip-0").has_class("-active")
        assert app.queue.current_track["video_id"] == "v0"
        app._refresh_queue_page.assert_called()
        app._prefetch_next_track.assert_called()


async def test_a_playlist_queue_has_no_chips_and_ends_with_autoplay_that_toggles():
    app = _Host(radio=False)
    async with app.run_test(size=(160, 45)) as pilot:
        await _settle(pilot, lambda: bool(app.query(AutoplayRow)))
        assert not app.query_one(QueueChips).has_class("visible")
        app.ytmusic.get_queue_chips.assert_not_awaited()
        assert "On" in str(app.query_one("#autoplay-switch").render())
        await pilot.click("#autoplay-switch")
        await pilot.pause()
        assert app.settings.playback.autoplay is False
        app.settings.save.assert_called_once()
        assert "Off" in str(app.query_one("#autoplay-switch").render())
        app._refill_queue.assert_not_called()
        await pilot.click("#autoplay-switch")
        await pilot.pause()
        assert app.settings.playback.autoplay is True
        app._refill_queue.assert_called_once()


async def test_save_offers_the_whole_queue_to_a_playlist():
    app = _Host(radio=False)
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause(0.2)
        assert "Save" in str(app.query_one("#np-save").render())
        await pilot.click("#np-save")
        await pilot.pause()
        assert len(app.pushed) == 1 and isinstance(app.pushed[0], PlaylistPicker)
        assert app.pushed[0].video_ids == ["v0", "v1", "v2"]


async def test_a_second_refresh_replaces_the_rows_without_reusing_an_id():
    """The interval refreshes every few seconds. A mount that ran before
    remove_children finished raised DuplicateIds on np-autoplay."""
    app = _Host(radio=False)
    async with app.run_test(size=(160, 45)) as pilot:
        await _settle(pilot, lambda: bool(app.query(AutoplayRow)))
        rows = app.query_one(UpNextList)
        assert len(app.query(AutoplayRow)) == 1
        # A new revision changes the signature, so refresh_rows mounts again.
        app.queue.add_multiple([_track("v3", "Deja Vu")])
        await rows.refresh_rows()
        await pilot.pause()
        await rows.refresh_rows()
        await pilot.pause()
        assert len(app.query(AutoplayRow)) == 1
        assert len(app.query(UpNextText)) == 4
