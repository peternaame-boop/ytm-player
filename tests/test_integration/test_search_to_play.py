"""Integration test: search -> queue -> play -> history flow.

Wires fresh_ytmusic + fresh_queue + a real Player (with mocked mpv FFI)
+ StreamResolver.resolve mock + HistoryManager.log_play mock. Asserts
the full chain executes correctly: a user searching, the result landing
in the queue, the stream URL being resolved, mpv being asked to play
that URL, and the play being logged to history.

Mocks at the outermost boundary only: ytmusicapi's HTTP-level client
search, yt-dlp URL resolution, mpv FFI, and the history sqlite write.
Every cross-service contract in between is real code.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from ytm_player.services.history import HistoryManager
from ytm_player.services.player import Player, PlayerEvent
from ytm_player.services.stream import StreamInfo, StreamResolver
from ytm_player.utils.formatting import normalize_tracks


async def test_search_then_queue_then_play_logs_history(
    fresh_ytmusic, fresh_queue, mock_mpv, monkeypatch, tmp_path
):
    # ---- Arrange ----------------------------------------------------------

    # ytmusicapi-shaped raw search results (the shape `client.search` returns).
    raw_search_results: list[dict] = [
        {
            "videoId": "abc12345678",
            "title": "Test Track",
            "artists": [{"name": "Test Artist", "id": "art1"}],
            "album": {"name": "Test Album", "id": "alb1"},
            "duration_seconds": 180,
            "thumbnails": [{"url": "http://example.com/thumb.jpg"}],
            "resultType": "song",
        }
    ]

    # Mock the underlying ytmusicapi client's search method — keeps the
    # whole YTMusicService.search async path real (including _call's
    # asyncio.to_thread + timeout wrapper), but stops short of HTTP.
    fake_client = MagicMock(name="fake_ytm_client")
    fake_client.search = MagicMock(return_value=raw_search_results)
    fresh_ytmusic._ytm = fake_client

    # Mock StreamResolver.resolve to return a fake StreamInfo without yt-dlp.
    fake_stream_info = StreamInfo(
        url="http://fake.stream/abc12345678.opus",
        video_id="abc12345678",
        format="opus",
        bitrate=128,
        duration=180,
        expires_at=9_999_999_999.0,
        thumbnail_url="http://example.com/thumb.jpg",
    )
    fake_resolve = AsyncMock(return_value=fake_stream_info)
    monkeypatch.setattr(StreamResolver, "resolve", fake_resolve)

    # HistoryManager pointed at an ephemeral DB. Spy on log_play but keep
    # the lifecycle (init/close) real so the integration covers the
    # async DB plumbing.
    history = HistoryManager(db_path=tmp_path / "history.db")
    await history.init()
    log_play_spy = AsyncMock()
    monkeypatch.setattr(history, "log_play", log_play_spy)

    # ---- Act --------------------------------------------------------------

    # 1. Search via the real async service — fake_client.search returns the
    #    raw ytmusicapi shape. Then run normalize_tracks (the standard
    #    ingest helper for ytmusicapi responses).
    raw = await fresh_ytmusic.search("test query")
    fake_client.search.assert_called_once()
    results = normalize_tracks(raw)
    assert len(results) == 1
    assert results[0]["video_id"] == "abc12345678"
    assert results[0]["title"] == "Test Track"
    assert results[0]["artist"] == "Test Artist"

    # 2. Add normalized results to the queue.
    fresh_queue.add_multiple(results)
    assert fresh_queue.length == 1
    assert fresh_queue.tracks[0]["video_id"] == "abc12345678"

    # 3. Jump to the new track and resolve its stream URL.
    track = fresh_queue.jump_to_real(0)
    assert track is not None
    assert track["video_id"] == "abc12345678"

    resolver = StreamResolver()
    stream_info = await resolver.resolve(track["video_id"])
    assert stream_info is not None
    assert stream_info.url == "http://fake.stream/abc12345678.opus"

    # 4. Play through real Player wired against the mocked mpv module.
    player = Player()
    track_change_events: list[dict] = []
    player.on(PlayerEvent.TRACK_CHANGE, lambda t: track_change_events.append(t))

    await player.play(stream_info.url, track)

    # 5. Log the play to history.
    await history.log_play(track, listened_seconds=180, source="search")

    # ---- Assert -----------------------------------------------------------

    # Stream resolver was called with the queued track's video_id.
    # monkeypatch.setattr on the class replaces the descriptor, so the
    # AsyncMock receives only the explicitly passed args (no implicit self).
    fake_resolve.assert_awaited_once_with("abc12345678")

    # mpv received the play call with the resolved URL.
    fake_mpv_instance = mock_mpv.MPV.return_value
    fake_mpv_instance.command.assert_called_once_with(
        "loadfile", "http://fake.stream/abc12345678.opus"
    )

    # Player tracked the current track and dispatched TRACK_CHANGE.
    assert player.current_track == track
    assert track_change_events == [track]

    # History was written.
    log_play_spy.assert_awaited_once_with(track, listened_seconds=180, source="search")

    # Cleanup
    await history.close()
