"""The mounted PlaylistPicker: filter focus and one flow at a time.

Driven through the real Textual event loop so gestures already queued
behind another (a second Enter, a second click) are exercised, not only
direct method calls. One flow owns the picker from the gesture that
starts it — Enter on a playlist, or on Create New — until it ends in a
failure the user can retry or in the picker closing. An outcome that could
not be confirmed (a timeout) keeps ownership for good: the request may
still be applied, so that picker takes no further submission. No live
services.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from textual.app import App
from textual.widgets import Input, ListView, Static

from ytm_player.config.settings import Settings
from ytm_player.ui.popups.create_playlist_popup import CreatePlaylistPopup
from ytm_player.ui.popups.playlist_picker import PlaylistPicker, _CreateNewItem, _PlaylistItem

TIMEOUT_TEXT = (
    "No confirmation in time; the change may have gone through. Reload to check before retrying."
)


class _Host(App):
    """Minimal host exposing the attributes the picker reads."""

    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings()
        self.player = MagicMock(current_track=None)
        self.ytmusic = MagicMock()
        self.ytmusic.get_library_playlists = AsyncMock(
            return_value=[
                {"playlistId": "ALPHA", "title": "Alpha", "count": 0},
                {"playlistId": "BETA", "title": "Beta", "count": 3},
            ]
        )
        self._current_page = "search"
        self._current_page_kwargs = {}

    def get_css_variables(self) -> dict[str, str]:
        return {**super().get_css_variables(), "selected-item": "#3a3a3a"}


def _patches():
    return (
        patch("ytm_player.config.settings._settings", Settings()),
        patch("ytm_player.ui.popups.playlist_picker._load_recent_ids", return_value=[]),
        patch("ytm_player.ui.popups.playlist_picker._record_recent"),
    )


async def _ready(app: _Host, pilot) -> PlaylistPicker:
    """Push a picker for one track and wait for its playlist list."""
    picker = PlaylistPicker(["song"])
    await app.push_screen(picker)
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()
    return picker


def _blocking_add(app: _Host) -> tuple[list[str], asyncio.Event]:
    """An ``add_playlist_items`` that blocks until released; returns (calls, release)."""
    calls: list[str] = []
    release = asyncio.Event()

    async def add(playlist_id, *_args, **_kwargs):
        calls.append(playlist_id)
        await release.wait()
        return "network"

    app.ytmusic.add_playlist_items = add
    return calls, release


def _answering_add(app: _Host, answer: str) -> list[str]:
    """An ``add_playlist_items`` that answers *answer* at once; returns its calls."""
    calls: list[str] = []

    async def add(playlist_id, *_args, **_kwargs):
        calls.append(playlist_id)
        return answer

    app.ytmusic.add_playlist_items = add
    return calls


def _notices(picker: PlaylistPicker) -> list[str]:
    notices: list[str] = []
    picker.notify = lambda message, **_kwargs: notices.append(str(message))  # type: ignore[method-assign]
    return notices


def _view(picker: PlaylistPicker) -> ListView:
    return picker.query_one("#playlist-list", ListView)


def _item(picker: PlaylistPicker, playlist_id: str) -> _PlaylistItem:
    return next(c for c in _view(picker).children if getattr(c, "playlist_id", None) == playlist_id)


def _create_item(picker: PlaylistPicker) -> _CreateNewItem:
    return next(c for c in _view(picker).children if isinstance(c, _CreateNewItem))


def _select(picker: PlaylistPicker, item, index: int) -> None:
    """Queue the message the ListView posts for Enter or a click on *item*."""
    picker.post_message(ListView.Selected(_view(picker), item, index))


def _status(picker: PlaylistPicker) -> str:
    return str(picker.query_one("#picker-status", Static).content)


def _create_dialogs(app: _Host) -> list[CreatePlaylistPopup]:
    return [s for s in app.screen_stack if isinstance(s, CreatePlaylistPopup)]


async def _submit_create(app: _Host, pilot, picker: PlaylistPicker, name: str) -> None:
    """Select Create New, fill the dialog in, wait for the flow to settle."""
    _select(picker, _create_item(picker), 0)
    await pilot.pause()
    (dialog,) = _create_dialogs(app)
    dialog.dismiss((name, "", "PRIVATE"))
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


# ── Filter ────────────────────────────────────────────────────────────


async def test_typing_in_the_filter_keeps_focus_and_every_character():
    a, b, c = _patches()
    with a, b, c:
        app = _Host()
        async with app.run_test() as pilot:
            picker = await _ready(app, pilot)
            field = picker.query_one("#filter-input", Input)
            field.focus()
            await pilot.press("a", "l", "p")
            await pilot.pause()
            assert field.value == "alp"
            assert app.focused is field


async def test_a_queued_filter_change_followed_by_enter_submits_nothing():
    """Enter in the filter is not a submission: the list is rebuilt
    asynchronously, so what it shows may lag the filter text."""
    a, b, c = _patches()
    with a, b, c:
        app = _Host()
        async with app.run_test() as pilot:
            picker = await _ready(app, pilot)
            calls, _release = _blocking_add(app)
            field = picker.query_one("#filter-input", Input)
            field.focus()
            picker.post_message(Input.Changed(field, "Beta"))
            picker.post_message(Input.Submitted(field, "Beta"))
            await pilot.pause()
            await pilot.pause()
            shown = [getattr(c, "playlist_id", None) for c in _view(picker).children]
            assert calls == []
            assert shown == [None, "BETA"]
            assert app.focused is field


# ── One submission at a time ──────────────────────────────────────────


async def test_two_direct_submissions_send_one_request():
    a, b, c = _patches()
    with a, b, c:
        app = _Host()
        async with app.run_test() as pilot:
            picker = await _ready(app, pilot)
            calls, release = _blocking_add(app)
            picker._add_to_playlist("ALPHA", "Alpha")
            picker._add_to_playlist("ALPHA", "Alpha")
            await pilot.pause()
            release.set()
            await pilot.pause()
            assert calls == ["ALPHA"]


async def test_two_queued_selections_of_a_playlist_send_one_request():
    a, b, c = _patches()
    with a, b, c:
        app = _Host()
        async with app.run_test() as pilot:
            picker = await _ready(app, pilot)
            calls, release = _blocking_add(app)
            item = _item(picker, "ALPHA")
            _select(picker, item, 1)
            _select(picker, item, 1)
            await pilot.pause()
            release.set()
            await pilot.pause()
            assert calls == ["ALPHA"]


async def test_enter_twice_on_a_playlist_row_sends_one_request():
    a, b, c = _patches()
    with a, b, c:
        app = _Host()
        async with app.run_test() as pilot:
            picker = await _ready(app, pilot)
            calls, release = _blocking_add(app)
            view = _view(picker)
            view.focus()
            view.index = 1
            await pilot.pause()
            assert isinstance(view.highlighted_child, _PlaylistItem)
            await pilot.press("enter", "enter")
            await pilot.pause()
            release.set()
            await pilot.pause()
            assert calls == ["ALPHA"]


async def test_two_queued_create_selections_open_one_dialog():
    a, b, c = _patches()
    with a, b, c:
        app = _Host()
        async with app.run_test() as pilot:
            picker = await _ready(app, pilot)
            item = _create_item(picker)
            _select(picker, item, 0)
            _select(picker, item, 0)
            await pilot.pause()
            assert len(_create_dialogs(app)) == 1
            assert picker._submitting is True


async def test_cancelling_the_create_dialog_frees_the_picker():
    a, b, c = _patches()
    with a, b, c:
        app = _Host()
        async with app.run_test() as pilot:
            picker = await _ready(app, pilot)
            calls, release = _blocking_add(app)
            _select(picker, _create_item(picker), 0)
            await pilot.pause()
            (dialog,) = _create_dialogs(app)
            assert picker._submitting is True
            dialog.dismiss(None)
            await pilot.pause()
            assert picker._submitting is False
            _select(picker, _item(picker, "BETA"), 2)
            await pilot.pause()
            release.set()
            await pilot.pause()
            assert calls == ["BETA"]


async def test_a_retryable_failure_frees_the_picker():
    a, b, c = _patches()
    with a, b, c:
        app = _Host()
        async with app.run_test() as pilot:
            picker = await _ready(app, pilot)
            calls = _answering_add(app, "server_error")
            _select(picker, _item(picker, "ALPHA"), 1)
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert picker._submitting is False
            _select(picker, _item(picker, "BETA"), 2)
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert calls == ["ALPHA", "BETA"]


# ── An unconfirmed outcome keeps the picker owned ─────────────────────


async def _assert_owned_for_good(app: _Host, pilot, picker: PlaylistPicker, calls: list[str]):
    """No later gesture on this picker sends or opens anything."""
    sent = list(calls)
    assert picker._submitting is True
    _select(picker, _item(picker, "BETA"), 2)
    _select(picker, _create_item(picker), 0)
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()
    assert calls == sent
    assert _create_dialogs(app) == []
    assert picker in app.screen_stack


async def _assert_guidance_survives_filtering(
    app: _Host, pilot, picker: PlaylistPicker, calls: list[str], guidance: str
) -> None:
    """Typing in the filter rebuilds the list; the status line keeps the guidance.

    One filter change already queued, one typed: two rebuilds while the
    flow owns the picker. The list filters as usual; nothing is sent.
    """
    sent = list(calls)
    field = picker.query_one("#filter-input", Input)
    field.focus()
    picker.post_message(Input.Changed(field, "b"))
    await pilot.press("e")
    await pilot.pause()
    await pilot.pause()
    assert _status(picker) == guidance
    assert [getattr(c, "playlist_id", None) for c in _view(picker).children] == [None, "BETA"]
    assert picker._submitting is True
    _select(picker, _item(picker, "BETA"), 1)
    await pilot.pause()
    await app.workers.wait_for_complete()
    assert calls == sent
    assert _status(picker) == guidance


async def test_an_unconfirmed_add_keeps_the_picker_owned_and_says_what_to_do():
    a, b, c = _patches()
    with a, b, c:
        app = _Host()
        async with app.run_test() as pilot:
            picker = await _ready(app, pilot)
            calls = _answering_add(app, "timeout")
            notices = _notices(picker)
            _select(picker, _item(picker, "ALPHA"), 1)
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert calls == ["ALPHA"]
            guidance = "Unconfirmed — close this, reload the playlist and check before retrying"
            assert _status(picker) == guidance
            assert notices == [f"Couldn't confirm adding to 'Alpha' — {TIMEOUT_TEXT}"]
            await _assert_owned_for_good(app, pilot, picker, calls)
            await _assert_guidance_survives_filtering(app, pilot, picker, calls, guidance)


async def test_an_unconfirmed_creation_keeps_the_picker_owned_and_names_the_list():
    """Nothing to open yet: the user checks the playlist list, not a playlist."""
    a, b, c = _patches()
    with a, b, c:
        app = _Host()
        async with app.run_test() as pilot:
            picker = await _ready(app, pilot)
            app.ytmusic.create_playlist = AsyncMock(return_value=("timeout", ""))
            calls = _answering_add(app, "success")
            notices = _notices(picker)
            await _submit_create(app, pilot, picker, "Fresh")
            app.ytmusic.create_playlist.assert_awaited_once()
            assert calls == []
            guidance = (
                "Unconfirmed — close this, reload your playlist list and check before retrying"
            )
            assert _status(picker) == guidance
            assert notices == [f"Couldn't confirm creating 'Fresh' — {TIMEOUT_TEXT}"]
            await _assert_owned_for_good(app, pilot, picker, calls)
            await _assert_guidance_survives_filtering(app, pilot, picker, calls, guidance)
            app.ytmusic.create_playlist.assert_awaited_once()


async def test_an_unconfirmed_add_after_creating_keeps_the_picker_owned():
    """The playlist exists; whether the tracks landed in it is unknown."""
    a, b, c = _patches()
    with a, b, c:
        app = _Host()
        async with app.run_test() as pilot:
            picker = await _ready(app, pilot)
            app.ytmusic.create_playlist = AsyncMock(return_value=("success", "NEWPL"))
            calls = _answering_add(app, "timeout")
            notices = _notices(picker)
            await _submit_create(app, pilot, picker, "Fresh")
            assert calls == ["NEWPL"]
            guidance = "Unconfirmed — close this, reload 'Fresh' and check before retrying"
            assert _status(picker) == guidance
            assert notices == [f"Created 'Fresh' but couldn't confirm the add — {TIMEOUT_TEXT}"]
            await _assert_owned_for_good(app, pilot, picker, calls)
            await _assert_guidance_survives_filtering(app, pilot, picker, calls, guidance)
            app.ytmusic.create_playlist.assert_awaited_once()
