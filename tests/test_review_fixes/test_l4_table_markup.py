"""Outside text in TrackTable cells and the selection bar is shown literally.

Track titles, artists and albums come from YouTube. They must never be
parsed as Rich/Textual markup: ``[live]`` must not vanish, ``[/x]`` must
not raise and ``[@click=...]`` must not become a clickable action. The
table's own styling (bold playing row, mark glyph) must still render.
"""

from __future__ import annotations

import pytest
from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import Static

from ytm_player.config.settings import Settings
from ytm_player.ui.selection_info_bar import SelectionInfoBar
from ytm_player.ui.widgets.track_table import TrackTable
from ytm_player.utils.bidi import FSI, PDI

CLICK_PAYLOAD = "[@click=app.probe]x[/]"


def _track(video_id: str, title: str, artist: str = "Artist", album: str = "Album") -> dict:
    return {
        "video_id": video_id,
        "title": title,
        "artist": artist,
        "artists": [{"name": artist, "id": "1"}],
        "album": album,
        "album_id": None,
        "duration": 100,
        "thumbnail_url": None,
        "is_video": False,
    }


class _Host(App):
    """Minimal host: the theme variable TrackTable's CSS needs, plus a probe action."""

    def __init__(self) -> None:
        super().__init__()
        self.probe_runs = 0

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        # Built here, not in __init__: add_column measures with the app console.
        self.table = TrackTable(id="t")
        yield self.table

    def action_probe(self) -> None:
        self.probe_runs += 1


class _BarHost(App):
    """The selection bar under a filler, with the same probe action."""

    def __init__(self) -> None:
        super().__init__()
        self.probe_runs = 0

    def compose(self) -> ComposeResult:
        yield Static("filler")
        self.bar = SelectionInfoBar(id="bar")
        yield self.bar

    def action_probe(self) -> None:
        self.probe_runs += 1


def _strips(app: App) -> list:
    return list(app.screen._compositor.render_strips())


def _screen_lines(app: App) -> list[str]:
    return [strip.text for strip in _strips(app)]


def _line_with(app: App, needle: str) -> str:
    for line in _screen_lines(app):
        if needle in line:
            return line
    raise AssertionError(f"{needle!r} is not on screen: {_screen_lines(app)!r}")


def _click_actions(app: App) -> list[str]:
    """Every ``@click`` action attached to anything drawn on the screen."""
    found = []
    for strip in _strips(app):
        for segment in strip:
            meta = segment.style.meta if segment.style else None
            if meta and "@click" in meta:
                found.append(str(meta["@click"]))
    return found


def _is_bold(app: App, needle: str) -> bool:
    """Whether the segment drawing *needle* is bold."""
    for strip in _strips(app):
        for segment in strip:
            if needle in segment.text:
                return bool(segment.style and segment.style.bold)
    raise AssertionError(f"{needle!r} is not on screen: {_screen_lines(app)!r}")


def _x_of(table: TrackTable, key: str) -> int:
    """An x offset inside the column *key* (plus one cell of padding)."""
    for x in range(200):
        column = table._column_at_x(x)
        if column is not None and column.key is not None and column.key.value == key:
            return x + 1
    raise AssertionError(f"no column {key!r}")


def _cell(table: TrackTable, row: int, key: str) -> object:
    return table.get_cell_at((row, table.get_column_index(key)))


# ── TrackTable cells ─────────────────────────────────────────────────


async def test_bracket_text_in_title_artist_and_album_is_shown_as_typed():
    app = _Host()
    async with app.run_test(size=(140, 12)) as pilot:
        app.table.load_tracks([_track("a", "Song [live]", "Band [official]", "Hits [deluxe]")])
        await pilot.pause()

        line = _line_with(app, "Song")
        assert "Song [live]" in line
        assert "Band [official]" in line
        assert "Hits [deluxe]" in line


async def test_metadata_cells_are_literal_text_objects():
    app = _Host()
    async with app.run_test(size=(140, 12)) as pilot:
        app.table.load_tracks([_track("a", "Song [live]", "Band [official]", "Hits [deluxe]")])
        await pilot.pause()

        for key, expected in (
            ("title", "Song [live]"),
            ("artist", "Band [official]"),
            ("album", "Hits [deluxe]"),
        ):
            cell = _cell(app.table, 0, key)
            assert isinstance(cell, Text), key
            assert cell.plain == expected
            assert not cell.spans


async def test_malformed_closing_tags_do_not_raise_and_are_shown_as_typed():
    app = _Host()
    async with app.run_test(size=(140, 12)) as pilot:
        app.table.load_tracks([_track("a", "Song [/x]", "a [/] b", "Mix [/r/music]")])
        await pilot.pause()

        line = _line_with(app, "Song")
        assert "Song [/x]" in line
        assert "a [/] b" in line
        assert "Mix [/r/music]" in line


@pytest.mark.parametrize("field", ["title", "artist", "album"])
async def test_click_action_payload_is_plain_text_and_runs_nothing(field):
    track = _track("a", "Title", "Artist", "Album")
    track[field] = CLICK_PAYLOAD
    app = _Host()
    async with app.run_test(size=(140, 12)) as pilot:
        table = app.table
        table.load_tracks([track, _track("b", "Other")])
        await pilot.pause()

        assert CLICK_PAYLOAD in _line_with(app, "@click")
        assert _click_actions(app) == []

        # Click the cell twice: on a non-highlighted row and on the highlighted one.
        table.move_cursor(row=1)
        await pilot.pause()
        x = _x_of(table, field)
        await pilot.click(TrackTable, offset=(x, 1))
        await pilot.pause()
        await pilot.click(TrackTable, offset=(x, 1))
        await pilot.pause()
        assert app.probe_runs == 0


