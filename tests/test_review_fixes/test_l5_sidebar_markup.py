"""T6 (sidebars): outside text in the sidebars is shown as typed, never parsed as markup.

Playlist names and lyric lines come from YouTube Music, LRCLIB and other
users. Parsed as Textual markup, a lowercase ``[tag]`` vanished, ``[/x]``
raised ``MarkupError`` and ``[@click=app.quit]`` became a clickable action
(clicking a lyric line is the seek gesture). Each test mounts the real
widget in a headless app and reads back what it renders.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.content import Content
from textual.widgets import ListItem, Static
from textual.widgets._toast import Toast

from ytm_player.config.settings import Settings
from ytm_player.ui.sidebars import playlist_sidebar
from ytm_player.ui.sidebars.lyrics_sidebar import LyricsSidebar, _LyricLine
from ytm_player.ui.sidebars.playlist_sidebar import LibraryPanel, PlaylistSidebar
from ytm_player.utils.transliteration import transliterate_line

# Lowercase and uppercase tags: rich.markup.escape only escapes the lowercase
# form, while Textual 8 markup swallows both.
LIVE = "Song [live] [Live] [MV]"
BAD_CLOSE = "old [/x] mix"
CLICK = "[@click=app.quit]x[/]"


def _shown(widget: Static) -> Content:
    visual = widget.visual
    assert isinstance(visual, Content)
    return visual


def _actions(content: Content) -> list[str]:
    return [str(span.style) for span in content.spans if "@click" in str(span.style)]


class _Host(App[None]):
    """Counts ``app.quit`` instead of exiting, so a leaked click action is observable."""

    def __init__(self, widget_factory) -> None:
        super().__init__()
        self._factory = widget_factory
        self.quit_calls = 0
        self.settings = Settings()

    def get_css_variables(self) -> dict[str, str]:
        return {
            **super().get_css_variables(),
            "selected-item": "#3a3a3a",
            "lyrics-played": "#777777",
            "lyrics-current": "#ff4e45",
            "lyrics-upcoming": "#cccccc",
        }

    def compose(self) -> ComposeResult:
        yield self._factory()

    async def action_quit(self) -> None:
        self.quit_calls += 1


@pytest.fixture(autouse=True)
def _default_settings():
    with patch("ytm_player.config.settings._settings", Settings()):
        yield


# ── Playlist sidebar ─────────────────────────────────────────────────


def _row_texts(panel: LibraryPanel) -> list[Content]:
    return [_shown(item.query_one(Static)) for item in panel.query(ListItem)]


async def test_library_panel_rows_show_playlist_names_as_typed() -> None:
    app = _Host(lambda: LibraryPanel("Playlists", id="ps-playlists"))
    async with app.run_test() as pilot:
        panel = app.query_one(LibraryPanel)
        panel.load_items(
            [
                {"playlistId": "PL1", "title": LIVE, "count": 12},
                {"playlistId": "PL2", "title": BAD_CLOSE},
                {"playlistId": "PL3", "title": CLICK},
            ]
        )
        await pilot.pause()

        rows = _row_texts(panel)
        assert [row.plain for row in rows] == [f"{LIVE} (12 tracks)", BAD_CLOSE, CLICK]
        assert not any(_actions(row) for row in rows)


async def test_library_panel_prepended_row_is_literal() -> None:
    app = _Host(lambda: LibraryPanel("Playlists", id="ps-playlists"))
    async with app.run_test() as pilot:
        panel = app.query_one(LibraryPanel)
        panel.load_items([])
        panel.prepend_item({"playlistId": "PL9", "title": f"{BAD_CLOSE} {CLICK}"})
        await pilot.pause()

        rows = _row_texts(panel)
        assert [row.plain for row in rows] == [f"{BAD_CLOSE} {CLICK}"]
        assert not _actions(rows[0])


async def test_copy_link_fallback_toast_shows_the_link_literally(monkeypatch) -> None:
    monkeypatch.setattr(playlist_sidebar, "copy_to_clipboard", MagicMock(return_value=False))
    app = _Host(lambda: Static("host"))
    async with app.run_test(notifications=True) as pilot:
        PlaylistSidebar().copy_item_link({"playlistId": "PL[/x]"})
        await pilot.pause()

        toasts = list(app.screen.query(Toast))
        assert [toast.render().plain for toast in toasts] == [
            "https://music.youtube.com/playlist?list=PL[/x]"
        ]


# ── Lyrics sidebar ───────────────────────────────────────────────────


class _FakePlayer:
    def __init__(self, track: dict) -> None:
        self.current_track = track
        self.position = 0.0
        self.seek_absolute = AsyncMock()

    def on(self, *_args) -> None: ...

    def off(self, *_args) -> None: ...


def _lyrics_host(track: dict, lyrics: dict, *, transliterate: bool = False) -> _Host:
    app = _Host(lambda: LyricsSidebar(id="lyrics"))
    app.settings.lyrics.transliteration = transliterate
    app.player = _FakePlayer(track)  # type: ignore[attr-defined]
    app.ytmusic = SimpleNamespace(get_lyrics=AsyncMock(return_value=lyrics))  # type: ignore[attr-defined]
    return app


def _synced(*lines: str) -> dict:
    """ytmusicapi's timed-lyrics shape: one entry per line, start in milliseconds."""
    entries = [SimpleNamespace(start_time=(i + 1) * 1000, text=t) for i, t in enumerate(lines)]
    return {"lyrics": entries, "hasTimestamps": True}


