"""QueueManager entry ids: one per queue occurrence, stable across moves and shuffle.

The ids back the queue page's marks — a mark has to stay on the very entry
it was put on even when the same track (or the same dict object) is queued
twice, and go away with that entry. They never enter the track dicts.
"""

from __future__ import annotations

from ytm_player.services.queue import QueueManager


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


def _ids(queue: QueueManager) -> list[int]:
    return [entry_id for entry_id, _ in queue.entries]


def _aligned(queue: QueueManager) -> bool:
    """Entries pair each id with the track ``tracks`` shows at that position."""
    entries = queue.entries
    return (
        len(queue._entry_ids) == len(queue._tracks)
        and [t for _, t in entries] == list(queue.tracks)
        and len(set(_ids(queue))) == len(entries)
    )


def test_every_insertion_path_mints_distinct_aligned_ids():
    queue = QueueManager()
    queue.add(_track("a"))
    queue.add_multiple([_track("b"), _track("c")])
    queue.jump_to(0)
    queue.add_next(_track("d"))
    queue.add_next_multiple([_track("e"), _track("f")])
    queue.add(_track("g"), position=2)
    queue.set_radio_tracks([_track("h"), _track("a")])  # "a" is skipped as a dupe

    assert [t["video_id"] for t in queue.tracks] == ["a", "e", "g", "f", "d", "b", "c", "h"]
    assert _aligned(queue)
    assert len(set(_ids(queue))) == 8


def test_ids_stay_out_of_the_track_dicts():
    queue = QueueManager()
    track = _track("a")
    queue.add(track)
    assert track == _track("a")
    assert queue.tracks[0] is track


def test_same_dict_object_inserted_twice_gets_two_ids():
    queue = QueueManager()
    shared = _track("same")
    queue.add(shared)
    queue.add(shared)

    ids = _ids(queue)
    assert queue.tracks[0] is queue.tracks[1] is shared
    assert len(ids) == 2 and ids[0] != ids[1]


def test_removal_drops_that_entry_only():
    queue = QueueManager()
    queue.add_multiple([_track("x"), _track("x"), _track("y")])
    first, second, third = _ids(queue)

    queue.remove(0)

    assert _ids(queue) == [second, third]
    assert _aligned(queue)


def test_move_carries_the_id_with_the_track():
    queue = QueueManager()
    queue.add_multiple([_track("a"), _track("b"), _track("c")])
    a, b, c = _ids(queue)

    queue.move(0, 2)

    assert [t["video_id"] for t in queue.tracks] == ["b", "c", "a"]
    assert _ids(queue) == [b, c, a]


def test_shuffle_keeps_ids_paired_with_their_tracks():
    queue = QueueManager()
    queue.add_multiple([_track(f"v{i}") for i in range(6)])
    by_id = {entry_id: track["video_id"] for entry_id, track in queue.entries}

    queue.toggle_shuffle()
    shuffled = queue.entries
    assert sorted(entry_id for entry_id, _ in shuffled) == sorted(by_id)
    assert all(by_id[entry_id] == track["video_id"] for entry_id, track in shuffled)
    assert _aligned(queue)

    # Insertions, a move and a removal under shuffle keep the pairing.
    queue.jump_to(0)
    queue.add_next(_track("next"))
    queue.add_next_multiple([_track("n1"), _track("n2")])
    queue.add(_track("tail"))
    queue.move(1, 3)
    queue.remove(2)
    assert _aligned(queue)

    queue.toggle_shuffle()
    assert _aligned(queue)


def test_clear_empties_the_entries():
    queue = QueueManager()
    queue.add_multiple([_track("a"), _track("b")])
    queue.clear()
    assert queue.entries == ()
    assert _aligned(queue)
    # Ids keep counting up after a clear — a new entry never reuses an old id.
    queue.add(_track("c"))
    assert _ids(queue) == [3]
