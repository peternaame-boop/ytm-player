"""The playback-bar shuffle button re-renders the Queue page after toggling."""

from __future__ import annotations

from unittest.mock import MagicMock

from textual.app import App, ComposeResult

from ytm_player.services.queue import QueueManager
from ytm_player.ui.playback_bar import _ShuffleButton


class _Host(App):
    """Just the button, plus the host attributes its click handler reads."""

    def __init__(self) -> None:
        super().__init__()
        self.queue = QueueManager()
        self.queue.add_multiple(
            [
                {"video_id": f"v{i}", "title": f"T{i}", "artist": "A", "duration": 60}
                for i in range(3)
            ]
        )
        self.queue.jump_to(0)
        self._refresh_queue_page = MagicMock()

    def compose(self) -> ComposeResult:
        yield _ShuffleButton(id="pb-shuffle")


async def test_click_toggles_shuffle_and_refreshes_the_queue_page():
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.click("#pb-shuffle")

        assert app.queue.shuffle_enabled
        app._refresh_queue_page.assert_called_once()


async def test_locked_button_changes_nothing():
    app = _Host()
    async with app.run_test() as pilot:
        app.query_one(_ShuffleButton).locked = True
        await pilot.pause()

        await pilot.click("#pb-shuffle")

        assert not app.queue.shuffle_enabled
        app._refresh_queue_page.assert_not_called()