async def test_playing_row_is_still_bold_and_the_row_it_left_is_restored_as_typed():
    tracks = [
        _track("a", "Song [live]", "Band [official]", "Hits [/x]"),
        _track("b", CLICK_PAYLOAD, "Other [mv]", "Second [ep]"),
        _track("c", "Third"),
    ]
    app = _Host()
    async with app.run_test(size=(140, 12)) as pilot:
        table = app.table
        table.load_tracks(tracks)
        # Park the highlight on "c": the cursor row is drawn bold too.
        table.move_cursor(row=2)
        await pilot.pause()
        assert not _is_bold(app, "Song [live]")

        table.set_playing("a")
        await pilot.pause()
        line = _line_with(app, "Song")
        assert "▶" in line
        assert "Song [live]" in line and "Band [official]" in line and "Hits [/x]" in line
        assert _is_bold(app, "Song [live]")
        assert _is_bold(app, "Band [official]")
        assert not _is_bold(app, "Other [mv]")

        # Playback moves on: row "a" goes back to plain cells, "b" turns bold.
        table.set_playing("b")
        await pilot.pause()
        line = _line_with(app, "Song")
        assert "▶" not in line
        assert "Song [live]" in line and "Band [official]" in line and "Hits [/x]" in line
        assert not _is_bold(app, "Song [live]")
        assert isinstance(_cell(table, 0, "title"), Text)
        assert CLICK_PAYLOAD in _line_with(app, "▶")
        assert _is_bold(app, CLICK_PAYLOAD)
        assert _is_bold(app, "Other [mv]")
        assert _click_actions(app) == []


async def test_mark_glyph_still_renders_beside_literal_cells():
    app = _Host()
    async with app.run_test(size=(140, 12)) as pilot:
        table = app.table
        table.load_tracks([_track("a", "Song [live]"), _track("b", "Other")])
        await pilot.pause()

        await pilot.click(TrackTable, offset=(_x_of(table, "mark"), 1))
        await pilot.pause()
        line = _line_with(app, "Song [live]")
        assert "✓" in line
        assert "✓" not in _line_with(app, "Other")


async def test_rtl_cells_keep_their_bidi_isolation(monkeypatch):
    # Default settings, not the developer's config: bidi_mode changes the cell text.
    monkeypatch.setattr("ytm_player.config.settings._settings", Settings())
    title = "שלום [live]"
    app = _Host()
    async with app.run_test(size=(140, 12)) as pilot:
        app.table.load_tracks([_track("a", title, "Band", "Album")])
        await pilot.pause()

        assert str(_cell(app.table, 0, "title")) == f"{FSI}{title}{PDI}"
        assert str(_cell(app.table, 0, "artist")) == "Band"
        assert title in _line_with(app, "Band")


async def test_missing_title_and_album_still_render_an_empty_cell():
    track = _track("a", "x", "Solo [live]")
    track["title"] = None
    track["album"] = None
    app = _Host()
    async with app.run_test(size=(140, 12)) as pilot:
        app.table.load_tracks([track])
        await pilot.pause()

        assert "Solo [live]" in _line_with(app, "Solo")
        assert str(_cell(app.table, 0, "title") or "") == ""


# ── SelectionInfoBar ─────────────────────────────────────────────────


async def test_selection_bar_shows_bracket_text_as_typed():
    app = _BarHost()
    async with app.run_test(size=(60, 6)) as pilot:
        app.bar.text = "Song [mv] — Band [live]"
        await pilot.pause()

        # The bar's own layout still applies: one line, one cell of padding.
        assert _line_with(app, "Song").rstrip() == " Song [mv] — Band [live]"


async def test_selection_bar_malformed_closing_tag_does_not_raise():
    app = _BarHost()
    async with app.run_test(size=(60, 6)) as pilot:
        app.bar.text = "a [/x] b [/] c"
        await pilot.pause()

        assert _line_with(app, "a [").strip() == "a [/x] b [/] c"


async def test_selection_bar_click_action_payload_is_plain_text_and_runs_nothing():
    app = _BarHost()
    async with app.run_test(size=(60, 6)) as pilot:
        app.bar.text = CLICK_PAYLOAD
        await pilot.pause()

        line = _line_with(app, "@click")
        assert line.strip() == CLICK_PAYLOAD
        assert _click_actions(app) == []

        await pilot.click(SelectionInfoBar, offset=(line.index("x[/]"), 0))
        await pilot.pause()
        assert app.probe_runs == 0


async def test_selection_bar_truncates_bracket_text_to_its_width():
    text = "[live] " * 40
    app = _BarHost()
    async with app.run_test(size=(60, 6)) as pilot:
        app.bar.text = text
        await pilot.pause()

        shown = _line_with(app, "[live]").strip()
        # 60 columns minus the bar's padding and the 2 spare cells it keeps.
        assert shown == text[:55] + "…"
