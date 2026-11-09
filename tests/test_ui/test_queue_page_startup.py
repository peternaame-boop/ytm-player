"""The Queue page must mount while the app has no player yet.

After a failed start the app stays up for two seconds before exiting and
the key handler is live; ``z`` opens this page with ``app.player`` None.
"""

from __future__ import annotations

from textual.app import App, ComposeResult

from ytm_player.services.queue import QueueManager
from ytm_player.ui.pages.queue import QueuePage


class _NoPlayerHost(App):
    def __init__(self) -> None:
        super().__init__()
        self.queue = QueueManager()
        self.player = None

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        yield QueuePage()


async def test_queue_page_mounts_without_a_player():
    app = _NoPlayerHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(QueuePage)
        assert page._track_change_callback is None
