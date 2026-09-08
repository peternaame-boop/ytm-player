"""Playback ownership regressions. Synthetic queues; no native audio or network."""

import asyncio
import threading
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.test_app.test_playback import _fresh_playback_host, _stream_info
from tests.test_app.test_queue_refresh_and_identity import _confirm, _host
from ytm_player.app._mpris import MPRISMixin
from ytm_player.app._playback import PlaybackMixin
from ytm_player.app._track_actions import TrackActionsMixin
from ytm_player.services.player import Player
from ytm_player.services.queue import QueueManager, RepeatMode


def track(vid):
    return {"video_id": vid, "title": vid, "duration": 180}


def host():
    h = _fresh_playback_host()
    h.player.stop = AsyncMock()
    h.queue = QueueManager()
    h.settings.playback.autoplay = False
    h._refresh_queue_page = MagicMock()
    h._log_listen_for = AsyncMock()
    h._log_current_listen = AsyncMock()
    h.stream_resolver.resolve = AsyncMock(side_effect=_stream_info)
    return h


async def test_stop_wins_against_inflight_native_load(monkeypatch):
    native = MagicMock()
    commands = []
    native.command.side_effect = lambda *args: commands.append("play") or {"playlist_entry_id": 1}
    native.stop.side_effect = lambda: commands.append("stop")
    monkeypatch.setattr(Player, "_instance", None)
    monkeypatch.setattr("ytm_player.services.player.mpv.MPV", lambda **kw: native)
    player = Player()
    h = host()
    h.player = player
    entered, release = threading.Event(), threading.Event()
    real_play_sync = player._play_sync

    def delayed(url):
        entered.set()
        if not release.wait(3):
            raise AssertionError("native probe gate timed out")
        return real_play_sync(url)

    monkeypatch.setattr(player, "_play_sync", delayed)
    playing = asyncio.create_task(h.play_track(track("A")))
    stopping = None
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        stopping = asyncio.create_task(MPRISMixin._mpris_stop(h))
        await asyncio.sleep(0)  # Stop starts while native load is still gated.
        release.set()
        await asyncio.wait_for(asyncio.gather(playing, stopping), 3)
        assert commands[-1] == "stop", commands
        assert player.current_track is None
    finally:
        release.set()
        await asyncio.gather(playing, *([stopping] if stopping else []), return_exceptions=True)


async def test_eof_after_stop_does_not_advance():
    h = host()
    h._play_generation = 1
    h.player.stop = AsyncMock()
    h._play_next = AsyncMock()
    await MPRISMixin._mpris_stop(h)
    await h._on_track_end({"reason": 0, "track": track("A"), "attempt": 1})
    h._play_next.assert_not_awaited()


async def test_eof_during_skip_does_not_skip_successor():
    h = host()
    h.queue.add_multiple([track(v) for v in "ABC"])
    h.queue.jump_to(0)
    h._play_generation = 1
    entered, release = asyncio.Event(), asyncio.Event()

    async def resolve(vid):
        entered.set()
        await release.wait()
        return _stream_info(vid)

    h.stream_resolver.resolve = AsyncMock(side_effect=resolve)
    skipping = asyncio.create_task(h._play_next())
    await entered.wait()
    ending = asyncio.create_task(h._on_track_end({"reason": 0, "track": track("A"), "attempt": 1}))
    await asyncio.sleep(0)
    release.set()
    await asyncio.wait_for(asyncio.gather(skipping, ending), 3)
    assert h.queue.current_track["video_id"] == "B"


@pytest.mark.parametrize("append", [False, True])
async def test_old_radio_cannot_mutate_replacement_queue(append):
    h = host()
    h.queue.add(track("A"))
    h.queue.jump_to(0)
    h.play_track = AsyncMock()
    entered, release = asyncio.Event(), asyncio.Event()

    async def radio(*args):
        entered.set()
        await release.wait()
        return [track("R")]

    h.ytmusic = MagicMock(get_radio=radio)
    task = asyncio.create_task(h._fetch_and_play_radio(track("A"), append=append))
    await entered.wait()
    h.queue.clear()
    h.queue.add(track("B"))
    h.queue.jump_to(0)
    h._play_generation += 1
    release.set()
    await task
    assert [t["video_id"] for t in h.queue.tracks] == ["B"]
    h.play_track.assert_not_awaited()


