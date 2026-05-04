"""Tests for PlaybackMixin.play_track guards and dispatch.

Three bug classes guarded against:
1. Double-click producing two play_track calls (debounce within 1 second).
2. Tracks lacking video_id (AI-generated streams) hanging the queue
   instead of being skipped.
3. Cached audio path bypasses stream resolver entirely.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from ytm_player.app._playback import PlaybackMixin


def _fresh_playback_host():
    """Build a PlaybackMixin instance with the attrs play_track reads."""
    p = PlaybackMixin()
    p.player = MagicMock()
    p.player.play = AsyncMock()
    p.player.current_track = None
    p.player.position = 0.0
    p.stream_resolver = MagicMock()
    p.stream_resolver.resolve = AsyncMock(return_value=None)
    p.stream_resolver.clear_cache = MagicMock()
    p.queue = MagicMock()
    p.queue.next_track = MagicMock(return_value=None)
    p.queue.peek_next = MagicMock(return_value=None)
    p.history = None
    p.cache = None
    p.discord = None
    p.lastfm = None
    p.mpris = None
    p.mac_media = None
    p.settings = MagicMock()
    p.settings.notifications.enabled = False
    p.notify = MagicMock()
    p.call_later = MagicMock()
    p.run_worker = MagicMock()
    # query_one raises — caught by play_track's try/except around UI updates
    p.query_one = MagicMock(side_effect=Exception("no widget in test"))
    p._last_play_video_id = None
    p._last_play_time = 0.0
    p._consecutive_failures = 0
    p._track_start_position = 0.0
    p._advancing = False
    p._pending_resume_video_id = None
    p._pending_resume_position = 0.0
    return p


class TestPlayTrackDebounce:
    async def test_failed_play_clears_debounce_so_retry_works(self, monkeypatch):
        """Regression: stream-resolve failure must clear the debounce stamp.

        Otherwise the user clicking the same track again within 1s gets
        silently no-op'd instead of retrying.
        """
        host = _fresh_playback_host()
        # Resolver returns None — simulating "stream unavailable".
        host.stream_resolver.resolve = AsyncMock(return_value=None)
        # No queue advance candidate.
        host.queue.next_track = MagicMock(return_value=None)

        monkeypatch.setattr("ytm_player.app._playback.time.monotonic", lambda: 100.0)

        track = {"video_id": "abc", "title": "X"}
        await host.play_track(track)

        # First call set the stamp, then the failure handler should have cleared it.
        assert host._last_play_video_id == ""

        # Second call within "1s" should NOT be debounced.
        from ytm_player.services.stream import StreamInfo

        host.stream_resolver.resolve = AsyncMock(
            return_value=StreamInfo(
                url="http://x",
                video_id="abc",
                format="opus",
                bitrate=128,
                duration=200,
                expires_at=float("inf"),
                thumbnail_url=None,
            )
        )
        await host.play_track(track)
        host.player.play.assert_called_once()

    async def test_same_video_id_within_1s_is_debounced(self, monkeypatch):
        """Calling play_track twice for same video_id within 1s is a no-op the second time."""
        host = _fresh_playback_host()
        from ytm_player.services.stream import StreamInfo

        host.stream_resolver.resolve = AsyncMock(
            return_value=StreamInfo(
                url="http://x",
                video_id="abc",
                format="opus",
                bitrate=128,
                duration=200,
                expires_at=float("inf"),
                thumbnail_url=None,
            )
        )

        track = {"video_id": "abc", "title": "X", "artist": "Y"}

        # Freeze monotonic so both calls land in the same instant.
        monkeypatch.setattr("ytm_player.app._playback.time.monotonic", lambda: 100.0)
        await host.play_track(track)
        await host.play_track(track)

        host.player.play.assert_called_once()

    async def test_different_video_ids_not_debounced(self, monkeypatch):
        host = _fresh_playback_host()
        from ytm_player.services.stream import StreamInfo

        async def resolve_side_effect(vid):
            return StreamInfo(
                url=f"http://{vid}",
                video_id=vid,
                format="opus",
                bitrate=128,
                duration=200,
                expires_at=float("inf"),
                thumbnail_url=None,
            )

        host.stream_resolver.resolve = AsyncMock(side_effect=resolve_side_effect)
        monkeypatch.setattr("ytm_player.app._playback.time.monotonic", lambda: 100.0)
        await host.play_track({"video_id": "abc", "title": "A"})
        await host.play_track({"video_id": "xyz", "title": "B"})
        assert host.player.play.call_count == 2


class TestPlayTrackMissingVideoId:
    async def test_missing_video_id_skips_and_advances(self):
        host = _fresh_playback_host()
        host.queue.next_track = MagicMock(return_value={"video_id": "next123", "title": "Next"})
        await host.play_track({"video_id": "", "title": "AI track", "artist": ""})
        # User notified, queue advance attempted.
        assert host.notify.called
        host.queue.next_track.assert_called_once()
        # Stream resolver was NOT invoked for the broken track.
        host.stream_resolver.resolve.assert_not_called()


class TestPlayTrackCacheHit:
    async def test_cache_hit_bypasses_stream_resolver(self, tmp_path):
        host = _fresh_playback_host()
        cached_file = tmp_path / "abc.opus"
        cached_file.write_bytes(b"")
        host.cache = MagicMock()
        host.cache.get = AsyncMock(return_value=cached_file)

        track = {"video_id": "abc", "title": "X", "duration": 200}
        await host.play_track(track)

        # Cached path used; remote resolver skipped.
        host.cache.get.assert_called_once_with("abc")
        host.stream_resolver.resolve.assert_not_called()
        host.player.play.assert_called_once()
        played_url = host.player.play.call_args[0][0]
        assert played_url == str(cached_file)


def _resume_capable_host():
    """Host configured to actually reach the resume-apply block in play_track."""
    from ytm_player.services.stream import StreamInfo

    host = _fresh_playback_host()
    host.player.seek_absolute = AsyncMock()
    host.stream_resolver.resolve = AsyncMock(
        return_value=StreamInfo(
            url="http://x",
            video_id="ignored",
            format="opus",
            bitrate=128,
            duration=200,
            expires_at=float("inf"),
            thumbnail_url=None,
        )
    )
    return host


class TestPlayTrackPendingResume:
    async def test_pending_resume_kept_when_video_id_does_not_match(self):
        """User clicks a different track first — resume opportunity preserved."""
        host = _resume_capable_host()
        host._pending_resume_video_id = "abc"
        host._pending_resume_position = 83.0

        await host.play_track({"video_id": "xyz", "title": "Other"})

        # Pending resume left intact for the eventual matching play.
        assert host._pending_resume_video_id == "abc"
        assert host._pending_resume_position == 83.0
        # And we did NOT seek — that's only for the matching track.
        host.player.seek_absolute.assert_not_called()

    async def test_pending_resume_consumed_when_video_id_matches(self):
        """First play of the resumed track seeks and clears pending state."""
        host = _resume_capable_host()
        host._pending_resume_video_id = "abc"
        host._pending_resume_position = 83.0

        await host.play_track({"video_id": "abc", "title": "Resumed"})

        host.player.seek_absolute.assert_awaited_once_with(83.0)
        assert host._pending_resume_video_id is None
        assert host._pending_resume_position == 0.0
        assert host._track_start_position == 83.0


class TestToggleLikeCurrent:
    """Cover _toggle_like_current state transitions and no-op guards."""

    async def test_like_to_indifferent(self):
        """LIKE → INDIFFERENT clears the like and notifies the user."""
        host = _fresh_playback_host()
        track = {"video_id": "abc", "title": "X", "likeStatus": "LIKE"}
        host.player.current_track = track
        host.ytmusic = MagicMock()
        host.ytmusic.rate_song = AsyncMock(return_value="success")

        await host._toggle_like_current()

        host.ytmusic.rate_song.assert_awaited_once_with("abc", "INDIFFERENT")
        assert track["likeStatus"] == "INDIFFERENT"
        host.notify.assert_called_once_with("Removed from Liked songs", timeout=2)

    async def test_indifferent_to_like(self):
        """INDIFFERENT → LIKE adds the like and notifies the user."""
        host = _fresh_playback_host()
        track = {"video_id": "abc", "title": "X", "likeStatus": "INDIFFERENT"}
        host.player.current_track = track
        host.ytmusic = MagicMock()
        host.ytmusic.rate_song = AsyncMock(return_value="success")

        await host._toggle_like_current()

        host.ytmusic.rate_song.assert_awaited_once_with("abc", "LIKE")
        assert track["likeStatus"] == "LIKE"
        host.notify.assert_called_once_with("Added to Liked songs", timeout=2)

    async def test_dislike_to_like(self):
        """DISLIKE → LIKE clears the dislike (treated as non-LIKE → LIKE)."""
        host = _fresh_playback_host()
        track = {"video_id": "abc", "title": "X", "likeStatus": "DISLIKE"}
        host.player.current_track = track
        host.ytmusic = MagicMock()
        host.ytmusic.rate_song = AsyncMock(return_value="success")

        await host._toggle_like_current()

        host.ytmusic.rate_song.assert_awaited_once_with("abc", "LIKE")
        assert track["likeStatus"] == "LIKE"
        host.notify.assert_called_once_with("Added to Liked songs", timeout=2)

    async def test_no_auth_notifies_and_skips_rate_song(self):
        """Not signed in: warn the user and don't touch ytmusic."""
        host = _fresh_playback_host()
        track = {"video_id": "abc", "title": "X", "likeStatus": "INDIFFERENT"}
        host.player.current_track = track
        # Simulate "not signed in".
        host.ytmusic = None

        await host._toggle_like_current()

        host.notify.assert_called_once_with("Sign in to like songs", severity="warning", timeout=2)
        # Track state untouched.
        assert track["likeStatus"] == "INDIFFERENT"

    async def test_no_current_track_is_silent_noop(self):
        """No track playing: return immediately, no notify, no rate_song."""
        host = _fresh_playback_host()
        host.player.current_track = None
        host.ytmusic = MagicMock()
        host.ytmusic.rate_song = AsyncMock()

        await host._toggle_like_current()

        host.ytmusic.rate_song.assert_not_called()
        host.notify.assert_not_called()

    async def test_failure_shows_per_cause_toast_network(self):
        """Task 4.11: network failure renders the network-specific suffix."""
        host = _fresh_playback_host()
        track = {"video_id": "abc", "title": "X", "likeStatus": "INDIFFERENT"}
        host.player.current_track = track
        host.ytmusic = MagicMock()
        host.ytmusic.rate_song = AsyncMock(return_value="network")

        await host._toggle_like_current()

        # Track state must NOT change on failure.
        assert track["likeStatus"] == "INDIFFERENT"
        # Toast must mention the network cause; severity must be error.
        host.notify.assert_called_once()
        call_kwargs = host.notify.call_args
        msg = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs["message"]
        assert "connection" in msg.lower()
        assert call_kwargs.kwargs.get("severity") == "error"

    async def test_failure_shows_per_cause_toast_auth_expired(self):
        """Task 4.11: auth_expired surfaces the `ytm setup` hint."""
        host = _fresh_playback_host()
        track = {"video_id": "abc", "title": "X", "likeStatus": "INDIFFERENT"}
        host.player.current_track = track
        host.ytmusic = MagicMock()
        host.ytmusic.rate_song = AsyncMock(return_value="auth_expired")

        await host._toggle_like_current()

        assert track["likeStatus"] == "INDIFFERENT"
        host.notify.assert_called_once()
        msg = host.notify.call_args.args[0]
        # The user must see *some* mention of re-running setup.
        assert "setup" in msg.lower()


