"""Browse › Charts workers: the region reload is the Charts loader (so a tab
switch that cancels it is retried like any cancelled load), the pill fetch
is the page's own worker (so its failure keeps the tab retryable), and a pill
click cannot race a region change or land on the wrong country's pills."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static
from textual.worker import Worker, WorkerState

from tests.conftest import make_ytmusic_service
from ytm_player.ui.pages import browse
from ytm_player.ui.pages.browse import BrowsePage, ChartsSection

TAB_CHARTS = 1
FEED = [{"title": "Quick picks", "contents": [{"title": "S", "videoId": "v1", "thumbnails": []}]}]


@pytest.fixture(autouse=True)
def _fixed_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(ui=SimpleNamespace(home_shelves=3, region="ZZ"), save=lambda: None)
    monkeypatch.setattr(browse, "get_settings", lambda: settings)


def _charts(country: str) -> dict[str, Any]:
    # Three country charts, no events; sorted by the page's chart priority.
    return {
        "daily": [
            {"title": f"Top 100 Songs - {country}", "playlistId": f"PL_{country}_top"},
            {"title": f"Trending 20 - {country}", "playlistId": f"PL_{country}_trend"},
            {"title": f"Top Music Videos - {country}", "playlistId": f"PL_{country}_videos"},
        ]
    }


class _Host(App[None]):
    """BrowsePage on a fake service whose chart and shelf fetches wait on gates."""

    def __init__(self) -> None:
        super().__init__()
        self.ytmusic = MagicMock()
        self.ytmusic.get_home = AsyncMock(return_value=FEED)
        self.ytmusic.get_library_artists = AsyncMock(return_value=[])
        self.ytmusic.get_new_releases = AsyncMock(return_value=[])
        self.ytmusic.get_charts = AsyncMock(side_effect=self._get_charts)
        self.ytmusic.get_chart_shelf_tracks = AsyncMock(side_effect=self._shelf_tracks)
        self.navigate_to = AsyncMock()
        self._replace_queue_and_play = AsyncMock()
        self.charts_gates: dict[str, asyncio.Event] = {}
        self.shelf_gates: dict[str, asyncio.Event] = {}
        self.failing_shelves: set[str] = set()
        self.shelf_calls: list[str] = []

    async def _get_charts(self, country: str = "ZZ") -> Any:
        gate = self.charts_gates.get(country)
        if gate is not None:
            await gate.wait()
        return _charts(country)

    async def _shelf_tracks(self, playlist_id: str, limit: int = 100) -> list[dict[str, Any]]:
        self.shelf_calls.append(playlist_id)
        if playlist_id in self.failing_shelves:
            raise RuntimeError("server said no")
        gate = self.shelf_gates.get(playlist_id)
        if gate is not None:
            await gate.wait()
        return [{"title": f"track-of-{playlist_id}", "videoId": f"v_{playlist_id}", "artists": []}]

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        yield BrowsePage(id="page")


class _FakeClick:
    def __init__(self, widget) -> None:
        self.widget = widget


async def _settle(host: _Host, pilot) -> None:
    await asyncio.wait_for(host.workers.wait_for_complete(), 5)
    await pilot.pause()
    await pilot.pause()


async def _open_charts(host: _Host, pilot) -> tuple[BrowsePage, ChartsSection]:
    await _settle(host, pilot)
    await pilot.click("#tab-1")
    await _settle(host, pilot)
    return host.query_one("#page", BrowsePage), host.query_one("#section-charts", ChartsSection)


def _msg(host: _Host, static_id: str) -> str:
    widget = host.query_one(static_id, Static)
    return str(widget.render()) if widget.display else ""


def _titles(host: _Host) -> list[str]:
    return [t.get("title") for t in host.query_one("#charts-table").tracks]


def _countries(host: _Host) -> list[str]:
    return [call.kwargs["country"] for call in host.ytmusic.get_charts.await_args_list]


# ── Region change ────────────────────────────────────────────────────


async def test_a_region_change_cancelled_by_a_tab_switch_is_fetched_when_charts_is_next_selected():
    host = _Host()
    async with host.run_test(size=(120, 30)) as pilot:
        page, charts = await _open_charts(host, pilot)
        assert host.shelf_calls == ["PL_ZZ_top"]

        host.charts_gates["GB"] = asyncio.Event()
        await page._on_country_picked("GB")
        await pilot.pause()
        reload = page._tab_workers[TAB_CHARTS]
        assert (reload.name, reload.group) == ("load-charts", BrowsePage._LOAD_GROUP)

        await pilot.click("#tab-4")  # cancels the reload
        await pilot.pause()
        host.charts_gates["GB"].set()
        await _settle(host, pilot)
        assert reload.state == WorkerState.CANCELLED
        assert TAB_CHARTS not in page._tabs_loaded

        await pilot.click("#tab-1")
        await _settle(host, pilot)

        assert _countries(host) == ["ZZ", "GB", "GB"]
        assert [d["playlistId"] for d in charts._dailies][0] == "PL_GB_top"
        assert "GB" in _msg(host, "#charts-country")
        assert host.shelf_calls == ["PL_ZZ_top", "PL_GB_top"]
        assert TAB_CHARTS in page._tabs_loaded


async def test_the_country_label_stays_with_the_data_until_the_new_charts_arrive():
    host = _Host()
    async with host.run_test(size=(120, 30)) as pilot:
        page, charts = await _open_charts(host, pilot)

        host.charts_gates["GB"] = asyncio.Event()
        await page._on_country_picked("GB")
        await pilot.pause()
        await pilot.pause()
        assert charts._country == "ZZ"
        assert "ZZ" in _msg(host, "#charts-country")

        host.charts_gates["GB"].set()
        await _settle(host, pilot)
        assert charts._country == "GB"
        assert "GB" in _msg(host, "#charts-country")
        assert host.shelf_calls == ["PL_ZZ_top", "PL_GB_top"]


async def test_a_region_change_during_the_first_load_keeps_charts_loaded():
    host = _Host()
    async with host.run_test(size=(120, 30)) as pilot:
        await _settle(host, pilot)
        page = host.query_one("#page", BrowsePage)
        charts = host.query_one("#section-charts", ChartsSection)
        host.charts_gates["ZZ"] = asyncio.Event()
        await pilot.click("#tab-1")
        await pilot.pause()
        first = page._tab_workers[TAB_CHARTS]

        host.charts_gates["GB"] = asyncio.Event()
        await page._on_country_picked("GB")
        await pilot.pause()
        await pilot.pause()
        assert first.state == WorkerState.CANCELLED
        assert TAB_CHARTS in page._tabs_loaded  # the superseded loader's outcome is ignored

        host.charts_gates["ZZ"].set()
        host.charts_gates["GB"].set()
        await _settle(host, pilot)
        assert TAB_CHARTS in page._tabs_loaded
        assert [d["playlistId"] for d in charts._dailies][0] == "PL_GB_top"
        assert _countries(host) == ["ZZ", "GB"]


# ── Pill clicks against a region change ───────────────────────────────


async def test_a_pill_click_during_a_region_change_is_ignored():
    host = _Host()
    async with host.run_test(size=(120, 30)) as pilot:
        page, charts = await _open_charts(host, pilot)

        host.shelf_gates["PL_GB_top"] = asyncio.Event()
        await page._on_country_picked("GB")
        await pilot.pause()
        await pilot.pause()
        assert host.shelf_calls == ["PL_ZZ_top", "PL_GB_top"]

        await pilot.click("#charts-pill-1")  # the GB pills, already rendered
        await pilot.pause()
        await pilot.pause()
        assert host.shelf_calls == ["PL_ZZ_top", "PL_GB_top"]
        assert page._shelf_worker is None

        host.shelf_gates["PL_GB_top"].set()
        await _settle(host, pilot)
        assert charts._active_daily == 0
        assert _titles(host) == ["track-of-PL_GB_top"]


async def test_a_shelf_request_queued_before_a_region_change_is_refused():
    host = _Host()
    async with host.run_test(size=(120, 30)) as pilot:
        page, charts = await _open_charts(host, pilot)
        host.charts_gates["GB"] = asyncio.Event()

        page.post_message(ChartsSection.ShelfRequested(charts, 1, charts.revision))
        await page._on_country_picked("GB")  # scheduled before the request is handled
        await pilot.pause()
        await pilot.pause()

        assert page._shelf_worker is None
        assert host.shelf_calls == ["PL_ZZ_top"]
        assert charts._active_daily == 0
        assert not host.query_one("#charts-pill-1").has_class("active")

        host.charts_gates["GB"].set()
        await _settle(host, pilot)
        assert host.shelf_calls == ["PL_ZZ_top", "PL_GB_top"]
        assert _titles(host) == ["track-of-PL_GB_top"]


async def test_a_click_between_the_reload_being_scheduled_and_started_is_ignored():
    host = _Host()
    async with host.run_test(size=(120, 30)) as pilot:
        page, charts = await _open_charts(host, pilot)
        host.charts_gates["GB"] = asyncio.Event()

        await page._on_country_picked("GB")  # nothing awaited: the loader has not run yet
        charts.on_click(_FakeClick(host.query_one("#charts-pill-1")))
        await pilot.pause()
        await pilot.pause()

        assert page._shelf_worker is None
        assert host.shelf_calls == ["PL_ZZ_top"]

        host.charts_gates["GB"].set()
        await _settle(host, pilot)
        assert charts._active_daily == 0
        assert _titles(host) == ["track-of-PL_GB_top"]


async def test_a_pill_fetch_in_flight_is_cancelled_by_a_region_change():
    host = _Host()
    async with host.run_test(size=(120, 30)) as pilot:
        page, charts = await _open_charts(host, pilot)
        host.shelf_gates["PL_ZZ_trend"] = asyncio.Event()

        await pilot.click("#charts-pill-1")
        await pilot.pause()
        await pilot.pause()
        shelf = page._shelf_worker
        assert shelf is not None and shelf.state == WorkerState.RUNNING
        assert (shelf.name, shelf.group) == (BrowsePage._SHELF_WORKER, BrowsePage._SHELF_GROUP)
        assert host.shelf_calls == ["PL_ZZ_top", "PL_ZZ_trend"]

        host.charts_gates["GB"] = asyncio.Event()
        await page._on_country_picked("GB")
        await pilot.pause()
        await pilot.pause()
        assert shelf.state == WorkerState.CANCELLED
        assert page._shelf_worker is None
        assert TAB_CHARTS in page._tabs_loaded  # cancelled by the reload: no failure

        host.charts_gates["GB"].set()
        host.shelf_gates["PL_ZZ_trend"].set()
        await _settle(host, pilot)
        assert charts._active_daily == 0
        assert _titles(host) == ["track-of-PL_GB_top"]
        assert host.shelf_calls == ["PL_ZZ_top", "PL_ZZ_trend", "PL_GB_top"]


# ── Pill fetch outcomes ──────────────────────────────────────────────


async def test_a_failing_pill_leaves_charts_retryable():
    host = _Host()
    host.failing_shelves.add("PL_ZZ_trend")
    async with host.run_test(size=(120, 30)) as pilot:
        page, charts = await _open_charts(host, pilot)

        await pilot.click("#charts-pill-1")
        await _settle(host, pilot)
        assert _msg(host, "#charts-loading").startswith("Failed to load chart playlist")
        assert charts.load_failed is True
        assert TAB_CHARTS not in page._tabs_loaded

        await pilot.click("#tab-0")
        await _settle(host, pilot)
        await pilot.click("#tab-1")
        await _settle(host, pilot)
        assert _countries(host) == ["ZZ", "ZZ"]
        assert host.shelf_calls == ["PL_ZZ_top", "PL_ZZ_trend", "PL_ZZ_top"]
        assert _msg(host, "#charts-loading") == ""
        assert host.query_one("#charts-content").display is True
        assert TAB_CHARTS in page._tabs_loaded


async def test_a_pill_fetch_that_raises_shows_a_failure_and_leaves_charts_retryable(
    monkeypatch: pytest.MonkeyPatch,
):
    host = _Host()
    async with host.run_test(size=(120, 30)) as pilot:
        page, charts = await _open_charts(host, pilot)
        original = ChartsSection._load_active_daily

        async def boom(self: ChartsSection) -> None:
            raise RuntimeError("shelf bug")

        monkeypatch.setattr(ChartsSection, "_load_active_daily", boom)
        await pilot.click("#charts-pill-1")
        await _settle(host, pilot)
        assert page._shelf_worker is not None
        assert page._shelf_worker.state == WorkerState.ERROR
        assert _msg(host, "#charts-loading") == f"Failed to load Charts — {browse._RETRY}."
        assert TAB_CHARTS not in page._tabs_loaded

        monkeypatch.setattr(ChartsSection, "_load_active_daily", original)
        await pilot.click("#tab-0")
        await _settle(host, pilot)
        await pilot.click("#tab-1")
        await _settle(host, pilot)
        assert _countries(host) == ["ZZ", "ZZ"]
        assert _msg(host, "#charts-loading") == ""
        assert host.query_one("#charts-content").display is True


async def test_a_pill_fetch_superseded_by_a_newer_pill_does_not_fail_the_tab():
    host = _Host()
    async with host.run_test(size=(120, 30)) as pilot:
        page, charts = await _open_charts(host, pilot)
        host.shelf_gates["PL_ZZ_trend"] = asyncio.Event()

        await pilot.click("#charts-pill-1")
        await pilot.pause()
        await pilot.pause()
        first = page._shelf_worker
        assert first is not None

        await pilot.click("#charts-pill-2")
        await _settle(host, pilot)
        assert first.state == WorkerState.CANCELLED
        assert TAB_CHARTS in page._tabs_loaded
        assert charts._active_daily == 2
        assert _titles(host) == ["track-of-PL_ZZ_videos"]
        assert host.query_one("#charts-pill-2").has_class("active")
        assert host.query_one("#charts-content").display is True

        # A stale pill fetch's outcome is ignored as well.
        stale = MagicMock(spec=Worker)
        stale.name = BrowsePage._SHELF_WORKER
        page.on_worker_state_changed(Worker.StateChanged(stale, WorkerState.ERROR))
        assert TAB_CHARTS in page._tabs_loaded
        assert _msg(host, "#charts-loading") == ""


async def test_pills_are_hidden_during_the_first_shelf_fetch():
    """Control: the first shelf fetch happens behind the loading placeholder."""
    host = _Host()
    async with host.run_test(size=(120, 30)) as pilot:
        await _settle(host, pilot)
        host.shelf_gates["PL_ZZ_top"] = asyncio.Event()
        await pilot.click("#tab-1")
        await pilot.pause()
        await pilot.pause()
        assert host.shelf_calls == ["PL_ZZ_top"]
        assert host.query_one("#charts-content").display is False

        await pilot.click("#charts-pill-1")
        await pilot.pause()
        await pilot.pause()
        assert host.shelf_calls == ["PL_ZZ_top"]

        host.shelf_gates["PL_ZZ_top"].set()
        await _settle(host, pilot)
        assert _titles(host) == ["track-of-PL_ZZ_top"]


# ── Through the real service wrapper ──────────────────────────────────
# ``YTMusicService.get_chart_shelf_tracks`` turns a failed playlist request
# into an empty list (``get_playlist`` returns ``{}``, the OLAK5 path's
# ``get_watch_playlist`` returns ``[]``), so the page sees no exception: an
# empty shelf has to be retryable on its own.

EMPTY_SHELF = "No chart tracks returned — select the tab again to retry."


def _service_host(shelf_id: str, client_playlist) -> _Host:
    """A host whose chart shelves resolve through the real wrapper on a fake client."""
    host = _Host()
    service = make_ytmusic_service()
    feed = _charts("ZZ")
    feed["daily"][1]["playlistId"] = shelf_id
    host.ytmusic.get_charts = AsyncMock(return_value=feed)
    host.ytmusic.get_chart_shelf_tracks = service.get_chart_shelf_tracks
    service._ytm.get_playlist = MagicMock(side_effect=client_playlist)
    service._ytm.get_watch_playlist = MagicMock(side_effect=client_playlist)
    return host


def _client_playlist(*, failing: set[str], empty: set[str] = frozenset()):
    def client_playlist(playlist_id=None, *args, **kwargs):
        requested = playlist_id or kwargs.get("playlistId")
        if requested in failing:
            raise ConnectionError("synthetic offline request")
        if requested in empty:
            return {"tracks": []}
        return {"tracks": [{"videoId": f"v_{requested}", "title": f"track-of-{requested}"}]}

    return client_playlist


@pytest.mark.parametrize("shelf_id", ["PL_failed", "OLAK5_failed"])
async def test_a_shelf_the_service_could_not_fetch_is_retryable(shelf_id: str):
    failing = {shelf_id}
    host = _service_host(shelf_id, _client_playlist(failing=failing))
    async with host.run_test(size=(120, 30)) as pilot:
        page, charts = await _open_charts(host, pilot)
        assert _titles(host) == ["track-of-PL_ZZ_top"]  # a shelf that resolves renders

        await pilot.click("#charts-pill-1")
        await _settle(host, pilot)
        assert _msg(host, "#charts-loading") == EMPTY_SHELF
        assert charts.load_failed is True
        assert TAB_CHARTS not in page._tabs_loaded

        failing.clear()
        await pilot.click("#tab-0")
        await _settle(host, pilot)
        await pilot.click("#tab-1")
        await _settle(host, pilot)
        assert host.ytmusic.get_charts.await_count == 2
        assert _titles(host) == ["track-of-PL_ZZ_top"]
        assert TAB_CHARTS in page._tabs_loaded

        await pilot.click("#charts-pill-1")
        await _settle(host, pilot)
        assert _titles(host) == [f"track-of-{shelf_id}"]
        assert TAB_CHARTS in page._tabs_loaded


async def test_a_genuinely_empty_shelf_is_retryable_too():
    host = _service_host("PL_empty", _client_playlist(failing=set(), empty={"PL_empty"}))
    async with host.run_test(size=(120, 30)) as pilot:
        page, charts = await _open_charts(host, pilot)

        await pilot.click("#charts-pill-1")
        await _settle(host, pilot)
        assert _msg(host, "#charts-loading") == EMPTY_SHELF
        assert charts.load_failed is True
        assert TAB_CHARTS not in page._tabs_loaded

        await pilot.click("#tab-0")
        await _settle(host, pilot)
        await pilot.click("#tab-1")
        await _settle(host, pilot)
        assert host.ytmusic.get_charts.await_count == 2
        assert _titles(host) == ["track-of-PL_ZZ_top"]
        assert TAB_CHARTS in page._tabs_loaded


async def test_a_first_shelf_the_service_could_not_fetch_leaves_the_tab_retryable():
    failing = {"PL_ZZ_top"}
    host = _service_host("PL_other", _client_playlist(failing=failing))
    async with host.run_test(size=(120, 30)) as pilot:
        page, charts = await _open_charts(host, pilot)
        assert _msg(host, "#charts-loading") == EMPTY_SHELF
        assert charts.load_failed is True
        assert TAB_CHARTS not in page._tabs_loaded

        failing.clear()
        await pilot.click("#tab-0")
        await _settle(host, pilot)
        await pilot.click("#tab-1")
        await _settle(host, pilot)
        assert host.ytmusic.get_charts.await_count == 2
        assert _titles(host) == ["track-of-PL_ZZ_top"]
        assert TAB_CHARTS in page._tabs_loaded