async def test_old_playlist_tail_cannot_append_to_replacement():
    h = host()
    h.queue.add(track("A"))
    entered, release = asyncio.Event(), asyncio.Event()

    async def remaining(*args, **kwargs):
        entered.set()
        await release.wait()
        return [{"videoId": "OLD", "title": "Old tail"}]

    h.ytmusic = MagicMock(get_playlist_remaining=remaining)
    task = asyncio.create_task(TrackActionsMixin._fetch_remaining_for_queue(h, "PL", 1))
    await entered.wait()
    h.queue.clear()
    h.queue.add(track("B"))
    release.set()
    await task
    assert [t["video_id"] for t in h.queue.tracks] == ["B"]


async def test_old_artist_tail_cannot_use_overlap_as_queue_identity():
    h = host()
    h.queue.add(track("A"))
    entered, release = asyncio.Event(), asyncio.Event()

    async def playlist(*args, **kwargs):
        entered.set()
        await release.wait()
        return {"tracks": [{"videoId": "A", "title": "Old A"}, {"videoId": "OLD", "title": "Old"}]}

    h.ytmusic = MagicMock(get_playlist=playlist)
    task = asyncio.create_task(
        TrackActionsMixin._fetch_remaining_artist_songs(h, "ARTIST", [track("A")])
    )
    await entered.wait()
    h.queue.clear()
    h.queue.add_multiple([track("A"), track("B")])
    release.set()
    await task
    assert [(t["video_id"], t["title"]) for t in h.queue.tracks] == [("A", "A"), ("B", "B")]


async def test_popup_play_outside_queue_replaces_queue():
    q = QueueManager()
    q.add_multiple([track("A"), track("B")])
    q.jump_to(0)
    h = _host(q)
    h._play_request = 0
    h._play_popup_track = MethodType(TrackActionsMixin._play_popup_track, h)
    h.play_track = AsyncMock()
    h._replace_queue_and_play = AsyncMock()
    tasks = []
    h.run_worker.side_effect = lambda coro, **kwargs: tasks.append(asyncio.create_task(coro))
    callback = _confirm(h, track("X"))
    callback("play")
    await asyncio.gather(*tasks)
    h._replace_queue_and_play.assert_awaited_once()
    h.play_track.assert_not_awaited()


async def test_remove_audible_entry_does_not_skip_its_successor_at_eof():
    h = host()
    h.queue.add_multiple([track(v) for v in "ABC"])
    h.queue.jump_to(0)
    h._play_generation = 1
    h.play_track = AsyncMock()
    entry = h.queue.entries[0][0]
    TrackActionsMixin._remove_from_queue(h, track("A"), entry, from_queue=True)
    await h._on_track_end({"reason": 0, "track": track("A"), "attempt": 1})
    assert h.play_track.await_args.args[0]["video_id"] == "B"


async def test_failed_load_preserves_pending_resume():
    h = host()
    h._pending_resume_video_id = "A"
    h._pending_resume_position = 42.0
    h.player.seek_absolute = AsyncMock()
    await h.play_track(track("A"))  # stub load leaves current_track None
    assert (h._pending_resume_video_id, h._pending_resume_position) == ("A", 42.0)
    h.player.seek_absolute.assert_not_awaited()


async def test_last_failed_recovery_reports_no_next_track():
    h = host()
    h.queue.add(track("A"))
    h.queue.jump_to(0)
    h._play_generation = 2
    h._recovery_generation = 2
    await h._on_player_error({"reason": 4, "error": -1, "track": track("A"), "attempt": 2})
    assert any("end of queue" in c.args[0].lower() for c in h.notify.call_args_list)


