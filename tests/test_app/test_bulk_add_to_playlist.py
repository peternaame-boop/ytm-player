"""``A`` with marks: the picker gets the marked tracks, success clears only that selection.

The handler lives in TrackActionsMixin; it is driven here with a stand-in
host and real, mounted TrackTables (marks repaint cells and toggle a CSS
class, so the tables must be attached).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from textual.app import App, ComposeResult

from ytm_player.app._track_actions import TrackActionsMixin
from ytm_player.config.keymap import Action
from ytm_player.ui.popups.playlist_picker import PlaylistPicker
from ytm_player.ui.widgets.track_table import TrackTable


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


def _tracks() -> list[dict]:
    return [
        _track("a", "Alpha"),
        _track("b", "Bravo"),
        _track("b", "Bravo again"),
        _track("d", "Delta"),
    ]


def _keys(tracks: list[dict]) -> list[str]:
    return [t["video_id"] for t in tracks]


class _Tables(App):
    """Two tables, standing in for a page's track lists."""

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        self.first = TrackTable(id="first")
        self.second = TrackTable(id="second")
        yield self.first
        yield self.second


def _host(app: _Tables, playing: dict | None = None) -> MagicMock:
    """A stand-in app for the mixin: the current page reports the two tables."""
    host = MagicMock()
    page = MagicMock()
    page.query = lambda _cls: [app.first, app.second]
    host._get_current_page = MagicMock(return_value=page)
    host.player = MagicMock()
    host.player.current_track = playing
    host._add_marked_to_playlist = lambda table: TrackActionsMixin._add_marked_to_playlist(
        host, table
    )
    return host


async def _mark(table: TrackTable, *rows: int) -> None:
    for row in rows:
        table.move_cursor(row=row)
        await table.handle_action(Action.MARK_TOGGLE)


def _pushed(host: MagicMock) -> tuple[PlaylistPicker, object]:
    host.push_screen.assert_called_once()
    args = host.push_screen.call_args.args
    assert isinstance(args[0], PlaylistPicker)
    return args[0], args[1]


async def test_marked_tracks_go_to_the_picker_in_displayed_order_and_success_clears_them():
    app = _Tables()
    async with app.run_test() as pilot:
        table = app.first
        table.load_tracks(_tracks())
        await pilot.pause()
        await _mark(table, 3, 1, 2)  # Delta, Bravo, Bravo again (a duplicate video)
        table.sort_by("title")
        table.sort_by("title")  # Delta, Charlie... reversed: Delta, Bravo again, Bravo, Alpha
        await pilot.pause()
        host = _host(app)

        await TrackActionsMixin._open_add_to_playlist(host)

        picker, done = _pushed(host)
        # Both marked copies of "b" are submitted, in the displayed order.
        assert picker.video_ids == ["d", "b", "b"]
        assert [t["title"] for t in picker.tracks] == ["Delta", "Bravo again", "Bravo"]
        host.notify.assert_not_called()

        done("PL1")
        await pilot.pause()
        assert table.marked_count == 0
        assert not table.has_class("-marked")


async def test_cancel_keeps_the_marks():
    app = _Tables()
    async with app.run_test() as pilot:
        table = app.first
        table.load_tracks(_tracks())
        await pilot.pause()
        await _mark(table, 0, 3)
        host = _host(app)

        await TrackActionsMixin._open_add_to_playlist(host)
        _, done = _pushed(host)
        done(None)
        await pilot.pause()

        assert table.marked_count == 2


async def test_a_refresh_that_drops_a_marked_row_while_the_picker_is_open_keeps_the_rest():
    app = _Tables()
    async with app.run_test() as pilot:
        table = app.first
        tracks = [_track("a", "Alpha"), _track("c", "Charlie"), _track("d", "Delta")]
        table.load_tracks(tracks, keys=_keys(tracks))
        await pilot.pause()
        await _mark(table, 0, 2)  # Alpha, Delta
        host = _host(app)

        await TrackActionsMixin._open_add_to_playlist(host)
        _, done = _pushed(host)
        without_delta = tracks[:2]
        table.refresh_tracks(without_delta, keys=_keys(without_delta))
        await pilot.pause()
        done("PL1")
        await pilot.pause()

        assert [t["title"] for t in table.marked_tracks()] == ["Alpha"]


