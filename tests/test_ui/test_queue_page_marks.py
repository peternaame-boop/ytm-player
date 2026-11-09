"""Queue page marks ride on queue entry ids.

Every re-render of the queue goes through ``refresh_tracks`` keyed by the
entry id of each occurrence, so a mark stays on the very entry it was put
on — through insertions ahead of it, duplicates of the same song, reorders
and shuffle — and disappears with that entry. An empty queue clears the
table and the marks, not just the widget's visibility.
"""

from __future__ import annotations

from typing import Any

from textual.app import App, ComposeResult

from ytm_player.config.keymap import Action
from ytm_player.services.queue import QueueManager
from ytm_player.ui.pages.queue import QueuePage
from ytm_player.ui.widgets.track_table import TrackTable


class _FakePlayer:
    current_track: dict | None = None

    def on(self, *_args: Any) -> None: ...

    def off(self, *_args: Any) -> None: ...


class _QueueHost(App):
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


def _track(video_id: str, title: str) -> dict:
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


def _make_queue() -> QueueManager:
    queue = QueueManager()
    queue.add_multiple([_track("t1", "one"), _track("x", "same song"), _track("t3", "three")])
    return queue


def _glyphs(table: TrackTable) -> str:
    return "".join(str(table.get_row_at(i)[0]) for i in range(table.row_count))


def _titles(table: TrackTable) -> list[str]:
    return [t["title"] for t in table.visible_tracks]


async def _mark_row(page: QueuePage, table: TrackTable, row: int) -> None:
    table.move_cursor(row=row)
    await page.handle_action(Action.MARK_TOGGLE)


async def test_inserting_the_same_song_before_a_marked_duplicate_keeps_the_mark_on_the_original():
    queue = _make_queue()
    app = _QueueHost(queue)
    async with app.run_test() as pilot:
        page = app.query_one(QueuePage)
        table = page.query_one("#queue-table", TrackTable)
        await pilot.pause()
        await _mark_row(page, table, 1)  # "same song" at row 1
        generation = table.selection_generation

        queue.jump_to(0)
        queue.add_next(_track("x", "same song (new copy)"))  # lands at row 1
        page._refresh_queue()
        await pilot.pause()

        assert _titles(table) == ["one", "same song (new copy)", "same song", "three"]
        assert _glyphs(table) == "  ✓ "
        assert table.selection_generation == generation


async def test_removing_either_duplicate_leaves_the_other_alone():
    queue = _make_queue()
    queue.jump_to(0)
    queue.add_next(_track("x", "same song (new copy)"))
    app = _QueueHost(queue)
    async with app.run_test() as pilot:
        page = app.query_one(QueuePage)
        table = page.query_one("#queue-table", TrackTable)
        await pilot.pause()
        await _mark_row(page, table, 2)  # the original "same song"

        queue.remove(1)  # the unmarked copy ahead of it
        page._refresh_queue()
        await pilot.pause()
        assert _titles(table) == ["one", "same song", "three"]
        assert _glyphs(table) == " ✓ "

        generation = table.selection_generation
        queue.remove(1)  # now the marked one
        page._refresh_queue()
        await pilot.pause()
        assert _titles(table) == ["one", "three"]
        assert _glyphs(table) == "  "
        assert table.marked_count == 0
        assert table.selection_generation != generation
        assert not table.has_class("-marked")


async def test_the_same_dict_queued_twice_is_two_independent_rows():
    queue = QueueManager()
    shared = _track("s", "shared")
    queue.add(shared)
    queue.add(shared)
    app = _QueueHost(queue)
    async with app.run_test() as pilot:
        page = app.query_one(QueuePage)
        table = page.query_one("#queue-table", TrackTable)
        await pilot.pause()
        await _mark_row(page, table, 1)
        assert _glyphs(table) == " ✓"

        page._refresh_queue()
        await pilot.pause()
        assert _glyphs(table) == " ✓"

        queue.add_next(_track("y", "another"))  # nothing current: inserted at the front
        page._refresh_queue()
        await pilot.pause()
        assert _titles(table) == ["another", "shared", "shared"]
        assert _glyphs(table) == "  ✓"


async def test_reorder_keeps_the_mark_on_the_moved_entry_and_the_cursor_with_it():
    queue = _make_queue()
    app = _QueueHost(queue)
    async with app.run_test() as pilot:
        page = app.query_one(QueuePage)
        table = page.query_one("#queue-table", TrackTable)
        await pilot.pause()
        await _mark_row(page, table, 2)  # "three"

        await page.handle_action(Action.REORDER_UP, 2)
        await pilot.pause()

        assert _titles(table) == ["three", "one", "same song"]
        assert _glyphs(table) == "✓  "
        assert table.cursor_row == 0


async def test_shuffle_keeps_marks_on_their_entries():
    queue = QueueManager()
    queue.add_multiple([_track(f"v{i}", f"song {i}") for i in range(8)])
    app = _QueueHost(queue)
    async with app.run_test() as pilot:
        page = app.query_one(QueuePage)
        table = page.query_one("#queue-table", TrackTable)
        await pilot.pause()
        await _mark_row(page, table, 2)
        await _mark_row(page, table, 5)
        marked_ids = {queue.entries[2][0], queue.entries[5][0]}

        queue.toggle_shuffle()
        page._refresh_queue()
        await pilot.pause()

        expected = "".join("✓" if eid in marked_ids else " " for eid, _ in queue.entries)
        assert _glyphs(table) == expected
        assert table.marked_count == 2

        queue.toggle_shuffle()
        page._refresh_queue()
        await pilot.pause()
        assert _glyphs(table) == "  ✓  ✓  "


async def test_range_mode_survives_a_background_insertion():
    queue = _make_queue()
    app = _QueueHost(queue)
    async with app.run_test() as pilot:
        page = app.query_one(QueuePage)
        table = page.query_one("#queue-table", TrackTable)
        await pilot.pause()
        await page.handle_action(Action.MARK_RANGE)  # anchor "one"
        await page.handle_action(Action.MOVE_DOWN)
        await pilot.pause()
        assert _glyphs(table) == "✓✓ "

        queue.add_next(_track("n", "new"))  # nothing current: front of the queue
        page._refresh_queue()
        await pilot.pause()
        assert _titles(table) == ["new", "one", "same song", "three"]
        assert _glyphs(table) == " ✓✓ "
        assert table._range_mode is True

        await page.handle_action(Action.MOVE_DOWN)
        await pilot.pause()
        assert _glyphs(table) == " ✓✓✓"


async def test_clearing_the_queue_clears_the_table_and_the_marks():
    queue = _make_queue()
    app = _QueueHost(queue)
    async with app.run_test() as pilot:
        page = app.query_one(QueuePage)
        table = page.query_one("#queue-table", TrackTable)
        await pilot.pause()
        await _mark_row(page, table, 1)
        assert table.has_class("-marked")

        queue.clear()
        page._refresh_queue()
        await pilot.pause()

        assert table.row_count == 0
        assert table.track_count == 0
        assert table.marked_count == 0
        assert not table.has_class("-marked")
        assert table.display is False

        queue.add_multiple([_track("a", "after")])
        page._refresh_queue()
        await pilot.pause()
        assert table.display is True
        assert _glyphs(table) == " "