async def test_repeat_one_does_not_restart_exhausted_recovery():
    h = host()
    h.queue.add_multiple([track("A"), track("B")])
    h.queue.jump_to(0)
    h.queue.set_repeat(RepeatMode.ONE)
    h._play_generation = 2
    h._recovery_generation = 2
    h.play_track = AsyncMock()
    tasks = []
    h.call_later.side_effect = lambda callback: callback()
    h.run_worker.side_effect = lambda coro, **kwargs: tasks.append(asyncio.create_task(coro))
    await h._on_player_error({"reason": 4, "error": -1, "track": track("A"), "attempt": 2})
    await asyncio.gather(*tasks)
    assert not any(c.args[0]["video_id"] == "A" for c in h.play_track.await_args_list)


async def test_control_current_eof_advances_once():
    h = host()
    h.queue.add_multiple([track("A"), track("B")])
    h.queue.jump_to(0)
    h._play_generation = 1
    h.play_track = AsyncMock()
    await h._on_track_end({"reason": 0, "track": track("A"), "attempt": 1})
    h.play_track.assert_awaited_once_with(h.queue.current_track)
    assert h.queue.current_track["video_id"] == "B"


async def test_control_current_playlist_tail_appends():
    h = host()
    h.queue.add(track("A"))
    h.ytmusic = MagicMock(
        get_playlist_remaining=AsyncMock(return_value=[{"videoId": "TAIL", "title": "Tail"}])
    )
    await TrackActionsMixin._fetch_remaining_for_queue(h, "PL", 1)
    assert [t["video_id"] for t in h.queue.tracks] == ["A", "TAIL"]


async def test_control_accepted_matching_load_consumes_resume():
    h = host()
    h._pending_resume_video_id = "A"
    h._pending_resume_position = 42.0
    h.player.seek_absolute = AsyncMock()

    async def accept(url, item, attempt=None):
        h.player.current_track = item

    h.player.play = AsyncMock(side_effect=accept)
    await h.play_track(track("A"))
    assert h._pending_resume_video_id is None
    h.player.seek_absolute.assert_awaited_once_with(42.0)


class CollectionHost(TrackActionsMixin, PlaybackMixin):
    pass


def collection_host():
    h = CollectionHost()
    h.__dict__.update(vars(host()))
    h.shuffle_prefs = {}
    h._sync_shuffle_bar = MagicMock()
    h.ytmusic = MagicMock()
    return h


async def _start_a(h):
    h.queue.add_multiple([track("A"), track("B")])
    h.queue.jump_to(0)
    await h.play_track(track("A"))
    h.player.current_track = track("A")
    return h._play_generation


@pytest.mark.parametrize("event_kind", ["eof", "error"])
@pytest.mark.parametrize(
    "operation", ["artist_radio", "radio_empty", "popup_stale", "playlist_empty"]
)
async def test_unsuccessful_request_keeps_audible_attempt_owned(operation, event_kind):
    h = collection_host()
    attempt = await _start_a(h)
    if operation == "artist_radio":
        h.ytmusic.get_artist = AsyncMock(return_value=None)
        await h._start_artist_radio("ARTIST")
    elif operation == "radio_empty":
        h.ytmusic.get_radio = AsyncMock(return_value=[])
        await h._fetch_and_play_radio(track("X"))
    elif operation == "popup_stale":
        await h._play_popup_track(track("A"), 999, from_queue=True)
    else:
        h.ytmusic.get_playlist = AsyncMock(return_value={"tracks": [], "trackCount": 0})
        await h._play_playlist("PL", "Empty")
    assert h.player.current_track["video_id"] == "A"
    assert h.queue.current_track["video_id"] == "A"
    h.player.current_track = None  # Native EOF/ERROR clears this before dispatch.
    h.play_track = AsyncMock()
    if event_kind == "eof":
        await h._on_track_end({"reason": 0, "track": track("A"), "attempt": attempt})
        h.play_track.assert_awaited_once_with(h.queue.current_track)
        assert h.queue.current_track["video_id"] == "B"
    else:
        await h._on_player_error(
            {"reason": 4, "error": -1, "track": track("A"), "attempt": attempt}
        )
        h.play_track.assert_awaited_once_with(track("A"), recovery_of=attempt)


