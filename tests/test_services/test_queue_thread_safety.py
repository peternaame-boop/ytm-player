"""Thread-safety tests for QueueManager."""

import threading

from ytm_player.services.queue import QueueManager


def _make_track(i: int) -> dict:
    return {
        "video_id": f"vid_{i:03d}",
        "title": f"Track {i}",
        "artist": "Artist",
        "artists": [],
        "album": "",
        "album_id": None,
        "duration": 100,
        "thumbnail_url": None,
        "is_video": False,
    }


class TestConcurrentAdds:
    def test_parallel_adds_no_lost_tracks(self):
        """Multiple threads adding tracks should not lose any."""
        qm = QueueManager()
        barrier = threading.Barrier(4)

        def add_batch(start: int) -> None:
            barrier.wait()
            for i in range(start, start + 50):
                qm.add(_make_track(i))

        threads = [threading.Thread(target=add_batch, args=(s,)) for s in range(0, 200, 50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert qm.length == 200

    def test_parallel_add_and_navigate(self):
        """One thread adds while another navigates — no crash."""
        qm = QueueManager()
        for i in range(10):
            qm.add(_make_track(i))
        qm.jump_to(0)

        errors: list[Exception] = []

        def add_tracks() -> None:
            try:
                for i in range(10, 60):
                    qm.add(_make_track(i))
            except Exception as e:
                errors.append(e)

        def navigate() -> None:
            try:
                for _ in range(50):
                    qm.next_track()
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=add_tracks)
        t2 = threading.Thread(target=navigate)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors
        assert qm.length == 60


class TestConcurrentOperations:
    def test_parallel_add_remove(self):
        """Concurrent add and remove should not corrupt state."""
        qm = QueueManager()
        for i in range(100):
            qm.add(_make_track(i))

        errors: list[Exception] = []

        def add_tracks() -> None:
            try:
                for i in range(100, 150):
                    qm.add(_make_track(i))
            except Exception as e:
                errors.append(e)

        def remove_tracks() -> None:
            try:
                for _ in range(50):
                    qm.remove(0)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=add_tracks)
        t2 = threading.Thread(target=remove_tracks)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors
        assert qm.length == 100  # 100 + 50 added - 50 removed

    def test_parallel_shuffle_toggle(self):
        """Toggling shuffle while navigating should not crash."""
        qm = QueueManager()
        for i in range(20):
            qm.add(_make_track(i))
        qm.jump_to(0)

        errors: list[Exception] = []

        def toggle_shuffle() -> None:
            try:
                for _ in range(50):
                    qm.toggle_shuffle()
            except Exception as e:
                errors.append(e)

        def navigate() -> None:
            try:
                for _ in range(50):
                    qm.next_track()
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=toggle_shuffle)
        t2 = threading.Thread(target=navigate)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors
