"""The unified "play selected track" path (T4 of the 360° review).

Every page must route selection-plays through the host's
``_replace_queue_and_play`` helper. These tests pin the three page bugs
the unification fixed (Browse For You bypassed the queue, Recently
Played appended duplicates, Liked Songs skipped the queue-page/shuffle
-bar sync) plus the helper's bidirectional shuffle-pref restore.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from textual.widget import Widget

from ytm_player.app._track_actions import TrackActionsMixin
from ytm_player.services.queue import QueueManager
from ytm_player.ui.pages.browse import BrowsePage
from ytm_player.ui.pages.context import ContextPage
from ytm_player.ui.pages.library import LibraryPage
from ytm_player.ui.pages.liked_songs import LikedSongsPage
from ytm_player.ui.pages.recently_played import RecentlyPlayedPage
from ytm_player.ui.pages.search import SearchPage
from ytm_player.ui.widgets.track_table import TrackTable


def _track(video_id: str, title: str = "T") -> dict:
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


def _make_host(*, queue: QueueManager | None = None, real_helper: bool = False) -> MagicMock:
    host = MagicMock()
    host.queue = queue if queue is not None else QueueManager()
    host.shuffle_prefs = MagicMock()
    host.shuffle_prefs.get = MagicMock(return_value=None)
    host.play_track = AsyncMock()
    host.notify = MagicMock()
    host._refresh_queue_page = MagicMock()
    host._sync_shuffle_bar = MagicMock()
    if real_helper:
        host._replace_queue_and_play = TrackActionsMixin._replace_queue_and_play.__get__(host)
    else:
        host._replace_queue_and_play = AsyncMock()
    return host


def _attach_fake_app(page: Widget, host: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    """Shadow ``Widget.app`` (walks to a live App we don't have) with the host."""
    monkeypatch.setattr(type(page), "app", property(lambda self: host))


# ── helper: bidirectional shuffle-pref restore ──────────────────────


async def test_helper_restores_saved_shuffle_off():
    """A saved OFF pref must clear a currently-ON shuffle (previously the
    helper only forced ON; the page-level copies it replaces did both)."""
    queue = QueueManager()
    queue.add_multiple([_track("a"), _track("b")])
    queue.toggle_shuffle()
    assert queue.shuffle_enabled is True
    host = _make_host(queue=queue)
    host.shuffle_prefs.get = MagicMock(return_value=False)

    await TrackActionsMixin._replace_queue_and_play(
        host, [_track("a"), _track("b")], entity_id="PL1", autoplay=False
    )

    assert host.queue.shuffle_enabled is False


async def test_helper_still_forces_saved_shuffle_on():
    queue = QueueManager()
    host = _make_host(queue=queue)
    host.shuffle_prefs.get = MagicMock(return_value=True)

    await TrackActionsMixin._replace_queue_and_play(
        host, [_track("a"), _track("b")], entity_id="PL1", autoplay=False
    )

    assert host.queue.shuffle_enabled is True


async def test_helper_explicit_shuffle_with_saved_off_pref():
    """Explicit shuffle=True pre-randomizes the track list; the saved-OFF
    pref then clears the queue's shuffle MODE without defeating the
    already-randomized order."""
    queue = QueueManager()
    queue.add_multiple([_track("seed")])
    queue.toggle_shuffle()
    host = _make_host(queue=queue)
    host.shuffle_prefs.get = MagicMock(return_value=False)
    tracks = [_track(f"t{i}") for i in range(4)]

    await TrackActionsMixin._replace_queue_and_play(
        host, tracks, entity_id="PL1", shuffle=True, autoplay=False
    )

    assert host.queue.shuffle_enabled is False
    assert {t["video_id"] for t in host.queue.tracks} == {"t0", "t1", "t2", "t3"}


# ── Browse "For You" single-track play ───────────────────────────────


async def test_browse_for_you_song_replaces_queue(monkeypatch):
    old_queue = QueueManager()
    old_queue.add_multiple([_track("old1"), _track("old2")])
    old_queue.jump_to_real(0)

    page = BrowsePage.__new__(BrowsePage)
    host = _make_host(queue=old_queue, real_helper=True)
    _attach_fake_app(page, host, monkeypatch)

    await page._navigate_item({"resultType": "song", "videoId": "v1", "title": "Song"})

    # Queue is REPLACED with the selection; next-track can't resume the
    # old queue (the old code played around the queue entirely).
    assert [t["video_id"] for t in old_queue.tracks] == ["v1"]
    assert old_queue.next_track() is None
    host.play_track.assert_awaited_once()
    assert host.play_track.await_args[0][0]["video_id"] == "v1"
    host._refresh_queue_page.assert_called()


# ── Recently Played: replace, don't append ───────────────────────────


async def test_recently_played_selection_replaces_and_never_duplicates(monkeypatch):
    history = [_track("h1"), _track("h2"), _track("h3")]
    queue = QueueManager()
    queue.add_multiple([_track("old1"), _track("old2")])

    page = RecentlyPlayedPage()
    host = _make_host(queue=queue, real_helper=True)
    _attach_fake_app(page, host, monkeypatch)
    table = MagicMock()
    table.tracks = list(history)
    object.__setattr__(page, "query_one", lambda selector, *a, **kw: table)

    event = TrackTable.TrackSelected(history[1], 1)
    await page.on_track_table_track_selected(event)

    assert [t["video_id"] for t in queue.tracks] == ["h1", "h2", "h3"]
    assert queue.current_track is not None and queue.current_track["video_id"] == "h2"
    assert queue.current_context_id == "__RECENTLY_PLAYED__"
    host.play_track.assert_awaited_once_with(history[1])
    host._refresh_queue_page.assert_called()
    host._sync_shuffle_bar.assert_called()

    # Selecting again must not grow the queue (the old code appended).
    await page.on_track_table_track_selected(TrackTable.TrackSelected(history[1], 1))
    assert queue.length == 3


# ── Liked Songs: unified path with sentinel context ──────────────────


async def test_liked_songs_selection_uses_unified_path(monkeypatch):
    liked = [_track("l1"), _track("l2")]
    page = LikedSongsPage.__new__(LikedSongsPage)
    host = _make_host()
    _attach_fake_app(page, host, monkeypatch)
    table = MagicMock()
    table.tracks = list(liked)
    object.__setattr__(page, "query_one", lambda selector, *a, **kw: table)

    event = TrackTable.TrackSelected(liked[1], 1)
    await page.on_track_table_track_selected(event)

    host._replace_queue_and_play.assert_awaited_once()
    args, kwargs = host._replace_queue_and_play.call_args
    assert [t["video_id"] for t in args[0]] == ["l1", "l2"]
    assert kwargs == {"entity_id": "__LIKED_SONGS__", "start_index": 1, "autoplay": False}
    host.play_track.assert_awaited_once_with(liked[1])


# ── The already-correct sites must stay on the unified path ──────────


@pytest.mark.parametrize(
    ("page_cls", "attrs", "expected_entity"),
    [
        (SearchPage, {}, None),
        (BrowsePage, {}, None),  # charts table
        (ContextPage, {"context_id": "PLctx"}, "PLctx"),
        (LibraryPage, {"_active_playlist_id": "PLlib"}, "PLlib"),
    ],
)
async def test_correct_sites_still_use_unified_path(page_cls, attrs, expected_entity, monkeypatch):
    tracks = [_track("s1"), _track("s2")]
    page = page_cls.__new__(page_cls)
    for name, value in attrs.items():
        object.__setattr__(page, name, value)
    host = _make_host()
    _attach_fake_app(page, host, monkeypatch)
    table = MagicMock()
    table.tracks = list(tracks)
    object.__setattr__(page, "query_one", lambda selector, *a, **kw: table)

    await page.on_track_table_track_selected(TrackTable.TrackSelected(tracks[1], 1))

    args, kwargs = host._replace_queue_and_play.call_args
    assert [t["video_id"] for t in args[0]] == ["s1", "s2"]
    assert kwargs == {"entity_id": expected_entity, "start_index": 1, "autoplay": False}
    host.play_track.assert_awaited_once_with(tracks[1])