@pytest.mark.parametrize("after_clear", ["empty", "add", "autoplay", "error"])
async def test_queue_clear_preserves_audible_attempt_completion(after_clear):
    h = collection_host()
    attempt = await _start_a(h)
    h.queue.clear()  # IPC queue_clear does not stop the audible track.
    if after_clear == "add":
        h.queue.add(track("X"))
    elif after_clear == "autoplay":
        h.settings.playback.autoplay = True
        h.ytmusic.get_radio = AsyncMock(return_value=[track("R")])
    h.player.current_track = None
    h.play_track = AsyncMock()
    h.notify.reset_mock()
    if after_clear == "error":
        await h._on_player_error(
            {"reason": 4, "error": -1, "track": track("A"), "attempt": attempt}
        )
        h.play_track.assert_awaited_once_with(track("A"), recovery_of=attempt)
    else:
        await h._on_track_end({"reason": 0, "track": track("A"), "attempt": attempt})
        if after_clear == "empty":
            h.play_track.assert_not_awaited()
            assert any("End of queue" in c.args[0] for c in h.notify.call_args_list)
        else:
            h.play_track.assert_awaited_once_with(h.queue.current_track)
            assert h.queue.current_track["video_id"] == ("X" if after_clear == "add" else "R")


@pytest.mark.parametrize("operation", ["radio", "playlist", "artist", "album", "entity"])
async def test_queued_request_cannot_start_after_stop(operation):
    h = collection_host()
    if operation == "radio":
        work = h._fetch_and_play_radio(track("A"))
    elif operation == "playlist":
        work = h._play_playlist("PL", "Playlist")
    elif operation == "artist":
        work = h._play_artist_top_songs("ARTIST")
    elif operation == "album":
        work = h._play_album("ALBUM", "Album")
    else:
        work = h._dispatch_entity_action(
            action_id="play_all", item={"browseId": "ALBUM"}, item_type="album"
        )
    await MPRISMixin._mpris_stop(h)
    await work
    assert h.ytmusic.mock_calls == []
    assert h.queue.is_empty


async def test_nested_collection_keeps_ownership_after_replacing_queue():
    h = collection_host()
    h.ytmusic.get_playlist = AsyncMock(
        return_value={"tracks": [{"videoId": "A", "title": "A"}], "trackCount": 2}
    )
    h.ytmusic.get_playlist_remaining = AsyncMock(return_value=[{"videoId": "B", "title": "B"}])
    h.play_track = AsyncMock()
    scheduled = []
    h.run_worker.side_effect = lambda coro, **kwargs: scheduled.append(coro)
    await h._dispatch_entity_action("play_all", {"playlistId": "PL"}, "playlist")
    assert len(scheduled) == 1
    await scheduled[0]
    assert [t["video_id"] for t in h.queue.tracks] == ["A", "B"]
    h.play_track.assert_awaited_once()


async def test_duplicate_click_does_not_cancel_the_inflight_resolve():
    h = host()
    entered, release = asyncio.Event(), asyncio.Event()

    async def resolve(vid):
        entered.set()
        await release.wait()
        return _stream_info(vid)

    h.stream_resolver.resolve = AsyncMock(side_effect=resolve)
    first = asyncio.create_task(h.play_track(track("A")))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await h.play_track(track("A"))
        release.set()
        await asyncio.wait_for(first, 3)
        h.player.play.assert_awaited_once()
        h.stream_resolver.resolve.assert_awaited_once()
    finally:
        release.set()
        await first


async def test_next_same_song_occurrence_is_not_debounced():
    h = host()
    same = track("A")
    h.queue.add_multiple([same, same])
    h.queue.jump_to(0)
    await h.play_track(same)
    await h._play_next()
    assert h.player.play.await_count == 2
    assert h.queue.current_index == 1


