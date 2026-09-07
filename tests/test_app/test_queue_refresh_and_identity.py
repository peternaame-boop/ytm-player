"""Queue changes made outside the Queue page re-render it, and the track
menu's Remove from Queue acts on the occurrence it was opened on.

A queue occurrence is named by its entry id (see QueueManager.entries). The
popup receives that id when opened on a Queue page row; with it, only that
entry may go — if the entry is gone by the time the user confirms, nothing
else is removed. Without an id (playback bar, other pages) there is no
occurrence to name and the first copy by video id goes, as before.
"""

from __future__ import annotations

import types
from unittest.mock import AsyncMock, MagicMock

from ytm_player.app._track_actions import TrackActionsMixin
from ytm_player.services.queue import QueueManager
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


def _ids(queue: QueueManager) -> list[int]:
    return [entry_id for entry_id, _ in queue.entries]


def _host(queue: QueueManager) -> MagicMock:
    """A mocked host whose remove/open helpers are the real ones."""
    host = MagicMock()
    host.queue = queue
    for name in ("_remove_from_queue", "_open_actions_for_track"):
        setattr(host, name, types.MethodType(getattr(TrackActionsMixin, name), host))
    return host


def _confirm(host: MagicMock, track: dict, **kwargs) -> object:
    """Open the popup for *track* and return its result callback."""
    host._open_actions_for_track(track, **kwargs)
    return host.push_screen.call_args.args[1]


def _two_copies() -> tuple[QueueManager, dict]:
    queue = QueueManager()
    same = _track("x")
    queue.add(_track("a"))
    queue.add(same)
    queue.add(_track("b"))
    queue.add(same)  # the same dict object, queued twice
    return queue, same


class TestRemoveFromQueueByOccurrence:
    def test_removes_the_selected_copy_not_the_first(self):
        queue, same = _two_copies()
        ids = _ids(queue)
        host = _host(queue)
        confirm = _confirm(host, same, queue_entry_id=ids[3])

        confirm("remove_from_queue")

        assert _ids(queue) == ids[:3]
        assert queue.tracks[1] is same
        host._refresh_queue_page.assert_called_once()
        assert host.notify.call_args.args[0] == "Removed from queue"

    def test_occurrence_removed_meanwhile_removes_nothing_else(self):
        queue, same = _two_copies()
        ids = _ids(queue)
        host = _host(queue)
        confirm = _confirm(host, same, queue_entry_id=ids[3])
        queue.remove_entry(ids[3])  # gone while the popup was open (e.g. `d` on the page)

        confirm("remove_from_queue")

        assert _ids(queue) == ids[:3]
        host._refresh_queue_page.assert_called_once()
        assert host.notify.call_args.args[0] == "That queue entry is no longer present"
        assert host.notify.call_args.kwargs.get("severity") == "warning"

    def test_queue_rebuilt_with_the_same_song_keeps_the_new_copy(self):
        queue, same = _two_copies()
        ids = _ids(queue)
        host = _host(queue)
        confirm = _confirm(host, same, queue_entry_id=ids[1])
        queue.clear()
        queue.add_multiple([same, _track("c")])
        rebuilt = _ids(queue)
        assert ids[1] not in rebuilt

        confirm("remove_from_queue")

        assert _ids(queue) == rebuilt
        assert queue.tracks[0] is same
        host._refresh_queue_page.assert_called_once()
        assert host.notify.call_args.kwargs.get("severity") == "warning"

    def test_queue_origin_without_an_id_never_falls_back(self):
        """A Queue page row whose id could not be read is stale, not nameless."""
        queue, same = _two_copies()
        ids = _ids(queue)
        host = _host(queue)
        confirm = _confirm(host, same, from_queue=True)

        confirm("remove_from_queue")

        assert _ids(queue) == ids
        host._refresh_queue_page.assert_called_once()
        assert host.notify.call_args.kwargs.get("severity") == "warning"

    def test_without_an_occurrence_the_first_copy_goes(self):
        queue, same = _two_copies()
        ids = _ids(queue)
        host = _host(queue)
        confirm = _confirm(host, same)

        confirm("remove_from_queue")

        assert _ids(queue) == [ids[0], ids[2], ids[3]]
        host._refresh_queue_page.assert_called_once()
        assert host.notify.call_args.args[0] == "Removed from queue"

    def test_without_an_occurrence_and_no_copy_nothing_happens(self):
        queue, _ = _two_copies()
        ids = _ids(queue)
        host = _host(queue)
        confirm = _confirm(host, _track("zzz"))

        confirm("remove_from_queue")

        assert _ids(queue) == ids
        host._refresh_queue_page.assert_not_called()


def _table_stub(*, queue_entry_keys: bool, key: object) -> MagicMock:
    table = MagicMock()
    table.queue_entry_keys = queue_entry_keys
    table.occurrence_key.return_value = key
    return table


