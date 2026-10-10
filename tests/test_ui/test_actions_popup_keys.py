"""Enter in the track actions popup must not leak to the page underneath.

Issue #177: ``a`` → Play Next → Enter replaced the queue with the page's
list and started playing it, while the same choice made with the mouse
inserted the track as expected. The popup closed on Enter without
consuming the key, so the app keymap then saw the same Enter as SELECT on
the restored page.

These tests drive the real ``KeyHandlingMixin.on_key`` with the default
keymap, because that is the route a keypress takes in the app. A host
without it sends Enter through DataTable's own binding instead and does
not reproduce the bug.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import ListView

from ytm_player.app._keys import KeyHandlingMixin
from ytm_player.app._track_actions import TrackActionsMixin
from ytm_player.config.keymap import Action, KeyMap
from ytm_player.services.queue import QueueManager
from ytm_player.ui.popups.actions import ActionsPopup
from ytm_player.ui.widgets.track_table import TrackTable


def _track(video_id: str) -> dict:
    return {
        "video_id": video_id,
        "title": video_id,
        "artist": "A",
        "artists": [{"name": "A", "id": "artist-1"}],
        "album": "",
        "album_id": None,
        "duration": 120,
        "thumbnail_url": None,
        "is_video": False,
    }


class _FakePlayer:
    current_track: dict | None = None

    def on(self, *_args: Any) -> None: ...

    def off(self, *_args: Any) -> None: ...


class _Page(Container):
    """Stands in for Library/Search: SELECT on a row replaces the queue."""

    def compose(self) -> ComposeResult:
        yield TrackTable(id="tracks")

    async def handle_action(self, action: Action, count: int = 1) -> None:
        await self.query_one(TrackTable).handle_action(action, count)

    def on_track_table_track_selected(self, event: TrackTable.TrackSelected) -> None:
        event.stop()
        self.app.selected.append(event.track["video_id"])  # type: ignore[attr-defined]


class _Host(KeyHandlingMixin, TrackActionsMixin, App):
    def __init__(self) -> None:
        super().__init__()
        self.keymap = KeyMap()
        self.keymap._load_defaults()
        self._key_buffer: list[str] = []
        self._count_buffer = ""
        self.queue = QueueManager()
        self.player = _FakePlayer()  # type: ignore[assignment]
        self.navigate_mock = AsyncMock()
        self.navigate_to = self.navigate_mock
        self.selected: list[str] = []
        self._current_page = "library"
        self._current_page_kwargs: dict[str, Any] = {}

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        yield _Page()

    def notify(self, *_args: Any, **_kwargs: Any) -> None:  # type: ignore[override]
        pass

    def _current_pane(self) -> str:
        return "content"

    def _get_current_page(self) -> Any:
        return self.query_one(_Page)

    def _refresh_queue_page(self) -> None:
        pass


async def _setup(pilot: Any, app: _Host) -> TrackTable:
    app.queue.add_multiple([_track("playing"), _track("queued")])
    app.queue.jump_to_real(0)
    table = app.query_one(TrackTable)
    table.load_tracks([_track("row-A"), _track("row-B")])
    table.focus()
    await pilot.pause()
    return table


async def _choose(pilot: Any, app: _Host, action_id: str) -> None:
    """Open the menu with ``a`` and move the highlight to *action_id*."""
    await pilot.press("a")
    await pilot.pause()
    assert isinstance(app.screen, ActionsPopup)
    list_view = app.screen.query_one("#actions-list", ListView)
    ids = [getattr(child, "action_id", None) for child in list_view.children]
    list_view.index = ids.index(action_id)
    await pilot.pause()


def _queue_ids(app: _Host) -> list[str]:
    return [t["video_id"] for t in app.queue.tracks]


async def test_keyboard_play_next_inserts_without_selecting_the_row() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        await _setup(pilot, app)
        await _choose(pilot, app, "play_next")

        await pilot.press("enter")
        await pilot.pause()

        assert not isinstance(app.screen, ActionsPopup)
        assert app.selected == []
        assert _queue_ids(app) == ["playing", "row-A", "queued"]


async def test_keyboard_add_to_queue_does_not_select_the_row() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        await _setup(pilot, app)
        await _choose(pilot, app, "add_to_queue")

        await pilot.press("enter")
        await pilot.pause()

        assert app.selected == []
        assert _queue_ids(app) == ["playing", "queued", "row-A"]


async def test_keyboard_go_to_artist_navigates_without_selecting_the_row() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        await _setup(pilot, app)
        await _choose(pilot, app, "go_to_artist")

        await pilot.press("enter")
        await pilot.pause()

        assert app.selected == []
        app.navigate_mock.assert_awaited_once_with(
            "context", context_type="artist", context_id="artist-1"
        )


async def test_enter_after_closing_the_menu_still_selects_the_row() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        await _setup(pilot, app)
        await _choose(pilot, app, "play_next")
        await pilot.press("enter")
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        assert app.selected == ["row-A"]


async def test_enter_after_escaping_the_menu_selects_the_row() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        await _setup(pilot, app)
        await _choose(pilot, app, "play_next")

        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ActionsPopup)

        await pilot.press("enter")
        await pilot.pause()

        assert app.selected == ["row-A"]
        assert _queue_ids(app) == ["playing", "queued"]


async def test_enter_works_when_the_list_is_not_focused() -> None:
    """The popup's Enter fallback still picks the highlighted action."""
    app = _Host()
    async with app.run_test() as pilot:
        await _setup(pilot, app)
        await _choose(pilot, app, "play_next")
        app.screen.set_focus(None)
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        assert not isinstance(app.screen, ActionsPopup)
        assert app.selected == []
        assert _queue_ids(app) == ["playing", "row-A", "queued"]
