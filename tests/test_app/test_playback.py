"""Tests for PlaybackMixin.play_track guards and dispatch.

Three bug classes guarded against:
1. Double-click producing two play_track calls (debounce within 1 second).
2. Tracks lacking video_id (AI-generated streams) hanging the queue
   instead of being skipped.
3. Cached audio path bypasses stream resolver entirely.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from ytm_player.app._mpris import MPRISMixin
from ytm_player.app._playback import _MAX_CONSECUTIVE_FAILURES, PlaybackMixin


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
    p.ytmusic = None
    p.settings = MagicMock()
    p.settings.notifications.enabled = False
    p.notify = MagicMock()
    p.call_later = MagicMock()
    p.run_worker = MagicMock()
    p.set_timer = MagicMock()
    # query_one raises — caught by play_track's try/except around UI updates
    p.query_one = MagicMock(side_effect=Exception("no widget in test"))
    p._last_play_video_id = None
    p._last_play_time = 0.0
    p._consecutive_failures = 0
    p._track_start_position = 0.0
    p._advancing = False
    p._pending_resume_video_id = None
    p._pending_resume_position = 0.0
    p._play_generation = 0
    p._recovery_generation = None
    p._handled_error_attempt = 0
    p._local_history_claim = None
    p._play_lock = asyncio.Lock()
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


class TestFetchAndPlayRadioNotifyOrder:
    """Regression: 'Playing: X' must announce as soon as the radio is
    fetched and queued, not wait for the first track's stream resolve to
    finish (which can include a one-time cookie decryption)."""

    async def test_notify_fires_before_play_track(self):
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
            ]
        )
        host._refresh_queue_page = MagicMock()

        call_order = []

        def _record_notify(message, *args, **kwargs):
            if message.startswith("Playing:"):
                call_order.append("notify")

        host.notify = MagicMock(side_effect=_record_notify)

        async def _slow_play_track(track):
            call_order.append("play_track")

        host.play_track = AsyncMock(side_effect=_slow_play_track)

        seeds = [{"videoId": "s1", "title": "Seed 1", "artists": [{"name": "A"}]}]
        await host._fetch_and_play_radio(seeds, label="Test Radio")

        assert call_order == ["notify", "play_track"]


class TestCrossTrackRace:
    """T11: a later play_track call must supersede an in-flight one —
    the older call may not steal playback back or push stale metadata."""

    def _track(self, video_id: str, title: str) -> dict:
        return {"video_id": video_id, "title": title, "artist": "", "album": ""}

    async def test_slow_resolve_loses_to_later_click(self):
        import asyncio

        host = _fresh_playback_host()
        host.discord = MagicMock()
        host.discord.is_connected = True
        host.discord.update = AsyncMock()

        a_resolving = asyncio.Event()
        release_a = asyncio.Event()

        async def resolve(video_id):
            if video_id == "AAA":
                a_resolving.set()
                await release_a.wait()
            info = MagicMock()
            info.url = f"url-{video_id}"
            info.duration = 100
            return info

        host.stream_resolver.resolve = resolve

        task_a = asyncio.create_task(host.play_track(self._track("AAA", "A")))
        await a_resolving.wait()  # A is parked in stream resolution
        await host.play_track(self._track("BBB", "B"))  # B completes fully
        release_a.set()
        await task_a  # A's resolve finishes — must abort, not play

        played = [c.args[0] for c in host.player.play.await_args_list]
        assert played == ["url-BBB"], "superseded call stole playback back"
        titles = [c.kwargs["title"] for c in host.discord.update.await_args_list]
        assert titles == ["B"], "superseded call pushed stale metadata"

    async def test_late_fanout_suppressed_after_supersede(self):
        import asyncio

        host = _fresh_playback_host()

        async def resolve(video_id):
            info = MagicMock()
            info.url = f"url-{video_id}"
            info.duration = 100
            return info

        host.stream_resolver.resolve = resolve

        a_in_discord = asyncio.Event()
        release_discord = asyncio.Event()
        discord_titles: list[str] = []

        async def discord_update(**kwargs):
            discord_titles.append(kwargs["title"])
            if kwargs["title"] == "A":
                a_in_discord.set()
                await release_discord.wait()

        host.discord = MagicMock()
        host.discord.is_connected = True
        host.discord.update = discord_update
        host.lastfm = MagicMock()
        host.lastfm.is_connected = True
        host.lastfm.now_playing = AsyncMock()

        task_a = asyncio.create_task(host.play_track(self._track("AAA", "A")))
        await a_in_discord.wait()  # A played and is parked mid-fan-out
        await host.play_track(self._track("BBB", "B"))
        release_discord.set()
        await task_a  # A resumes after Discord — must skip Last.fm etc.

        titles = [c.kwargs["title"] for c in host.lastfm.now_playing.await_args_list]
        assert titles == ["B"], "superseded call continued its metadata fan-out"

    async def test_superseded_failed_resolve_does_not_advance_queue(self):
        import asyncio

        host = _fresh_playback_host()
        a_resolving = asyncio.Event()
        release_a = asyncio.Event()

        async def resolve(video_id):
            if video_id == "AAA":
                a_resolving.set()
                await release_a.wait()
                return None  # A's resolve fails — after being superseded
            info = MagicMock()
            info.url = f"url-{video_id}"
            info.duration = 100
            return info

        host.stream_resolver.resolve = resolve

        task_a = asyncio.create_task(host.play_track(self._track("AAA", "A")))
        await a_resolving.wait()
        await host.play_track(self._track("BBB", "B"))
        host.queue.next_track.reset_mock()
        host.notify.reset_mock()
        release_a.set()
        await task_a

        host.queue.next_track.assert_not_called()
        host.notify.assert_not_called()

    async def test_superseded_during_history_log_never_resolves(self):
        import asyncio

        host = _fresh_playback_host()
        a_logging = asyncio.Event()
        release_log = asyncio.Event()
        resolved: list[str] = []
        log_calls = {"n": 0}

        async def slow_log():
            log_calls["n"] += 1
            if log_calls["n"] == 1:  # A's call
                a_logging.set()
                await release_log.wait()

        host._log_current_listen = slow_log

        async def resolve(video_id):
            resolved.append(video_id)
            info = MagicMock()
            info.url = f"url-{video_id}"
            info.duration = 100
            return info

        host.stream_resolver.resolve = resolve

        task_a = asyncio.create_task(host.play_track(self._track("AAA", "A")))
        await a_logging.wait()
        await host.play_track(self._track("BBB", "B"))
        release_log.set()
        await task_a

        assert resolved == ["BBB"], "superseded call kept going after history log"


class TestDiscordRecoveryFanout:
    def _stream_info(self):
        from ytm_player.services.stream import StreamInfo

        return StreamInfo(
            url="http://x",
            video_id="abc",
            format="opus",
            bitrate=128,
            duration=200,
            expires_at=float("inf"),
            thumbnail_url=None,
        )

    async def test_track_start_calls_discord_even_when_marked_disconnected(self):
        host = _fresh_playback_host()
        host.stream_resolver.resolve = AsyncMock(return_value=self._stream_info())
        host.discord = MagicMock()
        host.discord.is_connected = False
        host.discord.update = AsyncMock()
        track = {"video_id": "abc", "title": "Song", "artist": "Artist"}

        async def _play(url, t, attempt=None):
            host.player.current_track = t

        host.player.play = AsyncMock(side_effect=_play)

        await host.play_track(track)

        host.discord.update.assert_awaited_once()

    def test_pause_calls_discord_even_when_marked_disconnected(self):
        host = _fresh_playback_host()
        host.discord = MagicMock()
        host.discord.is_connected = False

        host._on_pause_change(paused=True)

        host.call_later.assert_called_once()


class TestTrackEndFinalize:
    async def test_stale_track_end_noops_when_new_play_current(self):
        """A new play can commit between mpv's end-file event and this
        callback running — the whole event is stale. Finalizing could
        consume the new play's live claim (worst case: a same-video replay
        dropped from history), and advancing would skip straight over the
        track that is already playing."""
        host = _fresh_playback_host()
        host._log_listen_for = AsyncMock()
        host._play_next = AsyncMock()
        host.player.current_track = {"video_id": "new"}

        await host._on_track_end({"track": {"video_id": "old"}})

        host._log_listen_for.assert_not_awaited()
        host._play_next.assert_not_awaited()
        assert host._advancing is False

    async def test_natural_track_end_finalizes_and_advances(self):
        host = _fresh_playback_host()
        host._log_listen_for = AsyncMock()
        host._play_next = AsyncMock()
        host.player.current_track = None

        await host._on_track_end({"track": {"video_id": "old"}})

        host._log_listen_for.assert_awaited_once_with({"video_id": "old"})
        host._play_next.assert_awaited_once()


class TestHistoryArming:
    def _stream_info(self):
        from ytm_player.services.stream import StreamInfo

        return StreamInfo(
            url="http://x",
            video_id="abc",
            format="opus",
            bitrate=128,
            duration=200,
            expires_at=float("inf"),
            thumbnail_url=None,
        )

    async def test_history_not_armed_when_play_load_fails(self):
        """play() swallows load failures (current_track stays None); history
        reporting must not be armed for a play that never started."""
        host = _fresh_playback_host()
        host.history = MagicMock()
        host.settings.playback.sync_history_to_ytmusic = False
        host.settings.playback.history_min_listen_seconds = 5
        host.stream_resolver.resolve = AsyncMock(return_value=self._stream_info())
        # player.play mock returns without setting current_track — load failed.
        await host.play_track({"video_id": "abc", "title": "X"})

        host.set_timer.assert_not_called()
        assert host._local_history_claim is None

    async def test_history_armed_when_play_starts(self):
        host = _fresh_playback_host()
        host.history = MagicMock()
        host.settings.playback.sync_history_to_ytmusic = False
        host.settings.playback.history_min_listen_seconds = 5
        host.stream_resolver.resolve = AsyncMock(return_value=self._stream_info())
        track = {"video_id": "abc", "title": "X"}

        async def _play(url, t, attempt=None):
            host.player.current_track = t

        host.player.play = AsyncMock(side_effect=_play)
        await host.play_track(track)

        host.set_timer.assert_called_once()
        assert host._local_history_claim is not None


# ── Stream error recovery (#135) ────────────────────────────────────────────

_TRACK = {"video_id": "abc12345678", "title": "Song"}
_OTHER = {"video_id": "zyx98765432", "title": "Other"}


def _recovery_host():
    host = _fresh_playback_host()
    host.stream_resolver.resolve = AsyncMock(
        side_effect=lambda vid: SimpleNamespace(url=f"http://stream/{vid}", duration=1)
    )
    return host


def _stream_error(attempt, track, error=-1):
    """What Player dispatches when mpv's end-file reports an error for *attempt*."""
    return {"reason": 4, "error": error, "track": track, "attempt": attempt}


