"""Queue page d/J/K must act on the queue index, not the visible row.

After the user sorts (header click / SORT_* actions) or filters the
table, ``cursor_row`` is a visible-row index that no longer matches the
queue position. Selection already maps through the table's view — these
tests pin the same mapping for the mutation paths (remove/move).
"""

from __future__ import annotations

import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from textual.app import App, ComposeResult

from ytm_player.app._ipc import IPCMixin
from ytm_player.app._keys import KeyHandlingMixin
from ytm_player.app._track_actions import TrackActionsMixin
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
    # Queue order t1..t4; titles reverse-alphabetical so a title sort
    # visibly diverges from queue order.
    queue.add_multiple(
        [
            _track("t1", "d-song"),
            _track("t2", "c-song"),
            _track("t3", "b-song"),
            _track("t4", "a-song"),
        ]
    )
    return queue


def _ids(queue: QueueManager) -> list[str]:
    return [t["video_id"] for t in queue.tracks]


def _entry_ids(queue: QueueManager) -> list[int]:
    return [entry_id for entry_id, _ in queue.entries]


def _shown(table: TrackTable) -> list[str]:
    return [t["video_id"] for t in table.visible_tracks]


def _cursor_entry(table: TrackTable) -> object:
    index = table.selected_original_index
    return table.occurrence_key(index) if index is not None else None


async def test_delete_removes_highlighted_track_after_sort():
    queue = _make_queue()
    app = _QueueHost(queue)
    async with app.run_test() as pilot:
        page = app.query_one(QueuePage)
        table = app.query_one("#queue-table", TrackTable)
        table.sort_by("title")  # visible: a,b,c,d = queue index 3,2,1,0
        table.move_cursor(row=0)  # highlight "a-song" (queue index 3)
        await pilot.pause()

        await page.handle_action(Action.DELETE_ITEM)

        # Pre-fix this removed queue index 0 ("d-song"), the positional twin.
        assert _ids(queue) == ["t1", "t2", "t3"]


async def test_delete_removes_highlighted_track_in_filtered_view():
    queue = _make_queue()
    app = _QueueHost(queue)
    async with app.run_test() as pilot:
        page = app.query_one(QueuePage)
        table = app.query_one("#queue-table", TrackTable)
        # Bypass apply_filter's debounce timer for determinism.
        table._filter_text = "b-song"
        table._execute_filter()  # visible: only "b-song" (queue index 2)
        table.move_cursor(row=0)
        await pilot.pause()

        await page.handle_action(Action.DELETE_ITEM)

        assert _ids(queue) == ["t1", "t2", "t4"]


async def test_move_up_moves_highlighted_track_after_sort():
    queue = _make_queue()
    app = _QueueHost(queue)
    async with app.run_test() as pilot:
        page = app.query_one(QueuePage)
        table = app.query_one("#queue-table", TrackTable)
        table.sort_by("title")  # visible: a,b,c,d = queue index 3,2,1,0
        table.move_cursor(row=1)  # highlight "b-song" (queue index 2)
        await pilot.pause()

        await page.handle_action(Action.REORDER_UP)

        # b-song moves one QUEUE position earlier. Pre-fix this moved
        # visible row 1 to row 0 → t2,t1,t3,t4 (wrong track).
        assert _ids(queue) == ["t1", "t3", "t2", "t4"]
        # The sort is cleared so the view shows queue order, with the
        # cursor following the moved track.
        assert _shown(table) == _ids(queue)
        assert table.cursor_row == 1


async def test_move_down_still_works_without_sort_or_filter():
    queue = _make_queue()
    app = _QueueHost(queue)
    async with app.run_test() as pilot:
        page = app.query_one(QueuePage)
        table = app.query_one("#queue-table", TrackTable)
        table.move_cursor(row=0)
        await pilot.pause()

        await page.handle_action(Action.REORDER_DOWN)

        assert _ids(queue) == ["t2", "t1", "t3", "t4"]
        assert table.cursor_row == 1


