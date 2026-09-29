"""The actions menu must not replay a row when it closes, nor eat the next one.

Selecting an action in the ActionsPopup (or cancelling it) hands focus back
to the TrackTable underneath, and the dismissal's stray key/click arrives
there as a RowSelected — which TrackActionsMixin replays as an immediate
jump-and-play (the reported "Play Next skips to the song" bug). The
right-click path armed a one-shot suppression against this; the keyboard
menu ('c') and the playback-bar menu never did, and a menu closed with
Escape left the armed flags eating the next genuine selection.
"""

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
    """Real app + real mixin, so the popup/refocus mechanics are genuine.

    ``play_track`` is an AsyncMock: TrackActionsMixin's own
    ``on_track_table_track_selected`` jumps the queue and plays the row, so
    any stray selection the popup dismissal delivers shows up there.
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


# ── The reported bug: activate Play Next, row must not replay ────────


@pytest.mark.parametrize("via_right_click", [False, True], ids=["keyboard", "right-click"])
async def test_play_next_from_menu_never_replays_the_row(via_right_click):
    app = _Host()
    async with app.run_test(size=(120, 40)) as pilot:
        _seed_queue(app)
        app.query_one(TrackTable).load_tracks(_TRACKS)
        await pilot.pause()

        if via_right_click:
            await _open_via_right_click(pilot, app)
        else:
            await _open_via_keyboard(pilot, app)

        if via_right_click:
            await _choose_play_next_with_click(pilot)
        else:
            await _choose_play_next_with_enter(pilot)

        # No stray replay: the row must not start playing.
        app.play_track.assert_not_awaited()
        # The action itself still ran: "Two" sits behind the current track.
        ids = [t["video_id"] for t in app.queue.tracks]
        assert ids == ["1", "2", "2", "3"] and ids[1] == "2"


# ── Cancelling must not eat the next genuine selection ───────────────


@pytest.mark.parametrize("via_right_click", [False, True], ids=["keyboard", "right-click"])
async def test_menu_escape_then_genuine_selection_still_works(via_right_click):
    app = _Host()
    async with app.run_test(size=(120, 40)) as pilot:
        _seed_queue(app)
        app.query_one(TrackTable).load_tracks(_TRACKS)
        await pilot.pause()

        if via_right_click:
            await _open_via_right_click(pilot, app)
        else:
            await _open_via_keyboard(pilot, app)

        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()
        assert not isinstance(app.screen, ActionsPopup)
        app.play_track.assert_not_awaited()

        # A genuine selection right after the cancel must go through: jump
        # to "Three" and play it.
        table = app.query_one(TrackTable)
        table.focus()
        table.move_cursor(row=2)
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        app.play_track.assert_awaited_once()
        assert app.play_track.await_args[0][0]["video_id"] == "3"
