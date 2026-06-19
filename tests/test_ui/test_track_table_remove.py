"""Tests for TrackTable.remove_track."""

from __future__ import annotations

from unittest.mock import MagicMock

from ytm_player.ui.widgets.track_table import TrackTable


def _make_track(video_id: str, title: str = "T") -> dict:
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


class TestRemoveTrack:
    def test_removes_existing_track(self):
        table = TrackTable.__new__(TrackTable)
        table._show_index = True
        table._show_album = True
        table._playing_video_id = None
        table._playing_index = None
        table._sort_column = None
        table._sort_reverse = False
        table._filter_text = ""
        table._filter_active = False
        table._title_manual_width = False
        table._resize_col = None
        table._resize_start_x = 0
        table._resize_start_width = 0
        table._row_keys = []
        table._filtered_map = []
        table._all_tracks = []
        table._tracks = []
        table.remove_row = MagicMock()
        table._fill_title_column = MagicMock()
        table._invalidate_table = MagicMock()
        table._highlight_playing = MagicMock()

        tracks = [_make_track("v1"), _make_track("v2"), _make_track("v3")]
        table._all_tracks = list(tracks)
        table._tracks = list(tracks)
        table._filtered_map = [0, 1, 2]
        table._row_keys = ["k0", "k1", "k2"]

        assert table.remove_track("v2") is True
        assert len(table._tracks) == 2
        assert [t["video_id"] for t in table._tracks] == ["v1", "v3"]
        assert len(table._all_tracks) == 2
        table.remove_row.assert_called_once_with("k1")
        table._fill_title_column.assert_called()
        table._invalidate_table.assert_called()

    def test_returns_false_for_missing_track(self):
        table = TrackTable.__new__(TrackTable)
        table._tracks = [_make_track("v1")]
        table._all_tracks = [_make_track("v1")]
        table._row_keys = ["k0"]
        table.remove_row = MagicMock()

        assert table.remove_track("v999") is False
        table.remove_row.assert_not_called()

    def test_removes_from_filtered_view(self):
        table = TrackTable.__new__(TrackTable)
        table._show_index = True
        table._show_album = True
        table._playing_video_id = None
        table._playing_index = None
        table._sort_column = None
        table._sort_reverse = False
        table._filter_text = ""
        table._filter_active = False
        table._title_manual_width = False
        table._resize_col = None
        table._resize_start_x = 0
        table._resize_start_width = 0
        table._row_keys = []
        table._filtered_map = []
        table._all_tracks = []
        table._tracks = []
        table.remove_row = MagicMock()
        table._fill_title_column = MagicMock()
        table._invalidate_table = MagicMock()
        table._highlight_playing = MagicMock()

        all_tracks = [_make_track("v1"), _make_track("v2"), _make_track("v3")]
        # Filtered view only shows v1 and v3
        table._all_tracks = list(all_tracks)
        table._tracks = [all_tracks[0], all_tracks[2]]
        table._filtered_map = [0, 2]
        table._row_keys = ["k0", "k2"]

        assert table.remove_track("v3") is True
        assert len(table._tracks) == 1
        assert table._tracks[0]["video_id"] == "v1"
        assert len(table._all_tracks) == 2
        assert table._all_tracks[0]["video_id"] == "v1"
        assert table._all_tracks[1]["video_id"] == "v2"
        table.remove_row.assert_called_once_with("k2")

    def test_filtered_map_correct_with_identical_dicts(self):
        # Two tracks with identical content but distinct setVideoId. The
        # _filtered_map rebuild must match by identity, not dict equality, or
        # the surviving duplicate would be dropped/mismapped.
        table = TrackTable.__new__(TrackTable)
        table._show_index = True
        table._show_album = True
        table._playing_video_id = None
        table._playing_index = None
        table._sort_column = None
        table._sort_reverse = False
        table._filter_text = ""
        table._filter_active = False
        table._title_manual_width = False
        table._resize_col = None
        table._resize_start_x = 0
        table._resize_start_width = 0
        table.remove_row = MagicMock()
        table._fill_title_column = MagicMock()
        table._invalidate_table = MagicMock()
        table._highlight_playing = MagicMock()

        first = _make_track("v1")
        first["setVideoId"] = "s1"
        second = _make_track("v1")
        second["setVideoId"] = "s2"
        assert first == second or first["setVideoId"] != second["setVideoId"]

        table._all_tracks = [first, second]
        table._tracks = [first, second]
        table._filtered_map = [0, 1]
        table._row_keys = ["k0", "k1"]

        assert table.remove_track("v1", set_video_id="s1") is True
        assert len(table._tracks) == 1
        # The surviving row is the second dict (identity-preserved).
        assert table._tracks[0] is second
        assert table._all_tracks == [second]
        assert table._filtered_map == [0]
        table.remove_row.assert_called_once_with("k0")
