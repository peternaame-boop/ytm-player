"""Queue page shows which tracks autoplay added, and lets the user veto them.

Autoplay rows carry a ↻ in the row-label column (the playing row keeps ▶)
and the footer counts them. Deleting one from the queue page keeps the
next autoplay refill from adding it straight back.
"""

from __future__ import annotations

from typing import Any

from textual.app import App, ComposeResult
from textual.widgets import Static

from ytm_player.config.keymap import Action
from ytm_player.services.queue import QueueManager
from ytm_player.ui.pages.queue import QueuePage
from ytm_player.ui.widgets.track_table import TrackTable


class _FakePlayer:
    """Just enough Player surface for QueuePage/TrackTable mounting."""

    current_track: dict | None = None

    def on(self, *_args: Any) -> None: ...

    def off(self, *_args: Any) -> None: ...


class _QueueHost(App):
    """Minimal host app exposing the attributes QueuePage reads."""

    def __init__(self, queue: QueueManager) -> None:
        super().__init__()
        self.queue = queue
        self.player = _FakePlayer()

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        yield QueuePage()


def _track(video_id: str) -> dict:
    return {
        "video_id": video_id,
        "title": f"song {video_id}",
        "artist": "A",
        "artists": [{"name": "A", "id": "1"}],
        "album": "",
        "album_id": None,
        "duration": 120,
        "thumbnail_url": None,
        "is_video": False,
    }


def _make_queue() -> QueueManager:
    queue = QueueManager()
    queue.add_multiple([_track("u1"), _track("u2")])
    queue.jump_to(0)
    queue.set_radio_tracks([_track("a1"), _track("a2")], autoplay=True)
    return queue


def _labels(table: TrackTable) -> list[str]:
    return [str(table.rows[key].label) for key in table._row_keys]


def _footer(app: App) -> str:
    return str(app.query_one("#queue-footer", Static).render())


async def test_autoplay_rows_are_labelled_and_counted():
    queue = _make_queue()
    app = _QueueHost(queue)
    async with app.run_test() as pilot:
        table = app.query_one("#queue-table", TrackTable)
        table.set_playing("u1")
        await pilot.pause()

        assert _labels(table) == ["▶", " ", "↻", "↻"]
        assert "↻ 2 added by autoplay" in _footer(app)


async def test_playing_autoplay_row_gets_its_label_back_when_playback_moves_on():
    queue = _make_queue()
    app = _QueueHost(queue)
    async with app.run_test() as pilot:
        table = app.query_one("#queue-table", TrackTable)
        table.set_playing("a1")
        await pilot.pause()
        assert _labels(table)[2] == "▶"

        table.set_playing("a2")
        await pilot.pause()

        assert _labels(table)[2:] == ["↻", "▶"]


async def test_queue_dicts_are_not_flagged():
    """The ↻ flag lives on the table's copies, never on the queue's tracks."""
    queue = _make_queue()
    app = _QueueHost(queue)
    async with app.run_test() as pilot:
        await pilot.pause()

        assert not any("_autoplay" in track for track in queue.tracks)


async def test_deleting_an_autoplay_row_vetoes_it():
    queue = _make_queue()
    app = _QueueHost(queue)
    async with app.run_test() as pilot:
        page = app.query_one(QueuePage)
        table = app.query_one("#queue-table", TrackTable)
        table.move_cursor(row=2)  # a1
        await pilot.pause()

        await page.handle_action(Action.DELETE_ITEM)
        queue.set_radio_tracks([_track("a1"), _track("a3")], autoplay=True)
        page._refresh_queue()
        await pilot.pause()

        assert [t["video_id"] for t in queue.tracks] == ["u1", "u2", "a2", "a3"]
        assert "↻ 2 added by autoplay" in _footer(app)


async def test_footer_has_no_autoplay_note_without_autoplay_rows():
    queue = QueueManager()
    queue.add_multiple([_track("u1")])
    app = _QueueHost(queue)
    async with app.run_test() as pilot:
        await pilot.pause()

        assert _footer(app) == "Tracks: 1"
