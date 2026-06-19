"""Tests for artist context menu actions in the search page."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from ytm_player.app._track_actions import TrackActionsMixin


def _make_host(*, get_artist_return=None, get_watch_playlist_return=None):
    """Build a MagicMock standing in for a host app with ytmusic service."""
    host = MagicMock()
    host.ytmusic = MagicMock()
    host.ytmusic.get_artist = AsyncMock(return_value=get_artist_return or {})
    host.ytmusic.get_watch_playlist = AsyncMock(return_value=get_watch_playlist_return or [])
    host.ytmusic.get_playlist = AsyncMock(return_value={})
    host.ytmusic.subscribe_artist = AsyncMock(return_value="success")
    host.ytmusic.unsubscribe_artist = AsyncMock(return_value="success")
    host.queue = MagicMock()
    host.queue.next_track = MagicMock(return_value={"video_id": "v1", "title": "Track 1"})
    host.play_track = AsyncMock()
    host._fetch_and_play_radio = AsyncMock()
    host._replace_queue_and_play = AsyncMock()
    host._append_to_queue = MagicMock()
    host._sync_shuffle_bar = MagicMock()
    host._refresh_queue_page = MagicMock()
    host.shuffle_prefs = MagicMock()
    host.shuffle_prefs.get = MagicMock(return_value=False)
    host.notify = MagicMock()
    host.run_worker = MagicMock()
    return host


# ── start_radio tests ──────────────────────────────────────────────────


class TestStartArtistRadio:
    async def test_uses_radio_id(self):
        """Primary path: uses radioId from get_artist with get_watch_playlist."""
        host = _make_host(
            get_artist_return={
                "name": "Test Artist",
                "radioId": "RDAMPL_test",
                "songs": {"results": []},
            },
            get_watch_playlist_return=[
                {"videoId": "r1", "title": "Radio 1", "artists": [{"name": "A", "id": "a1"}]},
                {"videoId": "r2", "title": "Radio 2", "artists": [{"name": "A", "id": "a1"}]},
            ],
        )
        await TrackActionsMixin._start_artist_radio(host, "UC_test")

        host.ytmusic.get_artist.assert_awaited_once_with("UC_test")
        host.ytmusic.get_watch_playlist.assert_awaited_once_with(
            playlist_id="RDAMPL_test", radio=True
        )
        host.queue.clear.assert_called_once()
        host.queue.set_context.assert_called_once_with(None)
        host.queue.set_radio_tracks.assert_called_once()
        host.play_track.assert_awaited_once()

    async def test_fallback_to_top_songs(self):
        """When radioId absent, uses top songs as seeds."""
        host = _make_host(
            get_artist_return={
                "name": "Test Artist",
                "songs": {
                    "results": [
                        {"videoId": "s1", "title": "Song 1"},
                        {"videoId": "s2", "title": "Song 2"},
                    ]
                },
            },
        )
        await TrackActionsMixin._start_artist_radio(host, "UC_test")

        host._fetch_and_play_radio.assert_awaited_once()
        call_args = host._fetch_and_play_radio.call_args
        seeds = call_args[0][0]
        assert len(seeds) == 2
        assert call_args[1]["label"] == "Radio from Test Artist"

    async def test_artist_fetch_failure(self):
        """When get_artist returns empty, shows warning."""
        host = _make_host(get_artist_return={})
        await TrackActionsMixin._start_artist_radio(host, "UC_test")

        host.notify.assert_any_call("Couldn't load artist data.", severity="warning", timeout=3)
        host.queue.clear.assert_not_called()

    async def test_empty_watch_playlist(self):
        """When radioId present but returns empty tracks, shows warning."""
        host = _make_host(
            get_artist_return={"name": "Test", "radioId": "RDAMPL_test"},
            get_watch_playlist_return=[],
        )
        await TrackActionsMixin._start_artist_radio(host, "UC_test")

        host.notify.assert_any_call(
            "No radio suggestions available.", severity="warning", timeout=3
        )

    async def test_no_songs_to_seed(self):
        """When no radioId and no playable songs, shows warning."""
        host = _make_host(
            get_artist_return={"name": "Empty", "songs": {"results": []}},
        )
        await TrackActionsMixin._start_artist_radio(host, "UC_test")

        host.notify.assert_any_call("No songs to seed radio.", severity="warning", timeout=3)


# ── play_top_songs tests ──────────────────────────────────────────────


class TestPlayArtistTopSongs:
    async def test_queues_and_plays(self):
        """Fetches artist, queues top songs, starts playback."""
        host = _make_host(
            get_artist_return={
                "name": "Artist X",
                "songs": {
                    "results": [
                        {
                            "videoId": "s1",
                            "title": "Hit 1",
                            "artists": [{"name": "Artist X", "id": "ax"}],
                        },
                        {
                            "videoId": "s2",
                            "title": "Hit 2",
                            "artists": [{"name": "Artist X", "id": "ax"}],
                        },
                    ],
                },
            },
        )
        host._replace_queue_and_play = AsyncMock()
        await TrackActionsMixin._play_artist_top_songs(host, "UC_ax")

        host._replace_queue_and_play.assert_awaited_once()
        call_kwargs = host._replace_queue_and_play.call_args[1]
        assert call_kwargs.get("shuffle") is None
        host.notify.assert_any_call("Playing top songs from Artist X", timeout=4)

    async def test_background_fetch_triggered(self):
        """When songs.browseId present, triggers background fetch."""
        host = _make_host(
            get_artist_return={
                "name": "Artist X",
                "songs": {
                    "browseId": "VLPLfull",
                    "results": [
                        {"videoId": "s1", "title": "Hit 1", "artists": [{"name": "X", "id": "x"}]},
                    ],
                },
            },
        )
        host._replace_queue_and_play = AsyncMock()
        await TrackActionsMixin._play_artist_top_songs(host, "UC_ax")

        host.run_worker.assert_called_once()

    async def test_no_songs(self):
        """When artist has no songs, shows warning."""
        host = _make_host(
            get_artist_return={"name": "Empty Artist", "songs": {"results": []}},
        )
        host._replace_queue_and_play = AsyncMock()
        await TrackActionsMixin._play_artist_top_songs(host, "UC_empty")

        host.notify.assert_any_call(
            "No songs found for this artist.", severity="warning", timeout=3
        )
        host._replace_queue_and_play.assert_not_awaited()

    async def test_artist_failure(self):
        """When get_artist returns empty, shows warning."""
        host = _make_host(get_artist_return={})
        host._replace_queue_and_play = AsyncMock()
        await TrackActionsMixin._play_artist_top_songs(host, "UC_fail")

        host.notify.assert_any_call("Couldn't load artist data.", severity="warning", timeout=3)


# ── toggle_subscribe tests ────────────────────────────────────────────


class TestToggleArtistSubscribe:
    async def test_subscribe_success(self):
        """Subscribe to an unsubscribed artist notifies success."""
        host = _make_host(
            get_artist_return={"name": "Sub Artist", "channelId": "UCsub123", "subscribed": False},
        )
        await TrackActionsMixin._toggle_artist_subscribe_simple(host, "UC_sub")

        host.ytmusic.subscribe_artist.assert_awaited_once_with("UCsub123")
        host.notify.assert_any_call("Subscribed", timeout=2)

    async def test_unsubscribe_success(self):
        """Unsubscribe from a subscribed artist notifies success."""
        host = _make_host(
            get_artist_return={"name": "Sub Artist", "channelId": "UCsub123", "subscribed": True},
        )
        await TrackActionsMixin._toggle_artist_subscribe_simple(host, "UC_sub")

        host.ytmusic.unsubscribe_artist.assert_awaited_once_with("UCsub123")
        host.notify.assert_any_call("Unsubscribed", timeout=2)

    async def test_subscribe_failure(self):
        """Subscribe failure shows error notification."""
        host = _make_host(
            get_artist_return={"name": "Sub Artist", "channelId": "UCsub123", "subscribed": False},
        )
        host.ytmusic.subscribe_artist = AsyncMock(return_value="network")
        await TrackActionsMixin._toggle_artist_subscribe_simple(host, "UC_sub")

        host.notify.assert_any_call(
            "Couldn't subscribe — check your connection",
            severity="error",
            timeout=3,
        )

    async def test_missing_channel_id(self):
        """When artist data lacks channelId key, shows warning."""
        host = _make_host(
            get_artist_return={"name": "No Channel"},
        )
        await TrackActionsMixin._toggle_artist_subscribe_simple(host, "UC_noid")

        host.notify.assert_any_call("Couldn't load artist data.", severity="warning", timeout=3)

    async def test_empty_channel_id(self):
        """When channelId is empty string, shows warning instead of sending empty to API."""
        host = _make_host(
            get_artist_return={"name": "Bad Data", "channelId": ""},
        )
        await TrackActionsMixin._toggle_artist_subscribe_simple(host, "UC_bad")

        host.notify.assert_any_call("Couldn't load artist data.", severity="warning", timeout=3)
        host.ytmusic.subscribe_artist.assert_not_awaited()

    async def test_artist_fetch_failure(self):
        """When get_artist returns empty, shows warning."""
        host = _make_host(get_artist_return={})
        await TrackActionsMixin._toggle_artist_subscribe_simple(host, "UC_fail")

        host.notify.assert_any_call("Couldn't load artist data.", severity="warning", timeout=3)
        host.ytmusic.subscribe_artist.assert_not_awaited()

    async def test_unsubscribe_failure(self):
        """Unsubscribe failure shows error notification."""
        host = _make_host(
            get_artist_return={"name": "Sub Artist", "channelId": "UCsub123", "subscribed": True},
        )
        host.ytmusic.unsubscribe_artist = AsyncMock(return_value="server_error")
        await TrackActionsMixin._toggle_artist_subscribe_simple(host, "UC_sub")

        host.notify.assert_any_call(
            "Couldn't unsubscribe — YouTube Music had a problem, try again",
            severity="error",
            timeout=3,
        )


# ── _fetch_remaining_artist_songs tests ───────────────────────────────


class TestFetchRemainingArtistSongs:
    async def test_appends_new_tracks_and_enriches_initial(self):
        """Fetches playlist, enriches initial tracks with durations, appends new ones."""
        host = _make_host()
        host.ytmusic.get_playlist = AsyncMock(
            return_value={
                "tracks": [
                    {
                        "videoId": "s1",
                        "title": "Existing",
                        "duration_seconds": 210,
                        "artists": [{"name": "A", "id": "a"}],
                    },
                    {"videoId": "s2", "title": "New One", "artists": [{"name": "A", "id": "a"}]},
                    {"videoId": "s3", "title": "New Two", "artists": [{"name": "A", "id": "a"}]},
                ]
            }
        )
        queue_track = {"video_id": "s1", "title": "Existing"}
        host.queue.tracks = (queue_track,)
        initial = [{"video_id": "s1", "title": "Existing"}]
        await TrackActionsMixin._fetch_remaining_artist_songs(host, "VLPLfull", initial)

        host.ytmusic.get_playlist.assert_awaited_once_with("VLPLfull")
        assert host.queue.add.call_count == 2
        assert queue_track.get("duration") == 210
        host._refresh_queue_page.assert_called_once()

    async def test_bails_when_queue_replaced(self):
        """Skips enrichment if the queue no longer contains initial tracks."""
        host = _make_host()
        host.ytmusic.get_playlist = AsyncMock(
            return_value={
                "tracks": [
                    {"videoId": "s1", "title": "Old", "artists": [{"name": "A", "id": "a"}]},
                ]
            }
        )
        host.queue.tracks = ({"video_id": "different", "title": "New Content"},)
        initial = [{"video_id": "s1", "title": "Old"}]
        await TrackActionsMixin._fetch_remaining_artist_songs(host, "VLPLfull", initial)

        host.queue.add.assert_not_called()
        host._refresh_queue_page.assert_not_called()

    async def test_handles_fetch_failure_silently(self):
        """On exception, does not crash and does not modify queue."""
        host = _make_host()
        host.ytmusic.get_playlist = AsyncMock(side_effect=Exception("API down"))
        initial = [{"video_id": "s1", "title": "Track"}]
        await TrackActionsMixin._fetch_remaining_artist_songs(host, "VLPLfull", initial)

        host.queue.add.assert_not_called()


# ── _replace_queue_and_play shuffle tri-state tests ──────────────────


class TestReplaceQueueAndPlay:
    def _make_host(self, shuffle_locked=False):
        host = MagicMock()
        host.queue = MagicMock()
        host.queue.shuffle_enabled = False
        host.shuffle_prefs = MagicMock()
        host.shuffle_prefs.get = MagicMock(return_value=shuffle_locked)
        host.play_track = AsyncMock()
        host._refresh_queue_page = MagicMock()
        host._sync_shuffle_bar = MagicMock()
        host.notify = MagicMock()
        return host

    async def test_empty_tracks_notifies_and_returns(self):
        host = self._make_host()
        await TrackActionsMixin._replace_queue_and_play(host, [])
        host.notify.assert_called_once()
        host.queue.clear.assert_not_called()

    async def test_autoplay_true_calls_play_track(self):
        host = self._make_host()
        tracks = [{"video_id": "v1", "title": "T1"}]
        await TrackActionsMixin._replace_queue_and_play(host, tracks, autoplay=True)
        host.play_track.assert_awaited_once()

    async def test_autoplay_false_skips_play_track(self):
        host = self._make_host()
        tracks = [{"video_id": "v1", "title": "T1"}]
        await TrackActionsMixin._replace_queue_and_play(host, tracks, autoplay=False)
        host.play_track.assert_not_awaited()

    async def test_set_context_called_with_none(self):
        host = self._make_host()
        tracks = [{"video_id": "v1", "title": "T1"}]
        await TrackActionsMixin._replace_queue_and_play(host, tracks, entity_id=None)
        host.queue.set_context.assert_called_once_with(None)

    async def test_shuffle_none_no_lock_leaves_shuffle_unchanged(self):
        host = self._make_host(shuffle_locked=False)
        tracks = [{"video_id": "v1"}]
        await TrackActionsMixin._replace_queue_and_play(host, tracks, shuffle=None)
        host.queue.toggle_shuffle.assert_not_called()

    async def test_shuffle_none_with_lock_enables_shuffle(self):
        host = self._make_host(shuffle_locked=True)
        host.queue.shuffle_enabled = False
        tracks = [{"video_id": "v1"}]
        await TrackActionsMixin._replace_queue_and_play(host, tracks, entity_id="pid", shuffle=None)
        host.queue.toggle_shuffle.assert_called_once()

    async def test_shuffle_true_shuffles_tracks_not_mode(self):
        host = self._make_host(shuffle_locked=False)
        tracks = [{"video_id": f"v{i}"} for i in range(20)]
        await TrackActionsMixin._replace_queue_and_play(host, tracks, shuffle=True)
        host.queue.toggle_shuffle.assert_not_called()
        loaded = host.queue.add_multiple.call_args[0][0]
        assert len(loaded) == 20
        assert loaded != tracks  # shuffled order (probabilistic but 1/20! chance of false positive)

    async def test_shuffle_false_no_lock_disables_shuffle(self):
        host = self._make_host(shuffle_locked=False)
        host.queue.shuffle_enabled = True
        tracks = [{"video_id": "v1"}]
        await TrackActionsMixin._replace_queue_and_play(host, tracks, shuffle=False)
        host.queue.toggle_shuffle.assert_called_once()

    async def test_shuffle_false_lock_set_lock_wins(self):
        # Lock overrides explicit shuffle=False: shuffle gets toggled ON.
        host = self._make_host(shuffle_locked=True)
        host.queue.shuffle_enabled = False
        tracks = [{"video_id": "v1"}]
        await TrackActionsMixin._replace_queue_and_play(
            host, tracks, entity_id="pid", shuffle=False
        )
        host.queue.toggle_shuffle.assert_called_once()

    async def test_shuffle_true_lock_set_both_agree(self):
        host = self._make_host(shuffle_locked=True)
        host.queue.shuffle_enabled = False
        tracks = [{"video_id": "v1"}]
        await TrackActionsMixin._replace_queue_and_play(host, tracks, entity_id="pid", shuffle=True)
        host.queue.toggle_shuffle.assert_called_once()

    async def test_start_index_nonzero(self):
        host = self._make_host()
        tracks = [{"video_id": "v1"}, {"video_id": "v2"}, {"video_id": "v3"}]
        await TrackActionsMixin._replace_queue_and_play(host, tracks, start_index=2)
        host.queue.jump_to_real.assert_called_once_with(2)

    async def test_entity_id_set_no_lock_shuffle_none_no_toggle(self):
        host = self._make_host(shuffle_locked=False)
        tracks = [{"video_id": "v1"}]
        await TrackActionsMixin._replace_queue_and_play(
            host, tracks, entity_id="some-playlist", shuffle=None
        )
        host.queue.set_context.assert_called_once_with("some-playlist")
        host.queue.toggle_shuffle.assert_not_called()


# ── _play_playlist tests ──────────────────────────────────────────────


class TestPlayPlaylist:
    def _make_host(self, get_playlist_return=None):
        host = MagicMock()
        host.ytmusic = MagicMock()
        host.ytmusic.get_playlist = AsyncMock(return_value=get_playlist_return or {})
        host._replace_queue_and_play = AsyncMock()
        host.notify = MagicMock()
        host.run_worker = MagicMock()
        return host

    async def test_plays_playlist(self):
        host = self._make_host(
            get_playlist_return={
                "tracks": [
                    {"videoId": "v1", "title": "T1", "artists": [{"name": "A", "id": "a"}]},
                ],
                "trackCount": 1,
            }
        )
        await TrackActionsMixin._play_playlist(host, "PL1", "My Playlist")

        host._replace_queue_and_play.assert_awaited_once()
        kwargs = host._replace_queue_and_play.call_args[1]
        assert kwargs["entity_id"] == "PL1"
        host.notify.assert_any_call("Playing: My Playlist", timeout=4)

    async def test_empty_playlist_warns(self):
        host = self._make_host(get_playlist_return={"tracks": [], "trackCount": 0})
        await TrackActionsMixin._play_playlist(host, "PL1", "Empty")

        host._replace_queue_and_play.assert_not_awaited()
        host.notify.assert_any_call("Playlist is empty", severity="warning")

    async def test_background_fetch_triggered_when_more_tracks(self):
        host = self._make_host(
            get_playlist_return={
                "tracks": [
                    {"videoId": "v1", "title": "T1", "artists": [{"name": "A", "id": "a"}]},
                ],
                "trackCount": 100,
            }
        )
        await TrackActionsMixin._play_playlist(host, "PL1", "Big Playlist")

        host.run_worker.assert_called_once()

    async def test_no_background_fetch_when_all_loaded(self):
        host = self._make_host(
            get_playlist_return={
                "tracks": [
                    {"videoId": "v1", "title": "T1", "artists": [{"name": "A", "id": "a"}]},
                ],
                "trackCount": 1,
            }
        )
        await TrackActionsMixin._play_playlist(host, "PL1", "Small")

        host.run_worker.assert_not_called()

    async def test_error_shows_notification(self):
        host = MagicMock()
        host.ytmusic = MagicMock()
        host.ytmusic.get_playlist = AsyncMock(side_effect=Exception("API error"))
        host.notify = MagicMock()
        await TrackActionsMixin._play_playlist(host, "PL1", "Fail")

        host.notify.assert_any_call("Failed to load playlist", severity="error")

    async def test_shuffle_kwarg_propagated(self):
        host = self._make_host(
            get_playlist_return={
                "tracks": [
                    {"videoId": "v1", "title": "T1", "artists": [{"name": "A", "id": "a"}]},
                ],
                "trackCount": 1,
            }
        )
        await TrackActionsMixin._play_playlist(host, "PL1", "Shuffled", shuffle=True)

        kwargs = host._replace_queue_and_play.call_args[1]
        assert kwargs["shuffle"] is True


# ── _add_playlist_to_queue tests ─────────────────────────────────────


class TestAddPlaylistToQueue:
    async def test_appends_with_name(self):
        host = MagicMock()
        host.ytmusic = MagicMock()
        host.ytmusic.get_playlist = AsyncMock(
            return_value={
                "tracks": [
                    {"videoId": "v1", "title": "T1", "artists": [{"name": "A", "id": "a"}]},
                ]
            }
        )
        host._append_to_queue = MagicMock()
        host.notify = MagicMock()
        await TrackActionsMixin._add_playlist_to_queue(host, "PL1", "My Playlist")

        host._append_to_queue.assert_called_once()
        args = host._append_to_queue.call_args[0]
        assert args[1] == "My Playlist"

    async def test_empty_playlist_warns(self):
        host = MagicMock()
        host.ytmusic = MagicMock()
        host.ytmusic.get_playlist = AsyncMock(return_value={"tracks": []})
        host._append_to_queue = MagicMock()
        host.notify = MagicMock()
        await TrackActionsMixin._add_playlist_to_queue(host, "PL1", "Empty")

        host._append_to_queue.assert_not_called()
        host.notify.assert_any_call("Playlist is empty", severity="warning", timeout=2)

    async def test_exception_shows_error(self):
        host = MagicMock()
        host.ytmusic = MagicMock()
        host.ytmusic.get_playlist = AsyncMock(side_effect=Exception("API error"))
        host.notify = MagicMock()
        await TrackActionsMixin._add_playlist_to_queue(host, "PL1", "Fail")

        host.notify.assert_any_call("Failed to add to queue", severity="error", timeout=2)


# ── _dispatch_entity_action tests ────────────────────────────────────


class TestDispatchEntityAction:
    def _make_host(self):
        host = MagicMock()
        host._play_album = AsyncMock()
        host._play_playlist = AsyncMock()
        host._add_album_to_queue = AsyncMock()
        host._add_playlist_to_queue = AsyncMock()
        host._start_artist_radio = AsyncMock()
        host._start_playlist_radio = AsyncMock()
        host._play_artist_top_songs = AsyncMock()
        host._add_album_to_library = AsyncMock()
        host.navigate_to = AsyncMock()
        host.notify = MagicMock()
        host.run_worker = MagicMock()
        return host

    async def test_play_all_album_dispatches(self):
        host = self._make_host()
        item = {"browseId": "ALB1", "title": "Album"}
        result = await TrackActionsMixin._dispatch_entity_action(host, "play_all", item, "album")
        assert result is True
        host._play_album.assert_awaited_once_with("ALB1", "Album", shuffle=False)

    async def test_play_all_playlist_dispatches(self):
        host = self._make_host()
        item = {"browseId": "PL1", "title": "Playlist"}
        result = await TrackActionsMixin._dispatch_entity_action(host, "play_all", item, "playlist")
        assert result is True
        host._play_playlist.assert_awaited_once_with("PL1", "Playlist", shuffle=False)

    async def test_shuffle_play_album_dispatches(self):
        host = self._make_host()
        item = {"browseId": "ALB1", "title": "Album"}
        result = await TrackActionsMixin._dispatch_entity_action(
            host, "shuffle_play", item, "album"
        )
        assert result is True
        host._play_album.assert_awaited_once_with("ALB1", "Album", shuffle=True)

    async def test_shuffle_play_playlist_dispatches(self):
        host = self._make_host()
        item = {"browseId": "PL1", "title": "Playlist"}
        result = await TrackActionsMixin._dispatch_entity_action(
            host, "shuffle_play", item, "playlist"
        )
        assert result is True
        host._play_playlist.assert_awaited_once_with("PL1", "Playlist", shuffle=True)

    async def test_add_to_queue_album_no_id_notifies(self):
        host = self._make_host()
        item = {"title": "No ID"}
        result = await TrackActionsMixin._dispatch_entity_action(
            host, "add_to_queue", item, "album"
        )
        assert result is True
        host.notify.assert_called_once()
        host._add_album_to_queue.assert_not_awaited()

    async def test_start_radio_artist_no_id_notifies(self):
        host = self._make_host()
        item = {"title": "No ID"}
        result = await TrackActionsMixin._dispatch_entity_action(
            host, "start_radio", item, "artist"
        )
        assert result is True
        host.notify.assert_called_once()
        host._start_artist_radio.assert_not_awaited()

    async def test_start_radio_artist_dispatches(self):
        host = self._make_host()
        item = {"browseId": "UC1", "artist": "Artist"}
        result = await TrackActionsMixin._dispatch_entity_action(
            host, "start_radio", item, "artist"
        )
        assert result is True
        host._start_artist_radio.assert_awaited_once_with("UC1")

    async def test_start_radio_playlist_dispatches(self):
        host = self._make_host()
        item = {"browseId": "PL1", "title": "Playlist"}
        result = await TrackActionsMixin._dispatch_entity_action(
            host, "start_radio", item, "playlist"
        )
        assert result is True
        host._start_playlist_radio.assert_awaited_once_with(item)

    async def test_copy_link_returns_false(self):
        host = self._make_host()
        item = {"browseId": "X", "title": "X"}
        result = await TrackActionsMixin._dispatch_entity_action(host, "copy_link", item, "album")
        assert result is False

    async def test_toggle_subscribe_returns_false(self):
        host = self._make_host()
        item = {"browseId": "X"}
        result = await TrackActionsMixin._dispatch_entity_action(
            host, "toggle_subscribe", item, "artist"
        )
        assert result is False

    async def test_missing_entity_id_notifies(self):
        host = self._make_host()
        item = {"title": "No ID"}
        result = await TrackActionsMixin._dispatch_entity_action(host, "play_all", item, "album")
        host.notify.assert_called_once()
        assert result is True

    async def test_entity_id_from_playlist_id_field(self):
        host = self._make_host()
        item = {"playlistId": "PL_via_playlistId", "title": "P"}
        result = await TrackActionsMixin._dispatch_entity_action(host, "play_all", item, "playlist")
        assert result is True
        host._play_playlist.assert_awaited_once_with("PL_via_playlistId", "P", shuffle=False)

    async def test_unhandled_action_returns_false(self):
        host = self._make_host()
        item = {"browseId": "X", "title": "X"}
        result = await TrackActionsMixin._dispatch_entity_action(
            host, "unknown_action_xyz", item, "album"
        )
        assert result is False

    async def test_add_to_queue_album_dispatches(self):
        host = self._make_host()
        item = {"browseId": "ALB1", "title": "Album"}
        result = await TrackActionsMixin._dispatch_entity_action(
            host, "add_to_queue", item, "album"
        )
        assert result is True
        host._add_album_to_queue.assert_awaited_once_with("ALB1", "Album")

    async def test_add_to_queue_playlist_dispatches(self):
        host = self._make_host()
        item = {"playlistId": "PL1", "title": "Playlist"}
        result = await TrackActionsMixin._dispatch_entity_action(
            host, "add_to_queue", item, "playlist"
        )
        assert result is True
        host._add_playlist_to_queue.assert_awaited_once_with("PL1", "Playlist")


# ── _open_actions_for_artist / _open_actions_for_album tests ─────────


class TestOpenActionsForArtist:
    def test_no_artists_warns(self):
        """Track with no artists list shows warning."""
        host = MagicMock()
        TrackActionsMixin._open_actions_for_artist(host, {"title": "Track"})
        host.notify.assert_called_once_with(
            "No artist info available.", severity="warning", timeout=2
        )
        host.push_screen.assert_not_called()

    def test_no_valid_artists_warns(self):
        """Track with artists lacking IDs shows warning."""
        host = MagicMock()
        track = {"artists": [{"name": "A"}, {"name": "B"}]}
        TrackActionsMixin._open_actions_for_artist(host, track)
        host.notify.assert_called_once_with(
            "No artist info available.", severity="warning", timeout=2
        )

    def test_single_valid_artist_shows_actions(self):
        """Single artist with ID delegates to _show_artist_actions."""
        host = MagicMock()
        track = {"artists": [{"name": "A", "id": "UC1"}]}
        TrackActionsMixin._open_actions_for_artist(host, track)
        host._show_artist_actions.assert_called_once_with({"name": "A", "id": "UC1"})

    def test_multi_valid_artists_shows_picker(self):
        """Multiple artists with IDs delegates to _show_artist_picker."""
        host = MagicMock()
        track = {"artists": [{"name": "A", "id": "UC1"}, {"name": "B", "id": "UC2"}]}
        TrackActionsMixin._open_actions_for_artist(host, track)
        host._show_artist_picker.assert_called_once()


class TestOpenActionsForAlbum:
    def test_no_album_id_warns(self):
        """Track with no album ID shows warning."""
        host = MagicMock()
        TrackActionsMixin._open_actions_for_album(host, {"title": "Track"})
        host.notify.assert_called_once_with(
            "No album info available.", severity="warning", timeout=2
        )
        host.push_screen.assert_not_called()

    def test_album_id_from_track_field(self):
        """Album ID from track's album_id field opens popup."""
        host = MagicMock()
        track = {"album_id": "ALB1", "album": "My Album", "artists": [{"name": "A", "id": "a"}]}
        TrackActionsMixin._open_actions_for_album(host, track)
        host.push_screen.assert_called_once()

    def test_album_id_from_album_dict(self):
        """Album ID from nested album dict opens popup."""
        host = MagicMock()
        track = {"album": {"name": "My Album", "id": "ALB2"}}
        TrackActionsMixin._open_actions_for_album(host, track)
        host.push_screen.assert_called_once()


