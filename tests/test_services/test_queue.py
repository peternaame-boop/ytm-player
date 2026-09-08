"""Tests for ytm_player.services.queue.QueueManager."""

import random

import pytest

from ytm_player.services.queue import QueueManager, RepeatMode


class TestEmptyQueue:
    def test_is_empty(self, queue_manager):
        assert queue_manager.is_empty
        assert queue_manager.length == 0

    def test_current_is_none(self, queue_manager):
        assert queue_manager.current() is None

    def test_next_is_none(self, queue_manager):
        assert queue_manager.next_track() is None

    def test_previous_is_none(self, queue_manager):
        assert queue_manager.previous_track() is None

    def test_tracks_empty(self, queue_manager):
        assert queue_manager.tracks == ()


class TestSingleTrack:
    def test_add_and_current(self, queue_manager, sample_track):
        queue_manager.add(sample_track)
        assert queue_manager.length == 1
        assert not queue_manager.is_empty
        # Must jump to index 0 first.
        queue_manager.jump_to(0)
        assert queue_manager.current() == sample_track

    def test_repeat_off_next_returns_none(self, queue_manager, sample_track):
        queue_manager.add(sample_track)
        queue_manager.jump_to(0)
        # Next should return None (only 1 track, repeat off).
        assert queue_manager.next_track() is None

    def test_repeat_one(self, queue_manager, sample_track):
        queue_manager.add(sample_track)
        queue_manager.jump_to(0)
        queue_manager.set_repeat(RepeatMode.ONE)
        assert queue_manager.next_track() == sample_track
        assert queue_manager.next_track() == sample_track

    def test_repeat_all_wraps(self, queue_manager, sample_track):
        queue_manager.add(sample_track)
        queue_manager.jump_to(0)
        queue_manager.set_repeat(RepeatMode.ALL)
        assert queue_manager.next_track() == sample_track