async def test_delete_after_sort_in_shuffle_mode():
    """The table shows playback (shuffle) order and queue.remove() takes a
    playback-order index, so the mapping must hold under shuffle too."""
    queue = _make_queue()
    # Force a deterministic shuffle order instead of toggle_shuffle()'s
    # random one: playback order t3, t1, t4, t2.
    queue._shuffle = True
    queue._shuffle_order = [2, 0, 3, 1]
    queue._shuffle_position = -1
    assert _ids(queue) == ["t3", "t1", "t4", "t2"]

    app = _QueueHost(queue)
    async with app.run_test() as pilot:
        page = app.query_one(QueuePage)
        table = app.query_one("#queue-table", TrackTable)
        table.sort_by("title")  # visible: a(t4), b(t3), c(t2), d(t1)
        table.move_cursor(row=0)  # highlight "a-song" (t4, playback index 2)
        await pilot.pause()

        await page.handle_action(Action.DELETE_ITEM)

        assert _ids(queue) == ["t3", "t1", "t2"]


async def test_delete_is_noop_when_filter_matches_nothing():
    queue = _make_queue()
    app = _QueueHost(queue)
    async with app.run_test() as pilot:
        page = app.query_one(QueuePage)
        table = app.query_one("#queue-table", TrackTable)
        table._filter_text = "no-match"
        table._execute_filter()  # zero visible rows
        await pilot.pause()

        await page.handle_action(Action.DELETE_ITEM)

        assert _ids(queue) == ["t1", "t2", "t3", "t4"]


async def test_delete_last_track_shows_empty_state():
    queue = QueueManager()
    queue.add_multiple([_track("t1", "only-song")])
    app = _QueueHost(queue)
    async with app.run_test() as pilot:
        page = app.query_one(QueuePage)
        table = app.query_one("#queue-table", TrackTable)
        table.move_cursor(row=0)
        await pilot.pause()

        await page.handle_action(Action.DELETE_ITEM)

        assert queue.length == 0
        assert table.display is False
        assert app.query_one("#queue-empty").display is True


# ── Queue changes made elsewhere re-render the page ──────────────────


async def test_delete_after_app_shuffle_toggle_removes_the_displayed_row():
    """The app-level shuffle key reorders the queue; the page must follow
    before `d` maps the highlighted row to a queue position."""
    queue = _make_queue()
    queue.jump_to(0)
    app = _QueueHost(queue)
    async with app.run_test() as pilot:
        page = app.query_one(QueuePage)
        table = app.query_one("#queue-table", TrackTable)
        host = MagicMock()
        host.queue = queue
        host.shuffle_prefs.get.return_value = False
        host._refresh_queue_page = MagicMock(side_effect=page._refresh_queue)
        with patch("ytm_player.services.queue.random.shuffle", side_effect=lambda xs: xs.reverse()):
            await KeyHandlingMixin._handle_action(host, Action.TOGGLE_SHUFFLE)
        await pilot.pause()

        host._refresh_queue_page.assert_called_once()
        assert _shown(table) == _ids(queue)
        assert _ids(queue) != ["t1", "t2", "t3", "t4"]

        table.move_cursor(row=1)
        displayed = _entry_ids(queue)[1]
        await page.handle_action(Action.DELETE_ITEM)

        assert displayed not in _entry_ids(queue)
        assert _shown(table) == _ids(queue)


