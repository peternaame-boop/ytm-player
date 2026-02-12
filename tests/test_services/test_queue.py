"""Tests for ytm_player.services.queue.QueueManager."""


from ytm_player.services.queue import RepeatMode


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

        extra = {"video_id": "extra_1", "title": "Extra", "artist": "X",
                 "artists": [], "album": "", "album_id": None,
                 "duration": 100, "thumbnail_url": None, "is_video": False}
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