async def _load(app: _Host, pilot) -> LyricsSidebar:
    sidebar = app.query_one(LyricsSidebar)
    sidebar.activate()
    await asyncio.wait_for(app.workers.wait_for_complete(), 5)
    await pilot.pause()
    return sidebar


TRACK = {"video_id": "v1", "title": LIVE, "artist": "[/x] Band", "duration": 200}


async def test_synced_lyric_lines_and_header_show_as_typed() -> None:
    app = _lyrics_host(TRACK, _synced("[Chorus]", "oh [/] no", "la la [x2]"))
    async with app.run_test(size=(80, 24)) as pilot:
        sidebar = await _load(app, pilot)

        lines = [_shown(line).plain for line in sidebar.query(_LyricLine)]
        assert lines == ["[Chorus]", "oh [/] no", "la la [x2]"]
        assert _shown(sidebar.query_one("#ls-title", Static)).plain == f"{LIVE} — [/x] Band"


async def test_plain_lyric_lines_and_their_transliteration_show_as_typed() -> None:
    russian = "привет [/x] [Live]"
    plain = {"lyrics": f"[Verse]\nclose [/b] here\n{russian}", "hasTimestamps": False}
    app = _lyrics_host(TRACK, plain, transliterate=True)
    with patch("ytm_player.services.lrclib.get_synced_lyrics", AsyncMock(return_value=None)):
        async with app.run_test(size=(80, 24)) as pilot:
            sidebar = await _load(app, pilot)

            lines = [_shown(line).plain for line in sidebar.query(_LyricLine)]
            assert lines == ["[Verse]", "close [/b] here", russian]
            extra = sidebar.query(".lyrics-transliterated").first(Static)
            assert _shown(extra).plain == transliterate_line(russian)


async def test_transliterated_lines_show_as_typed() -> None:
    line = "привет [/x] [MV]"
    app = _lyrics_host(TRACK, _synced(line), transliterate=True)
    async with app.run_test(size=(80, 24)) as pilot:
        sidebar = await _load(app, pilot)

        extra = sidebar.query(".lyrics-transliterated").first(Static)
        assert _shown(extra).plain == transliterate_line(line)


async def test_clicking_a_lyric_line_seeks_and_runs_no_markup_action() -> None:
    payload = "[@click=app.quit]never gonna give you up[/]"
    plain_track = {"video_id": "v1", "title": "Song", "artist": "Band", "duration": 200}
    app = _lyrics_host(plain_track, _synced(payload))
    async with app.run_test(size=(80, 24)) as pilot:
        sidebar = await _load(app, pilot)
        line = sidebar.query_one(_LyricLine)

        # _LyricLine has two columns of left padding; click on the text itself.
        await pilot.click(line, offset=(3, 0))
        await pilot.pause()

        assert app.quit_calls == 0
        app.player.seek_absolute.assert_awaited_once_with(1.0)  # type: ignore[attr-defined]
        assert _shown(line).plain == payload
        assert not _actions(_shown(line))


async def test_current_lyric_line_keeps_its_highlight_style() -> None:
    app = _lyrics_host(TRACK, _synced("first [Live]", "second"))
    async with app.run_test(size=(80, 24)) as pilot:
        sidebar = await _load(app, pilot)
        sidebar._on_position_change(1.5)
        await pilot.pause()

        current = sidebar.query(_LyricLine).first()
        assert current.has_class("lyrics-current")
        painted = [
            segment
            for strip in current.render_lines(current.region.reset_offset)
            for segment in strip
            if segment.text.strip()
        ]
        assert "".join(s.text for s in painted).strip() == "first [Live]"
        assert all(s.style is not None and s.style.bold for s in painted)