class TestMultipleTracks:
    def test_forward_navigation(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.jump_to(0)
        assert queue_manager.current()["video_id"] == "vid_01"
        assert queue_manager.next_track()["video_id"] == "vid_02"
        assert queue_manager.next_track()["video_id"] == "vid_03"

    def test_backward_navigation(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.jump_to(2)
        assert queue_manager.current()["video_id"] == "vid_03"
        assert queue_manager.previous_track()["video_id"] == "vid_02"
        assert queue_manager.previous_track()["video_id"] == "vid_01"

    def test_no_wrap_without_repeat(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.jump_to(4)
        assert queue_manager.next_track() is None

    def test_wrap_with_repeat_all(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.set_repeat(RepeatMode.ALL)
        queue_manager.jump_to(4)
        track = queue_manager.next_track()
        assert track["video_id"] == "vid_01"

    def test_backward_wrap_with_repeat_all(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.set_repeat(RepeatMode.ALL)
        queue_manager.jump_to(0)
        track = queue_manager.previous_track()
        assert track["video_id"] == "vid_05"


class TestShuffle:
    def test_toggle_shuffle_on_off(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.jump_to(0)

        queue_manager.toggle_shuffle()
        assert queue_manager.shuffle_enabled
        # Current track should remain the same.
        assert queue_manager.current()["video_id"] == "vid_01"

        queue_manager.toggle_shuffle()
        assert not queue_manager.shuffle_enabled

    def test_shuffle_plays_all_tracks(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.jump_to(0)
        queue_manager.toggle_shuffle()

        seen = {queue_manager.current()["video_id"]}
        for _ in range(4):
            t = queue_manager.next_track()
            assert t is not None
            seen.add(t["video_id"])

        assert len(seen) == 5

    def test_add_while_shuffled(self, queue_manager, sample_tracks, sample_track):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.jump_to(0)
        queue_manager.toggle_shuffle()

        queue_manager.add(sample_track)
        assert queue_manager.length == 6


class TestOperations:
    def test_add_at_position(self, queue_manager, sample_tracks, sample_track):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.add(sample_track, position=2)
        assert queue_manager.length == 6
        queue_manager.jump_to(2)
        assert queue_manager.current()["video_id"] == sample_track["video_id"]

    def test_clear(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.clear()
        assert queue_manager.is_empty
        assert queue_manager.current() is None

    def test_remove(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.remove(1)
        assert queue_manager.length == 4
        queue_manager.jump_to(1)
        assert queue_manager.current()["video_id"] == "vid_03"

    def test_jump_to_valid(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        result = queue_manager.jump_to(3)
        assert result["video_id"] == "vid_04"

    def test_jump_to_invalid(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        assert queue_manager.jump_to(10) is None
        assert queue_manager.jump_to(-1) is None

    def test_cycle_repeat(self, queue_manager):
        assert queue_manager.repeat_mode == RepeatMode.OFF
        assert queue_manager.cycle_repeat() == RepeatMode.ALL
        assert queue_manager.cycle_repeat() == RepeatMode.ONE
        assert queue_manager.cycle_repeat() == RepeatMode.OFF

    def test_add_multiple(self, queue_manager, sample_tracks):
        queue_manager.add_multiple(sample_tracks)
        assert queue_manager.length == 5

    def test_move(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.move(0, 4)
        queue_manager.jump_to(0)
        assert queue_manager.current()["video_id"] == "vid_02"
        queue_manager.jump_to(4)
        assert queue_manager.current()["video_id"] == "vid_01"


class TestShuffleAdvanced:
    """Shuffle-mode tests for remove, move, add_next, previous_track, and wrap."""

    def test_remove_in_shuffle_mode(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.jump_to(0)
        queue_manager.toggle_shuffle()

        # Advance once so we're at position 1.
        next_t = queue_manager.next_track()
        assert next_t is not None
        current_vid = next_t["video_id"]

        # Remove position 0 (already played) — current should stay the same.
        queue_manager.remove(0)
        assert queue_manager.length == 4
        assert queue_manager.current()["video_id"] == current_vid

    def test_move_in_shuffle_mode(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.jump_to(0)
        queue_manager.toggle_shuffle()

        current_vid = queue_manager.current()["video_id"]
        # Move current track (position 0) to position 3.
        queue_manager.move(0, 3)
        # After move, current should still point at the same track.
        assert queue_manager.current()["video_id"] == current_vid

    def test_add_next_in_shuffle_mode(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.jump_to(0)
        queue_manager.toggle_shuffle()

        extra = {
            "video_id": "extra_1",
            "title": "Extra",
            "artist": "X",
            "artists": [],
            "album": "",
            "album_id": None,
            "duration": 100,
            "thumbnail_url": None,
            "is_video": False,
        }
        queue_manager.add_next(extra)
        assert queue_manager.length == 6
        # The next track should be the one we just added.
        nxt = queue_manager.next_track()
        assert nxt["video_id"] == "extra_1"

    def test_previous_in_shuffle_mode(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.jump_to(0)
        queue_manager.toggle_shuffle()

        first_vid = queue_manager.current()["video_id"]
        second = queue_manager.next_track()
        assert second is not None

        prev = queue_manager.previous_track()
        assert prev is not None
        assert prev["video_id"] == first_vid

    def test_previous_at_start_no_repeat(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.jump_to(0)
        queue_manager.toggle_shuffle()

        assert queue_manager.previous_track() is None

    def test_next_wraps_with_repeat_all_shuffle(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.jump_to(0)
        queue_manager.toggle_shuffle()
        queue_manager.set_repeat(RepeatMode.ALL)

        # Exhaust all tracks.
        seen = {queue_manager.current()["video_id"]}
        for _ in range(4):
            t = queue_manager.next_track()
            assert t is not None
            seen.add(t["video_id"])
        assert len(seen) == 5

        # Next should wrap (rebuild shuffle) and return a valid track.
        wrap = queue_manager.next_track()
        assert wrap is not None
        assert wrap["video_id"] in {t["video_id"] for t in sample_tracks}

    def test_next_end_no_repeat_shuffle(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.jump_to(0)
        queue_manager.toggle_shuffle()

        # Exhaust all tracks.
        for _ in range(4):
            queue_manager.next_track()

        # No repeat → None at end.
        assert queue_manager.next_track() is None

    def test_play_random(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        result = queue_manager.play_random()
        assert result is not None
        assert result["video_id"] in {t["video_id"] for t in sample_tracks}

    def test_play_random_empty(self, queue_manager):
        assert queue_manager.play_random() is None


class TestRemoveEdgeCases:
    """Edge cases for QueueManager.remove() — issue #22."""

    def test_remove_currently_playing(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.jump_to(2)
        assert queue_manager.current()["video_id"] == "vid_03"

        queue_manager.remove(2)
        assert queue_manager.length == 4
        # The audible entry finishes outside the queue; EOF selects its successor.
        assert queue_manager.current() is None
        assert queue_manager.next_track()["video_id"] == "vid_04"

    def test_remove_last_track_empties_queue(self, queue_manager):
        from tests.conftest import _make_track

        track = _make_track("only", "Only Track", "Solo Artist", 100)
        queue_manager.add(track)
        queue_manager.jump_to(0)
        assert queue_manager.current()["video_id"] == "only"

        queue_manager.remove(0)
        assert queue_manager.is_empty
        assert queue_manager.current() is None

    def test_remove_before_current_adjusts_index(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.jump_to(3)
        assert queue_manager.current()["video_id"] == "vid_04"

        queue_manager.remove(1)
        assert queue_manager.length == 4
        # Current track should still be vid_04 (index shifted down).
        assert queue_manager.current()["video_id"] == "vid_04"

    def test_remove_after_current_keeps_index(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.jump_to(1)
        assert queue_manager.current()["video_id"] == "vid_02"

        queue_manager.remove(3)
        assert queue_manager.length == 4
        assert queue_manager.current()["video_id"] == "vid_02"


class TestRemoveShuffleCurrentTrack:
    """remove() of the current track under shuffle must match the
    non-shuffle contract: finish the removed song, then select its successor."""

    def _shuffled_order(self, queue_manager, sample_tracks) -> list[str]:
        """Populate, enable shuffle, return video_ids in shuffle order."""
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.jump_to(0)
        queue_manager.toggle_shuffle()
        return [t["video_id"] for t in queue_manager.tracks]

    def test_remove_current_lands_on_next_shuffle_track(self, queue_manager, sample_tracks):
        order = self._shuffled_order(queue_manager, sample_tracks)
        queue_manager.next_track()
        assert queue_manager.current()["video_id"] == order[1]

        queue_manager.remove(1)
        assert queue_manager.current() is None
        assert queue_manager.next_track()["video_id"] == order[2]

    def test_remove_current_at_end_does_not_replay_previous(self, queue_manager, sample_tracks):
        order = self._shuffled_order(queue_manager, sample_tracks)
        queue_manager.jump_to(len(order) - 1)

        queue_manager.remove(len(order) - 1)
        assert queue_manager.current() is None
        assert queue_manager.next_track() is None

    def test_remove_before_current_keeps_current_track(self, queue_manager, sample_tracks):
        order = self._shuffled_order(queue_manager, sample_tracks)
        queue_manager.jump_to(2)

        queue_manager.remove(0)
        assert queue_manager.current()["video_id"] == order[2]

    def test_remove_after_current_keeps_current_track(self, queue_manager, sample_tracks):
        order = self._shuffled_order(queue_manager, sample_tracks)
        queue_manager.jump_to(1)

        queue_manager.remove(3)
        assert queue_manager.current()["video_id"] == order[1]

    def test_remove_only_track_shuffle_empties_queue(self, queue_manager):
        from tests.conftest import _make_track

        queue_manager.add(_make_track("only", "Only Track", "Solo Artist", 100))
        queue_manager.jump_to(0)
        queue_manager.toggle_shuffle()

        queue_manager.remove(0)
        assert queue_manager.is_empty
        assert queue_manager.current() is None
        assert queue_manager.current_index == -1


class TestRemovedAudibleBoundary:
    @pytest.mark.parametrize("shuffle", [False, True])
    @pytest.mark.parametrize("repeat", [RepeatMode.OFF, RepeatMode.ALL, RepeatMode.ONE])
    @pytest.mark.parametrize("count,position", [(1, 0), (4, 0), (4, 1), (4, 3)])
    def test_finish_then_next(self, queue_manager, shuffle, repeat, count, position):
        q = queue_manager
        # Duplicate values and even shared dicts cannot define occurrence identity.
        same = {"video_id": "A"}
        q.add_multiple([same] * count)
        if shuffle:
            q.toggle_shuffle()
        q.set_repeat(repeat)
        q.jump_to(position)
        before = q.entries
        q.remove_entry(before[position][0])
        assert q.current_track is None
        assert q.current_index == -1
        expected = before[position + 1][0] if position + 1 < count else None
        if expected is None and count > 1 and repeat == RepeatMode.ALL:
            expected = before[0][0]
        next_track = q.next_track()
        if expected is None:
            assert next_track is None
        else:
            assert next_track is same
            assert q.entries[q.current_index][0] == expected

    @pytest.mark.parametrize("shuffle", [False, True])
    def test_removing_successor_again_keeps_following_survivor(self, queue_manager, shuffle):
        q = queue_manager
        q.add_multiple([{"video_id": str(i)} for i in range(5)])
        if shuffle:
            q.toggle_shuffle()
        q.jump_to(1)
        before = q.entries
        q.remove_entry(before[1][0])
        q.remove_entry(before[2][0])
        assert q.peek_next() is before[3][1]
        assert q.next_track() is before[3][1]

    @pytest.mark.parametrize("shuffle", [False, True])
    def test_play_next_insert_at_removed_boundary(self, queue_manager, shuffle):
        q = queue_manager
        q.add_multiple([{"video_id": str(i)} for i in range(4)])
        if shuffle:
            q.toggle_shuffle()
        q.jump_to(1)
        before = q.entries
        q.remove_entry(before[1][0])
        new = [{"video_id": "X"}, {"video_id": "Y"}]
        q.add_next_multiple(new)
        assert q.next_track() is new[0]
        assert q.next_track() is new[1]
        assert q.next_track() is before[2][1]

    @pytest.mark.parametrize("shuffle", [False, True])
    def test_append_after_removed_last_starts_new_tail(self, queue_manager, shuffle):
        q = queue_manager
        q.add_multiple([{"video_id": "A"}, {"video_id": "B"}])
        if shuffle:
            q.toggle_shuffle()
        q.jump_to(1)
        q.remove(1)
        tail = {"video_id": "C"}
        q.add(tail)
        assert q.next_track() is tail

    def test_reorder_keeps_the_surviving_successor_identity(self, queue_manager):
        q = queue_manager
        q.add_multiple([{"video_id": str(i)} for i in range(5)])
        q.jump_to(1)
        successor = q.entries[2]
        q.remove(1)
        q.move(1, 3)
        q.toggle_shuffle()
        assert q.next_track() is successor[1]
        assert q.entries[q.current_index][0] == successor[0]

    def test_clear_only_changes_lifetime(self, queue_manager):
        q = queue_manager
        generation = q.generation
        q.add_multiple([{"video_id": "A"}, {"video_id": "A"}])
        q.jump_to(0)
        q.next_track()
        q.toggle_shuffle()
        q.move(0, 1)
        q.remove(0)
        assert q.generation == generation
        q.clear()
        assert q.generation == generation + 1


class TestRadioTracks:
    def test_set_radio_tracks_deduplicates(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)

        radio = [
            {"video_id": "vid_01", "title": "Dup"},
            {"video_id": "vid_new", "title": "New Track"},
        ]
        queue_manager.set_radio_tracks(radio)
        assert queue_manager.length == 6  # 5 + 1 new

    def test_set_radio_tracks_all_new(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)

        radio = [
            {"video_id": "new_1", "title": "New 1"},
            {"video_id": "new_2", "title": "New 2"},
        ]
        queue_manager.set_radio_tracks(radio)
        assert queue_manager.length == 7

    def test_set_radio_tracks_empty_shuffle_rebuilds(
        self, queue_manager, sample_tracks, monkeypatch
    ):
        """Radio tracks into an empty shuffled queue must rebuild the shuffle
        order — the old piecemeal path skipped _rebuild_shuffle (regression)."""
        queue_manager._shuffle = True  # shuffle on while queue is still empty

        calls: list[bool] = []
        original = queue_manager._rebuild_shuffle

        def spy(keep_current: bool = True) -> None:
            calls.append(keep_current)
            original(keep_current)

        monkeypatch.setattr(queue_manager, "_rebuild_shuffle", spy)

        queue_manager.set_radio_tracks(sample_tracks)

        assert calls == [False]  # full rebuild, matching add_multiple on an empty queue
        assert sorted(queue_manager._shuffle_order) == list(range(len(sample_tracks)))


class TestQueuePositionTracking:
    """Tests for real_index and remaining_tracks properties."""

    def test_real_index_without_shuffle(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.jump_to(2)
        assert queue_manager.real_index == 2

    def test_real_index_with_shuffle_resolves_to_tracks_index(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.toggle_shuffle()
        queue_manager.jump_to(0)
        assert 0 <= queue_manager.real_index < len(sample_tracks)

    def test_remaining_tracks_without_shuffle(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.jump_to(2)
        assert queue_manager.remaining_tracks == 2  # tracks 3 and 4

    def test_remaining_tracks_with_shuffle(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.toggle_shuffle()
        queue_manager.jump_to(0)
        assert queue_manager.remaining_tracks == len(sample_tracks) - 1


class TestContextId:
    def test_default_is_none(self, queue_manager):
        assert queue_manager.current_context_id is None

    def test_set_and_read_back(self, queue_manager):
        queue_manager.set_context("PLABCD")
        assert queue_manager.current_context_id == "PLABCD"

    def test_set_to_none_clears(self, queue_manager):
        queue_manager.set_context("PLABCD")
        queue_manager.set_context(None)
        assert queue_manager.current_context_id is None

    def test_clear_does_not_affect_context(self, queue_manager, sample_track):
        # clear() resets tracks/shuffle state but does NOT reset context_id —
        # the context only changes via explicit set_context() at the
        # playback-start sites.
        queue_manager.set_context("PLABCD")
        queue_manager.add(sample_track)
        queue_manager.clear()
        assert queue_manager.current_context_id == "PLABCD"


class TestRadioSeeds:
    def test_default_is_none(self, queue_manager):
        assert queue_manager.radio_seeds is None

    def test_set_and_read_back(self, queue_manager):
        seeds = [{"title": "Song A"}, {"title": "Song B"}]
        queue_manager.radio_seeds = seeds
        assert queue_manager.radio_seeds is seeds

    def test_clear_resets_to_none(self, queue_manager, sample_track):
        queue_manager.radio_seeds = [{"title": "Song A"}]
        queue_manager.add(sample_track)
        queue_manager.clear()
        assert queue_manager.radio_seeds is None


class TestPeekNext:
    """peek_next is prefetch-critical: it must never advance the position."""

    def test_empty_queue_is_none(self, queue_manager):
        assert queue_manager.peek_next() is None

    def test_returns_next_without_advancing(self, queue_manager, sample_tracks):
        queue_manager.add_multiple(sample_tracks)
        queue_manager.jump_to(0)
        assert queue_manager.peek_next() == sample_tracks[1]
        # Position must be unchanged after peeking.
        assert queue_manager.current() == sample_tracks[0]

    def test_last_track_repeat_off_is_none(self, queue_manager, sample_tracks):
        queue_manager.add_multiple(sample_tracks)
        queue_manager.jump_to(len(sample_tracks) - 1)
        assert queue_manager.peek_next() is None

    def test_last_track_repeat_all_wraps_to_first(self, queue_manager, sample_tracks):
        queue_manager.add_multiple(sample_tracks)
        queue_manager.jump_to(len(sample_tracks) - 1)
        queue_manager.set_repeat(RepeatMode.ALL)
        assert queue_manager.peek_next() == sample_tracks[0]

    def test_repeat_one_returns_current(self, queue_manager, sample_tracks):
        queue_manager.add_multiple(sample_tracks)
        queue_manager.jump_to(2)
        queue_manager.set_repeat(RepeatMode.ONE)
        assert queue_manager.peek_next() == sample_tracks[2]

    def test_shuffle_end_of_order_is_none(self, queue_manager, sample_tracks):
        queue_manager.add_multiple(sample_tracks)
        queue_manager.jump_to(0)
        queue_manager.toggle_shuffle()
        # Jump to the final shuffle position — no known next to prefetch.
        queue_manager.jump_to(len(sample_tracks) - 1)
        assert queue_manager.peek_next() is None

    def test_shuffle_mid_order_returns_a_track(self, queue_manager, sample_tracks):
        queue_manager.add_multiple(sample_tracks)
        queue_manager.jump_to(0)
        queue_manager.toggle_shuffle()
        queue_manager.jump_to(0)
        nxt = queue_manager.peek_next()
        assert nxt in sample_tracks


def _extra(video_id: str) -> dict:
    """A minimal standardized track dict for add_next_multiple tests."""
    return {
        "video_id": video_id,
        "title": video_id,
        "artist": "X",
        "artists": [],
        "album": "",
        "album_id": None,
        "duration": 100,
        "thumbnail_url": None,
        "is_video": False,
    }


class TestAddNextMultiple:
    """add_next_multiple: insert N tracks right after the current track, in order."""

    def test_empty_input_is_noop(self, queue_manager, sample_tracks):
        queue_manager.add_multiple(sample_tracks)
        queue_manager.jump_to(0)
        queue_manager.add_next_multiple([])
        assert queue_manager.length == 5

    def test_empty_queue_inserts_at_front_in_order(self, queue_manager):
        queue_manager.add_next_multiple([_extra("x1"), _extra("x2")])
        assert queue_manager.length == 2
        assert [t["video_id"] for t in queue_manager.tracks] == ["x1", "x2"]

    def test_mid_queue_inserts_after_current_in_order(self, queue_manager, sample_tracks):
        queue_manager.add_multiple(sample_tracks)
        queue_manager.jump_to(1)  # current = vid_02
        queue_manager.add_next_multiple([_extra("x1"), _extra("x2")])
        vids = [t["video_id"] for t in queue_manager.tracks]
        assert vids == ["vid_01", "vid_02", "x1", "x2", "vid_03", "vid_04", "vid_05"]
        # Current track is unaffected by the insertion after it.
        assert queue_manager.current()["video_id"] == "vid_02"

    def test_next_plays_inserted_tracks_in_order(self, queue_manager, sample_tracks):
        queue_manager.add_multiple(sample_tracks)
        queue_manager.jump_to(0)  # current = vid_01
        queue_manager.add_next_multiple([_extra("x1"), _extra("x2")])
        assert queue_manager.next_track()["video_id"] == "x1"
        assert queue_manager.next_track()["video_id"] == "x2"
        assert queue_manager.next_track()["video_id"] == "vid_02"

    def test_order_preserved_for_many(self, queue_manager, sample_tracks):
        queue_manager.add_multiple(sample_tracks)
        queue_manager.jump_to(2)
        queue_manager.add_next_multiple([_extra(f"x{i}") for i in range(5)])
        played = [queue_manager.next_track()["video_id"] for _ in range(5)]
        assert played == ["x0", "x1", "x2", "x3", "x4"]

    def test_shuffle_inserts_next_in_order(self, queue_manager, sample_tracks):
        queue_manager.add_multiple(sample_tracks)
        queue_manager.jump_to(0)
        queue_manager.toggle_shuffle()
        queue_manager.add_next_multiple([_extra("x1"), _extra("x2")])
        assert queue_manager.length == 7
        # Under shuffle the inserted tracks are the next N to play, in order.
        assert queue_manager.next_track()["video_id"] == "x1"
        assert queue_manager.next_track()["video_id"] == "x2"

    def test_duplicates_inserted_as_is(self, queue_manager, sample_tracks):
        # No dedup, consistent with add_next().
        queue_manager.add_multiple(sample_tracks)
        queue_manager.jump_to(0)
        queue_manager.add_next_multiple([sample_tracks[0]])  # vid_01 already present
        assert queue_manager.length == 6
        assert queue_manager.next_track()["video_id"] == "vid_01"


class TestShuffleOffKeepsThePlayingOccurrence:
    """Turning shuffle off must keep the track reached under shuffle.

    Only ``jump_to`` kept ``_current_index`` in sync while shuffled, so after
    ``next_track``/``previous_track`` the toggle used to snap back to the
    track that was playing when shuffle went on.
    """

    def _same_song(self, n: int) -> list[dict]:
        return [
            {"video_id": "same", "title": "Same", "artist": "A", "duration": 100} for _ in range(n)
        ]

    def test_shuffle_off_keeps_the_track_reached_by_next(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.jump_to(0)
        queue_manager.toggle_shuffle()
        reached = queue_manager.next_track()
        assert reached is not None

        queue_manager.toggle_shuffle()

        assert queue_manager.current_track is reached
        position = sample_tracks.index(reached)
        assert queue_manager.current_index == position
        following = sample_tracks[position + 1] if position + 1 < len(sample_tracks) else None
        assert queue_manager.next_track() is following

    def test_shuffle_off_keeps_the_track_reached_by_previous(self, queue_manager, sample_tracks):
        for t in sample_tracks:
            queue_manager.add(t)
        queue_manager.jump_to(0)
        queue_manager.toggle_shuffle()
        queue_manager.next_track()
        queue_manager.next_track()
        reached = queue_manager.previous_track()
        assert reached is not None

        queue_manager.toggle_shuffle()

        assert queue_manager.current_track is reached
        assert queue_manager.current_index == sample_tracks.index(reached)

    def test_shuffle_off_keeps_the_occurrence_among_identical_songs(self):
        """Asserted by entry id: the same song six times, random walks, then off."""
        for seed in range(40):
            rng = random.Random(seed)
            queue = QueueManager()
            queue.add_multiple(self._same_song(6))
            queue.jump_to(rng.randrange(6))
            queue.toggle_shuffle()
            for _ in range(rng.randrange(1, 12)):
                step = rng.choice(("next", "previous", "jump"))
                if step == "next":
                    queue.next_track()
                elif step == "previous":
                    queue.previous_track()
                else:
                    queue.jump_to(rng.randrange(6))
            playing_id, playing = queue.entries[queue.current_index]

            queue.toggle_shuffle()

            entry_id, track = queue.entries[queue.current_index]
            assert entry_id == playing_id, f"seed {seed}"
            assert track is playing and queue.current_track is playing