async def test_a_refresh_that_keeps_the_marked_rows_while_the_picker_is_open_still_clears():
    app = _Tables()
    async with app.run_test() as pilot:
        table = app.first
        tracks = [_track("a", "Alpha"), _track("c", "Charlie"), _track("d", "Delta")]
        table.load_tracks(tracks, keys=_keys(tracks))
        await pilot.pause()
        await _mark(table, 0, 2)
        host = _host(app)

        await TrackActionsMixin._open_add_to_playlist(host)
        _, done = _pushed(host)
        prepended = [_track("n", "New"), *tracks]
        table.refresh_tracks(prepended, keys=_keys(prepended))
        await pilot.pause()
        assert table.marked_count == 2
        done("PL1")
        await pilot.pause()

        assert table.marked_count == 0


async def test_marks_in_two_tables_warn_instead_of_guessing():
    app = _Tables()
    async with app.run_test() as pilot:
        app.first.load_tracks(_tracks())
        app.second.load_tracks(_tracks())
        await pilot.pause()
        await _mark(app.first, 0)
        await _mark(app.second, 1)
        host = _host(app)

        await TrackActionsMixin._open_add_to_playlist(host)

        host.push_screen.assert_not_called()
        assert host.notify.call_args.kwargs["severity"] == "warning"
        assert app.first.marked_count == 1 and app.second.marked_count == 1


async def test_without_marks_the_playing_track_goes_to_the_picker():
    app = _Tables()
    async with app.run_test() as pilot:
        app.first.load_tracks(_tracks())
        await pilot.pause()
        host = _host(app, playing=_track("p", "Playing"))

        await TrackActionsMixin._open_add_to_playlist(host)

        picker, _ = host.push_screen.call_args.args[0], None
        assert isinstance(picker, PlaylistPicker)
        assert picker.video_ids == ["p"]


async def test_marked_rows_without_video_ids_warn():
    app = _Tables()
    async with app.run_test() as pilot:
        table = app.first
        table.load_tracks([{"title": "No id", "artist": "A"}, _track("a", "Alpha")])
        await pilot.pause()
        await _mark(table, 0)
        host = _host(app)

        await TrackActionsMixin._open_add_to_playlist(host)

        host.push_screen.assert_not_called()
        assert host.notify.call_args.kwargs["severity"] == "warning"


async def test_a_table_gone_before_the_picker_returns_is_left_alone():
    app = _Tables()
    async with app.run_test() as pilot:
        table = app.first
        table.load_tracks(_tracks())
        await pilot.pause()
        await _mark(table, 0)
        host = _host(app)

        await TrackActionsMixin._open_add_to_playlist(host)
        _, done = _pushed(host)
        await table.remove()
        await pilot.pause()

        done("PL1")  # must not raise


async def test_opening_the_picker_ends_range_mode_and_cancel_keeps_the_marks():
    app = _Tables()
    async with app.run_test() as pilot:
        table = app.first
        table.load_tracks(_tracks())
        await pilot.pause()
        await table.handle_action(Action.MARK_RANGE)
        await table.handle_action(Action.MOVE_DOWN)
        await pilot.pause()
        assert table.marked_count == 2
        host = _host(app)

        await TrackActionsMixin._open_add_to_playlist(host)

        picker, done = _pushed(host)
        assert picker.video_ids == ["a", "b"]
        assert table._range_mode is False
        done(None)
        await pilot.pause()
        await table.handle_action(Action.MOVE_DOWN)  # would have extended the range
        await pilot.pause()
        assert [t["title"] for t in table.marked_tracks()] == ["Alpha", "Bravo"]


async def test_two_marked_copies_are_submitted_as_two_copies():
    app = _Tables()
    async with app.run_test() as pilot:
        table = app.first
        table.load_tracks(_tracks())
        await pilot.pause()
        await _mark(table, 1, 2)  # Bravo, Bravo again — the same video twice
        host = _host(app)

        await TrackActionsMixin._open_add_to_playlist(host)

        picker, _ = _pushed(host)
        assert picker.video_ids == ["b", "b"]
        assert [t["title"] for t in picker.tracks] == ["Bravo", "Bravo again"]
