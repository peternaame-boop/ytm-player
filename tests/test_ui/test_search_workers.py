"""SearchPage's workers: a search and the suggestions overlay's fetches run
in groups of their own, so typing while a search runs no longer cancels the
search — and a search still supersedes a suggestions fetch in flight."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static
from textual.worker import WorkerCancelled, WorkerFailed, WorkerState

from ytm_player.ui.pages.search import SearchPage, SuggestionList


class _Host(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.search_started = asyncio.Event()
        self.search_gate = asyncio.Event()
        self.suggest_started = asyncio.Event()
        self.suggest_gate = asyncio.Event()
        self.ytmusic = MagicMock()
        self.ytmusic.search = AsyncMock(side_effect=self._search)
        self.ytmusic.get_search_suggestions = AsyncMock(side_effect=self._suggest)
        self.history = MagicMock()
        self.history.log_search = AsyncMock()
        self.history.get_search_history = AsyncMock(return_value=[{"query": "earlier"}])

    async def _search(self, query, filter=None, limit=10):
        self.search_started.set()
        await self.search_gate.wait()
        return []

    async def _suggest(self, query):
        self.suggest_started.set()
        await self.suggest_gate.wait()
        return [f"{query}a", f"{query}b"]

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        yield SearchPage(id="search")


@pytest.fixture
def worker_kwargs(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, Any]]:
    """The keyword arguments of every worker the page starts, by worker name."""
    seen: dict[str, dict[str, Any]] = {}
    original = SearchPage.run_worker

    def recording(self, *args, **kwargs):
        seen[kwargs.get("name", "")] = kwargs
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SearchPage, "run_worker", recording)
    return seen


def _worker(app: _Host, name: str):
    return next(w for w in app.workers if w.name == name)


async def _settle(app: _Host, pilot) -> None:
    """Wait for every worker, the cancelled ones included."""
    for worker in list(app.workers):
        try:
            await asyncio.wait_for(worker.wait(), 5)
        except (WorkerCancelled, WorkerFailed):
            pass
    await pilot.pause()


async def test_a_suggestions_fetch_during_a_search_leaves_the_search_running() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        page = app.query_one(SearchPage)
        page._start_search("abc")
        await asyncio.wait_for(app.search_started.wait(), 5)
        search = _worker(app, "search")

        page._fetch_suggestions("abcd")
        await asyncio.wait_for(app.suggest_started.wait(), 5)
        await pilot.pause()

        assert search.state == WorkerState.RUNNING

        app.search_gate.set()
        app.suggest_gate.set()
        await _settle(app, pilot)

        assert search.state == WorkerState.SUCCESS
        assert page._last_query == "abc"
        assert page.is_loading is False
        assert str(app.query_one("#loading-msg", Static).render()) == ""


async def test_a_search_cancels_the_suggestions_fetch_in_flight() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        page = app.query_one(SearchPage)
        page._fetch_suggestions("ab")
        await asyncio.wait_for(app.suggest_started.wait(), 5)
        suggestions = _worker(app, "suggestions")

        app.search_gate.set()  # this search is fast
        page._start_search("ab")
        search = _worker(app, "search")
        while search.state == WorkerState.RUNNING:
            await pilot.pause()
        assert search.state == WorkerState.SUCCESS

        # Had the fetch survived, its suggestions would now cover the results.
        app.suggest_gate.set()
        await _settle(app, pilot)

        assert suggestions.state == WorkerState.CANCELLED
        assert not app.query_one("#suggestion-overlay", SuggestionList).has_class("visible")


async def test_the_page_workers_run_in_their_groups(worker_kwargs) -> None:
    app = _Host()
    app.search_gate.set()
    app.suggest_gate.set()
    async with app.run_test() as pilot:
        page = app.query_one(SearchPage)
        page._fetch_suggestions("ab")
        await _settle(app, pilot)
        page._show_recent_searches()
        await _settle(app, pilot)
        page._start_search("ab")
        await _settle(app, pilot)
        page._toggle_search_mode()  # re-runs the last search in the other mode
        await _settle(app, pilot)

    assert worker_kwargs["search"] == {
        "name": "search",
        "group": SearchPage.SEARCH_GROUP,
        "exclusive": True,
    }
    assert worker_kwargs["suggestions"] == {
        "name": "suggestions",
        "group": SearchPage.SUGGEST_GROUP,
        "exclusive": True,
    }
    assert worker_kwargs["recent"] == {
        "name": "recent",
        "group": SearchPage.SUGGEST_GROUP,
        "exclusive": True,
    }
    assert SearchPage.SEARCH_GROUP != SearchPage.SUGGEST_GROUP
    assert "default" not in (SearchPage.SEARCH_GROUP, SearchPage.SUGGEST_GROUP)
