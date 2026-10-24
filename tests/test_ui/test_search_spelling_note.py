"""Search's spelling note, as on the web: "Showing results for" and "Did you mean". Clicked through Pilot."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input

from ytm_player.ui.pages.search import SearchPage, SpellingNote
from ytm_player.ui.widgets import cover

SONG = {"resultType": "song", "title": "Halo", "videoId": "v1", "duration_seconds": 261,
        "artists": [{"name": "Beyoncé", "id": "UC1"}], "thumbnails": []}  # fmt: skip

SHOWING = {"kind": "showing", "corrected": "beyonce halo", "original": "beyonse halo",
           "runs": [("beyonce", True), (" halo", False)]}  # fmt: skip
DID_YOU_MEAN = {"kind": "did_you_mean", "corrected": "beyonce halo", "original": "beyonse halo",
                "runs": [("beyonce", True), (" halo", False)]}  # fmt: skip


class _Host(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.ytmusic = MagicMock()
        self.ytmusic.search = AsyncMock(return_value=[SONG])
        self.ytmusic.get_search_suggestions = AsyncMock(return_value=[])
        self.ytmusic.get_search_correction = AsyncMock(side_effect=self._correction)
        self.history = MagicMock()
        self.history.log_search = AsyncMock()
        self.history.get_search_history = AsyncMock(return_value=[])
        self.navigate_to = AsyncMock()

    async def _correction(self, query, ignore_spelling=False):
        if query == "beyonse halo":
            return DID_YOU_MEAN if ignore_spelling else SHOWING
        return None

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        yield SearchPage(id="search")


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cover, "fetch_thumbnail", lambda url: None)


async def _wait(pilot, condition) -> None:
    for _ in range(60):
        await pilot.pause(0.05)
        if condition():
            return
    raise AssertionError("condition never held")


def _text(app: App, widget_id: str) -> str:
    return str(app.query_one(widget_id).render())


async def test_a_corrected_search_shows_both_queries():
    app = _Host()
    async with app.run_test(size=(160, 45)) as pilot:
        app.query_one("#search-input", Input).value = "beyonse halo"
        await pilot.press("enter")
        note = app.query_one(SpellingNote)
        await _wait(pilot, lambda: note.has_class("visible"))
        assert _text(app, "#spell-lead") == "Showing results for"
        assert _text(app, "#spell-corrected") == "beyonce halo"
        assert _text(app, "#spell-instead") == "Search instead for"
        assert _text(app, "#spell-original") == "beyonse halo"
        assert "ignore_spelling" not in app.ytmusic.search.await_args.kwargs


async def test_search_instead_searches_as_typed_and_then_suggests_the_correction():
    app = _Host()
    async with app.run_test(size=(160, 45)) as pilot:
        app.query_one("#search-input", Input).value = "beyonse halo"
        await pilot.press("enter")
        note = app.query_one(SpellingNote)
        await _wait(pilot, lambda: note.has_class("visible"))
        app.ytmusic.search.reset_mock()
        await pilot.click("#spell-original")
        await _wait(pilot, lambda: _text(app, "#spell-lead") == "Did you mean:")
        assert all(c.kwargs.get("ignore_spelling") for c in app.ytmusic.search.await_args_list)
        assert not app.query_one("#spell-original").display
        # Did you mean: the corrected query is a link that searches it, with correction on.
        app.ytmusic.search.reset_mock()
        await pilot.click("#spell-corrected")
        await _wait(pilot, lambda: not note.has_class("visible"))
        assert app.query_one("#search-input", Input).value == "beyonce halo"
        assert app.ytmusic.search.await_args.args[0] == "beyonce halo"
        assert "ignore_spelling" not in app.ytmusic.search.await_args.kwargs


async def test_a_search_needing_no_correction_shows_no_note():
    app = _Host()
    async with app.run_test(size=(160, 45)) as pilot:
        app.query_one("#search-input", Input).value = "halo"
        await pilot.press("enter")
        await _wait(pilot, lambda: app.ytmusic.get_search_correction.await_count == 1)
        await pilot.pause(0.1)
        assert not app.query_one(SpellingNote).has_class("visible")