async def test_ipc_clear_and_add_rerender_the_page():
    queue = _make_queue()
    app = _QueueHost(queue)
    # The real app has this mixin method; the IPC handler runs with the app as self.
    app._refresh_queue_page = lambda: app.query_one(QueuePage)._refresh_queue()
    async with app.run_test() as pilot:
        page = app.query_one(QueuePage)
        table = app.query_one("#queue-table", TrackTable)
        await pilot.pause()
        await page.handle_action(Action.MARK_TOGGLE)
        assert table.marked_count == 1

        result = await IPCMixin._handle_ipc_command(app, "queue_clear", {})
        await pilot.pause()

        assert result == {"ok": True}
        assert table.row_count == 0
        assert table.marked_count == 0
        assert not table.display
        assert app.query_one("#queue-empty").display

        app.ytmusic = MagicMock()
        app.ytmusic.get_watch_playlist = AsyncMock(
            return_value=[{"videoId": "new", "title": "New", "artists": []}]
        )
        result = await IPCMixin._ipc_queue_add(app, {"video_id": "new"})
        await pilot.pause()

        assert result == {"ok": True}
        assert _shown(table) == ["new"]
        assert table.display

        await page.handle_action(Action.DELETE_ITEM)
        assert queue.is_empty


# ── Identical songs: the row names its occurrence ────────────────────


def _queue_with_two_copies() -> tuple[QueueManager, dict]:
    queue = QueueManager()
    same = _track("x", "same song")
    queue.add_multiple([_track("a", "a-song"), same, _track("b", "b-song"), same, _track("c", "c")])
    return queue, same


async def test_queue_page_rows_name_their_queue_entry():
    queue = _make_queue()
    app = _QueueHost(queue)
    async with app.run_test() as pilot:
        table = app.query_one("#queue-table", TrackTable)
        await pilot.pause()
        table.sort_by("title")  # visible: a,b,c,d = queue index 3,2,1,0
        table.move_cursor(row=0)
        track = table.selected_track
        assert track is not None

        assert table.queue_entry_keys is True
        index = table.selected_original_index
        assert index == 3
        key = table.occurrence_key(index)

        assert key == _entry_ids(queue)[3]
        assert TrackActionsMixin._queue_entry_id(table.queue_entry_keys, key) == key
        # The snapshot a right-click on that row takes.
        message = TrackTable.TrackRightClicked(table, track, 0)
        assert (message.occurrence_key, message.from_queue) == (key, True)


async def test_popup_remove_on_the_second_copy_removes_that_row():
    """Right-click on the second copy → Remove from Queue → the first copy stays."""
    queue, same = _queue_with_two_copies()
    app = _QueueHost(queue)
    host = MagicMock()
    host.queue = queue
    host._refresh_queue_page = MagicMock(
        side_effect=lambda: app.query_one(QueuePage)._refresh_queue()
    )
    host._queue_entry_id = TrackActionsMixin._queue_entry_id
    for name in (
        "_remove_from_queue",
        "_open_actions_for_track",
        "on_track_table_track_right_clicked",
    ):
        setattr(host, name, types.MethodType(getattr(TrackActionsMixin, name), host))
    async with app.run_test() as pilot:
        table = app.query_one("#queue-table", TrackTable)
        await pilot.pause()
        ids = _entry_ids(queue)
        row_track = table.visible_tracks[3]
        assert row_track["video_id"] == "x"

        host.on_track_table_track_right_clicked(TrackTable.TrackRightClicked(table, row_track, 3))
        host.push_screen.call_args.args[1]("remove_from_queue")
        await pilot.pause()

        assert _entry_ids(queue) == [ids[0], ids[1], ids[2], ids[4]]
        assert queue.tracks[1] is same
        assert _shown(table) == ["a", "x", "b", "c"]


async def test_move_on_the_second_of_two_identical_songs_moves_that_occurrence():
    queue, _ = _queue_with_two_copies()
    app = _QueueHost(queue)
    async with app.run_test() as pilot:
        page = app.query_one(QueuePage)
        table = app.query_one("#queue-table", TrackTable)
        await pilot.pause()
        first_copy, second_copy = _entry_ids(queue)[1], _entry_ids(queue)[3]
        table.move_cursor(row=3)

        await page.handle_action(Action.REORDER_DOWN)

        ids = _entry_ids(queue)
        assert ids.index(second_copy) == 4
        assert ids.index(first_copy) == 1
        assert _cursor_entry(table) == second_copy

        await page.handle_action(Action.REORDER_UP, 3)

        ids = _entry_ids(queue)
        assert ids.index(second_copy) == 1
        assert ids.index(first_copy) == 2
        assert _cursor_entry(table) == second_copy
        assert _shown(table) == _ids(queue)


