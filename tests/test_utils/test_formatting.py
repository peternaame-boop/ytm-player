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
    sanitize_title_for_lyric_lookup,
    truncate,
)

# ── format_duration ──────────────────────────────────────────────────


class TestFormatDuration:
    @pytest.mark.parametrize(
        "seconds, expected",
        [
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
        ],
    )
    def test_format_duration(self, seconds, expected):
        assert format_duration(seconds) == expected


# ── truncate ─────────────────────────────────────────────────────────


class TestTruncate:
    @pytest.mark.parametrize(
        "text, max_len, expected",
        [
            ("", 10, ""),
            ("Hello", 10, "Hello"),
            ("Hello", 5, "Hello"),
            ("Hello World", 8, "Hello W…"),
            ("Hello World", 3, "He…"),
            ("Hello World", 2, "H…"),
            ("Hello World", 1, "H"),
            ("Hello World", 0, ""),
            ("Hi", 4, "Hi"),
        ],
    )
    def test_truncate(self, text, max_len, expected):
        assert truncate(text, max_len) == expected

    def test_truncate_uses_unicode_ellipsis_on_overflow(self):
        """When text exceeds max_len, the suffix is the single Unicode char '…' not '...'."""
        result = truncate("Hello World", 8)
        # Expect "Hello W…" — 7 chars + 1 ellipsis = 8 total
        assert result == "Hello W…"
        assert "…" in result
        assert "..." not in result

    def test_truncate_unicode_ellipsis_max_len_one(self):
        """When max_len is 1, return single ellipsis char (or first char — match implementation)."""
        result = truncate("abcdef", 1)
        # max_len <= 1 falls into the small-buffer path; we accept either "a" or "…"
        # as long as it's 1 char
        assert len(result) == 1

    def test_truncate_unicode_ellipsis_no_overflow(self):
        """When text fits, no ellipsis added."""
        result = truncate("short", 10)
        assert result == "short"
        assert "…" not in result


# ── format_count ─────────────────────────────────────────────────────


class TestFormatCount:
    @pytest.mark.parametrize(
        "n, expected",
        [
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
        ],
    )
    def test_format_count(self, n, expected):
        assert format_count(n) == expected


# ── format_size ──────────────────────────────────────────────────────


class TestFormatSize:
    @pytest.mark.parametrize(
        "bytes_val, expected",
        [
            (0, "0 B"),
            (1023, "1023 B"),
            (1024, "1.0 KB"),
            (1536, "1.5 KB"),
            (1048576, "1.0 MB"),
            (1073741824, "1.0 GB"),
            (1099511627776, "1.0 TB"),
        ],
    )
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

    def test_length_string_mm_ss(self):
        """get_watch_playlist returns duration as 'length' key."""
        assert extract_duration({"length": "3:07"}) == 187

    def test_length_string_hh_mm_ss(self):
        assert extract_duration({"length": "1:00:00"}) == 3600

    def test_duration_seconds_takes_priority_over_length(self):
        assert extract_duration({"duration_seconds": 100, "length": "5:00"}) == 100

    def test_duration_takes_priority_over_length(self):
        assert extract_duration({"duration": 200, "length": "5:00"}) == 200


# ── normalize_tracks ─────────────────────────────────────────────────