class TestOccurrenceSnapshot:
    """The right-click message snapshots the row's occurrence when it is created.

    Its handler runs later — after the table may have re-rendered (a queue
    change) or been removed (navigation) — and must not look the row up again.
    """

    def test_right_click_snapshots_the_occurrence_when_created(self):
        table = _table_stub(queue_entry_keys=True, key=7)
        track = {"video_id": "x", "_original_index": 1}

        message = TrackTable.TrackRightClicked(table, track, 0)

        table.occurrence_key.assert_called_once_with(1)
        assert message.occurrence_key == 7
        assert message.from_queue is True
        assert message.control is table

        # Whatever the table says by delivery time is not consulted.
        table.occurrence_key.return_value = 99
        table.queue_entry_keys = False
        host = MagicMock()
        host._queue_entry_id = TrackActionsMixin._queue_entry_id
        TrackActionsMixin.on_track_table_track_right_clicked(host, message)

        host._open_actions_for_track.assert_called_once_with(
            track, queue_entry_id=7, from_queue=True
        )
        table.occurrence_key.assert_called_once()

    def test_right_click_outside_the_queue_page_names_no_occurrence(self):
        table = _table_stub(queue_entry_keys=False, key="a-video-id-key")
        track = {"video_id": "x", "_original_index": 0}
        message = TrackTable.TrackRightClicked(table, track, 0)
        host = MagicMock()
        host._queue_entry_id = TrackActionsMixin._queue_entry_id

        TrackActionsMixin.on_track_table_track_right_clicked(host, message)

        host._open_actions_for_track.assert_called_once_with(
            track, queue_entry_id=None, from_queue=False
        )

    def test_right_click_on_a_track_without_a_row_index_keeps_the_queue_origin(self):
        table = _table_stub(queue_entry_keys=True, key=7)

        message = TrackTable.TrackRightClicked(table, {"video_id": "x"}, 0)

        table.occurrence_key.assert_not_called()
        assert message.occurrence_key is None
        assert message.from_queue is True

    async def test_keyboard_popup_forwards_the_focused_occurrence(self):
        host = MagicMock()
        host._queue_entry_id = TrackActionsMixin._queue_entry_id
        table = _table_stub(queue_entry_keys=True, key=5)
        table.selected_track = {"video_id": "x", "_original_index": 2}
        host._focused_track_table = MagicMock(return_value=table)

        await TrackActionsMixin._open_track_actions(host)

        table.occurrence_key.assert_called_once_with(2)
        host._open_actions_for_track.assert_called_once_with(
            table.selected_track, queue_entry_id=5, from_queue=True
        )

    async def test_keyboard_popup_on_another_table_names_no_occurrence(self):
        host = MagicMock()
        host._queue_entry_id = TrackActionsMixin._queue_entry_id
        table = _table_stub(queue_entry_keys=False, key="a-video-id-key")
        table.selected_track = {"video_id": "x", "_original_index": 2}
        host._focused_track_table = MagicMock(return_value=table)

        await TrackActionsMixin._open_track_actions(host)

        host._open_actions_for_track.assert_called_once_with(
            table.selected_track, queue_entry_id=None, from_queue=False
        )

    async def test_keyboard_popup_on_an_empty_queue_table_uses_the_playing_track(self):
        """A Queue table with no selected row (filtered to nothing) is not a Queue row."""
        host = MagicMock()
        host._queue_entry_id = TrackActionsMixin._queue_entry_id
        table = _table_stub(queue_entry_keys=True, key=5)
        table.selected_track = None
        host._focused_track_table = MagicMock(return_value=table)
        playing = {"video_id": "p"}
        host.player.current_track = playing

        await TrackActionsMixin._open_track_actions(host)

        table.occurrence_key.assert_not_called()
        host._open_actions_for_track.assert_called_once_with(
            playing, queue_entry_id=None, from_queue=False
        )

    async def test_keyboard_popup_without_a_table_uses_the_playing_track(self):
        host = MagicMock()
        host._focused_track_table = MagicMock(return_value=None)
        playing = {"video_id": "p"}
        host.player.current_track = playing

        await TrackActionsMixin._open_track_actions(host)

        host._open_actions_for_track.assert_called_once_with(
            playing, queue_entry_id=None, from_queue=False
        )


class TestBackgroundFillRefresh:
    async def test_appended_tracks_refresh_the_page(self):
        host = MagicMock()
        host.queue = QueueManager()
        host.ytmusic.get_playlist_remaining = AsyncMock(
            return_value=[{"videoId": "v9", "title": "T", "artists": []}]
        )

        await TrackActionsMixin._fetch_remaining_for_queue(host, "PL", 1)

        assert [t["video_id"] for t in host.queue.tracks] == ["v9"]
        host._refresh_queue_page.assert_called_once()

    async def test_nothing_remaining_does_not_refresh(self):
        host = MagicMock()
        host.queue = QueueManager()
        host.ytmusic.get_playlist_remaining = AsyncMock(return_value=[])

        await TrackActionsMixin._fetch_remaining_for_queue(host, "PL", 1)

        assert host.queue.is_empty
        host._refresh_queue_page.assert_not_called()