# ── Reordering under a sort or filter ────────────────────────────────


def _colour_queue() -> QueueManager:
    queue = QueueManager()
    queue.add_multiple(
        [
            _track("t1", "red one"),
            _track("t2", "blue two"),
            _track("t3", "red three"),
            _track("t4", "blue four"),
        ]
    )
    return queue


async def test_move_down_under_sort_clears_the_sort_and_shows_the_move():
    queue = _make_queue()
    app = _QueueHost(queue)
    async with app.run_test() as pilot:
        page = app.query_one(QueuePage)
        table = app.query_one("#queue-table", TrackTable)
        table.sort_by("title")  # visible: a,b,c,d = queue index 3,2,1,0
        table.move_cursor(row=3)  # d-song (t1, queue index 0)
        await pilot.pause()
        moving = _entry_ids(queue)[0]

        await page.handle_action(Action.REORDER_DOWN)

        assert _ids(queue) == ["t2", "t1", "t3", "t4"]
        assert _shown(table) == _ids(queue)
        assert table.cursor_row == 1
        assert _cursor_entry(table) == moving


async def test_move_up_under_sort_honours_the_count_and_clamps():
    queue = _make_queue()
    app = _QueueHost(queue)
    async with app.run_test() as pilot:
        page = app.query_one(QueuePage)
        table = app.query_one("#queue-table", TrackTable)
        table.sort_by("title")
        table.move_cursor(row=0)  # a-song (t4, queue index 3)
        await pilot.pause()
        moving = _entry_ids(queue)[3]

        await page.handle_action(Action.REORDER_UP, 2)

        assert _ids(queue) == ["t1", "t4", "t2", "t3"]
        assert _shown(table) == _ids(queue)
        assert table.cursor_row == 1
        assert _cursor_entry(table) == moving

        await page.handle_action(Action.REORDER_UP, 10)  # clamps at the top

        assert _ids(queue) == ["t4", "t1", "t2", "t3"]
        assert _shown(table) == _ids(queue)
        assert table.cursor_row == 0
        assert _cursor_entry(table) == moving


async def test_move_under_sort_keeps_marks_and_filter():
    queue = _colour_queue()
    app = _QueueHost(queue)
    async with app.run_test() as pilot:
        page = app.query_one(QueuePage)
        table = app.query_one("#queue-table", TrackTable)
        await pilot.pause()
        table._filter_text = "red"
        table._execute_filter()  # visible: t1, t3
        table.sort_by("title")  # visible: red one (t1), red three (t3)
        table.move_cursor(row=1)
        await page.handle_action(Action.MARK_TOGGLE)  # mark t3
        moving = _entry_ids(queue)[2]
        assert _shown(table) == ["t1", "t3"]

        await page.handle_action(Action.REORDER_UP, 2)

        assert _ids(queue) == ["t3", "t1", "t2", "t4"]
        assert _shown(table) == ["t3", "t1"]  # filter kept, sort gone
        assert [t["video_id"] for t in table.marked_tracks()] == ["t3"]
        assert table.cursor_row == 0
        assert _cursor_entry(table) == moving