# ── _build_actions source filtering tests ────────────────────────────


class TestBuildActionsSourceFilter:
    def test_delete_hidden_for_playlist_in_search(self):
        from ytm_player.ui.popups.actions import _build_actions

        item = {"title": "P"}
        actions = _build_actions(item, "playlist", source="search")
        action_ids = [a[0] for a in actions]
        assert "delete" not in action_ids

    def test_delete_shown_for_playlist_with_default_source(self):
        from ytm_player.ui.popups.actions import _build_actions

        item = {"title": "P"}
        actions = _build_actions(item, "playlist")
        action_ids = [a[0] for a in actions]
        assert "delete" in action_ids

    def test_other_actions_not_filtered_by_search_source(self):
        from ytm_player.ui.popups.actions import _build_actions

        item = {"title": "P"}
        actions = _build_actions(item, "playlist", source="search")
        action_ids = [a[0] for a in actions]
        assert "play_all" in action_ids
        assert "shuffle_play" in action_ids
        assert "add_to_queue" in action_ids


# ── _start_playlist_radio tests ───────────────────────────────────────


class TestStartPlaylistRadio:
    def _make_host(self, radio_return=None, radio_side_effect=None):
        host = MagicMock()
        host.ytmusic = MagicMock()
        if radio_side_effect is not None:
            host.ytmusic.get_playlist_radio = AsyncMock(side_effect=radio_side_effect)
        else:
            host.ytmusic.get_playlist_radio = AsyncMock(return_value=radio_return or [])
        host.queue = MagicMock()
        host.queue.shuffle_enabled = False
        host.queue.next_track = MagicMock(return_value={"video_id": "r1", "title": "Radio 1"})
        host.play_track = AsyncMock()
        host.shuffle_prefs = MagicMock()
        host.shuffle_prefs.get = MagicMock(return_value=False)
        host._sync_shuffle_bar = MagicMock()
        host._refresh_queue_page = MagicMock()
        host.notify = MagicMock()
        return host

    async def test_happy_path(self):
        """Happy path: queue is cleared, radio tracks set, first track played."""
        host = self._make_host(
            radio_return=[
                {"videoId": "r1", "title": "Radio 1", "artists": [{"name": "A", "id": "a"}]},
                {"videoId": "r2", "title": "Radio 2", "artists": [{"name": "A", "id": "a"}]},
            ]
        )
        item = {"playlistId": "PL1", "title": "My Playlist"}
        await TrackActionsMixin._start_playlist_radio(host, item)

        host.ytmusic.get_playlist_radio.assert_awaited_once_with("PL1")
        host.queue.clear.assert_called_once()
        host.queue.set_radio_tracks.assert_called_once()
        host.queue.set_context.assert_called_once_with("PL1")
        host._refresh_queue_page.assert_called_once()
        host._sync_shuffle_bar.assert_called_once()
        host.queue.next_track.assert_called_once()
        host.play_track.assert_awaited_once()

    async def test_empty_radio_tracks_warns(self):
        """When get_playlist_radio returns empty, shows warning."""
        host = self._make_host(radio_return=[])
        item = {"playlistId": "PL1", "title": "Empty"}
        await TrackActionsMixin._start_playlist_radio(host, item)

        host.queue.clear.assert_not_called()
        host.notify.assert_any_call("No radio tracks found", severity="warning", timeout=3)

    async def test_exception_shows_error_notification(self):
        """When get_playlist_radio raises, shows error notification."""
        host = self._make_host(radio_side_effect=Exception("API down"))
        item = {"playlistId": "PL1", "title": "Fail"}
        await TrackActionsMixin._start_playlist_radio(host, item)

        host.queue.clear.assert_not_called()
        host.notify.assert_any_call("Failed to start radio", severity="error")

    async def test_missing_playlist_id_returns_early(self):
        """When item has neither playlistId nor browseId, returns without API call."""
        host = self._make_host()
        item = {"title": "No ID"}
        await TrackActionsMixin._start_playlist_radio(host, item)

        host.ytmusic.get_playlist_radio.assert_not_awaited()
        host.notify.assert_not_called()

    async def test_falls_back_to_browse_id(self):
        """Uses browseId when playlistId is absent."""
        host = self._make_host(
            radio_return=[
                {"videoId": "r1", "title": "Track", "artists": [{"name": "A", "id": "a"}]},
            ]
        )
        item = {"browseId": "PL_browse", "title": "Playlist"}
        await TrackActionsMixin._start_playlist_radio(host, item)

        host.ytmusic.get_playlist_radio.assert_awaited_once_with("PL_browse")
        host.queue.clear.assert_called_once()