async def test_new_eof_is_not_blocked_by_old_finalizer():
    h = host()
    h.queue.add_multiple([track(v) for v in "ABC"])
    h.queue.jump_to(0)
    h._play_generation = 1
    entered, release = asyncio.Event(), asyncio.Event()

    async def log(item):
        if item["video_id"] == "A":
            entered.set()
            await release.wait()

    h._log_listen_for = log
    h.play_track = AsyncMock()
    old = asyncio.create_task(h._on_track_end({"track": track("A"), "attempt": 1}))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        h._play_generation = 2
        h.queue.jump_to(1)
        await h._on_track_end({"track": track("B"), "attempt": 2})
        release.set()
        await asyncio.wait_for(old, 3)
        h.play_track.assert_awaited_once_with(h.queue.current_track)
        assert h.queue.current_track["video_id"] == "C"
    finally:
        release.set()
        await old


@pytest.mark.parametrize("append", [False, True])
async def test_radio_return_after_stop_cannot_mutate_queue(append):
    h = host()
    h.queue.add(track("A"))
    h.queue.jump_to(0)
    entered, release = asyncio.Event(), asyncio.Event()

    async def radio(*args):
        entered.set()
        await release.wait()
        return [track("B")]

    h.ytmusic = MagicMock(get_radio=radio)
    pending = asyncio.create_task(h._fetch_and_play_radio(track("A"), append=append))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await MPRISMixin._mpris_stop(h)
        release.set()
        await asyncio.wait_for(pending, 3)
        assert [t["video_id"] for t in h.queue.tracks] == ["A"]
        h.player.play.assert_not_awaited()
    finally:
        release.set()
        await pending


async def test_tail_scheduled_before_replacement_never_attaches_to_new_queue():
    h = collection_host()
    h.ytmusic.get_playlist_remaining = AsyncMock()
    work = h._fetch_remaining_for_queue("PL", 1)
    h.queue.clear()
    h.queue.add(track("A"))
    await work
    h.ytmusic.get_playlist_remaining.assert_not_awaited()


async def test_tail_survives_ordinary_next_and_shuffle():
    h = collection_host()
    h.queue.add_multiple([track("A"), track("B")])
    h.queue.jump_to(0)
    h.ytmusic.get_playlist_remaining = AsyncMock(return_value=[{"videoId": "C", "title": "C"}])
    pending = h._fetch_remaining_for_queue("PL", 2)
    h.queue.next_track()
    h.queue.toggle_shuffle()
    await pending
    assert {t["video_id"] for t in h.queue.tracks} == {"A", "B", "C"}


@pytest.mark.parametrize("shuffle", [False, True])
async def test_popup_play_selects_exact_duplicate_and_preserves_context(shuffle):
    h = collection_host()
    same = track("A")
    h.queue.add_multiple([same, track("B"), same])
    h.queue.set_context("PL")
    entry = h.queue.entries[-1][0]
    if shuffle:
        h.queue.toggle_shuffle()
    before = h.queue.entries
    h.play_track = AsyncMock()
    await h._play_popup_track(same, entry, from_queue=True)
    assert h.queue.entries == before
    assert h.queue.entries[h.queue.current_index][0] == entry
    assert h.queue.current_context_id == "PL"


@pytest.mark.parametrize("entry", [None, 999])
async def test_popup_play_stale_queue_origin_never_falls_back(entry):
    h = collection_host()
    h.queue.add(track("A"))
    h.play_track = AsyncMock()
    await h._play_popup_track(track("A"), entry, from_queue=True)
    h.play_track.assert_not_awaited()
    assert "no longer present" in h.notify.call_args.args[0]


async def test_stop_allows_immediate_manual_replay_of_same_song():
    h = host()
    await h.play_track(track("A"))
    await MPRISMixin._mpris_stop(h)
    await h.play_track(track("A"))
    assert h.player.play.await_count == 2


async def test_previous_seek_keeps_current_eof_owned():
    h = host()
    h.queue.add_multiple([track("A"), track("B")])
    h.queue.jump_to(0)
    await h.play_track(track("A"))
    h.player.position = 10
    h.player.seek_start = AsyncMock()
    await h._play_previous()
    h.player.seek_start.assert_awaited_once()
    h.play_track = AsyncMock()
    await h._on_track_end({"track": track("A"), "attempt": h._play_generation})
    h.play_track.assert_awaited_once_with(h.queue.current_track)
    assert h.queue.current_track["video_id"] == "B"


