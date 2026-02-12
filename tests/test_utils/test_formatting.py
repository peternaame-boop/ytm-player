"""Tests for ytm_player.utils.formatting."""

from datetime import datetime, timedelta, timezone

import pytest

from ytm_player.utils.formatting import (
    VALID_VIDEO_ID,
    extract_artist,
    extract_duration,
    format_ago,
    format_count,
    format_duration,
    format_size,
    get_video_id,
    normalize_tracks,
    truncate,
)

# ── format_duration ──────────────────────────────────────────────────

class TestFormatDuration:
    @pytest.mark.parametrize("seconds, expected", [
        (0, "0:00"),
        (1, "0:01"),
        (59, "0:59"),
        (60, "1:00"),
        (125, "2:05"),
        (3599, "59:59"),
        (3600, "1:00:00"),
        (3661, "1:01:01"),
        (86400, "24:00:00"),
        (-5, "0:00"),
    ])
    def test_format_duration(self, seconds, expected):
        assert format_duration(seconds) == expected


# ── truncate ─────────────────────────────────────────────────────────

class TestTruncate:
    @pytest.mark.parametrize("text, max_len, expected", [
        ("", 10, ""),
        ("Hello", 10, "Hello"),
        ("Hello", 5, "Hello"),
        ("Hello World", 8, "Hello..."),
        ("Hello World", 3, "Hel"),
        ("Hello World", 2, "He"),
        ("Hello World", 1, "H"),
        ("Hello World", 0, ""),
        ("Hi", 4, "Hi"),
    ])
    def test_truncate(self, text, max_len, expected):
        assert truncate(text, max_len) == expected


# ── format_count ─────────────────────────────────────────────────────

class TestFormatCount:
    @pytest.mark.parametrize("n, expected", [
        (0, "0"),
        (999, "999"),
        (1000, "1.0K"),
        (1500, "1.5K"),
        (1000000, "1.0M"),
        (1500000, "1.5M"),
        (1000000000, "1.0B"),
        (-1500, "-1.5K"),
        (-2000000, "-2.0M"),
        (-3000000000, "-3.0B"),
    ])
    def test_format_count(self, n, expected):
        assert format_count(n) == expected


# ── format_size ──────────────────────────────────────────────────────

