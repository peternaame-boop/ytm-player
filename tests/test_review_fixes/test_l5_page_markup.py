"""T6 (pages): titles, names and descriptions on the pages are shown as typed.

Search results and suggestions, the Library and Context headers, the
Queue header, seed line and seed tooltip, and the Browse lists all show
text from YouTube Music. Parsed as Textual markup a lowercase ``[tag]``
vanished, ``[/x]`` raised ``MarkupError`` and ``[@click=...]`` became a
clickable action. Each test mounts the real widget with a fake service and
reads back what it renders. Track rows are kept plain here: the track
table is a different sink with its own fix.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.content import Content
from textual.widgets import DataTable, Label, ListItem, ListView, Static
from textual.widgets._toast import Toast
from textual.widgets._tooltip import Tooltip

from ytm_player.config.keymap import KeyMap
from ytm_player.config.settings import Settings
from ytm_player.services.queue import QueueManager
from ytm_player.ui.pages import search
from ytm_player.ui.pages.browse import (
    ChartsSection,
    ForYouSection,
    NewReleasesSection,
    PlaylistsSection,
    SubscriptionsSection,
)
from ytm_player.ui.pages.context import ContextPage
from ytm_player.ui.pages.help import HelpPage
from ytm_player.ui.pages.library import LibraryPage
from ytm_player.ui.pages.queue import QueuePage
from ytm_player.ui.pages.search import SearchPage, SearchResultPanel, SuggestionList

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


def _texts(widgets) -> list[str]:
    contents = [_shown(w) for w in widgets]
    assert not any(_actions(c) for c in contents)
    return [c.plain for c in contents]


def _list_texts(view: ListView) -> list[str]:
    return _texts(item.query_one(Label) for item in view.query(ListItem))


def _track(i: int) -> dict[str, Any]:
    return {
        "videoId": f"v{i}",
        "title": f"T{i}",
        "artists": [{"name": "A", "id": "UC1"}],
        "album": {"name": "Al", "id": "MPRE"},
        "duration_seconds": 100,
        "thumbnails": [],
    }


class _Host(App[None]):
    """Mounts the widgets under test; counts ``app.quit`` instead of exiting."""

    def __init__(self, *widgets) -> None:
        super().__init__()
        self._widgets = widgets
        self.quit_calls = 0
        self.ytmusic = MagicMock()
        self.shuffle_prefs = MagicMock(get=MagicMock(return_value=False))
        self.queue = QueueManager()
        self.player = None

    def get_css_variables(self) -> dict[str, str]:
        return {**super().get_css_variables(), "selected-item": "#3a3a3a"}

    def compose(self) -> ComposeResult:
        yield from self._widgets

    async def action_quit(self) -> None:
        self.quit_calls += 1


@pytest.fixture(autouse=True)
def _default_settings():
    with patch("ytm_player.config.settings._settings", Settings()):
        yield


# ── Search ───────────────────────────────────────────────────────────


async def test_search_result_rows_show_as_typed() -> None:
    app = _Host(SearchResultPanel("Results", id="albums-panel"))
    async with app.run_test() as pilot:
        panel = app.query_one(SearchResultPanel)
        panel.load_items(
            [
                {
                    "resultType": "album",
                    "title": "Hits [Deluxe]",
                    "artists": [{"name": "Band"}],
                    "year": "2020",
                },
                {"resultType": "artist", "artist": BAD_CLOSE, "subscribers": "2K"},
                {"resultType": "playlist", "title": CLICK, "author": "me", "itemCount": "5"},
            ]
        )
        await pilot.pause()

        assert _list_texts(panel.query_one(ListView)) == [
            "Hits [Deluxe] - Band (2020)",
            f"{BAD_CLOSE}  [2K]",
            f"{CLICK} by me (5 tracks)",
        ]


async def test_search_suggestions_show_as_typed() -> None:
    app = _Host(SuggestionList())
    async with app.run_test() as pilot:
        suggestions = app.query_one(SuggestionList)
        suggestions.show_suggestions([LIVE, BAD_CLOSE, CLICK])
        await pilot.pause()

        assert _list_texts(suggestions.query_one(ListView)) == [LIVE, BAD_CLOSE, CLICK]


async def test_search_copy_link_fallback_toast_is_literal(monkeypatch) -> None:
    monkeypatch.setattr(search, "copy_to_clipboard", MagicMock(return_value=False))
    app = _Host(Static("host"))
    async with app.run_test(notifications=True) as pilot:
        callbacks = []
        app.push_screen = lambda _screen, callback=None: callbacks.append(callback)  # type: ignore[method-assign]
        event = SearchResultPanel.ItemRightClicked({"browseId": "MPRE[/x]"}, "albums-panel")
        SearchPage().on_search_result_panel_item_right_clicked(event)
        callbacks[0]("copy_link")
        await pilot.pause()

        assert [toast.render().plain for toast in app.screen.query(Toast)] == [
            "https://music.youtube.com/browse/MPRE[/x]"
        ]


# ── Library ──────────────────────────────────────────────────────────


async def test_library_header_shows_playlist_metadata_as_typed() -> None:
    page = LibraryPage(playlist_id="PL1", id="library")
    app = _Host(page)
    app.ytmusic.get_playlist = AsyncMock(
        return_value={
            "id": "PL1",
            "title": LIVE,
            "trackCount": 1,
            "tracks": [_track(0)],
            "author": {"name": CLICK},
            "description": BAD_CLOSE,
            "privacy": "PRIVATE",
        }
    )
    async with app.run_test() as pilot:
        await asyncio.wait_for(app.workers.wait_for_complete(), 5)
        await pilot.pause()

        def header() -> list[str]:
            return _texts(
                page.query(".content-title, .content-description, .content-subtitle").results(Label)
            )

        assert header() == [LIVE, BAD_CLOSE, f"{CLICK} · Private · 1 track"]

        await page.refresh_header("renamed [/y]", "now [Live]", "PUBLIC")
        await pilot.pause()
        assert header() == [
            "renamed [/y]",
            "now [Live]",
            f"{CLICK} · Public · 1 track",
        ]

        page.update_track_count(+1)
        await pilot.pause()
        assert header()[-1] == f"{CLICK} · Public · 2 tracks"


# ── Context ──────────────────────────────────────────────────────────


async def _context(app: _Host, pilot) -> ContextPage:
    await asyncio.wait_for(app.workers.wait_for_complete(), 5)
    await pilot.pause()
    return app.query_one(ContextPage)


def _context_texts(page: ContextPage, selector: str) -> list[str]:
    return _texts(page.query(selector).results(Label))


async def test_album_header_is_literal_and_keeps_app_styling() -> None:
    app = _Host(ContextPage("album", "MPRE1", id="context"))
    app.ytmusic.get_album = AsyncMock(
        return_value={
            "title": "Hits [Deluxe]",
            "artists": [{"name": BAD_CLOSE}],
            "year": "2020",
            "tracks": [_track(0)],
        }
    )
    async with app.run_test() as pilot:
        page = await _context(app, pilot)

        assert _context_texts(page, ".context-title") == ["Hits [Deluxe]"]
        assert _context_texts(page, ".context-subtitle") == [f"{BAD_CLOSE} · 2020 · 1 track"]
        # The app's own markup in the same header still renders as styling.
        badge = next(
            _shown(label)
            for label in page.query(".context-header-row Label").results(Label)
            if _shown(label).plain == "Album"
        )
        assert [(s.start, s.end, str(s.style)) for s in badge.spans] == [(0, 5, "b")]


async def test_playlist_header_is_literal() -> None:
    app = _Host(ContextPage("playlist", "PL1", id="context"))
    app.ytmusic.get_playlist = AsyncMock(
        return_value={
            "title": BAD_CLOSE,
            "author": {"name": LIVE},
            "trackCount": 1,
            "tracks": [_track(0)],
            "owned": True,
        }
    )
    async with app.run_test() as pilot:
        page = await _context(app, pilot)

        assert _context_texts(page, ".context-title") == [BAD_CLOSE]
        assert _context_texts(page, ".context-subtitle") == [f"{LIVE} · 1 track"]


async def test_artist_header_and_similar_artists_are_literal() -> None:
    app = _Host(ContextPage("artist", "UC1", id="context"))
    app.ytmusic.get_artist = AsyncMock(
        return_value={
            "name": CLICK,
            "subscribers": "[/x]",
            "songs": {"results": [_track(0)]},
            "related": {"results": [{"name": LIVE}, {"name": BAD_CLOSE}]},
        }
    )
    async with app.run_test() as pilot:
        page = await _context(app, pilot)

        assert _context_texts(page, ".context-title") == [CLICK]
        assert _context_texts(page, ".context-subtitle") == ["[/x] subscribers"]
        assert _context_texts(page, ".similar-artists-bar") == [
            f"Similar Artists: {LIVE} · {BAD_CLOSE}"
        ]


# ── Queue ────────────────────────────────────────────────────────────


async def test_queue_header_seed_line_and_tooltip_are_literal() -> None:
    app = _Host(QueuePage(id="queue"))
    # The playing row also lands in the track table, a separate sink: keep it
    # free of closing tags so this test only exercises the Queue page's own text.
    track = {"video_id": "v1", "title": LIVE, "artist": CLICK, "artists": [], "duration": 100}
    app.queue.add_multiple([track])
    app.queue.jump_to_real(0)
    app.queue.radio_seeds = [{"title": BAD_CLOSE}, {"title": "two [MV]"}]
    async with app.run_test(tooltips=True) as pilot:
        page = app.query_one(QueuePage)
        await pilot.pause()

        assert _texts(page.query("#queue-header Label").results(Label)) == [
            f"Now Playing: {LIVE}",
            CLICK,
        ]
        source = page.query_one("#queue-source", Static)
        assert _texts([source]) == [f"Generated from: {BAD_CLOSE}, two [MV]"]

        # What the screen does once the hover delay runs out (no timer wait).
        app.screen._handle_tooltip_timer(source)
        await pilot.pause()
        tooltip = app.screen.query_one(Tooltip)
        assert tooltip.display
        assert _texts([tooltip]) == [f"Generated from:\n  • {BAD_CLOSE}\n  • two [MV]"]

        # The player's track-change callback rebuilds the header on its own path.
        app.queue.add({"video_id": "v2", "title": "Next [Live]", "artist": LIVE, "artists": []})
        app.queue.jump_to_real(1)
        page._on_track_change({})
        await pilot.pause()
        assert _texts(page.query("#queue-header Label").results(Label)) == [
            "Now Playing: Next [Live]",
            LIVE,
        ]


# ── Browse ───────────────────────────────────────────────────────────


async def test_for_you_shelves_show_as_typed() -> None:
    shelf = {
        "title": "Mixed [For You]",
        "contents": [
            {"title": BAD_CLOSE, "artists": [{"name": "Band"}], "videoId": "v1"},
            {"title": CLICK, "videoId": "v2"},
        ],
    }
    section = ForYouSection(home_feed=AsyncMock(return_value=[shelf]))
    app = _Host(section)
    async with app.run_test() as pilot:
        await section.load_data()
        await pilot.pause()

        assert _texts(section.query(".shelf-title").results(Label)) == ["Mixed [For You]"]
        assert _list_texts(section.query_one(ListView)) == [f"{BAD_CLOSE}  Band", CLICK]


async def test_new_releases_playlists_and_subscriptions_show_as_typed() -> None:
    releases = NewReleasesSection()
    playlists = PlaylistsSection(
        home_feed=AsyncMock(
            return_value=[
                {"contents": [{"playlistId": "PL1", "title": LIVE, "description": BAD_CLOSE}]}
            ]
        )
    )
    subs = SubscriptionsSection()
    app = _Host(releases, playlists, subs)
    app.ytmusic.get_new_releases = AsyncMock(
        return_value=[
            {
                "title": "Hits [Deluxe]",
                "artists": [{"name": CLICK}],
                "type": "Album",
                "year": "2026",
            }
        ]
    )
    app.ytmusic.get_library_artists = AsyncMock(
        return_value=[{"artist": BAD_CLOSE, "subscribers": "2K"}]
    )
    async with app.run_test(size=(120, 60)) as pilot:
        await releases.load_data()
        await playlists.load_data()
        await subs.load_data()
        await pilot.pause()

        assert _list_texts(releases.query_one("#releases-list", ListView)) == [
            f"Hits [Deluxe] by {CLICK} (Album, 2026)"
        ]
        assert _list_texts(playlists.query_one("#playlists-list", ListView)) == [
            f"{LIVE} ({BAD_CLOSE})"
        ]
        assert _list_texts(subs.query_one("#subs-list", ListView)) == [
            f"{BAD_CLOSE}  (2K subscribers)"
        ]


async def test_chart_pills_show_as_typed() -> None:
    section = ChartsSection()
    app = _Host(section)
    app.ytmusic.get_charts = AsyncMock(
        return_value={"daily": [{"title": "Top [Live] [/x]", "playlistId": "PLc"}]}
    )
    app.ytmusic.get_chart_shelf_tracks = AsyncMock(return_value=[])
    async with app.run_test(size=(120, 40)) as pilot:
        await section.load_data()
        await pilot.pause()

        assert _texts([section.query_one("#charts-pill-0", Static)]) == ["Top [Live] [/x]"]


@pytest.mark.parametrize("region", ["[/x]", "[Live]"])
async def test_chart_country_line_and_errors_show_the_region_as_typed(region: str) -> None:
    # ``[ui] region`` is the user's own config value and is not validated.
    settings = Settings()
    settings.ui.region = region
    with patch("ytm_player.config.settings._settings", settings):
        section = ChartsSection()
        app = _Host(section)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            def country() -> str:
                return _texts([section.query_one("#charts-country", Static)])[0]

            def message() -> str:
                return _texts([section.query_one("#charts-loading", Static)])[0]

            assert country() == f"Country: {region}    (press 'c' to change)"

            app.ytmusic.get_charts = AsyncMock(return_value=None)
            await section.load_data()
            await pilot.pause()
            assert message() == f"Failed to load charts for {region} — try again later."

            app.ytmusic.get_charts = AsyncMock(return_value={"daily": []})
            await section.load_data()
            await pilot.pause()
            assert message().startswith(f"No chart data available for {region}. ")

            app.ytmusic.get_charts = AsyncMock(
                return_value={"daily": [{"title": "Top 100", "playlistId": "PLc"}]}
            )
            app.ytmusic.get_chart_shelf_tracks = AsyncMock(return_value=[_track(0)])
            await section.load_data()
            await pilot.pause()
            assert country() == f"Country: {region}    (press 'c' to change)"


# ── Help ─────────────────────────────────────────────────────────────


async def test_help_page_shows_configured_keybindings_as_typed() -> None:
    # Bindings as a user's keymap.toml would set them.
    keymap = KeyMap()
    keymap._load_from_dict(
        {
            "playback": {
                "play_pause": ["[/x]", "[Live]"],
                "next_track": "[live]",
                "mute": "[@click=app.quit]x[/]",
            }
        }
    )
    app = _Host(HelpPage(id="help"))
    with patch("ytm_player.config.keymap._keymap", keymap):
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            table = app.query_one("#help-table", DataTable)
            strips = table.render_lines(table.region.reset_offset)
            lines = ["".join(seg.text for seg in strip) for strip in strips]

            def row(description: str) -> list[str]:
                return [line.split() for line in lines if description in line][0]

            assert row("Toggle play/pause")[2:5] == ["[/x]", "/", "[Live]"]
            assert row("Skip to next track")[2] == "[live]"
            # The 16-column Key cell crops the payload; what shows is the raw text.
            assert row("Toggle mute")[1] == "[@click=app.quit"
            assert not [
                seg
                for strip in strips
                for seg in strip
                if seg.style is not None and "@click" in (seg.style.meta or {})
            ]
            # The key column keeps its fixed width.
            assert table.columns["key"].width == 16
