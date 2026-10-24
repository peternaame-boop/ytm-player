"""QueueManager.replace_upcoming: an Up next chip swaps the tracks after the playing one."""

from __future__ import annotations

from tests.conftest import _make_track
from ytm_player.services.queue import QueueManager


def _ids(tracks) -> list[str]:
    return [t["video_id"] for t in tracks]


def _queue(n: int = 5, current: int = 1) -> QueueManager:
    queue = QueueManager()
    queue.add_multiple([_make_track(video_id=f"q{i}") for i in range(n)])
    queue.jump_to(current)
    return queue


def test_played_and_current_stay_and_the_rest_is_replaced():
    queue = _queue()
    added = queue.replace_upcoming([_make_track(video_id=v) for v in ("n1", "n2")])
    assert added == 2
    assert _ids(queue.tracks) == ["q0", "q1", "n1", "n2"]
    assert queue.current_track["video_id"] == "q1"
    assert queue.next_track()["video_id"] == "n1"


def test_tracks_already_kept_are_not_added_again():
    queue = _queue()
    added = queue.replace_upcoming([_make_track(video_id=v) for v in ("q1", "q0", "n1", "n1")])
    assert added == 1
    assert _ids(queue.tracks) == ["q0", "q1", "n1"]


def test_the_revision_counts_each_swap_and_the_generation_stays():
    queue = _queue()
    generation, revision = queue.generation, queue.revision
    queue.replace_upcoming([_make_track(video_id="n1")])
    assert queue.generation == generation
    assert queue.revision == revision + 1


def test_with_shuffle_on_the_played_order_stays_and_only_new_tracks_follow():
    queue = _queue(n=8, current=0)
    queue.toggle_shuffle()
    queue.next_track()
    queue.next_track()
    played = _ids(queue.tracks[: queue.current_index + 1])
    playing = queue.current_track["video_id"]
    queue.replace_upcoming([_make_track(video_id=f"n{i}") for i in range(4)])
    visible = _ids(queue.tracks)
    assert visible[: len(played)] == played
    assert sorted(visible[len(played) :]) == ["n0", "n1", "n2", "n3"]
    assert queue.current_track["video_id"] == playing
    assert queue.next_track()["video_id"].startswith("n")
    # Leaving shuffle keeps the playing track.
    queue.toggle_shuffle()
    assert queue.current_track["video_id"].startswith("n")


def test_after_the_playing_track_was_removed_the_first_new_track_plays_next():
    queue = _queue(current=2)
    queue.remove(2)  # the playing occurrence leaves the queue but keeps sounding
    queue.replace_upcoming([_make_track(video_id="n1"), _make_track(video_id="n2")])
    assert _ids(queue.tracks) == ["q0", "q1", "n1", "n2"]
    assert queue.next_track()["video_id"] == "n1"