class TestFetchAndPlayRadioSeedFirst:
    """Seeds should lead the queue when starting a radio (YTM native UX)."""

    async def test_seed_tracks_prepended_to_queue(self):
        from ytm_player.services.queue import QueueManager

        host = _fresh_playback_host()
        host.queue = QueueManager()
        host.ytmusic = MagicMock()
        host.ytmusic.get_radio = AsyncMock(
            return_value=[
                {
                    "video_id": "r1",
                    "title": "Radio 1",
                    "artist": "",
                    "artists": [],
                    "album": "",
                    "album_id": "",
                    "duration": 200,
                    "thumbnail_url": "",
                    "is_video": False,
                },
                {
                    "video_id": "r2",
                    "title": "Radio 2",
                    "artist": "",
                    "artists": [],
                    "album": "",
                    "album_id": "",
                    "duration": 200,
                    "thumbnail_url": "",
                    "is_video": False,
                },
            ]
        )
        host.play_track = AsyncMock()
        host._refresh_queue_page = MagicMock()

        seeds = [
            {"videoId": "s1", "title": "Seed 1", "artists": [{"name": "A"}]},
            {"videoId": "s2", "title": "Seed 2", "artists": [{"name": "B"}]},
        ]
        await host._fetch_and_play_radio(seeds, label="Test Radio")

        tracks = host.queue.tracks
        assert tracks[0]["video_id"] == "s1"
        assert tracks[1]["video_id"] == "s2"
        assert tracks[2]["video_id"] == "r1"
        assert tracks[3]["video_id"] == "r2"
        played = host.play_track.call_args[0][0]
        assert played["video_id"] == "s1"

    async def test_radio_tracks_matching_seeds_are_deduplicated(self):
        from ytm_player.services.queue import QueueManager

        host = _fresh_playback_host()
        host.queue = QueueManager()
        host.ytmusic = MagicMock()
        host.ytmusic.get_radio = AsyncMock(
            return_value=[
                {
                    "video_id": "s1",
                    "title": "Seed 1 (dup)",
                    "artist": "",
                    "artists": [],
                    "album": "",
                    "album_id": "",
                    "duration": 200,
                    "thumbnail_url": "",
                    "is_video": False,
                },
                {
                    "video_id": "r1",
                    "title": "Radio 1",
                    "artist": "",
                    "artists": [],
                    "album": "",
                    "album_id": "",
                    "duration": 200,
                    "thumbnail_url": "",
                    "is_video": False,
                },
            ]
        )
        host.play_track = AsyncMock()
        host._refresh_queue_page = MagicMock()

        seeds = [{"videoId": "s1", "title": "Seed 1", "artists": [{"name": "A"}]}]
        await host._fetch_and_play_radio(seeds, label="Test")

        tracks = host.queue.tracks
        assert len(tracks) == 2
        assert tracks[0]["video_id"] == "s1"
        assert tracks[1]["video_id"] == "r1"

    async def test_append_mode_does_not_prepend_seeds(self):
        from ytm_player.services.queue import QueueManager

        host = _fresh_playback_host()
        host.queue = QueueManager()
        host.queue.add({"video_id": "existing", "title": "Existing"})
        host.queue.next_track()
        host.ytmusic = MagicMock()
        host.ytmusic.get_radio = AsyncMock(
            return_value=[
                {
                    "video_id": "r1",
                    "title": "Radio 1",
                    "artist": "",
                    "artists": [],
                    "album": "",
                    "album_id": "",
                    "duration": 200,
                    "thumbnail_url": "",
                    "is_video": False,
                },
            ]
        )
        host.play_track = AsyncMock()
        host._refresh_queue_page = MagicMock()

        seeds = [{"videoId": "s1", "title": "Seed 1", "artists": [{"name": "A"}]}]
        await host._fetch_and_play_radio(seeds, append=True)

        tracks = host.queue.tracks
        assert tracks[0]["video_id"] == "existing"
        assert tracks[1]["video_id"] == "r1"
        host.play_track.assert_not_called()
