"""Create-new-playlist path: a ``"duplicate"`` answer gets the same confirmation.

Accepting retries against the playlist that was just created — never a
second one — and then runs the normal completion. Declining closes the
picker with ``None`` (the caller keeps its marks). A failed retry leaves
the picker open with the created playlist and no dismissal.
"""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, call, patch

from ytm_player.ui.popups.confirm_popup import ConfirmPopup
from ytm_player.ui.popups.playlist_picker import PlaylistPicker

IDS = ["same", "same"]


class _Run:
    """Drives ``_create_and_add`` and hands back what the picker did."""

    def __init__(self, add_results: list[str]) -> None:
        self.picker = PlaylistPicker(video_ids=list(IDS))
        self.status = MagicMock()
        object.__setattr__(self.picker, "query_one", lambda *_a, **_k: self.status)
        self.picker.notify = MagicMock()
        self.picker.dismiss = MagicMock()
        self.workers: list[Coroutine[Any, Any, None]] = []
        self.picker.run_worker = MagicMock(
            side_effect=lambda coro, **_kw: self.workers.append(coro)
        )

        self.app = MagicMock()
        self.app.ytmusic.create_playlist = AsyncMock(return_value=("success", "NEWPL"))
        self.app.ytmusic.add_playlist_items = AsyncMock(side_effect=add_results)
        self.app._current_page = "search"
        self.sidebar = MagicMock()
        self.sidebar.refresh_playlists = AsyncMock()
        self.app.query_one = lambda *_a: self.sidebar

    async def create(self) -> None:
        with (
            patch.object(
                type(self.picker), "app", new_callable=PropertyMock, return_value=self.app
            ),
            patch("ytm_player.ui.popups.playlist_picker._record_recent"),
        ):
            await self.picker._create_and_add("Fresh")

    def confirmation(self):
        self.app.push_screen.assert_called_once()
        popup, callback = self.app.push_screen.call_args.args
        assert isinstance(popup, ConfirmPopup)
        return callback

    async def answer(self, confirmed: bool) -> None:
        self.confirmation()(confirmed)
        with (
            patch.object(
                type(self.picker), "app", new_callable=PropertyMock, return_value=self.app
            ),
            patch("ytm_player.ui.popups.playlist_picker._record_recent"),
        ):
            for coro in self.workers:
                await coro


async def test_duplicate_then_accept_retries_the_same_playlist_and_completes():
    run = _Run(["duplicate", "success"])
    await run.create()
    run.picker.dismiss.assert_not_called()

    await run.answer(True)

    run.app.ytmusic.create_playlist.assert_awaited_once()
    assert run.app.ytmusic.add_playlist_items.await_args_list == [
        call("NEWPL", IDS, duplicates=False),
        call("NEWPL", IDS, duplicates=True),
    ]
    run.sidebar.refresh_playlists.assert_awaited_once()
    run.picker.dismiss.assert_called_once_with("NEWPL")
    assert "Added 2 tracks to 'Fresh'" in run.picker.notify.call_args.args[0]


async def test_duplicate_then_decline_closes_with_nothing_added():
    run = _Run(["duplicate"])
    await run.create()

    await run.answer(False)

    run.app.ytmusic.create_playlist.assert_awaited_once()
    run.app.ytmusic.add_playlist_items.assert_awaited_once_with("NEWPL", IDS, duplicates=False)
    run.picker.dismiss.assert_called_once_with(None)


async def test_duplicate_then_failed_retry_keeps_the_picker_open():
    run = _Run(["duplicate", "server_error"])
    await run.create()

    await run.answer(True)

    run.app.ytmusic.create_playlist.assert_awaited_once()
    assert run.app.ytmusic.add_playlist_items.await_count == 2
    run.picker.dismiss.assert_not_called()
    run.status.update.assert_called_with("Add failed")
    assert run.picker.notify.call_args.kwargs["severity"] == "warning"