@pytest.mark.parametrize("during_finalize", [False, True])
async def test_old_eof_cannot_advance_replacement_queue(during_finalize):
    h = host()
    h.queue.add(track("A"))
    await h.play_track(track("A"))
    attempt = h._play_generation

    async def replace(*args):
        h.queue.clear()
        h.queue.add_multiple([track("B"), track("C")])
        h.queue.jump_to(0)
        await h.play_track(h.queue.current_track)

    if during_finalize:
        h._log_listen_for = AsyncMock(side_effect=replace)
    else:
        await replace()
    h.player.play.reset_mock()
    await h._on_track_end({"track": track("A"), "attempt": attempt})
    assert h.player.play.await_count == int(during_finalize)
    assert h.queue.current_track["video_id"] == "B"


def test_old_audio_progress_cannot_reset_new_attempt_retry_budget():
    h = host()
    h._play_generation = 2
    h._recovery_generation = 2
    h._consecutive_failures = 3
    h.player.current_attempt = 1
    h.player.is_playing = True
    h.player.position = 20
    h._poll_position()
    assert (h._recovery_generation, h._consecutive_failures) == (2, 3)


@pytest.mark.parametrize("seek_raises", [False, True])
async def test_old_resume_seek_cannot_clear_new_staging(seek_raises):
    h = host()
    h._pending_resume_video_id = "A"
    h._pending_resume_position = 42

    async def accept(url, item, attempt=None):
        h.player.current_track = item

    async def seek(position):
        await MPRISMixin._mpris_stop(h)
        h._pending_resume_video_id = "B"
        h._pending_resume_position = 99
        if seek_raises:
            raise RuntimeError("seek failed after Stop")

    h.player.play = AsyncMock(side_effect=accept)
    h.player.seek_absolute = AsyncMock(side_effect=seek)
    await h.play_track(track("A"))
    assert (h._pending_resume_video_id, h._pending_resume_position) == ("B", 99)


@pytest.mark.parametrize("source", ["search", "sidebar", "context"])
async def test_real_ui_wrapper_captures_before_worker_starts(source):
    from ytm_player.app._sidebar import SidebarMixin
    from ytm_player.ui.pages.context import ContextPage
    from ytm_player.ui.pages.search import SearchPage

    h = collection_host()
    h.push_screen = MagicMock()
    h.navigate_to = AsyncMock()
    pending = []
    h.run_worker.side_effect = lambda coro, **kwargs: pending.append(coro)
    item = {"playlistId": "PL", "title": "Playlist"}
    if source == "search":
        page = SimpleNamespace(app=h)
        event = SimpleNamespace(item_data=item, panel_id="playlists-panel")
        SearchPage.on_search_result_panel_item_right_clicked(page, event)
        h.push_screen.call_args.args[1]("play_all")
    elif source == "sidebar":
        SidebarMixin._open_playlist_context_menu(h, item)
        h.push_screen.call_args.args[1]("play_all")
    else:
        page = SimpleNamespace(app=h, _data=item, context_id="PL")
        pending.append(ContextPage._start_radio(page))
    assert len(pending) == 1
    await MPRISMixin._mpris_stop(h)
    await pending[0]
    assert h.ytmusic.mock_calls == []
    h.navigate_to.assert_not_awaited()


async def test_stale_sidebar_load_cannot_change_active_playlist_or_navigate():
    from ytm_player.app._sidebar import SidebarMixin

    h = collection_host()
    h.navigate_to = AsyncMock()
    h._active_library_playlist_id = "CURRENT"

    async def replace_while_fetching(*args, **kwargs):
        h.queue.clear()
        h.queue.add(track("B"))
        h.queue.jump_to(0)
        return {"tracks": [{"videoId": "A", "title": "A"}]}

    h.ytmusic.get_playlist = AsyncMock(side_effect=replace_while_fetching)
    message = SimpleNamespace(item_data={"playlistId": "OLD", "title": "Old"})
    await SidebarMixin.on_playlist_sidebar_playlist_double_clicked(h, message)
    assert h._active_library_playlist_id == "CURRENT"
    h.navigate_to.assert_not_awaited()