async def test_move_under_filter_past_a_hidden_row_changes_the_queue_not_the_view():
    """Hidden rows still count as positions: a one-step move past one leaves
    the visible order as it was. The queue, not the view, is what moved."""
    queue = _colour_queue()
    app = _QueueHost(queue)
    async with app.run_test() as pilot:
        page = app.query_one(QueuePage)
        table = app.query_one("#queue-table", TrackTable)
        await pilot.pause()
        table._filter_text = "red"
        table._execute_filter()  # visible: t1, t3
        table.move_cursor(row=0)
        moving = _entry_ids(queue)[0]

        await page.handle_action(Action.REORDER_DOWN)

        assert _ids(queue) == ["t2", "t1", "t3", "t4"]
        assert _shown(table) == ["t1", "t3"]
        assert table.cursor_row == 0
        assert _cursor_entry(table) == moving


# ── A queued right-click keeps the occurrence it was made on ─────────


class _RightClickHost(_QueueHost):
    """Queue page host with the app's right-click → popup → remove path bound.

    The popup is replaced by capturing what would open, so a test can confirm
    the removal later — after the table changed underneath the queued message.
    """

    on_track_table_track_right_clicked = TrackActionsMixin.on_track_table_track_right_clicked
    _open_track_actions = TrackActionsMixin._open_track_actions
    _focused_track_table = TrackActionsMixin._focused_track_table
    _queue_entry_id = staticmethod(TrackActionsMixin._queue_entry_id)
    _remove_from_queue = TrackActionsMixin._remove_from_queue
    _refresh_queue_page = TrackActionsMixin._refresh_queue_page

    def __init__(self, queue: QueueManager) -> None:
        super().__init__(queue)
        self.captured: tuple[dict, int | None, bool] | None = None
        self.notify = MagicMock()

    def _open_actions_for_track(
        self, track: dict, *, queue_entry_id: int | None = None, from_queue: bool = False
    ) -> None:
        self.captured = (track, queue_entry_id, from_queue)

    def confirm_remove(self) -> None:
        assert self.captured is not None
        track, queue_entry_id, from_queue = self.captured
        self._remove_from_queue(track, queue_entry_id, from_queue=from_queue)


def _two_copies_then_b() -> tuple[QueueManager, dict]:
    queue = QueueManager()
    same = _track("x", "same song")
    queue.add_multiple([same, same, _track("b", "b-song")])
    return queue, same


async def test_real_right_click_snapshots_the_clicked_row():
    queue, _ = _two_copies_then_b()
    ids = _entry_ids(queue)
    app = _RightClickHost(queue)
    async with app.run_test(size=(100, 24)) as pilot:
        table = app.query_one("#queue-table", TrackTable)
        await pilot.pause()

        await pilot.click(table, offset=(8, 2), button=3)  # header row, then row 1
        await pilot.pause()

        assert app.captured is not None
        track, queue_entry_id, from_queue = app.captured
        assert (track["video_id"], queue_entry_id, from_queue) == ("x", ids[1], True)


async def test_queued_right_click_survives_an_earlier_row_removal():
    queue, _ = _two_copies_then_b()
    ids = _entry_ids(queue)
    app = _RightClickHost(queue)
    async with app.run_test() as pilot:
        table = app.query_one("#queue-table", TrackTable)
        await pilot.pause()
        clicked = table.visible_tracks[1]  # the second copy
        table.post_message(TrackTable.TrackRightClicked(table, clicked, 1))
        # Before delivery: the first copy goes and the page re-renders, so
        # row 1 now shows b.
        queue.remove_entry(ids[0])
        app._refresh_queue_page()
        await pilot.pause()

        assert app.captured == (clicked, ids[1], True)
        app.confirm_remove()

        assert _entry_ids(queue) == [ids[2]]
        assert _shown(table) == ["b"]