# ── _fetch_remaining_for_queue tests ─────────────────────────────────


class TestFetchRemainingForQueue:
    def _make_host(self, remaining_return=None, remaining_side_effect=None):
        host = MagicMock()
        host.ytmusic = MagicMock()
        if remaining_side_effect is not None:
            host.ytmusic.get_playlist_remaining = AsyncMock(side_effect=remaining_side_effect)
        else:
            host.ytmusic.get_playlist_remaining = AsyncMock(return_value=remaining_return or [])
        host.queue = MagicMock()
        return host

    async def test_happy_path_appends_tracks(self):
        """Fetches remaining tracks and appends them to the queue."""
        host = self._make_host(
            remaining_return=[
                {"videoId": "v2", "title": "T2", "artists": [{"name": "A", "id": "a"}]},
                {"videoId": "v3", "title": "T3", "artists": [{"name": "A", "id": "a"}]},
            ]
        )
        await TrackActionsMixin._fetch_remaining_for_queue(host, "PL1", 1)

        host.ytmusic.get_playlist_remaining.assert_awaited_once_with("PL1", 1, order=None)
        host.queue.add_multiple.assert_called_once()
        added = host.queue.add_multiple.call_args[0][0]
        assert len(added) == 2

    async def test_empty_result_skips_queue_modification(self):
        """When remaining is empty, queue.add_multiple is not called."""
        host = self._make_host(remaining_return=[])
        await TrackActionsMixin._fetch_remaining_for_queue(host, "PL1", 10)

        host.queue.add_multiple.assert_not_called()

    async def test_exception_fails_silently(self):
        """On exception, does not crash and does not modify queue."""
        host = self._make_host(remaining_side_effect=Exception("API down"))
        await TrackActionsMixin._fetch_remaining_for_queue(host, "PL1", 5)

        host.queue.add_multiple.assert_not_called()

    async def test_order_kwarg_passed_through(self):
        """The order parameter is forwarded to get_playlist_remaining."""
        host = self._make_host(remaining_return=[])
        await TrackActionsMixin._fetch_remaining_for_queue(host, "PL1", 0, order="a_to_z")

        host.ytmusic.get_playlist_remaining.assert_awaited_once_with("PL1", 0, order="a_to_z")


