"""Turning Shuffle lock on for the playing playlist re-renders the Queue page."""

from __future__ import annotations

from unittest.mock import MagicMock

from ytm_player.services.queue import QueueManager
from ytm_player.ui.pages.library import LibraryPage


def _page(queue_context: str) -> MagicMock:
    """A Library page for playlist PL1 whose host queue plays *queue_context*."""
    page = MagicMock()
    page._active_playlist_id = "PL1"
    host = page.app
    host.queue = QueueManager()
    host.queue.add_multiple(
        [{"video_id": f"v{i}", "title": f"T{i}", "artist": "A", "duration": 60} for i in range(3)]
    )
    host.queue.jump_to(0)
    host.queue.set_context(queue_context)
    host.shuffle_prefs.get.return_value = False
    return page


def test_locking_the_playing_playlist_forces_shuffle_and_refreshes():
    page = _page("PL1")

    LibraryPage._toggle_shuffle_lock(page)

    host = page.app
    host.shuffle_prefs.set.assert_called_once_with("PL1", True)
    assert host.queue.shuffle_enabled
    host._refresh_queue_page.assert_called_once()


def test_locking_another_playlist_leaves_the_queue_alone():
    page = _page("PL2")

    LibraryPage._toggle_shuffle_lock(page)

    host = page.app
    host.shuffle_prefs.set.assert_called_once_with("PL1", True)
    assert not host.queue.shuffle_enabled
    host._refresh_queue_page.assert_not_called()
