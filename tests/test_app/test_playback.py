"""Tests for PlaybackMixin.play_track guards and dispatch.

Three bug classes guarded against:
1. Double-click producing two play_track calls (debounce within 1 second).
2. Tracks lacking video_id (AI-generated streams) hanging the queue
   instead of being skipped.
3. Cached audio path bypasses stream resolver entirely.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from ytm_player.app._playback import PlaybackMixin


@pytest.fixture(autouse=True)
def _no_cookie_extraction_toast(monkeypatch):
    """play_track() checks claim_cookie_extraction_notification() before
    resolving — a real module-level function backed by real global state
    (see services/stream.py). Almost every test in this file reaches that
    check (stream_resolver.resolve defaults to returning None, so nothing
    short-circuits before it), and without stubbing this, whichever test
    runs first "claims" it, leaving every later test to see a different,
    run-order-dependent result. Default it to a stable False; tests that
    specifically cover the toast override it explicitly.
    """
    monkeypatch.setattr(
        "ytm_player.services.stream.claim_cookie_extraction_notification", lambda: False
    )


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
    p.stream_resolver.consume_missing_remote_components = MagicMock(return_value=False)
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
    p.push_screen = MagicMock()
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
    p._local_history_claim = None
    p._play_lock = asyncio.Lock()
    p._remote_components_prompted = False
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


class TestCookieExtractionToast:
    """play_track() shows a one-time toast if it has to pay a cookie
    extraction cost live, but only when it wins the process-wide claim —
    otherwise the startup warmup (or a racing play_track call) already
    claimed it."""

    async def test_toast_shown_when_claim_granted(self, monkeypatch):
        host = _fresh_playback_host()
        monkeypatch.setattr(
            "ytm_player.services.stream.claim_cookie_extraction_notification", lambda: True
        )
        host.stream_resolver.resolve = AsyncMock(return_value=None)

        await host.play_track({"video_id": "abc", "title": "X"})

        toast_calls = [c for c in host.notify.call_args_list if "decrypting" in c.args[0]]
        assert len(toast_calls) == 1

    async def test_no_toast_when_claim_denied(self):
        """Default fixture behaviour: claim always denied (see autouse fixture)."""
        host = _fresh_playback_host()
        host.stream_resolver.resolve = AsyncMock(return_value=None)

        await host.play_track({"video_id": "abc", "title": "X"})

        toast_calls = [c for c in host.notify.call_args_list if "decrypting" in c.args[0]]
        assert len(toast_calls) == 0


class TestMissingRemoteComponentsHandling:
    """A resolve failure caused by missing remote_components gets first-class
    handling instead of the generic 'region-locked' message — see
    _handle_missing_remote_components_failure and _show_remote_components_prompt."""

    async def test_first_occurrence_shows_prompt_instead_of_generic_error(self):
        host = _fresh_playback_host()
        host.stream_resolver.resolve = AsyncMock(return_value=None)
        host.stream_resolver.consume_missing_remote_components = MagicMock(return_value=True)

        await host.play_track({"video_id": "abc", "title": "Some Track"})

        # The generic "region-locked" message must NOT have been shown.
        generic_calls = [c for c in host.notify.call_args_list if "region-locked" in c.args[0]]
        assert generic_calls == []
        host.push_screen.assert_called_once()
        assert host._remote_components_prompted is True

    async def test_accepting_prompt_enables_setting_and_retries(self):
        host = _fresh_playback_host()
        host.stream_resolver.resolve = AsyncMock(return_value=None)
        host.stream_resolver.consume_missing_remote_components = MagicMock(return_value=True)
        host.run_worker = MagicMock()

        def _fake_push_screen(screen, callback):
            callback(True)  # simulate the user accepting

        host.push_screen = MagicMock(side_effect=_fake_push_screen)

        await host.play_track({"video_id": "abc", "title": "Some Track"})

        assert host.settings.yt_dlp.remote_components == "ejs:github"
        host.settings.save.assert_called_once()
        host.stream_resolver.clear_cache.assert_called_once()
        # Retries the same track via run_worker(play_track(track)). run_worker
        # is mocked so the coroutine it's handed is never awaited — close it
        # explicitly rather than leaking an "unawaited coroutine" warning.
        host.run_worker.assert_called_once()
        retried_coro = host.run_worker.call_args[0][0]
        retried_coro.close()

    async def test_declining_prompt_skips_with_accurate_message(self):
        host = _fresh_playback_host()
        host.stream_resolver.resolve = AsyncMock(return_value=None)
        host.stream_resolver.consume_missing_remote_components = MagicMock(return_value=True)

        def _fake_push_screen(screen, callback):
            callback(False)  # simulate the user declining

        host.push_screen = MagicMock(side_effect=_fake_push_screen)

        await host.play_track({"video_id": "abc", "title": "Some Track"})

        host.settings.save.assert_not_called()
        host.stream_resolver.clear_cache.assert_not_called()
        decline_calls = [
            c for c in host.notify.call_args_list if "JS challenge solver" in c.args[0]
        ]
        assert len(decline_calls) == 1

    async def test_second_occurrence_gives_accurate_error_and_skips(self):
        """Already prompted this session (accepted or declined) — no
        second popup; the failure message names the real cause."""
        host = _fresh_playback_host()
        host.stream_resolver.resolve = AsyncMock(return_value=None)
        host.stream_resolver.consume_missing_remote_components = MagicMock(return_value=True)
        host._remote_components_prompted = True
        host.queue.next_track = MagicMock(return_value=None)

        await host.play_track({"video_id": "abc", "title": "Some Track"})

        host.push_screen.assert_not_called()
        error_calls = [
            c
            for c in host.notify.call_args_list
            if "remote_components" in c.args[0] and "Skipping" in c.args[0]
        ]
        assert len(error_calls) == 1

    async def test_prompt_is_one_shot_across_calls(self):
        """A second call to _show_remote_components_prompt in the same
        session is a no-op — regardless of which caller triggers it."""
        host = _fresh_playback_host()
        host._remote_components_prompted = True

        host._show_remote_components_prompt(
            message="test",
            on_accept=MagicMock(),
        )

        host.push_screen.assert_not_called()


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

        async def _play(url, t):
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

        async def _play(url, t):
            host.player.current_track = t

        host.player.play = AsyncMock(side_effect=_play)
        await host.play_track(track)

        host.set_timer.assert_called_once()
        assert host._local_history_claim is not None