# ── additional _dispatch_entity_action routing tests ─────────────────


class TestDispatchEntityActionExtended:
    def _make_host(self):
        host = MagicMock()
        host._play_album = AsyncMock()
        host._play_playlist = AsyncMock()
        host._add_album_to_queue = AsyncMock()
        host._add_playlist_to_queue = AsyncMock()
        host._start_artist_radio = AsyncMock()
        host._start_playlist_radio = AsyncMock()
        host._play_artist_top_songs = AsyncMock()
        host._add_album_to_library = AsyncMock()
        host.navigate_to = AsyncMock()
        host.notify = MagicMock()
        host.run_worker = MagicMock()
        return host

    async def test_go_to_artist_uses_artists_id(self):
        """go_to_artist with artists[0].id navigates to artist context."""
        host = self._make_host()
        item = {"browseId": "X", "artists": [{"name": "A", "id": "UC_artist"}]}
        result = await TrackActionsMixin._dispatch_entity_action(
            host, "go_to_artist", item, "track"
        )
        assert result is True
        host.navigate_to.assert_awaited_once_with(
            "context", context_type="artist", context_id="UC_artist"
        )

    async def test_go_to_artist_falls_back_to_browse_id(self):
        """go_to_artist with artists[0].browseId (no id key) still navigates."""
        host = self._make_host()
        item = {"browseId": "X", "artists": [{"name": "A", "browseId": "UC_browse"}]}
        result = await TrackActionsMixin._dispatch_entity_action(
            host, "go_to_artist", item, "track"
        )
        assert result is True
        host.navigate_to.assert_awaited_once_with(
            "context", context_type="artist", context_id="UC_browse"
        )

    async def test_go_to_artist_no_id_notifies(self):
        """go_to_artist with no resolvable ID shows error notification."""
        host = self._make_host()
        item = {"artists": [{"name": "A"}]}
        result = await TrackActionsMixin._dispatch_entity_action(
            host, "go_to_artist", item, "track"
        )
        assert result is True
        host.notify.assert_called_once()
        host.navigate_to.assert_not_awaited()

    async def test_go_to_album_dispatches(self):
        """go_to_album navigates to album context."""
        host = self._make_host()
        item = {"album_id": "ALB123", "title": "My Album"}
        result = await TrackActionsMixin._dispatch_entity_action(host, "go_to_album", item, "track")
        assert result is True
        host.navigate_to.assert_awaited_once_with(
            "context", context_type="album", context_id="ALB123"
        )

    async def test_go_to_album_no_id_notifies(self):
        """go_to_album with no ID shows error and does not navigate."""
        host = self._make_host()
        item = {"title": "Album"}
        result = await TrackActionsMixin._dispatch_entity_action(host, "go_to_album", item, "track")
        assert result is True
        host.notify.assert_called_once()
        host.navigate_to.assert_not_awaited()

    async def test_add_to_library_album_dispatches(self):
        """add_to_library for album type calls _add_album_to_library."""
        host = self._make_host()
        item = {"browseId": "ALB1", "title": "Album"}
        result = await TrackActionsMixin._dispatch_entity_action(
            host, "add_to_library", item, "album"
        )
        assert result is True
        host._add_album_to_library.assert_awaited_once_with("ALB1", "Album")

    async def test_add_to_library_non_album_returns_false(self):
        """add_to_library for non-album type is not handled."""
        host = self._make_host()
        item = {"browseId": "PL1", "title": "Playlist"}
        result = await TrackActionsMixin._dispatch_entity_action(
            host, "add_to_library", item, "playlist"
        )
        assert result is False

    async def test_go_to_artist_item_type_artist_uses_entity_id(self):
        """go_to_artist with item_type=artist and no artists list uses browseId."""
        host = self._make_host()
        item = {"browseId": "UC_direct"}
        result = await TrackActionsMixin._dispatch_entity_action(
            host, "go_to_artist", item, "artist"
        )
        assert result is True
        host.navigate_to.assert_awaited_once_with(
            "context", context_type="artist", context_id="UC_direct"
        )

    async def test_view_similar_dispatches_navigate(self):
        """view_similar routes to artist context navigation."""
        host = self._make_host()
        item = {"browseId": "UC1", "artists": [{"name": "A", "id": "UC1"}]}
        result = await TrackActionsMixin._dispatch_entity_action(
            host, "view_similar", item, "artist"
        )
        assert result is True
        host.navigate_to.assert_awaited_once_with(
            "context", context_type="artist", context_id="UC1"
        )