def _attempts(host):
    return [c.kwargs["attempt"] for c in host.player.play.await_args_list]


class TestPlayerErrorRecovery:
    async def test_first_failure_resets_resolver_and_replays_with_a_fresh_url(self):
        host = _recovery_host()
        host.stream_resolver.resolve = AsyncMock(
            side_effect=[SimpleNamespace(url="http://stale"), SimpleNamespace(url="http://fresh")]
        )
        await host.play_track(_TRACK)
        assert _attempts(host) == [1]

        await host._on_player_error(_stream_error(1, _TRACK))

        host.stream_resolver.clear_cache.assert_called_once()
        assert [c.args[0] for c in host.player.play.await_args_list] == [
            "http://stale",
            "http://fresh",
        ]
        assert host.player.play.await_args.args[1] is _TRACK
        assert _attempts(host) == [1, 2]
        assert host._recovery_generation == 2
        host.notify.assert_not_called()
        assert host._consecutive_failures == 0

    async def test_failed_recovery_applies_the_failure_policy_once(self):
        host = _recovery_host()
        host._handle_play_failure = MagicMock()
        await host.play_track(_TRACK)
        await host._on_player_error(_stream_error(1, _TRACK))
        host.stream_resolver.clear_cache.reset_mock()

        await host._on_player_error(_stream_error(2, _TRACK))

        host._handle_play_failure.assert_called_once()
        assert host._handle_play_failure.call_args.kwargs["failure_kind"] == "stream"
        host.notify.assert_called_once()
        assert host.notify.call_args.kwargs["severity"] == "error"
        host.stream_resolver.clear_cache.assert_not_called()
        assert _attempts(host) == [1, 2]  # the retry gets no retry of its own
        assert host._recovery_generation is None

    async def test_duplicate_errors_are_ignored(self):
        host = _recovery_host()
        host._handle_play_failure = MagicMock()
        await host.play_track(_TRACK)

        await host._on_player_error(_stream_error(1, _TRACK))
        await host._on_player_error(_stream_error(1, _TRACK))
        assert _attempts(host) == [1, 2]
        host.stream_resolver.clear_cache.assert_called_once()

        await host._on_player_error(_stream_error(2, _TRACK))
        await host._on_player_error(_stream_error(2, _TRACK))
        host._handle_play_failure.assert_called_once()
        assert _attempts(host) == [1, 2]

    async def test_late_error_after_a_newer_play_is_ignored(self):
        host = _recovery_host()
        host._handle_play_failure = MagicMock()
        await host.play_track(_TRACK)
        await host.play_track(_OTHER)

        await host._on_player_error(_stream_error(1, _TRACK))

        host.stream_resolver.clear_cache.assert_not_called()
        host._handle_play_failure.assert_not_called()
        host.notify.assert_not_called()
        assert _attempts(host) == [1, 2]

    async def test_late_error_after_stop_is_ignored(self):
        host = _recovery_host()
        host.player.stop = AsyncMock()
        await host.play_track(_TRACK)

        await MPRISMixin._mpris_stop(host)
        await host._on_player_error(_stream_error(1, _TRACK))

        host.player.stop.assert_awaited_once()
        host.stream_resolver.clear_cache.assert_not_called()
        assert _attempts(host) == [1]

    async def test_same_song_replay_ignores_the_old_error_and_gets_its_own_retry(self):
        host = _recovery_host()
        await host.play_track(_TRACK)
        host._last_play_video_id = ""  # the user replays after the debounce window
        await host.play_track(_TRACK)
        assert _attempts(host) == [1, 2]

        await host._on_player_error(_stream_error(1, _TRACK))
        host.stream_resolver.clear_cache.assert_not_called()
        assert _attempts(host) == [1, 2]

        await host._on_player_error(_stream_error(2, _TRACK))
        host.stream_resolver.clear_cache.assert_called_once()
        assert _attempts(host) == [1, 2, 3]
        assert host._recovery_generation == 3

    async def test_synchronous_load_failure_is_handled_once(self):
        """player.play() swallows a load failure and reports it as an ERROR
        event; play_track must not apply the failure policy on top."""
        host = _recovery_host()
        host._handle_play_failure = MagicMock()
        errors: list[dict] = []

        async def _play(url, track, attempt=None):
            errors.append(
                {
                    "reason": "load",
                    "error": RuntimeError("boom"),
                    "track": track,
                    "attempt": attempt,
                }
            )

        host.player.play = AsyncMock(side_effect=_play)

        await host.play_track(_TRACK)
        host._handle_play_failure.assert_not_called()
        assert host._consecutive_failures == 0
        assert [e["attempt"] for e in errors] == [1]

        await host._on_player_error(errors[0])
        host.stream_resolver.clear_cache.assert_called_once()
        assert [e["attempt"] for e in errors] == [1, 2]

        await host._on_player_error(errors[1])
        host._handle_play_failure.assert_called_once()
        assert [e["attempt"] for e in errors] == [1, 2]

    async def test_repeated_unplayable_tracks_reach_the_failure_limit(self):
        host = _recovery_host()
        tracks = [
            {"video_id": f"vid{i:08d}", "title": f"t{i}"} for i in range(_MAX_CONSECUTIVE_FAILURES)
        ]
        host.queue.next_track = MagicMock(side_effect=tracks[1:] + [None])
        await host.play_track(tracks[0])

        for i, track in enumerate(tracks):
            attempt = host._play_generation
            await host._on_player_error(_stream_error(attempt, track))
            # A play that never produced audio must not reset the counter.
            assert host._consecutive_failures == i
            await host._on_player_error(_stream_error(attempt + 1, track))
            if i < len(tracks) - 1:
                await host.play_track(tracks[i + 1])  # what the policy's call_later runs

        exhausted = [c for c in host.notify.call_args_list if "Multiple tracks failed" in c.args[0]]
        assert len(exhausted) == 1
        assert exhausted[0].kwargs["severity"] == "error"
        assert host._consecutive_failures == 0
        # One reset per track's first failure, plus the escalation's.
        assert host.stream_resolver.clear_cache.call_count == len(tracks) + 1

    async def _fail_twice_and_schedule_advance(self, host):
        """Initial play and its recovery both fail; the policy defers the queue advance."""
        host.queue.next_track = MagicMock(return_value=_OTHER)
        await host.play_track(_TRACK)
        await host._on_player_error(_stream_error(1, _TRACK))
        await host._on_player_error(_stream_error(2, _TRACK))
        host.call_later.assert_called_once()
        return host.call_later.call_args.args[0]

    async def _run_deferred(self, host, deferred):
        """What Textual does later: run the callback, then the worker it starts."""
        deferred()
        await host.run_worker.call_args.args[0]

    async def test_deferred_advance_plays_the_next_track_when_not_superseded(self):
        host = _recovery_host()
        deferred = await self._fail_twice_and_schedule_advance(host)

        await self._run_deferred(host, deferred)

        assert [c.args[1] for c in host.player.play.await_args_list] == [_TRACK, _TRACK, _OTHER]
        assert host._play_generation == 3

    async def test_deferred_advance_after_stop_does_not_resume_playback(self):
        host = _recovery_host()
        host.player.stop = AsyncMock()
        deferred = await self._fail_twice_and_schedule_advance(host)

        await MPRISMixin._mpris_stop(host)
        await self._run_deferred(host, deferred)

        assert _attempts(host) == [1, 2]
        assert host._play_generation == 3

    async def test_deferred_advance_after_a_newer_play_is_dropped(self):
        host = _recovery_host()
        deferred = await self._fail_twice_and_schedule_advance(host)
        chosen = {"video_id": "usr00000001", "title": "Chosen"}
        await host.play_track(chosen)

        await self._run_deferred(host, deferred)

        assert [c.args[1] for c in host.player.play.await_args_list] == [_TRACK, _TRACK, chosen]
        assert host._play_generation == 3

    def test_audio_progress_resets_failure_state(self):
        host = _recovery_host()
        host._consecutive_failures = 3
        host._recovery_generation = 4
        host.player.is_playing = True
        host.player.position = 12.5

        host._poll_position()

        assert (host._consecutive_failures, host._recovery_generation) == (0, None)

    def test_loaded_but_not_advancing_does_not_reset_failure_state(self):
        host = _recovery_host()
        host._consecutive_failures = 3
        host.player.is_playing = True
        host.player.position = 0.0

        host._poll_position()

        assert host._consecutive_failures == 3
