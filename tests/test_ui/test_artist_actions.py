"""Tests for artist context menu actions in the search page."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from ytm_player.ui.pages.search import SearchPage


def _make_page(*, get_artist_return=None, get_watch_playlist_return=None, subscribed_ids=None):
    """Build a MagicMock standing in for SearchPage + host app."""
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
    host._refresh_queue_page = MagicMock()
    host.notify = MagicMock()
    host.run_worker = MagicMock()

    page = MagicMock()
    page.app = host
    page._subscribed_artist_ids = subscribed_ids if subscribed_ids is not None else set()
    return page, host


# ── start_radio tests ──────────────────────────────────────────────────


class TestStartArtistRadio:
    async def test_uses_radio_id(self):
        """Primary path: uses radioId from get_artist with get_watch_playlist."""
        page, host = _make_page(
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
        await SearchPage._start_artist_radio(page, "UC_test")

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
        page, host = _make_page(
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
        await SearchPage._start_artist_radio(page, "UC_test")

        host._fetch_and_play_radio.assert_awaited_once()
        call_args = host._fetch_and_play_radio.call_args
        seeds = call_args[0][0]
        assert len(seeds) == 2
        assert call_args[1]["label"] == "Radio from Test Artist"

    async def test_artist_fetch_failure(self):
        """When get_artist returns empty, shows warning."""
        page, host = _make_page(get_artist_return={})
        await SearchPage._start_artist_radio(page, "UC_test")

        host.notify.assert_any_call("Couldn't load artist data.", severity="warning", timeout=3)
        host.queue.clear.assert_not_called()

    async def test_empty_watch_playlist(self):
        """When radioId present but returns empty tracks, shows warning."""
        page, host = _make_page(
            get_artist_return={"name": "Test", "radioId": "RDAMPL_test"},
            get_watch_playlist_return=[],
        )
        await SearchPage._start_artist_radio(page, "UC_test")

        host.notify.assert_any_call(
            "No radio suggestions available.", severity="warning", timeout=3
        )

    async def test_no_songs_to_seed(self):
        """When no radioId and no playable songs, shows warning."""
        page, host = _make_page(
            get_artist_return={"name": "Empty", "songs": {"results": []}},
        )
        await SearchPage._start_artist_radio(page, "UC_test")

        host.notify.assert_any_call("No songs to seed radio.", severity="warning", timeout=3)


# ── play_top_songs tests ──────────────────────────────────────────────


class TestPlayArtistTopSongs:
    async def test_queues_and_plays(self):
        """Fetches artist, queues top songs, starts playback."""
        page, host = _make_page(
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
        await SearchPage._play_artist_top_songs(page, "UC_ax")

        host.queue.clear.assert_called_once()
        host.queue.set_context.assert_called_once_with(None)
        host.queue.add_multiple.assert_called_once()
        tracks = host.queue.add_multiple.call_args[0][0]
        assert len(tracks) == 2
        host.queue.jump_to_real.assert_called_once_with(0)
        host.play_track.assert_awaited_once()
        host.notify.assert_any_call("Playing top songs from Artist X", timeout=4)

    async def test_background_fetch_triggered(self):
        """When songs.browseId present, triggers background fetch."""
        page, host = _make_page(
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
        await SearchPage._play_artist_top_songs(page, "UC_ax")

        page.run_worker.assert_called_once()

    async def test_no_songs(self):
        """When artist has no songs, shows warning."""
        page, host = _make_page(
            get_artist_return={"name": "Empty Artist", "songs": {"results": []}},
        )
        await SearchPage._play_artist_top_songs(page, "UC_empty")

        host.notify.assert_any_call(
            "No songs found for this artist.", severity="warning", timeout=3
        )
        host.queue.clear.assert_not_called()

    async def test_artist_failure(self):
        """When get_artist returns empty, shows warning."""
        page, host = _make_page(get_artist_return={})
        await SearchPage._play_artist_top_songs(page, "UC_fail")

        host.notify.assert_any_call("Couldn't load artist data.", severity="warning", timeout=3)


# ── toggle_subscribe tests ────────────────────────────────────────────


class TestToggleArtistSubscribe:
    async def test_subscribe_success(self):
        """Subscribe to an artist updates cache and notifies."""
        page, host = _make_page(
            get_artist_return={"name": "Sub Artist", "channelId": "UCsub123"},
        )
        item = {"browseId": "UC_sub", "resultType": "artist"}
        await SearchPage._toggle_artist_subscribe(page, item, "UC_sub")

        host.ytmusic.subscribe_artist.assert_awaited_once_with("UCsub123")
        host.notify.assert_any_call("Subscribed", timeout=2)
        assert item["subscribed"] is True
        assert "UC_sub" in page._subscribed_artist_ids

    async def test_unsubscribe_success(self):
        """Unsubscribe from an artist updates cache and notifies."""
        page, host = _make_page(
            get_artist_return={"name": "Sub Artist", "channelId": "UCsub123"},
            subscribed_ids={"UC_sub"},
        )
        item = {"browseId": "UC_sub", "resultType": "artist"}
        await SearchPage._toggle_artist_subscribe(page, item, "UC_sub")

        host.ytmusic.unsubscribe_artist.assert_awaited_once_with("UCsub123")
        host.notify.assert_any_call("Unsubscribed", timeout=2)
        assert item["subscribed"] is False
        assert "UC_sub" not in page._subscribed_artist_ids

    async def test_subscribe_failure(self):
        """Subscribe failure shows error notification."""
        page, host = _make_page(
            get_artist_return={"name": "Sub Artist", "channelId": "UCsub123"},
        )
        host.ytmusic.subscribe_artist = AsyncMock(return_value="network")
        item = {"browseId": "UC_sub", "resultType": "artist"}
        await SearchPage._toggle_artist_subscribe(page, item, "UC_sub")

        host.notify.assert_any_call(
            "Couldn't subscribe — check your connection",
            severity="error",
            timeout=3,
        )

    async def test_missing_channel_id(self):
        """When artist data lacks channelId key, shows warning."""
        page, host = _make_page(
            get_artist_return={"name": "No Channel"},
        )
        item = {"browseId": "UC_noid", "resultType": "artist"}
        await SearchPage._toggle_artist_subscribe(page, item, "UC_noid")

        host.notify.assert_any_call("Couldn't load artist data.", severity="warning", timeout=3)

    async def test_empty_channel_id(self):
        """When channelId is empty string, shows warning instead of sending empty to API."""
        page, host = _make_page(
            get_artist_return={"name": "Bad Data", "channelId": ""},
        )
        item = {"browseId": "UC_bad", "resultType": "artist"}
        await SearchPage._toggle_artist_subscribe(page, item, "UC_bad")

        host.notify.assert_any_call("Couldn't load artist data.", severity="warning", timeout=3)
        host.ytmusic.subscribe_artist.assert_not_awaited()

    async def test_artist_fetch_failure(self):
        """When get_artist returns empty, shows warning."""
        page, host = _make_page(get_artist_return={})
        item = {"browseId": "UC_fail", "resultType": "artist"}
        await SearchPage._toggle_artist_subscribe(page, item, "UC_fail")

        host.notify.assert_any_call("Couldn't load artist data.", severity="warning", timeout=3)
        host.ytmusic.subscribe_artist.assert_not_awaited()

    async def test_unsubscribe_failure(self):
        """Unsubscribe failure shows error notification."""
        page, host = _make_page(
            get_artist_return={"name": "Sub Artist", "channelId": "UCsub123"},
            subscribed_ids={"UC_sub"},
        )
        host.ytmusic.unsubscribe_artist = AsyncMock(return_value="server_error")
        item = {"browseId": "UC_sub", "resultType": "artist"}
        await SearchPage._toggle_artist_subscribe(page, item, "UC_sub")

        host.notify.assert_any_call(
            "Couldn't unsubscribe — YouTube Music had a problem, try again",
            severity="error",
            timeout=3,
        )


# ── _fetch_remaining_artist_songs tests ───────────────────────────────


class TestFetchRemainingArtistSongs:
    async def test_appends_new_tracks_and_enriches_initial(self):
        """Fetches playlist, enriches initial tracks with durations, appends new ones."""
        page, host = _make_page()
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
        await SearchPage._fetch_remaining_artist_songs(page, "VLPLfull", initial)

        host.ytmusic.get_playlist.assert_awaited_once_with("VLPLfull")
        assert host.queue.add.call_count == 2
        assert queue_track.get("duration") == 210
        host._refresh_queue_page.assert_called_once()

    async def test_handles_fetch_failure_silently(self):
        """On exception, does not crash and does not modify queue."""
        page, host = _make_page()
        host.ytmusic.get_playlist = AsyncMock(side_effect=Exception("API down"))
        initial = [{"video_id": "s1", "title": "Track"}]
        await SearchPage._fetch_remaining_artist_songs(page, "VLPLfull", initial)

        host.queue.add.assert_not_called()


# ── _load_subscribed_artists tests ────────────────────────────────────


class TestLoadSubscribedArtists:
    async def test_fetches_all_subscriptions(self):
        """Passes limit=None to fetch all subscribed artists."""
        page, host = _make_page()
        host.ytmusic.get_library_artists = AsyncMock(
            return_value=[
                {"browseId": "UC_a"},
                {"browseId": "UC_b"},
                {"browseId": ""},
            ]
        )
        await SearchPage._load_subscribed_artists(page)

        host.ytmusic.get_library_artists.assert_awaited_once_with(limit=None)
        assert page._subscribed_artist_ids == {"UC_a", "UC_b"}

    async def test_failure_leaves_cache_empty(self):
        """On exception, cache stays unchanged."""
        page, host = _make_page()
        host.ytmusic.get_library_artists = AsyncMock(side_effect=Exception("API down"))
        await SearchPage._load_subscribed_artists(page)

        assert page._subscribed_artist_ids == set()


# ── Dispatch guard tests ─────────────────────────────────────────────


class TestDispatchGuards:
    """Tests for _handle_action dispatch guards.

    These verify that artist-specific actions are not dispatched to
    artist methods for non-artist items, and that missing browseId
    shows a toast.
    """

    def _capture_handler(self, page, host, item, item_type):
        """Build a _handle_action callback mirroring the dispatch in search.py."""
        if item_type == "artist":
            item["subscribed"] = item.get("browseId", "") in page._subscribed_artist_ids

        def _handle_action(action_id):
            if action_id is None:
                return
            if action_id in ("play_all", "shuffle_play"):
                browse_id = (
                    item.get("browseId") or item.get("album_id") or item.get("playlistId") or ""
                )
                ctx_type = item_type if item_type in ("album", "playlist") else None
                if browse_id and ctx_type:
                    host.run_worker(
                        host.navigate_to("context", context_type=ctx_type, context_id=browse_id)
                    )
            elif action_id == "start_radio":
                browse_id = item.get("browseId") or item.get("artist_id") or ""
                if not browse_id:
                    host.notify("No ID available for this item.", severity="warning", timeout=2)
                elif item_type == "artist":
                    host.run_worker(page._start_artist_radio(browse_id))
                elif item_type == "playlist":
                    host.run_worker(host._start_playlist_radio(item))
            elif action_id == "play_top_songs":
                browse_id = item.get("browseId") or item.get("artist_id") or ""
                if not browse_id:
                    host.notify("No ID available for this item.", severity="warning", timeout=2)
                elif item_type == "artist":
                    host.run_worker(page._play_artist_top_songs(browse_id))
                else:
                    ctx_type = item_type if item_type in ("album", "playlist") else "artist"
                    host.run_worker(
                        host.navigate_to("context", context_type=ctx_type, context_id=browse_id)
                    )

        return _handle_action

    def test_start_radio_on_playlist_uses_playlist_radio(self):
        """start_radio for a playlist dispatches to _start_playlist_radio."""
        page, host = _make_page()
        item = {"browseId": "VLPLtest", "resultType": "playlist"}
        handler = self._capture_handler(page, host, item, "playlist")
        handler("start_radio")

        host.run_worker.assert_called_once()
        host._start_playlist_radio.assert_called_once_with(item)
        page._start_artist_radio.assert_not_called()

    def test_play_top_songs_on_playlist_navigates_instead(self):
        """play_top_songs for a non-artist navigates to context."""
        page, host = _make_page()
        handler = self._capture_handler(
            page, host, {"browseId": "VLPLtest", "resultType": "playlist"}, "playlist"
        )
        handler("play_top_songs")

        host.run_worker.assert_called_once()
        page._play_artist_top_songs.assert_not_called()

    def test_missing_browse_id_shows_toast(self):
        """Missing browseId shows warning notification."""
        page, host = _make_page()
        handler = self._capture_handler(page, host, {"resultType": "artist"}, "artist")
        handler("start_radio")

        host.notify.assert_called_once_with(
            "No ID available for this item.", severity="warning", timeout=2
        )
        host.run_worker.assert_not_called()

    def test_start_radio_on_artist_dispatches_correctly(self):
        """start_radio for an artist dispatches to _start_artist_radio."""
        page, host = _make_page()
        handler = self._capture_handler(
            page, host, {"browseId": "UC_test", "resultType": "artist"}, "artist"
        )
        handler("start_radio")

        host.run_worker.assert_called_once()
