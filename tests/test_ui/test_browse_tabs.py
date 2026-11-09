"""Tests for the Browse page's tabs: the shared home feed behind For You and
Playlists, the Playlists tab's row selection, the Subscriptions tab, the tab
order, and the failure/retry state of every lazily loaded tab.

The page is mounted in a headless Textual app with a fake ``ytmusic`` service;
the loaders run as the page's own workers.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label, ListView, Static
from textual.worker import Worker, WorkerState

from ytm_player.config.keymap import Action
from ytm_player.ui.pages import browse
from ytm_player.ui.pages.browse import (
    _PLAYLISTS_SHELF_DEPTH,
    _TABS,
    BrowsePage,
    PlaylistsSection,
    SubscriptionsSection,
    _is_playlist_row,
    _playlist_rows,
)

HOME_SHELVES = 3

# ── Home-feed rows, shaped like ytmusicapi's home parser emits them ──────


def _mix(title: str, playlist_id: str) -> dict[str, Any]:
    return {"title": title, "playlistId": playlist_id, "thumbnails": [], "description": "Mix"}


def _user_playlist(title: str, playlist_id: str) -> dict[str, Any]:
    return {"title": title, "playlistId": playlist_id, "thumbnails": [], "count": "12"}


def _album(title: str, playlist_id: str) -> dict[str, Any]:
    return {
        "title": title,
        "browseId": f"MPREb_{playlist_id}",
        "audioPlaylistId": playlist_id,
        "artists": [{"name": "Artist", "id": "UC1"}],
        "thumbnails": [],
    }


def _song(title: str, video_id: str, playlist_id: str | None = None) -> dict[str, Any]:
    return {"title": title, "videoId": video_id, "playlistId": playlist_id, "thumbnails": []}


def _artist(name: str) -> dict[str, Any]:
    return {"title": name, "browseId": "UCabc", "subscribers": "1.2M", "thumbnails": []}


def _shelf(title: str, *contents: Any) -> dict[str, Any]:
    return {"title": title, "contents": list(contents)}


DISCOVER = _mix("Discover Mix", "RDTMAK5uy_discover")
CURATED = _mix("Take it easy", "RDCLAK5uy_easy")
USER = _user_playlist("Road trip", "PLroadtrip")
RECAP = {"title": "Recap", "playlistId": "LRSRrecap", "thumbnails": [], "description": "Recap"}
LIKED = {"title": "Liked Music", "playlistId": "LM", "thumbnails": [], "description": ""}

SHALLOW_FEED = [
    _shelf("Quick picks", _song("Song A", "vA"), _song("Song B", "vB", "PLsongB")),
    _shelf("Listen again", USER, _song("Song C", "vC")),
    _shelf("Albums for you", _album("Album", "OLAK5uy_album")),
]
DEEP_FEED = SHALLOW_FEED + [
    _shelf("Forgotten favorites", None, USER, "junk"),
    _shelf("Artists", _artist("Someone")),
    _shelf("Mixed for you", DISCOVER, CURATED, {"playlistId": None}, {"playlistId": 5}),
    _shelf("Recaps", RECAP, LIKED),
    {"title": "No contents"},
    "not a shelf",
]
PLAYLIST_IDS = ["PLroadtrip", "RDTMAK5uy_discover", "RDCLAK5uy_easy", "LRSRrecap", "LM"]


# ── Classification ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        (DISCOVER, True),
        (CURATED, True),
        (USER, True),
        (RECAP, True),
        (LIKED, True),
        ({"title": "watch playlist", "playlistId": "RDAMVMabc", "thumbnails": []}, True),
        (_album("Album", "OLAK5uy_x"), False),
        (_song("Song", "v1"), False),
        (_song("Song with playlist", "v1", "PLx"), False),
        (_artist("Someone"), False),
        (None, False),
        ("junk", False),
        ({"playlistId": None}, False),
        ({"playlistId": ""}, False),
        ({"playlistId": 5}, False),
        ({"title": "no id"}, False),
    ],
)
def test_is_playlist_row(item: Any, expected: bool) -> None:
    assert _is_playlist_row(item) is expected


def test_playlist_rows_keeps_feed_order_one_row_per_id_and_skips_malformed_rows() -> None:
    rows = _playlist_rows(DEEP_FEED)

    assert [r["playlistId"] for r in rows] == PLAYLIST_IDS
    assert rows[0] is USER  # the first occurrence wins


def test_playlist_rows_of_a_shallow_feed() -> None:
    assert [r["playlistId"] for r in _playlist_rows(SHALLOW_FEED)] == ["PLroadtrip"]


# ── Headless host ────────────────────────────────────────────────────


class _Host(App):
    """Minimal host exposing what BrowsePage and its sections read."""

    def __init__(
        self, *, feeds: list[Any] | None = None, artists: Any = None, **kwargs: Any
    ) -> None:
        super().__init__()
        self.page_kwargs = kwargs
        self.ytmusic = MagicMock()
        # Each get_home call takes the next entry: a feed list, None (failure)
        # or an exception to raise.
        self._feeds = list(feeds or [])
        self.ytmusic.get_home = AsyncMock(side_effect=self._next_feed)
        self.ytmusic.get_library_artists = AsyncMock(return_value=artists or [])
        self.ytmusic.get_charts = AsyncMock(return_value=None)
        self.ytmusic.get_new_releases = AsyncMock(return_value=[])
        self.navigate_to = AsyncMock()
        self._replace_queue_and_play = AsyncMock()

    async def _next_feed(self, *, limit: int) -> Any:
        if not self._feeds:
            return []
        feed = self._feeds.pop(0)
        if isinstance(feed, Exception):
            raise feed
        return feed

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        yield BrowsePage(id="page", **self.page_kwargs)


@pytest.fixture(autouse=True)
def _fixed_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(ui=SimpleNamespace(home_shelves=HOME_SHELVES, region="ZZ"))
    monkeypatch.setattr(browse, "get_settings", lambda: settings)


def _limits(host: _Host) -> list[int]:
    return [call.kwargs["limit"] for call in host.ytmusic.get_home.await_args_list]


async def _settle(host: _Host, pilot) -> None:
    await host.workers.wait_for_complete()
    await pilot.pause()


def _list_texts(host: _Host, list_id: str) -> list[str]:
    return [str(label.render()) for label in host.query_one(list_id, ListView).query(Label)]


def _message(host: _Host, static_id: str) -> str:
    widget = host.query_one(static_id, Static)
    return str(widget.render()) if widget.display else ""


TAB_FOR_YOU, TAB_CHARTS, TAB_RELEASES, TAB_PLAYLISTS, TAB_SUBS = range(5)


# ── Tab order ────────────────────────────────────────────────────────


def test_tab_order_keeps_the_original_indices() -> None:
    assert _TABS == ("For You", "Charts", "Releases", "Playlists", "Subs")
    assert BrowsePage._SECTION_IDS[:3] == ("section-foryou", "section-charts", "section-releases")
    assert BrowsePage._LOAD_WORKER_TABS == {
        "load-foryou": 0,
        "load-charts": 1,
        "load-releases": 2,
        "load-playlists": 3,
        "load-subscriptions": 4,
    }


async def test_country_picker_only_on_the_charts_tab(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _Host(feeds=[SHALLOW_FEED])
    async with host.run_test(size=(120, 30)) as pilot:
        await _settle(host, pilot)
        page = host.query_one("#page", BrowsePage)
        pushed = MagicMock()
        monkeypatch.setattr(host, "push_screen", pushed)

        await page.handle_action(Action.PICK_COUNTRY)
        pushed.assert_not_called()

        await pilot.click("#tab-1")
        await _settle(host, pilot)
        await page.handle_action(Action.PICK_COUNTRY)
        pushed.assert_called_once()


async def test_nav_state_round_trips_the_playlists_tab() -> None:
    host = _Host(feeds=[SHALLOW_FEED, DEEP_FEED])
    async with host.run_test(size=(120, 30)) as pilot:
        await _settle(host, pilot)
        page = host.query_one("#page", BrowsePage)
        assert page.get_nav_state() == {}
        await pilot.click("#tab-3")
        await _settle(host, pilot)
        assert page.get_nav_state() == {"active_tab": TAB_PLAYLISTS}

    restored = _Host(feeds=[DEEP_FEED], active_tab=TAB_PLAYLISTS)
    async with restored.run_test(size=(120, 30)) as pilot:
        await _settle(restored, pilot)
        assert restored.query_one("#page", BrowsePage).active_tab == TAB_PLAYLISTS
        assert _limits(restored) == [_PLAYLISTS_SHELF_DEPTH]


# ── Shared home feed ─────────────────────────────────────────────────


async def test_for_you_alone_fetches_the_configured_depth() -> None:
    host = _Host(feeds=[SHALLOW_FEED])
    async with host.run_test(size=(120, 30)) as pilot:
        await _settle(host, pilot)
        page = host.query_one("#page", BrowsePage)

        assert page.active_tab == TAB_FOR_YOU
        assert _limits(host) == [HOME_SHELVES]
        assert page._home_depth == HOME_SHELVES
        titles = [str(t.render()) for t in page.query(".shelf-title")]
        assert titles == ["Quick picks", "Listen again", "Albums for you"]


async def test_playlists_expands_the_feed_and_lists_playlist_rows_only() -> None:
    host = _Host(feeds=[SHALLOW_FEED, DEEP_FEED])
    async with host.run_test(size=(120, 30)) as pilot:
        await _settle(host, pilot)
        await pilot.click("#tab-3")
        await _settle(host, pilot)
        page = host.query_one("#page", BrowsePage)

        assert _limits(host) == [HOME_SHELVES, _PLAYLISTS_SHELF_DEPTH]
        assert page._home_depth == _PLAYLISTS_SHELF_DEPTH
        assert _list_texts(host, "#playlists-list") == [
            "Road trip (12 songs)",
            "Discover Mix (Mix)",
            "Take it easy (Mix)",
            "Recap (Recap)",
            "Liked Music",
        ]


async def test_for_you_reuses_the_expanded_feed_and_shows_its_configured_depth() -> None:
    host = _Host(feeds=[DEEP_FEED], active_tab=TAB_PLAYLISTS)
    async with host.run_test(size=(120, 30)) as pilot:
        await _settle(host, pilot)
        await pilot.click("#tab-0")
        await _settle(host, pilot)
        page = host.query_one("#page", BrowsePage)

        assert _limits(host) == [_PLAYLISTS_SHELF_DEPTH]  # no second request
        titles = [str(t.render()) for t in page.query(".shelf-title")]
        assert titles == ["Quick picks", "Listen again", "Albums for you"]


async def test_playlists_reuses_a_cache_that_is_already_deep_enough() -> None:
    host = _Host(feeds=[DEEP_FEED], active_tab=TAB_PLAYLISTS)
    async with host.run_test(size=(120, 30)) as pilot:
        await _settle(host, pilot)
        page = host.query_one("#page", BrowsePage)

        assert await page.get_home_shelves(HOME_SHELVES) is page._home_shelves
        assert await page.get_home_shelves(_PLAYLISTS_SHELF_DEPTH) is page._home_shelves
        assert _limits(host) == [_PLAYLISTS_SHELF_DEPTH]


async def test_failed_expansion_keeps_the_shallow_feed_and_can_be_retried() -> None:
    host = _Host(feeds=[SHALLOW_FEED, None, DEEP_FEED])
    async with host.run_test(size=(120, 30)) as pilot:
        await _settle(host, pilot)
        await pilot.click("#tab-3")
        await _settle(host, pilot)
        page = host.query_one("#page", BrowsePage)

        assert _message(host, "#playlists-loading") == browse._PLAYLISTS_LOAD_FAILED
        assert page._home_shelves == SHALLOW_FEED and page._home_depth == HOME_SHELVES
        assert TAB_PLAYLISTS not in page._tabs_loaded

        # Selecting the tab again runs the loader again.
        await pilot.click("#tab-3")
        await _settle(host, pilot)
        assert _limits(host) == [HOME_SHELVES, _PLAYLISTS_SHELF_DEPTH, _PLAYLISTS_SHELF_DEPTH]
        assert _message(host, "#playlists-loading") == ""
        assert len(_list_texts(host, "#playlists-list")) == len(PLAYLIST_IDS)


async def test_slow_shallow_request_cannot_overwrite_the_expanded_feed() -> None:
    host = _Host()
    release_shallow = asyncio.Event()
    release_deep = asyncio.Event()

    async def get_home(*, limit: int) -> list[Any]:
        if limit == _PLAYLISTS_SHELF_DEPTH:
            await release_deep.wait()
            return DEEP_FEED
        await release_shallow.wait()
        return SHALLOW_FEED

    host.ytmusic.get_home = AsyncMock(side_effect=get_home)
    async with host.run_test(size=(120, 30)) as pilot:
        page = host.query_one("#page", BrowsePage)
        deep = asyncio.create_task(page.get_home_shelves(_PLAYLISTS_SHELF_DEPTH))
        await asyncio.sleep(0)
        release_deep.set()
        assert await deep == DEEP_FEED
        assert page._home_depth == _PLAYLISTS_SHELF_DEPTH

        # The page's own For You request was started first and finishes last.
        release_shallow.set()
        await _settle(host, pilot)
        assert page._home_shelves == DEEP_FEED
        assert page._home_depth == _PLAYLISTS_SHELF_DEPTH
        # ...and a late shallow caller still gets the deeper feed.
        assert await page.get_home_shelves(HOME_SHELVES) == DEEP_FEED


# ── Failure and retry states ─────────────────────────────────────────


async def test_loader_that_raises_shows_a_failure_and_the_tab_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _Host(feeds=[SHALLOW_FEED, DEEP_FEED])
    original = PlaylistsSection.load_data

    async def boom(self: PlaylistsSection) -> None:
        raise RuntimeError("loader bug")

    monkeypatch.setattr(PlaylistsSection, "load_data", boom)
    async with host.run_test(size=(120, 30)) as pilot:
        await _settle(host, pilot)
        await pilot.click("#tab-3")
        await _settle(host, pilot)
        page = host.query_one("#page", BrowsePage)

        assert _message(host, "#playlists-loading") == (
            f"Failed to load Playlists — {browse._RETRY}."
        )
        assert TAB_PLAYLISTS not in page._tabs_loaded

        monkeypatch.setattr(PlaylistsSection, "load_data", original)
        await pilot.click("#tab-3")
        await _settle(host, pilot)
        assert _limits(host) == [HOME_SHELVES, _PLAYLISTS_SHELF_DEPTH]
        assert _message(host, "#playlists-loading") == ""


async def test_cancelled_loader_is_forgotten_so_the_tab_reloads() -> None:
    host = _Host(feeds=[SHALLOW_FEED])
    async with host.run_test(size=(120, 30)) as pilot:
        await _settle(host, pilot)
        page = host.query_one("#page", BrowsePage)
        page._tabs_loaded.add(TAB_PLAYLISTS)
        worker = MagicMock(spec=Worker)
        worker.name = "load-playlists"

        page.on_worker_state_changed(Worker.StateChanged(worker, WorkerState.CANCELLED))

        assert TAB_PLAYLISTS not in page._tabs_loaded
        assert TAB_FOR_YOU in page._tabs_loaded


async def test_empty_playlists_message_and_the_tab_reloads() -> None:
    host = _Host(feeds=[SHALLOW_FEED, [_shelf("Quick picks", _song("Song", "v1"))]])
    async with host.run_test(size=(120, 30)) as pilot:
        await _settle(host, pilot)
        await pilot.click("#tab-3")
        await _settle(host, pilot)
        page = host.query_one("#page", BrowsePage)

        assert "No playlists" in _message(host, "#playlists-loading")
        assert TAB_PLAYLISTS not in page._tabs_loaded


async def test_for_you_failure_can_be_retried_by_selecting_the_tab_again() -> None:
    host = _Host(feeds=[None, SHALLOW_FEED])
    async with host.run_test(size=(120, 30)) as pilot:
        await _settle(host, pilot)
        page = host.query_one("#page", BrowsePage)
        assert _message(host, "#foryou-loading") == browse._FORYOU_LOAD_FAILED
        assert TAB_FOR_YOU not in page._tabs_loaded

        await pilot.click("#tab-0")
        await _settle(host, pilot)
        assert _limits(host) == [HOME_SHELVES, HOME_SHELVES]
        assert _message(host, "#foryou-loading") == ""


# ── Subscriptions ────────────────────────────────────────────────────


async def test_subscriptions_lists_every_subscription() -> None:
    artists = [
        {"artist": "Sia", "browseId": "UCsia", "subscribers": "3.88M"},
        {"artist": "Nobody", "browseId": "UCnobody"},
    ]
    host = _Host(feeds=[SHALLOW_FEED], artists=artists)
    async with host.run_test(size=(120, 30)) as pilot:
        await _settle(host, pilot)
        await pilot.click("#tab-4")
        await _settle(host, pilot)

        host.ytmusic.get_library_artists.assert_awaited_once_with(limit=None)
        assert _list_texts(host, "#subs-list") == ["Sia  (3.88M subscribers)", "Nobody"]


async def test_subscriptions_empty_and_failure_states_reload() -> None:
    host = _Host(feeds=[SHALLOW_FEED], artists=[])
    async with host.run_test(size=(120, 30)) as pilot:
        await _settle(host, pilot)
        page = host.query_one("#page", BrowsePage)
        await pilot.click("#tab-4")
        await _settle(host, pilot)
        assert "No subscriptions yet" in _message(host, "#subs-loading")
        assert TAB_SUBS not in page._tabs_loaded

        host.ytmusic.get_library_artists = AsyncMock(side_effect=RuntimeError("down"))
        await pilot.click("#tab-4")
        await _settle(host, pilot)
        assert _message(host, "#subs-loading") == browse._SUBSCRIPTIONS_LOAD_FAILED
        assert TAB_SUBS not in page._tabs_loaded


# ── Selection routing ────────────────────────────────────────────────


async def test_selecting_a_playlist_opens_its_context_page() -> None:
    host = _Host(feeds=[SHALLOW_FEED])
    async with host.run_test(size=(120, 30)) as pilot:
        await _settle(host, pilot)
        page = host.query_one("#page", BrowsePage)

        await page.on_playlists_section_playlist_selected(
            PlaylistsSection.PlaylistSelected(DISCOVER)
        )

        host.navigate_to.assert_awaited_once_with(
            "context", context_type="playlist", context_id="RDTMAK5uy_discover"
        )
        host._replace_queue_and_play.assert_not_awaited()


async def test_selecting_a_subscription_opens_the_artist_page() -> None:
    host = _Host(feeds=[SHALLOW_FEED])
    async with host.run_test(size=(120, 30)) as pilot:
        await _settle(host, pilot)
        page = host.query_one("#page", BrowsePage)

        await page.on_subscriptions_section_artist_selected(
            SubscriptionsSection.ArtistSelected({"artist": "Sia", "browseId": "UCsia"})
        )

        host.navigate_to.assert_awaited_once_with(
            "context", context_type="artist", context_id="UCsia"
        )


# ── Keyboard retry ───────────────────────────────────────────────────


async def test_enter_on_the_active_tab_retries_a_failed_load_but_not_a_loaded_one() -> None:
    host = _Host(feeds=[None, SHALLOW_FEED])
    async with host.run_test(size=(120, 30)) as pilot:
        await _settle(host, pilot)
        page = host.query_one("#page", BrowsePage)
        assert _message(host, "#foryou-loading") == browse._FORYOU_LOAD_FAILED

        host.query_one("#tab-0").focus()
        await pilot.pause()
        await page.handle_action(Action.SELECT)
        await _settle(host, pilot)
        assert _limits(host) == [HOME_SHELVES, HOME_SHELVES]
        assert _message(host, "#foryou-loading") == ""

        # A loaded tab is left alone.
        host.query_one("#tab-0").focus()
        await pilot.pause()
        await page.handle_action(Action.SELECT)
        await _settle(host, pilot)
        assert _limits(host) == [HOME_SHELVES, HOME_SHELVES]


# ── Service-to-UI contract: a failed fetch is not an empty list ──────


def _real_service():
    from tests.conftest import make_ytmusic_service

    svc = make_ytmusic_service()
    svc._ytm.get_home = MagicMock(return_value=[])
    return svc


async def test_subscriptions_service_failure_shows_the_failure_not_an_empty_list() -> None:
    host = _Host(active_tab=TAB_SUBS)
    svc = _real_service()
    svc._ytm.get_library_subscriptions = MagicMock(side_effect=RuntimeError("down"))
    host.ytmusic = svc
    async with host.run_test(size=(120, 30)) as pilot:
        await _settle(host, pilot)
        page = host.query_one("#page", BrowsePage)
        assert _message(host, "#subs-loading") == browse._SUBSCRIPTIONS_LOAD_FAILED
        assert TAB_SUBS not in page._tabs_loaded

        svc._ytm.get_library_subscriptions = MagicMock(return_value=[])
        await pilot.click("#tab-4")
        await _settle(host, pilot)
        assert "No subscriptions yet" in _message(host, "#subs-loading")

        svc._ytm.get_library_subscriptions = MagicMock(
            return_value=[{"artist": "Sia", "browseId": "UCsia", "subscribers": "3.88M"}]
        )
        await pilot.click("#tab-4")
        await _settle(host, pilot)
        assert _list_texts(host, "#subs-list") == ["Sia  (3.88M subscribers)"]
        svc._ytm.get_library_subscriptions.assert_called_once_with(limit=None)


async def test_new_releases_service_failure_shows_the_failure_not_an_empty_list() -> None:
    host = _Host(active_tab=TAB_RELEASES)
    svc = _real_service()
    svc._ytm.get_explore = MagicMock(side_effect=RuntimeError("down"))
    host.ytmusic = svc
    async with host.run_test(size=(120, 30)) as pilot:
        await _settle(host, pilot)
        page = host.query_one("#page", BrowsePage)
        assert _message(host, "#releases-loading") == browse._RELEASES_LOAD_FAILED
        assert TAB_RELEASES not in page._tabs_loaded

        svc._ytm.get_explore = MagicMock(return_value={"new_releases": []})
        await pilot.click("#tab-2")
        await _settle(host, pilot)
        assert "No new releases" in _message(host, "#releases-loading")
        assert TAB_RELEASES not in page._tabs_loaded

        svc._ytm.get_explore = MagicMock(
            return_value={"new_releases": [_album("Fresh", "OLAK5uy_fresh") | {"type": "Album"}]}
        )
        await pilot.click("#tab-2")
        await _settle(host, pilot)
        assert _list_texts(host, "#releases-list") == ["Fresh by Artist (Album)"]
        assert TAB_RELEASES in page._tabs_loaded
