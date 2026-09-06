"""Tests for the Recently Played page's All / Local / YT Music tabs.

- the YT Music loader shows the account's whole feed (nothing played locally
  is hidden), keeps the unfiltered feed cached and caps on display,
- All is derived from both sources: local rows first, then account-only rows
  in server order, one row per track, each source capped separately,
- a source that fails leaves All showing the other with an explicit note,
- All is the default tab, navigation state restores the others,
- live updates (a local play, a play the account accepted) re-render All,
- a fetch that started before an accepted play cannot hide that play, while
  a play accepted before the fetch keeps its server position.

Like ``test_page_failure_states``, we exercise the page methods directly
and replace the widgets the page queries with ``MagicMock`` at the
``query_one`` boundary — no live Textual ``App``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from ytm_player.app._playback import PlaybackMixin
from ytm_player.config.keymap import Action
from ytm_player.ui.pages.recently_played import (
    _DEFAULT_TAB,
    _MAX_TRACKS,
    _TAB_ALL,
    _TAB_DESCRIPTIONS,
    _TAB_LOCAL,
    _TAB_YTM,
    RecentlyPlayedPage,
    RecentTab,
    _compose_all,
)


def _attach_fake_app(page, fake_app, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(type(page), "app", property(lambda self: fake_app))


def _make_page(active_tab: int = _TAB_LOCAL):
    """Build a page with every queried widget stubbed as a MagicMock."""
    page = RecentlyPlayedPage(active_tab=active_tab)

    widgets = {
        "#recent-loading": MagicMock(name="recent-loading"),
        "#recent-table": MagicMock(name="recent-table"),
        "#recent-footer": MagicMock(name="recent-footer"),
        "#recent-tab-all": MagicMock(name="recent-tab-all"),
        "#recent-tab-local": MagicMock(name="recent-tab-local"),
        "#recent-tab-ytm": MagicMock(name="recent-tab-ytm"),
        "#recent-tab-desc": MagicMock(name="recent-tab-desc"),
        "#track-filter": MagicMock(name="track-filter"),
    }
    widgets["#recent-table"].row_count = 0
    widgets["#recent-table"].cursor_row = None
    widgets["#recent-table"].selected_track = None
    widgets["#track-filter"].value = ""

    def fake_query_one(selector: str, _expected_type=None):
        return widgets[selector]

    object.__setattr__(page, "query_one", fake_query_one)
    return page, widgets


def _fake_app(
    *,
    local: list[dict] | None = None,
    local_error: Exception | None = None,
    feed: list[dict] | None = None,
    ytmusic: bool = True,
):
    """An app whose history and ytmusic services answer with the given data."""
    app = MagicMock()
    app._ytm_history = None
    app._ytm_history_pending = []
    app._ytm_history_pending_seq = 0
    # The real parking code, so the pending entries look like the app's.
    app._add_to_ytm_history_cache = PlaybackMixin._add_to_ytm_history_cache.__get__(app)
    if local_error is not None:
        app.history.get_recently_played = AsyncMock(side_effect=local_error)
    else:
        app.history.get_recently_played = AsyncMock(return_value=list(local or []))
    if ytmusic:
        app.ytmusic.get_history = AsyncMock(return_value=feed)
    else:
        app.ytmusic = None
    return app


def _raw_tracks(n: int, prefix: str = "vid") -> list[dict]:
    """n playlistItem-shaped rows as returned by get_history()."""
    return [
        {
            "videoId": f"{prefix}{i:04d}",
            "title": f"Song {i}",
            "artists": [{"name": "Artist", "id": "A1"}],
            "album": {"name": "Album", "id": "AL1"},
            "duration": "3:00",
            "played": "Today",
        }
        for i in range(n)
    ]


def _local_rows(n: int, prefix: str = "loc") -> list[dict]:
    """n rows as HistoryManager.get_recently_played() returns them, newest first."""
    return [
        {
            "video_id": f"{prefix}{i:04d}",
            "title": f"Local {i}",
            "artist": "Artist",
            "album": "Album",
            "duration_seconds": 180,
            "played_at": f"2026-09-06T12:{59 - i:02d}:00",
        }
        for i in range(n)
    ]


def _ids(rows: list[dict]) -> list[str]:
    return [t["video_id"] for t in rows]


def _loaded(widgets) -> list[dict]:
    return widgets["#recent-table"].load_tracks.call_args.args[0]


def _footer(widgets) -> str:
    return str(widgets["#recent-footer"].update.call_args.args[0])


def _loading_messages(widgets) -> list[str]:
    return [str(c.args[0]) for c in widgets["#recent-loading"].update.call_args_list]


# ── YT Music loader ──────────────────────────────────────────────────


async def test_ytm_tab_shows_the_whole_feed_and_caps_on_display(monkeypatch) -> None:
    """Nothing played locally is hidden any more; the cache keeps the whole
    normalized feed and the tab slices to _MAX_TRACKS when it renders."""
    page, widgets = _make_page(active_tab=_TAB_YTM)
    fake_app = _fake_app(feed=_raw_tracks(200))
    _attach_fake_app(page, fake_app, monkeypatch)

    await page._load_ytm_history()

    fake_app.history.get_played_video_ids.assert_not_called()
    assert len(_loaded(widgets)) == _MAX_TRACKS
    assert _loaded(widgets)[0]["video_id"] == "vid0000"
    assert len(fake_app._ytm_history) == 200


async def test_ytm_tab_empty_history_message(monkeypatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_YTM)
    _attach_fake_app(page, _fake_app(feed=[]), monkeypatch)

    await page._load_ytm_history()

    widgets["#recent-table"].load_tracks.assert_called_once_with([])
    assert any("No YT Music play history found" in m for m in _loading_messages(widgets))


async def test_ytm_tab_error_shows_load_failed_message(monkeypatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_YTM)
    fake_app = _fake_app(feed=None)  # None = auth expired / network / server error
    _attach_fake_app(page, fake_app, monkeypatch)

    await page._load_ytm_history()

    assert fake_app._ytm_history is None
    widgets["#recent-table"].load_tracks.assert_called_once_with([])
    assert any("Couldn't load YT Music history" in m for m in _loading_messages(widgets))


async def test_ytm_tab_no_service_shows_auth_message(monkeypatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_YTM)
    _attach_fake_app(page, _fake_app(ytmusic=False), monkeypatch)

    await page._load_ytm_history()

    assert page._ytm_auth_required is True
    widgets["#recent-table"].load_tracks.assert_called_once_with([])
    assert any("Sign in to YT Music" in m for m in _loading_messages(widgets))


async def test_ytm_fetch_keeps_server_order_for_a_play_accepted_before_it_started(
    monkeypatch,
) -> None:
    """A play the account accepted BEFORE the fetch began is already in the
    feed it returns: the server's position and row stand. Putting it on top
    would reorder a feed that is newer than the report."""
    page, widgets = _make_page(active_tab=_TAB_YTM)
    feed = [{**_raw_tracks(1, prefix="phone")[0]}, {**_raw_tracks(1, prefix="tui")[0]}]
    fake_app = _fake_app(feed=feed)
    fake_app._add_to_ytm_history_cache({"video_id": "tui0000", "title": "Accepted earlier"})
    _attach_fake_app(page, fake_app, monkeypatch)

    await page._load_ytm_history()

    assert _ids(fake_app._ytm_history) == ["phone0000", "tui0000"]
    assert fake_app._ytm_history[1]["title"] == "Song 0"
    assert fake_app._ytm_history_pending == []


async def test_ytm_fetch_puts_an_earlier_accepted_play_ahead_only_when_the_feed_lacks_it(
    monkeypatch,
) -> None:
    page, widgets = _make_page(active_tab=_TAB_YTM)
    fake_app = _fake_app(feed=_raw_tracks(2))
    fake_app._add_to_ytm_history_cache({"video_id": "missing", "title": "Not in the feed yet"})
    _attach_fake_app(page, fake_app, monkeypatch)

    await page._load_ytm_history()

    assert _ids(fake_app._ytm_history) == ["missing", "vid0000", "vid0001"]
    assert fake_app._ytm_history_pending == []


async def test_ytm_fetch_in_flight_cannot_hide_a_newer_accepted_play(monkeypatch) -> None:
    """A report the account accepts while get_history() is still running
    post-dates the feed: it goes on top even though the (stale) feed lists
    the track further down, and the older fetch cannot overwrite it."""
    page, widgets = _make_page(active_tab=_TAB_YTM)
    release = asyncio.Event()

    async def slow_history():
        await release.wait()
        return _raw_tracks(2) + [{**_raw_tracks(1)[0], "videoId": "new", "title": "Stale row"}]

    fake_app = _fake_app()
    fake_app.ytmusic.get_history = AsyncMock(side_effect=slow_history)
    _attach_fake_app(page, fake_app, monkeypatch)

    fetch = asyncio.create_task(page._load_ytm_history())
    await asyncio.sleep(0)  # the fetch is now waiting on get_history()
    fake_app._add_to_ytm_history_cache({"video_id": "new", "title": "Just played"})
    release.set()
    await fetch

    assert _ids(fake_app._ytm_history) == ["new", "vid0000", "vid0001"]
    assert fake_app._ytm_history[0]["title"] == "Just played"
    assert _ids(_loaded(widgets)) == ["new", "vid0000", "vid0001"]
    assert fake_app._ytm_history_pending == []


async def test_ytm_fetch_orders_pending_plays_newer_first_then_earlier_missing(
    monkeypatch,
) -> None:
    page, widgets = _make_page(active_tab=_TAB_YTM)
    release = asyncio.Event()

    async def slow_history():
        await release.wait()
        return _raw_tracks(1) + [{**_raw_tracks(1)[0], "videoId": "before-present"}]

    fake_app = _fake_app()
    fake_app.ytmusic.get_history = AsyncMock(side_effect=slow_history)
    fake_app._add_to_ytm_history_cache({"video_id": "before-missing"})
    fake_app._add_to_ytm_history_cache({"video_id": "before-present"})
    _attach_fake_app(page, fake_app, monkeypatch)

    fetch = asyncio.create_task(page._load_ytm_history())
    await asyncio.sleep(0)
    fake_app._add_to_ytm_history_cache({"video_id": "during"})
    release.set()
    await fetch

    assert _ids(fake_app._ytm_history) == ["during", "before-missing", "vid0000", "before-present"]


async def test_ytm_fetch_failure_leaves_pending_plays_parked(monkeypatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_YTM)
    fake_app = _fake_app(feed=None)
    fake_app._add_to_ytm_history_cache({"video_id": "parked"})
    _attach_fake_app(page, fake_app, monkeypatch)

    await page._load_ytm_history()

    assert fake_app._ytm_history is None
    assert [t["video_id"] for _seq, t in fake_app._ytm_history_pending] == ["parked"]


# ── Local loader ─────────────────────────────────────────────────────


async def test_local_tab_requests_max_tracks(monkeypatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_LOCAL)
    fake_app = _fake_app(local=_local_rows(10))
    _attach_fake_app(page, fake_app, monkeypatch)

    await page._load_history()

    fake_app.history.get_recently_played.assert_awaited_once_with(limit=_MAX_TRACKS)
    assert len(_loaded(widgets)) == 10


# ── All: composition ─────────────────────────────────────────────────


def test_compose_all_is_local_first_then_account_only_in_server_order() -> None:
    local = _local_rows(2)
    account = [
        {"video_id": "acc0", "title": "A0"},
        {"video_id": "loc0001", "title": "Server copy"},
        {"video_id": "acc1", "title": "A1"},
    ]

    rows = _compose_all(local, account)

    assert _ids(rows) == ["loc0000", "loc0001", "acc0", "acc1"]


def test_compose_all_overlap_keeps_local_row_and_fills_missing_metadata() -> None:
    local = _local_rows(1)
    account = [
        {
            "video_id": "loc0000",
            "title": "Server title",
            "artist": "Server artist",
            "artists": [{"name": "Artist", "id": "A1"}],
            "album_id": "AL1",
            "duration": 200,
            "thumbnail_url": "https://img/x.jpg",
            "is_video": False,
        }
    ]

    (row,) = _compose_all(local, account)

    # Local values win and the local timestamp survives...
    assert row["title"] == "Local 0"
    assert row["artist"] == "Artist"
    assert row["played_at"] == local[0]["played_at"]
    assert row["duration_seconds"] == 180
    # ...while fields the local row lacks come from the account row.
    assert row["artists"] == [{"name": "Artist", "id": "A1"}]
    assert row["album_id"] == "AL1"
    assert row["thumbnail_url"] == "https://img/x.jpg"
    assert row["duration"] == 200
    assert row["is_video"] is False


def test_compose_all_overlap_fills_absent_none_and_empty_fields() -> None:
    """Whatever form 'lacking' takes in the local row — absent, None or "" —
    the account value fills it, without touching what the local row has."""
    local = [
        {
            "video_id": "loc0000",
            "title": "Local title",
            "artist": "",
            "album": None,
            "duration_seconds": 180,
            "played_at": "2026-09-06T12:59:00",
        }
    ]
    account = [
        {
            "video_id": "loc0000",
            "title": "Server title",
            "artist": "Server artist",
            "album": "Server album",
            "album_id": "AL1",
            "thumbnail_url": "",
            "played_at": "Today",
        }
    ]

    (row,) = _compose_all(local, account)

    assert row["artist"] == "Server artist"
    assert row["album"] == "Server album"
    assert row["album_id"] == "AL1"
    assert row["title"] == "Local title"
    assert row["played_at"] == "2026-09-06T12:59:00"
    assert row["duration_seconds"] == 180
    # An empty account value is no enrichment: the key stays absent.
    assert "thumbnail_url" not in row
    # The local row itself is untouched.
    assert local[0]["artist"] == "" and local[0]["album"] is None


def test_compose_all_dedups_before_the_account_only_cap() -> None:
    """100 local rows all present in a 200-row feed: All is 100 local plus the
    100 account-only rows, not 100 local plus nothing."""
    local = _local_rows(_MAX_TRACKS)
    account = [{"video_id": t["video_id"]} for t in local] + [
        {"video_id": f"acc{i:04d}"} for i in range(_MAX_TRACKS)
    ]

    rows = _compose_all(local, account)

    assert len(rows) == 2 * _MAX_TRACKS
    assert _ids(rows[:_MAX_TRACKS]) == _ids(local)
    assert _ids(rows[_MAX_TRACKS:]) == [f"acc{i:04d}" for i in range(_MAX_TRACKS)]


def test_compose_all_caps_each_source_separately() -> None:
    local = _local_rows(_MAX_TRACKS + 20)
    account = [{"video_id": f"acc{i:04d}"} for i in range(_MAX_TRACKS + 30)]

    rows = _compose_all(local, account)

    assert _ids(rows[:_MAX_TRACKS]) == _ids(local[:_MAX_TRACKS])
    assert len(rows) == 2 * _MAX_TRACKS
    assert rows[-1]["video_id"] == f"acc{_MAX_TRACKS - 1:04d}"


def test_compose_all_with_one_source_empty() -> None:
    assert _ids(_compose_all(_local_rows(2), [])) == ["loc0000", "loc0001"]
    assert _ids(_compose_all([], [{"video_id": "a"}, {"video_id": "b"}])) == ["a", "b"]
    assert _compose_all([], []) == []


# ── All: loading and partial results ─────────────────────────────────


async def test_all_tab_loads_both_sources_and_renders_grouped_rows(monkeypatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_ALL)
    feed = _raw_tracks(2, prefix="acc") + [{**_raw_tracks(1)[0], "videoId": "loc0001"}]
    fake_app = _fake_app(local=_local_rows(3), feed=feed)
    _attach_fake_app(page, fake_app, monkeypatch)

    await page._load_all()

    assert _ids(_loaded(widgets)) == ["loc0000", "loc0001", "loc0002", "acc0000", "acc0001"]
    assert "local history first" in _footer(widgets)
    assert "couldn't be loaded" not in _footer(widgets)
    assert page._get_cache(_TAB_LOCAL) is not None
    assert fake_app._ytm_history is not None and len(fake_app._ytm_history) == 3


async def test_all_tab_shows_local_only_with_a_note_when_ytm_fails(monkeypatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_ALL)
    fake_app = _fake_app(local=_local_rows(2), feed=None)
    _attach_fake_app(page, fake_app, monkeypatch)

    await page._load_all()

    assert _ids(_loaded(widgets)) == ["loc0000", "loc0001"]
    assert _footer(widgets).startswith(
        "YT Music history couldn't be loaded; showing local history only"
    )
    assert "2 tracks" in _footer(widgets)
    assert fake_app._ytm_history is None


async def test_all_tab_shows_account_only_with_a_note_when_local_fails(monkeypatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_ALL)
    fake_app = _fake_app(local_error=OSError("disk full"), feed=_raw_tracks(2))
    _attach_fake_app(page, fake_app, monkeypatch)

    await page._load_all()

    assert _ids(_loaded(widgets)) == ["vid0000", "vid0001"]
    assert _footer(widgets).startswith(
        "Local history couldn't be loaded; showing YT Music history only"
    )
    assert page._get_cache(_TAB_LOCAL) is None


async def test_all_tab_without_a_service_shows_local_with_a_sign_in_note(monkeypatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_ALL)
    _attach_fake_app(page, _fake_app(local=_local_rows(2), ytmusic=False), monkeypatch)

    await page._load_all()

    assert _ids(_loaded(widgets)) == ["loc0000", "loc0001"]
    assert _footer(widgets).startswith("Sign in to YT Music to include your account history")


async def test_all_tab_says_which_sources_failed_when_nothing_loads(monkeypatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_ALL)
    _attach_fake_app(page, _fake_app(local_error=OSError("locked"), feed=None), monkeypatch)

    await page._load_all()

    widgets["#recent-table"].load_tracks.assert_called_once_with([])
    message = _loading_messages(widgets)[-1]
    assert "Couldn't load local history" in message
    assert "couldn't load YT Music history" in message
    assert "Start listening" not in message


async def test_all_tab_genuinely_empty_shows_empty_state(monkeypatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_ALL)
    _attach_fake_app(page, _fake_app(local=[], feed=[]), monkeypatch)

    await page._load_all()

    assert "No play history yet" in _loading_messages(widgets)[-1]


async def test_all_loader_does_not_render_after_the_user_switched_tab(monkeypatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_ALL)
    release = asyncio.Event()

    async def slow_history():
        await release.wait()
        return _raw_tracks(2)

    fake_app = _fake_app(local=_local_rows(1))
    fake_app.ytmusic.get_history = AsyncMock(side_effect=slow_history)
    _attach_fake_app(page, fake_app, monkeypatch)

    load = asyncio.create_task(page._load_all())
    await asyncio.sleep(0)
    page._active_tab = _TAB_LOCAL  # the user moved on while the fetch was in flight
    release.set()
    await load

    widgets["#recent-table"].load_tracks.assert_not_called()
    assert fake_app._ytm_history is not None  # the fetch itself still fills the cache


# ── Default tab and navigation state ─────────────────────────────────


def test_all_is_the_default_tab() -> None:
    assert _DEFAULT_TAB == _TAB_ALL
    assert RecentlyPlayedPage()._active_tab == _TAB_ALL
    assert RecentlyPlayedPage(active_tab=99)._active_tab == _TAB_ALL


def test_nav_state_stores_non_default_tab_and_cursor() -> None:
    page, widgets = _make_page(active_tab=_TAB_YTM)
    widgets["#recent-table"].cursor_row = 4
    assert page.get_nav_state() == {"active_tab": _TAB_YTM, "cursor_row": 4}

    page, widgets = _make_page(active_tab=_TAB_ALL)
    widgets["#recent-table"].cursor_row = 0
    assert page.get_nav_state() == {}


def test_nav_state_restores_tab_and_cursor() -> None:
    page = RecentlyPlayedPage(active_tab=_TAB_LOCAL, cursor_row=3)
    assert page._active_tab == _TAB_LOCAL
    page, widgets = _make_page(active_tab=_TAB_LOCAL)
    page._restore_cursor_row = 3
    widgets["#recent-table"].row_count = 5

    page._display_tracks(_local_rows(5))

    widgets["#recent-table"].move_cursor.assert_called_once_with(row=3)


# ── Tab switching ────────────────────────────────────────────────────


def test_switch_tab_updates_labels_and_description(monkeypatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_ALL)
    fake_app = _fake_app()
    _attach_fake_app(page, fake_app, monkeypatch)
    monkeypatch.setattr(page, "_load_active_tab", MagicMock())

    page._switch_tab(_TAB_LOCAL)

    widgets["#recent-tab-all"].set_class.assert_called_once_with(False, "active")
    widgets["#recent-tab-local"].set_class.assert_called_once_with(True, "active")
    widgets["#recent-tab-ytm"].set_class.assert_called_once_with(False, "active")
    widgets["#recent-tab-desc"].update.assert_called_once_with(_TAB_DESCRIPTIONS[_TAB_LOCAL])
    page._load_active_tab.assert_called_once()


def test_switch_to_all_with_both_sources_cached_renders_without_fetching(monkeypatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_LOCAL)
    fake_app = _fake_app()
    fake_app._ytm_history = [{"video_id": "acc0"}]
    _attach_fake_app(page, fake_app, monkeypatch)
    page._set_cache(_TAB_LOCAL, _local_rows(1))
    monkeypatch.setattr(page, "_load_active_tab", MagicMock())

    page._switch_tab(_TAB_ALL)

    assert _ids(_loaded(widgets)) == ["loc0000", "acc0"]
    page._load_active_tab.assert_not_called()


def test_switch_to_all_with_one_source_missing_loads(monkeypatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_LOCAL)
    _attach_fake_app(page, _fake_app(), monkeypatch)  # account cache None
    page._set_cache(_TAB_LOCAL, _local_rows(1))
    monkeypatch.setattr(page, "_load_active_tab", MagicMock())

    page._switch_tab(_TAB_ALL)

    widgets["#recent-table"].load_tracks.assert_not_called()
    page._load_active_tab.assert_called_once()


def test_reselecting_all_drops_both_caches_and_reloads(monkeypatch) -> None:
    page, _ = _make_page(active_tab=_TAB_ALL)
    fake_app = _fake_app()
    fake_app._ytm_history = _raw_tracks(3)
    _attach_fake_app(page, fake_app, monkeypatch)
    page._set_cache(_TAB_LOCAL, _local_rows(2))
    monkeypatch.setattr(page, "_load_active_tab", MagicMock())

    page._switch_tab(_TAB_ALL)  # same tab → refresh

    assert fake_app._ytm_history is None
    assert page._get_cache(_TAB_LOCAL) is None
    page._load_active_tab.assert_called_once()
    fake_app.notify.assert_called_once()


def test_reselecting_active_tab_reloads(monkeypatch) -> None:
    """Clicking / Enter on the already-active tab drops its cache and
    refetches, so the YT Music tab can be refreshed without leaving."""
    page, _ = _make_page(active_tab=_TAB_YTM)
    fake_app = _fake_app()
    fake_app._ytm_history = _raw_tracks(3)
    _attach_fake_app(page, fake_app, monkeypatch)
    monkeypatch.setattr(page, "_load_active_tab", MagicMock())

    page._switch_tab(_TAB_YTM)

    assert fake_app._ytm_history is None
    page._load_active_tab.assert_called_once()
    fake_app.notify.assert_called_once()


async def test_enter_on_focused_tab_switches(monkeypatch) -> None:
    """With a tab label focused, SELECT (Enter) switches to that tab."""
    page, widgets = _make_page(active_tab=_TAB_LOCAL)
    focused_tab = RecentTab("YT Music", _TAB_YTM, id="recent-tab-ytm")
    fake_app = _fake_app()
    fake_app.focused = focused_tab
    fake_app._ytm_history = _raw_tracks(3)  # cached → no refetch
    _attach_fake_app(page, fake_app, monkeypatch)

    await page.handle_action(Action.SELECT)

    assert page._active_tab == _TAB_YTM
    widgets["#recent-table"].load_tracks.assert_called_once()


async def test_movement_on_focused_tab_drops_into_table(monkeypatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_LOCAL)
    fake_app = _fake_app()
    fake_app.focused = RecentTab("Local", _TAB_LOCAL, id="recent-tab-local")
    _attach_fake_app(page, fake_app, monkeypatch)

    await page.handle_action(Action.MOVE_DOWN)

    widgets["#recent-table"].focus.assert_called_once()
    widgets["#recent-table"].handle_action.assert_not_called()


# ── Live updates ─────────────────────────────────────────────────────


def _page_with_cache(monkeypatch, tracks, active_tab=_TAB_LOCAL):
    page, widgets = _make_page(active_tab=active_tab)
    fake_app = _fake_app()
    _attach_fake_app(page, fake_app, monkeypatch)
    page._set_cache(_TAB_LOCAL, tracks)
    return page, widgets, fake_app


def test_optimistic_add_prepends_dedups_and_rerenders(monkeypatch) -> None:
    page, widgets, _ = _page_with_cache(
        monkeypatch, [{"video_id": "a"}, {"video_id": "vid1"}, {"video_id": "b"}]
    )

    page.optimistic_add(_TAB_LOCAL, {"video_id": "vid1", "title": "X"})

    assert _ids(page._tab_cache[_TAB_LOCAL]) == ["vid1", "a", "b"]
    widgets["#recent-table"].load_tracks.assert_called_once()


def test_optimistic_add_noop_without_cache(monkeypatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_LOCAL)
    _attach_fake_app(page, _fake_app(), monkeypatch)

    page.optimistic_add(_TAB_LOCAL, {"video_id": "vid1"})

    assert _TAB_LOCAL not in page._tab_cache
    widgets["#recent-table"].load_tracks.assert_not_called()


def test_optimistic_add_caps_local_but_not_the_account_cache(monkeypatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_YTM)
    fake_app = _fake_app()
    fake_app._ytm_history = [{"video_id": f"acc{i:04d}"} for i in range(_MAX_TRACKS)]
    _attach_fake_app(page, fake_app, monkeypatch)
    page._set_cache(_TAB_LOCAL, [{"video_id": f"loc{i:04d}"} for i in range(_MAX_TRACKS)])

    page.optimistic_add(_TAB_LOCAL, {"video_id": "newloc"})
    page.optimistic_add(_TAB_YTM, {"video_id": "newacc"})

    assert len(page._tab_cache[_TAB_LOCAL]) == _MAX_TRACKS
    assert len(fake_app._ytm_history) == _MAX_TRACKS + 1
    assert len(_loaded(widgets)) == _MAX_TRACKS  # the view still caps
    assert _loaded(widgets)[0]["video_id"] == "newacc"


def test_local_play_rerenders_all_while_it_is_showing(monkeypatch) -> None:
    page, widgets, fake_app = _page_with_cache(monkeypatch, [{"video_id": "a"}], _TAB_ALL)
    fake_app._ytm_history = [{"video_id": "b"}]

    page.optimistic_add(_TAB_LOCAL, {"video_id": "c"})

    assert _ids(_loaded(widgets)) == ["c", "a", "b"]
    assert widgets["#recent-table"].focus.call_count == 0  # background refresh


def test_accepted_account_play_rerenders_all_while_it_is_showing(monkeypatch) -> None:
    page, widgets, fake_app = _page_with_cache(monkeypatch, [{"video_id": "a"}], _TAB_ALL)
    fake_app._ytm_history = [{"video_id": "new"}, {"video_id": "b"}]  # the app prepended "new"

    page._refresh_tab_from_cache(_TAB_YTM)

    assert _ids(_loaded(widgets)) == ["a", "new", "b"]


def test_background_update_of_another_tab_does_not_render(monkeypatch) -> None:
    page, widgets, fake_app = _page_with_cache(monkeypatch, [{"video_id": "a"}], _TAB_LOCAL)
    fake_app._ytm_history = [{"video_id": "b"}]

    page._refresh_tab_from_cache(_TAB_YTM)

    widgets["#recent-table"].load_tracks.assert_not_called()


def test_background_refresh_does_not_steal_focus(monkeypatch) -> None:
    page, widgets, _ = _page_with_cache(monkeypatch, [{"video_id": "a"}])

    page._refresh_tab_from_cache(_TAB_LOCAL)

    widgets["#recent-table"].load_tracks.assert_called_once()
    widgets["#recent-table"].focus.assert_not_called()


def test_initial_display_still_focuses_table(monkeypatch) -> None:
    page, widgets, _ = _page_with_cache(monkeypatch, [{"video_id": "a"}])

    page._display_tracks([{"video_id": "a"}])

    widgets["#recent-table"].focus.assert_called_once()


def test_background_refresh_reapplies_active_filter(monkeypatch) -> None:
    page, widgets, _ = _page_with_cache(monkeypatch, [{"video_id": "a"}])
    widgets["#track-filter"].value = "abc"

    page._refresh_tab_from_cache(_TAB_LOCAL)

    widgets["#recent-table"].apply_filter.assert_called_once_with("abc")


def test_background_refresh_keeps_cursor_on_same_track(monkeypatch) -> None:
    """A dedup-move refresh is net-zero: the cursored track keeps its row.
    Identity comes from ``selected_track`` (the highlighted VISIBLE row,
    mapped through any active sort) — a backing-list index would restore
    to the wrong track in a sorted view.
    """
    new = [{"video_id": "b", "title": "B"}, {"video_id": "a", "title": "A"}]
    page, widgets, _ = _page_with_cache(monkeypatch, new)
    widgets["#recent-table"].selected_track = {"video_id": "a", "title": "A"}
    widgets["#recent-table"].row_count = 2

    page._refresh_tab_from_cache(_TAB_LOCAL)

    widgets["#recent-table"].move_cursor.assert_called_once_with(row=1)


# ── Failure flags stay per source ────────────────────────────────────


async def test_local_failure_does_not_leak_onto_ytm_tab(monkeypatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_LOCAL)
    _attach_fake_app(page, _fake_app(local_error=OSError("locked"), feed=[]), monkeypatch)

    await page._load_history()
    assert page._load_failed is True

    page._active_tab = _TAB_YTM
    await page._load_ytm_history()

    assert "No YT Music play history found." in _loading_messages(widgets)[-1]
    assert page._load_failed is True  # survives for the local tab's own retry logic


async def test_ytm_visit_does_not_clear_local_failure_flag(monkeypatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_YTM)
    _attach_fake_app(page, _fake_app(feed=_raw_tracks(3)), monkeypatch)
    page._load_failed = True

    await page._load_ytm_history()

    assert page._load_failed is True