class TestNormalizeTracks:
    def test_basic_normalization(self):
        raw = [
            {
                "videoId": "abc",
                "title": "Test",
                "artist": "Someone",
                "artists": [{"name": "Someone"}],
                "album": {"name": "Album", "id": "alb1"},
                "duration_seconds": 200,
                "thumbnails": [{"url": "http://img/small"}, {"url": "http://img/large"}],
            }
        ]
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

    def test_missing_fields_with_video_id(self):
        result = normalize_tracks([{"videoId": "abc123"}])
        assert len(result) == 1
        assert result[0]["video_id"] == "abc123"
        assert result[0]["title"] == "Unknown"
        assert result[0]["artist"] == "Unknown"
        assert result[0]["duration"] is None

    def test_tracks_without_video_id_are_dropped(self):
        result = normalize_tracks([{}, {"videoId": None}, {"videoId": ""}])
        assert result == []

    def test_mixed_playable_and_unplayable(self):
        result = normalize_tracks(
            [
                {"videoId": "abc", "title": "Good"},
                {"title": "No ID"},
                {"videoId": "def", "title": "Also Good"},
            ]
        )
        assert len(result) == 2
        assert result[0]["title"] == "Good"
        assert result[1]["title"] == "Also Good"

    def test_string_duration_converted_to_int(self):
        result = normalize_tracks([{"videoId": "x", "duration": "3:45"}])
        assert result[0]["duration"] == 225

    def test_duration_seconds_zero(self):
        result = normalize_tracks([{"videoId": "x", "duration_seconds": 0}])
        assert result[0]["duration"] == 0

    def test_length_key_from_watch_playlist(self):
        """get_watch_playlist returns 'length' instead of 'duration'."""
        result = normalize_tracks([{"videoId": "x", "title": "Radio Track", "length": "3:07"}])
        assert result[0]["duration"] == 187

    def test_album_as_string(self):
        result = normalize_tracks([{"videoId": "x", "album": "My Album"}])
        assert result[0]["album"] == "My Album"
        assert result[0]["album_id"] is None

    def test_album_none(self):
        result = normalize_tracks([{"videoId": "x", "album": None}])
        assert result[0]["album"] == ""

    def test_is_video_true(self):
        result = normalize_tracks([{"videoId": "x", "isVideo": True}])
        assert result[0]["is_video"] is True

    def test_is_video_snake_case(self):
        result = normalize_tracks([{"videoId": "x", "is_video": True}])
        assert result[0]["is_video"] is True

    def test_video_id_snake_case_key(self):
        result = normalize_tracks([{"video_id": "xyz"}])
        assert result[0]["video_id"] == "xyz"

    def test_empty_thumbnails_list(self):
        result = normalize_tracks([{"videoId": "x", "thumbnails": []}])
        assert result[0]["thumbnail_url"] is None

    def test_artists_passthrough(self):
        artists = [{"name": "A", "id": "1"}, {"name": "B", "id": "2"}]
        result = normalize_tracks([{"videoId": "x", "artists": artists}])
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
    @pytest.mark.parametrize(
        "vid",
        [
            "dQw4w9WgXcQ",
            "abc123",
            "A-B_c",
        ],
    )
    def test_valid(self, vid):
        assert VALID_VIDEO_ID.match(vid)

    @pytest.mark.parametrize(
        "vid",
        [
            "",
            "a" * 65,
            "abc 123",
            "abc!@#",
        ],
    )
    def test_invalid(self, vid):
        assert not VALID_VIDEO_ID.match(vid)


# ── sanitize_title_for_lyric_lookup ──────────────────────────────────