class TestFormatSize:
    @pytest.mark.parametrize("bytes_val, expected", [
        (0, "0 B"),
        (1023, "1023 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (1048576, "1.0 MB"),
        (1073741824, "1.0 GB"),
        (1099511627776, "1.0 TB"),
    ])
    def test_format_size(self, bytes_val, expected):
        assert format_size(bytes_val) == expected


# ── get_video_id ─────────────────────────────────────────────────────

class TestGetVideoId:
    def test_video_id_key(self):
        assert get_video_id({"video_id": "abc123"}) == "abc123"

    def test_video_id_camel_case_key(self):
        assert get_video_id({"videoId": "xyz789"}) == "xyz789"

    def test_both_keys_prefers_camel_case(self):
        assert get_video_id({"videoId": "first", "video_id": "second"}) == "first"

    def test_missing(self):
        assert get_video_id({}) == ""


# ── extract_artist ───────────────────────────────────────────────────

class TestExtractArtist:
    def test_string_artist(self):
        assert extract_artist({"artist": "Rick Astley"}) == "Rick Astley"

    def test_artists_list(self):
        track = {"artists": [{"name": "A"}, {"name": "B"}]}
        assert extract_artist(track) == "A, B"

    def test_empty(self):
        assert extract_artist({}) == "Unknown"

    def test_empty_artists_list(self):
        assert extract_artist({"artists": []}) == "Unknown"

    def test_artists_with_non_dict_items(self):
        track = {"artists": ["Artist A", "Artist B"]}
        assert extract_artist(track) == "Artist A, Artist B"


# ── extract_duration ─────────────────────────────────────────────────

class TestExtractDuration:
    def test_duration_seconds(self):
        assert extract_duration({"duration_seconds": 213}) == 213

    def test_duration_int(self):
        assert extract_duration({"duration": 180}) == 180

    def test_duration_string_mm_ss(self):
        assert extract_duration({"duration": "3:45"}) == 225

    def test_duration_string_hh_mm_ss(self):
        assert extract_duration({"duration": "1:02:30"}) == 3750

    def test_missing(self):
        assert extract_duration({}) == 0

    def test_invalid_time_string(self):
        assert extract_duration({"duration": "3:ab"}) == 0

    def test_four_part_time_string(self):
        assert extract_duration({"duration": "1:2:3:4"}) == 0

    def test_duration_seconds_zero(self):
        assert extract_duration({"duration_seconds": 0}) == 0


# ── normalize_tracks ─────────────────────────────────────────────────

class TestNormalizeTracks:
    def test_basic_normalization(self):
        raw = [{
            "videoId": "abc",
            "title": "Test",
            "artist": "Someone",
            "artists": [{"name": "Someone"}],
            "album": {"name": "Album", "id": "alb1"},
            "duration_seconds": 200,
            "thumbnails": [{"url": "http://img/small"}, {"url": "http://img/large"}],
        }]
        result = normalize_tracks(raw)
        assert len(result) == 1
        t = result[0]
        assert t["video_id"] == "abc"
        assert t["title"] == "Test"
        assert t["artist"] == "Someone"
        assert t["album"] == "Album"
        assert t["album_id"] == "alb1"
        assert t["duration"] == 200
        assert t["thumbnail_url"] == "http://img/large"
        assert t["is_video"] is False

    def test_empty_list(self):
        assert normalize_tracks([]) == []

    def test_missing_fields(self):
        result = normalize_tracks([{}])
        assert len(result) == 1
        assert result[0]["video_id"] == ""
        assert result[0]["title"] == "Unknown"
        assert result[0]["artist"] == "Unknown"
        assert result[0]["duration"] is None

    def test_string_duration_converted_to_int(self):
        result = normalize_tracks([{"duration": "3:45"}])
        assert result[0]["duration"] == 225

    def test_duration_seconds_zero(self):
        result = normalize_tracks([{"duration_seconds": 0}])
        assert result[0]["duration"] == 0

    def test_album_as_string(self):
        result = normalize_tracks([{"album": "My Album"}])
        assert result[0]["album"] == "My Album"
        assert result[0]["album_id"] is None

    def test_album_none(self):
        result = normalize_tracks([{"album": None}])
        assert result[0]["album"] == ""

    def test_is_video_true(self):
        result = normalize_tracks([{"isVideo": True}])
        assert result[0]["is_video"] is True

    def test_is_video_snake_case(self):
        result = normalize_tracks([{"is_video": True}])
        assert result[0]["is_video"] is True

    def test_video_id_snake_case_key(self):
        result = normalize_tracks([{"video_id": "xyz"}])
        assert result[0]["video_id"] == "xyz"

    def test_empty_thumbnails_list(self):
        result = normalize_tracks([{"thumbnails": []}])
        assert result[0]["thumbnail_url"] is None

    def test_artists_passthrough(self):
        artists = [{"name": "A", "id": "1"}, {"name": "B", "id": "2"}]
        result = normalize_tracks([{"artists": artists}])
        assert result[0]["artists"] == artists

    def test_multiple_tracks(self):
        raw = [
            {"videoId": "a", "title": "T1"},
            {"videoId": "b", "title": "T2"},
            {"videoId": "c", "title": "T3"},
        ]
        result = normalize_tracks(raw)
        assert len(result) == 3
        assert [t["video_id"] for t in result] == ["a", "b", "c"]


# ── format_ago ───────────────────────────────────────────────────────

class TestFormatAgo:
    def test_seconds(self):
        ts = datetime.now(timezone.utc) - timedelta(seconds=30)
        result = format_ago(ts)
        assert result == "30 seconds ago"

    def test_one_second_singular(self):
        ts = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert format_ago(ts) == "1 second ago"

    def test_minutes(self):
        ts = datetime.now(timezone.utc) - timedelta(minutes=5)
        assert format_ago(ts) == "5 minutes ago"

    def test_one_minute_singular(self):
        ts = datetime.now(timezone.utc) - timedelta(minutes=1)
        assert format_ago(ts) == "1 minute ago"

    def test_hours(self):
        ts = datetime.now(timezone.utc) - timedelta(hours=3)
        assert format_ago(ts) == "3 hours ago"

    def test_one_hour_singular(self):
        ts = datetime.now(timezone.utc) - timedelta(hours=1)
        assert format_ago(ts) == "1 hour ago"

    def test_days(self):
        ts = datetime.now(timezone.utc) - timedelta(days=7)
        assert format_ago(ts) == "7 days ago"

    def test_one_day_singular(self):
        ts = datetime.now(timezone.utc) - timedelta(days=1)
        assert format_ago(ts) == "1 day ago"

    def test_months(self):
        ts = datetime.now(timezone.utc) - timedelta(days=60)
        assert format_ago(ts) == "2 months ago"

    def test_one_month_singular(self):
        ts = datetime.now(timezone.utc) - timedelta(days=30)
        assert format_ago(ts) == "1 month ago"

    def test_years(self):
        ts = datetime.now(timezone.utc) - timedelta(days=400)
        assert format_ago(ts) == "1 year ago"

    def test_future(self):
        ts = datetime.now(timezone.utc) + timedelta(hours=1)
        assert format_ago(ts) == "just now"

    def test_naive_timestamp(self):
        ts = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        assert format_ago(ts) == "1 hour ago"


# ── VALID_VIDEO_ID ───────────────────────────────────────────────────

class TestValidVideoId:
    @pytest.mark.parametrize("vid", [
        "dQw4w9WgXcQ",
        "abc123",
        "A-B_c",
    ])
    def test_valid(self, vid):
        assert VALID_VIDEO_ID.match(vid)

    @pytest.mark.parametrize("vid", [
        "",
        "a" * 65,
        "abc 123",
        "abc!@#",
    ])
    def test_invalid(self, vid):
        assert not VALID_VIDEO_ID.match(vid)
