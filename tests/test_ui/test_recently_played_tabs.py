"""Tests for the Recently Played page's Local / YT Music tabs.

Covers the behaviour added when the page gained a second tab backed by
the YT Music account history (``get_history()``):

- the YT Music loader normalises + caps the server rows at ``_MAX_TRACKS``,
- empty / missing-service states render the right message,
- the local loader honours the same cap,
- keyboard tab switching (Enter on a focused tab label) works.

Like ``test_page_failure_states``, we exercise the page methods directly
and replace the widgets the page queries with ``MagicMock`` at the
``query_one`` boundary — no live Textual ``App``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ytm_player.config.keymap import Action
from ytm_player.ui.pages.recently_played import (
    _MAX_TRACKS,
    _TAB_LOCAL,
    _TAB_YTM,
    RecentlyPlayedPage,
    RecentTab,
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
        "#recent-tab-local": MagicMock(name="recent-tab-local"),
        "#recent-tab-ytm": MagicMock(name="recent-tab-ytm"),
        "#track-filter": MagicMock(name="track-filter"),
    }
    widgets["#recent-table"].row_count = 0
    widgets["#recent-table"].cursor_row = None

    def fake_query_one(selector: str, _expected_type=None):
        return widgets[selector]

    object.__setattr__(page, "query_one", fake_query_one)
    return page, widgets


def _raw_tracks(n: int) -> list[dict]:
    """n playlistItem-shaped rows as returned by get_history()."""
    return [
        {
            "videoId": f"vid{i:04d}",
            "title": f"Song {i}",
            "artists": [{"name": "Artist", "id": "A1"}],
            "album": {"name": "Album", "id": "AL1"},
            "duration": "3:00",
            "played": "Today",
        }
        for i in range(n)
    ]


# ── YT Music loader ──────────────────────────────────────────────────


async def test_ytm_tab_caps_rows_at_max_tracks(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_history() returns ~200 unpaginated rows; the tab must slice to
    _MAX_TRACKS so the TUI stays responsive."""
    page, widgets = _make_page(active_tab=_TAB_YTM)

    fake_ytmusic = MagicMock()
    fake_ytmusic.get_history = AsyncMock(return_value=_raw_tracks(200))
    fake_app = MagicMock()
    fake_app.ytmusic = fake_ytmusic
    _attach_fake_app(page, fake_app, monkeypatch)

    await page._load_ytm_history()

    widgets["#recent-table"].load_tracks.assert_called_once()
    loaded = widgets["#recent-table"].load_tracks.call_args.args[0]
    assert len(loaded) == _MAX_TRACKS
    # Cache holds the same capped list.
    assert len(page._tab_cache[_TAB_YTM]) == _MAX_TRACKS


async def test_ytm_tab_empty_history_message(monkeypatch: pytest.MonkeyPatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_YTM)

    fake_ytmusic = MagicMock()
    fake_ytmusic.get_history = AsyncMock(return_value=[])
    fake_app = MagicMock()
    fake_app.ytmusic = fake_ytmusic
    _attach_fake_app(page, fake_app, monkeypatch)

    await page._load_ytm_history()

    widgets["#recent-table"].load_tracks.assert_not_called()
    msgs = [c.args[0] for c in widgets["#recent-loading"].update.call_args_list]
    assert any("No YT Music play history found" in m for m in msgs), msgs


async def test_ytm_tab_no_service_shows_auth_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """When there is no ytmusic service, the tab must prompt to sign in
    rather than claim the history is empty."""
    page, widgets = _make_page(active_tab=_TAB_YTM)

    fake_app = MagicMock()
    fake_app.ytmusic = None
    _attach_fake_app(page, fake_app, monkeypatch)

    await page._load_ytm_history()

    assert page._ytm_auth_required is True
    msgs = [c.args[0] for c in widgets["#recent-loading"].update.call_args_list]
    assert any("Sign in to YT Music" in m for m in msgs), msgs


# ── Local loader still honours the cap ───────────────────────────────


async def test_local_tab_requests_max_tracks(monkeypatch: pytest.MonkeyPatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_LOCAL)

    fake_history = MagicMock()
    fake_history.get_recently_played = AsyncMock(return_value=_raw_tracks(10))
    fake_app = MagicMock()
    fake_app.history = fake_history
    _attach_fake_app(page, fake_app, monkeypatch)

    await page._load_history()

    fake_history.get_recently_played.assert_awaited_once_with(limit=_MAX_TRACKS)


# ── Keyboard tab switching ───────────────────────────────────────────


async def test_enter_on_focused_tab_switches(monkeypatch: pytest.MonkeyPatch) -> None:
    """With a tab label focused, SELECT (Enter) switches to that tab."""
    page, widgets = _make_page(active_tab=_TAB_LOCAL)

    # Pre-seed the YT Music cache so _switch_tab takes the no-refetch path.
    page._tab_cache[_TAB_YTM] = _raw_tracks(3)

    focused_tab = RecentTab("YT Music", _TAB_YTM, id="recent-tab-ytm")
    fake_app = MagicMock()
    fake_app.focused = focused_tab
    _attach_fake_app(page, fake_app, monkeypatch)

    await page.handle_action(Action.SELECT)

    assert page._active_tab == _TAB_YTM
    widgets["#recent-table"].load_tracks.assert_called_once()


async def test_movement_on_focused_tab_drops_into_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """j/k while a tab is focused should move focus into the table, not
    switch tabs."""
    page, widgets = _make_page(active_tab=_TAB_LOCAL)

    focused_tab = RecentTab("YT Music", _TAB_YTM, id="recent-tab-ytm")
    fake_app = MagicMock()
    fake_app.focused = focused_tab
    _attach_fake_app(page, fake_app, monkeypatch)

    await page.handle_action(Action.MOVE_DOWN)

    assert page._active_tab == _TAB_LOCAL
    widgets["#recent-table"].focus.assert_called_once()
