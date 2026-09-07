"""PlaylistPicker after a successful add into the playlist that is open.

The add response maps videoId -> setVideoId, one entry per video. When the
submission held two copies of one video that map can't say which appended
row is which, so the picker reloads the playlist instead of stamping both
rows with the same setVideoId. Unique submissions keep the optimistic
append.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from ytm_player.ui.pages.library import LibraryPage
from ytm_player.ui.popups.playlist_picker import PlaylistPicker


def _track(video_id: str, title: str) -> dict:
    return {"video_id": video_id, "title": title, "artist": "A"}


async def _add(video_ids: list[str], tracks: list[dict]) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Run ``_do_add`` against a stand-in app with the target playlist open.

    Returns the library page, its table and the picker's ``dismiss`` mock.
    """
    picker = PlaylistPicker(video_ids=video_ids, tracks=tracks)
    object.__setattr__(picker, "query_one", lambda *_a, **_k: MagicMock())
    picker.notify = MagicMock()
    picker.dismiss = MagicMock()

    app = MagicMock()
    app.ytmusic.add_playlist_items = AsyncMock(return_value="success")
    app.ytmusic.last_added_set_video_ids = {vid: f"set-{vid}" for vid in video_ids}
    app._current_page = "library"
    app._current_page_kwargs = {"playlist_id": "PL1"}
    library = MagicMock()
    table = MagicMock()
    library.query_one.return_value = table
    app.query_one = lambda selector, *_a: library if selector is LibraryPage else MagicMock()

    with (
        patch.object(type(picker), "app", new_callable=PropertyMock, return_value=app),
        patch("ytm_player.ui.popups.playlist_picker._record_recent"),
    ):
        await picker._do_add("PL1", "My playlist")
    return library, table, picker.dismiss


async def test_duplicate_submission_reloads_the_open_playlist_instead_of_appending():
    library, table, dismiss = await _add(
        ["b", "b", "d"],
        [_track("b", "Bravo"), _track("b", "Bravo again"), _track("d", "Delta")],
    )

    table.append_tracks.assert_not_called()
    library.run_worker.assert_called_once()
    library.load_playlist.assert_called_once_with("PL1")
    dismiss.assert_called_once_with("PL1")


async def test_unique_submission_still_appends_optimistically():
    library, table, dismiss = await _add(["b", "d"], [_track("b", "Bravo"), _track("d", "Delta")])

    table.append_tracks.assert_called_once()
    appended = table.append_tracks.call_args.args[0]
    assert [t["setVideoId"] for t in appended] == ["set-b", "set-d"]
    library.run_worker.assert_not_called()
    dismiss.assert_called_once_with("PL1")
