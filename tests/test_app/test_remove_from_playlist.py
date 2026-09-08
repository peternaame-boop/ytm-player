"""Remove from Playlist on a row the picker just appended.

The handler lives in TrackActionsMixin and is driven with a stand-in host.
An appended row carries the setVideoId the add response assigned it, or
none: it never carries the id of the row it was taken from, so the remove
can't hit the original row.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from ytm_player.app._track_actions import TrackActionsMixin
from ytm_player.ui.popups.playlist_picker import PlaylistPicker

SOURCE = {"video_id": "song", "title": "Taken from this playlist", "setVideoId": "ORIGINAL-ROW"}


def _host() -> MagicMock:
    host = MagicMock()
    host._current_page_kwargs = {"playlist_id": "PL-SAME"}
    host.ytmusic.remove_playlist_items = AsyncMock(return_value="success")
    return host


async def _remove(host: MagicMock, row: dict) -> None:
    await TrackActionsMixin._remove_track_from_playlist.__get__(host)(row)


async def test_an_appended_row_without_a_response_id_asks_for_a_reload():
    row = PlaylistPicker(video_ids=["song"], tracks=[SOURCE])._tracks_for_append({})[0]
    host = _host()

    await _remove(host, row)

    host.ytmusic.remove_playlist_items.assert_not_awaited()
    assert host.notify.call_args.args[0] == "Reload the playlist before removing a just-added track"


async def test_an_appended_row_is_removed_by_the_id_the_response_assigned():
    row = PlaylistPicker(video_ids=["song"], tracks=[SOURCE])._tracks_for_append(
        {"song": "APPENDED-ROW"}
    )[0]
    host = _host()

    await _remove(host, row)

    host.ytmusic.remove_playlist_items.assert_awaited_once_with(
        "PL-SAME", [{"videoId": "song", "setVideoId": "APPENDED-ROW"}]
    )
