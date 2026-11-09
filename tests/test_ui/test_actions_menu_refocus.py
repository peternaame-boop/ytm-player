"""Actions menus preserve the next table selection after dismissal."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from textual.app import App, ComposeResult

from ytm_player.app._track_actions import TrackActionsMixin
from ytm_player.services.queue import QueueManager
from ytm_player.ui.popups.actions import ActionsPopup
from ytm_player.ui.widgets.track_table import TrackTable


def _track(video_id: str, title: str = "T") -> dict:
    return {
        "video_id": video_id,
        "title": title,
        "artist": "A",
        "artists": [{"name": "A", "id": "1"}],
        "album": "",
        "album_id": None,
        "duration": 120,
        "thumbnail_url": None,
        "is_video": False,
    }


_TRACKS = [_track("1", "One"), _track("2", "Two"), _track("3", "Three")]


class _Host(TrackActionsMixin, App[None]):
    """Minimal host for ``RowSelected`` handling: the real mixin, no keymap.

    Enter on the table reaches DataTable's own binding and arrives as
    ``RowSelected``. ``play_track`` is an AsyncMock, so a selection that
    gets through shows up as a play.
    """

    def __init__(self) -> None:
        super().__init__()
        self.play_track = AsyncMock()
        # Page state the popup builder reads (real app sets these in __init__).
        self._current_page = "search"
        self._current_page_kwargs = {}

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        yield TrackTable(id="t")


def _seed_queue(app: _Host) -> None:
    """Queue mirrors the table so the mixin's jump_to_real stays in range."""
    app.queue = QueueManager()
    app.queue.add_multiple(_TRACKS)
    app.queue.jump_to_real(0)  # "1" is playing


async def _open_via_keyboard(pilot, app: _Host) -> None:
    """Focus row 1 and open the actions menu the way the 'c' key does."""
    table = app.query_one(TrackTable)
    table.focus()
    table.move_cursor(row=1)
    await pilot.pause()
    await app._open_track_actions()
    await pilot.pause()
    assert isinstance(app.screen, ActionsPopup)


async def _open_via_right_click(pilot, app: _Host) -> None:
    """Right-click row 1 of the table; the mixin opens the popup."""
    await pilot.click(TrackTable, offset=(6, 2), button=3)
    await pilot.pause()
    assert isinstance(app.screen, ActionsPopup)


async def _choose_play_next_with_enter(pilot) -> None:
    await pilot.press("j")  # highlight "Play Next" (second action)
    await pilot.press("enter")
    await pilot.pause()
    await pilot.pause()


async def _choose_play_next_with_click(pilot) -> None:
    popup = pilot.app.screen
    assert isinstance(popup, ActionsPopup)
    lv = popup.query_one("#actions-list")
    item = next(c for c in lv.children if getattr(c, "action_id", None) == "play_next")
    await pilot.click(item, offset=(3, 0))
    await pilot.pause()
    await pilot.pause()


async def _assert_next_selection_plays(pilot, app: _Host) -> None:
    """A genuine selection after the menu closes must jump to "Three" and play it."""
    table = app.query_one(TrackTable)
    table.focus()
    table.move_cursor(row=2)
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()
    await pilot.pause()
    app.play_track.assert_awaited_once()
    assert app.play_track.await_args[0][0]["video_id"] == "3"


# ── Activating an action: no replay, and the next selection still works ──


@pytest.mark.parametrize("via_click", [False, True], ids=["enter", "click"])
@pytest.mark.parametrize("via_right_click", [False, True], ids=["keyboard", "right-click"])
async def test_play_next_from_menu_never_replays_the_row(via_right_click, via_click):
    app = _Host()
    async with app.run_test(size=(120, 40)) as pilot:
        _seed_queue(app)
        app.query_one(TrackTable).load_tracks(_TRACKS)
        await pilot.pause()

        if via_right_click:
            await _open_via_right_click(pilot, app)
        else:
            await _open_via_keyboard(pilot, app)

        if via_click:
            await _choose_play_next_with_click(pilot)
        else:
            await _choose_play_next_with_enter(pilot)

        # No stray replay: the row must not start playing.
        app.play_track.assert_not_awaited()
        # The action itself still ran: "Two" sits behind the current track.
        ids = [t["video_id"] for t in app.queue.tracks]
        assert ids == ["1", "2", "2", "3"] and ids[1] == "2"

        await _assert_next_selection_plays(pilot, app)


# ── Cancelling must not eat the next genuine selection ───────────────


@pytest.mark.parametrize("cancel_with_click", [False, True], ids=["escape", "backdrop"])
@pytest.mark.parametrize("via_right_click", [False, True], ids=["keyboard", "right-click"])
async def test_menu_cancel_then_genuine_selection_still_works(via_right_click, cancel_with_click):
    app = _Host()
    async with app.run_test(size=(120, 40)) as pilot:
        _seed_queue(app)
        app.query_one(TrackTable).load_tracks(_TRACKS)
        await pilot.pause()

        if via_right_click:
            await _open_via_right_click(pilot, app)
        else:
            await _open_via_keyboard(pilot, app)

        if cancel_with_click:
            await pilot.click(offset=(0, 0))
        else:
            await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()
        assert not isinstance(app.screen, ActionsPopup)
        app.play_track.assert_not_awaited()

        await _assert_next_selection_plays(pilot, app)