async def test_queued_right_click_after_clear_and_repopulate_removes_nothing():
    queue, same = _two_copies_then_b()
    ids = _entry_ids(queue)
    app = _RightClickHost(queue)
    async with app.run_test() as pilot:
        table = app.query_one("#queue-table", TrackTable)
        await pilot.pause()
        clicked = table.visible_tracks[1]
        table.post_message(TrackTable.TrackRightClicked(table, clicked, 1))
        queue.clear()
        queue.add_multiple([same, _track("c", "c-song")])
        app._refresh_queue_page()
        await pilot.pause()
        rebuilt = _entry_ids(queue)
        assert ids[1] not in rebuilt

        assert app.captured == (clicked, ids[1], True)
        app.confirm_remove()

        assert _entry_ids(queue) == rebuilt
        assert queue.tracks[0] is same
        assert _shown(table) == ["x", "c"]
        assert app.notify.call_args.kwargs.get("severity") == "warning"


async def test_right_click_delivered_after_the_page_is_gone_never_falls_back():
    """Detached table at delivery: the snapshot still names the occurrence."""
    queue, same = _two_copies_then_b()
    ids = _entry_ids(queue)
    app = _RightClickHost(queue)
    async with app.run_test() as pilot:
        table = app.query_one("#queue-table", TrackTable)
        await pilot.pause()
        clicked = table.visible_tracks[1]
        message = TrackTable.TrackRightClicked(table, clicked, 1)
        await app.query_one(QueuePage).remove()  # navigated away before delivery
        await pilot.pause()
        assert not table.is_attached

        app.on_track_table_track_right_clicked(message)

        assert app.captured == (clicked, ids[1], True)
        app.confirm_remove()

        assert _entry_ids(queue) == [ids[0], ids[2]]
        assert queue.tracks[0] is same


async def test_queued_right_click_with_the_page_removed_is_safe_either_way():
    """Posted, then the page goes: dropped or delivered, no first-match removal."""
    queue, _ = _two_copies_then_b()
    ids = _entry_ids(queue)
    app = _RightClickHost(queue)
    async with app.run_test() as pilot:
        table = app.query_one("#queue-table", TrackTable)
        await pilot.pause()
        clicked = table.visible_tracks[1]
        table.post_message(TrackTable.TrackRightClicked(table, clicked, 1))
        await app.query_one(QueuePage).remove()
        await pilot.pause()

        if app.captured is not None:
            assert app.captured == (clicked, ids[1], True)
            app.confirm_remove()
            assert _entry_ids(queue) == [ids[0], ids[2]]
        else:
            assert _entry_ids(queue) == ids


# ── Keyboard actions menu on the Queue page ───────────────────────────


async def test_keyboard_menu_on_a_filtered_empty_queue_table_uses_the_playing_track():
    """The focused Queue table shows no row: the menu is for the playing
    track, which came from no row — so removal is the first-match fallback,
    not a stale-entry refusal."""
    queue, same = _two_copies_then_b()
    ids = _entry_ids(queue)
    app = _RightClickHost(queue)
    app.player.current_track = same
    async with app.run_test() as pilot:
        table = app.query_one("#queue-table", TrackTable)
        table.focus()
        await pilot.pause()
        table._filter_text = "matches nothing"
        table._execute_filter()
        assert app.focused is table
        assert table.selected_track is None

        await app._open_track_actions()

        assert app.captured == (same, None, False)
        app.confirm_remove()

        assert _entry_ids(queue) == [ids[1], ids[2]]
        assert app.notify.call_args.args[0] == "Removed from queue"


async def test_keyboard_menu_on_a_queue_row_names_that_occurrence():
    queue, same = _two_copies_then_b()
    ids = _entry_ids(queue)
    app = _RightClickHost(queue)
    app.player.current_track = same
    async with app.run_test() as pilot:
        table = app.query_one("#queue-table", TrackTable)
        table.focus()
        table.move_cursor(row=1)
        await pilot.pause()

        await app._open_track_actions()

        assert app.captured is not None
        track, queue_entry_id, from_queue = app.captured
        assert (track["video_id"], queue_entry_id, from_queue) == ("x", ids[1], True)
        app.confirm_remove()

        assert _entry_ids(queue) == [ids[0], ids[2]]