class TestSanitizeTitleForLyricLookup:
    def test_passthrough_clean_title(self):
        assert sanitize_title_for_lyric_lookup("Bohemian Rhapsody") == "Bohemian Rhapsody"

    def test_strips_official_video(self):
        assert (
            sanitize_title_for_lyric_lookup("Bohemian Rhapsody (Official Video)")
            == "Bohemian Rhapsody"
        )

    def test_strips_official_music_video_brackets(self):
        assert (
            sanitize_title_for_lyric_lookup("Bohemian Rhapsody [Official Music Video]")
            == "Bohemian Rhapsody"
        )

    def test_strips_audio(self):
        assert sanitize_title_for_lyric_lookup("Song Name (Audio)") == "Song Name"

    def test_strips_lyrics_video(self):
        assert sanitize_title_for_lyric_lookup("Song Name [Lyrics Video]") == "Song Name"

    def test_strips_hd(self):
        assert sanitize_title_for_lyric_lookup("Song Name (HD)") == "Song Name"

    def test_strips_multiple_annotations(self):
        assert sanitize_title_for_lyric_lookup("Song Name (Audio) (HD)") == "Song Name"

    def test_strips_artist_prefix_when_provided(self):
        assert (
            sanitize_title_for_lyric_lookup("Queen - Bohemian Rhapsody", artist="Queen")
            == "Bohemian Rhapsody"
        )

    def test_no_artist_no_prefix_strip(self):
        assert (
            sanitize_title_for_lyric_lookup("Queen - Bohemian Rhapsody")
            == "Queen - Bohemian Rhapsody"
        )

    def test_empty_input_returns_empty(self):
        assert sanitize_title_for_lyric_lookup("") == ""

    def test_returns_original_if_sanitization_empties_it(self):
        # Pure noise — would become empty string. Return original instead.
        assert sanitize_title_for_lyric_lookup("(Official Video)") == "(Official Video)"

    def test_case_insensitive_match(self):
        assert sanitize_title_for_lyric_lookup("Song Name (OFFICIAL VIDEO)") == "Song Name"
        assert sanitize_title_for_lyric_lookup("Song Name (official video)") == "Song Name"

    # ── Featured-artist annotations ──

    def test_strips_feat_dot(self):
        assert sanitize_title_for_lyric_lookup("Song Name (feat. Bob)") == "Song Name"

    def test_strips_ft_dot(self):
        assert sanitize_title_for_lyric_lookup("Song Name (ft. Bob)") == "Song Name"

    def test_strips_featuring(self):
        assert sanitize_title_for_lyric_lookup("Song Name (featuring Bob)") == "Song Name"

    def test_strips_feat_brackets(self):
        assert sanitize_title_for_lyric_lookup("Song Name [feat. Bob & Alice]") == "Song Name"

    def test_strips_feat_multiple_artists(self):
        assert (
            sanitize_title_for_lyric_lookup("Song Name (feat. Bob, Alice & Carol)") == "Song Name"
        )

    # ── Versions / re-releases / editions ──

    def test_strips_remix(self):
        assert sanitize_title_for_lyric_lookup("Song Name (Remix)") == "Song Name"

    def test_strips_remastered(self):
        assert sanitize_title_for_lyric_lookup("Song Name (Remastered)") == "Song Name"

    def test_strips_remastered_with_year(self):
        assert sanitize_title_for_lyric_lookup("Song Name (Remastered 2009)") == "Song Name"
        assert sanitize_title_for_lyric_lookup("Song Name (Remastered 2020)") == "Song Name"

    def test_strips_remaster_no_ed(self):
        assert sanitize_title_for_lyric_lookup("Song Name (Remaster)") == "Song Name"

    def test_strips_deluxe(self):
        assert sanitize_title_for_lyric_lookup("Song Name (Deluxe)") == "Song Name"

    def test_strips_deluxe_edition(self):
        assert sanitize_title_for_lyric_lookup("Song Name (Deluxe Edition)") == "Song Name"

    # ── Performance / arrangement annotations ──

    def test_strips_live(self):
        assert sanitize_title_for_lyric_lookup("Song Name (Live)") == "Song Name"

    def test_strips_live_at_venue(self):
        assert sanitize_title_for_lyric_lookup("Song Name (Live at Wembley Stadium)") == "Song Name"

    def test_strips_acoustic(self):
        assert sanitize_title_for_lyric_lookup("Song Name (Acoustic)") == "Song Name"

    # ── Combined patterns ──

    def test_strips_combined_feat_remastered_hd(self):
        assert sanitize_title_for_lyric_lookup("Song (feat. Bob) (Remastered 2020) (HD)") == "Song"

    def test_strips_combined_official_video_remix(self):
        assert sanitize_title_for_lyric_lookup("Song Name (Official Video) [Remix]") == "Song Name"

    def test_strips_combined_with_artist_prefix(self):
        assert (
            sanitize_title_for_lyric_lookup(
                "Queen - Bohemian Rhapsody (Remastered 2011) (Official Video)",
                artist="Queen",
            )
            == "Bohemian Rhapsody"
        )

    # ── Nested parens in feat. annotations ──

    def test_strips_feat_with_nested_parens_junior(self):
        # Inner `(Junior)` must not leave an orphan `)` behind.
        assert sanitize_title_for_lyric_lookup("Track (feat. Bob (Junior))") == "Track"

    def test_strips_feat_with_nested_parens_band(self):
        assert sanitize_title_for_lyric_lookup("Song (feat. Bob (of Band X))") == "Song"

    def test_strips_feat_then_remastered_bracket_boundary(self):
        # Combined: feat. group followed by a separate [Remastered] group.
        # Locks the bracket boundary — the feat. branch must stop at its
        # own `)` and not eat across into the next bracketed annotation.
        assert sanitize_title_for_lyric_lookup("Song (feat. Bob & Alice) [Remastered]") == "Song"

    # ── Qualifier suffixes for Acoustic / Remix ──

    def test_strips_acoustic_version(self):
        assert sanitize_title_for_lyric_lookup("Song (Acoustic Version)") == "Song"

    def test_strips_acoustic_mix(self):
        assert sanitize_title_for_lyric_lookup("Song (Acoustic Mix)") == "Song"

    def test_strips_acoustic_live(self):
        assert sanitize_title_for_lyric_lookup("Song (Acoustic Live)") == "Song"

    def test_strips_extended_remix(self):
        assert sanitize_title_for_lyric_lookup("Song (Extended Remix)") == "Song"

    def test_strips_radio_remix(self):
        assert sanitize_title_for_lyric_lookup("Song (Radio Remix)") == "Song"

    def test_strips_club_remix(self):
        assert sanitize_title_for_lyric_lookup("Song (Club Remix)") == "Song"

    # ── Negative passthroughs (bracket requirement) ──

    def test_passthrough_remix_culture(self):
        # No brackets — must NOT be mangled by the remix qualifier branch.
        assert sanitize_title_for_lyric_lookup("Remix Culture") == "Remix Culture"

    def test_passthrough_live_and_let_die(self):
        assert sanitize_title_for_lyric_lookup("Live and Let Die") == "Live and Let Die"

    def test_passthrough_acoustic_sessions(self):
        assert (
            sanitize_title_for_lyric_lookup("Acoustic Sessions Vol 1") == "Acoustic Sessions Vol 1"
        )
