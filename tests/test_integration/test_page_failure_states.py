"""Integration test: Recently Played and Context pages show error
fallbacks (not stuck 'Loading…') when their data sources fail.

The v3 UX reviewer flagged that these pages previously hung silently
on load failure — `recently_played.py` lumped genuine empty-state
("No play history yet. Start listening!") with disk-failure state
under the same message, and `context.py` showed a terse "Failed to
load <type>." that gave the user no hint where to look. This module
asserts the new contract: on a caught (expected) exception, the
loading indicator is replaced with a clear error message that points
the user at the log file. Programming errors (TypeError,
AttributeError, etc.) propagate so bugs surface in development.

We exercise the page methods directly rather than booting a Textual
``App`` — the App harness is too heavy for this layer, and the
widgets the pages query (``Label``, ``DataTable``) can be replaced
with ``MagicMock`` instances at the ``query_one`` boundary so the
contract checks stay focused.
"""

from __future__ import annotations

import asyncio
import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest
import requests.exceptions
from textual.widget import Widget
from textual.worker import Worker, WorkerState

from ytm_player.ui.pages.context import ContextPage
from ytm_player.ui.pages.liked_songs import LikedSongsPage
from ytm_player.ui.pages.recently_played import _TAB_LOCAL, RecentlyPlayedPage


def _attach_fake_app(page: Widget, fake_app: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    """Override ``Widget.app`` (a property that walks up to the active
    Textual ``App``) with a class-level descriptor that returns our
    test double — there's no live App in the test, so the real
    property raises ``NoActiveAppError``.
    """
    monkeypatch.setattr(type(page), "app", property(lambda self: fake_app))


# ── recently_played.py ──────────────────────────────────────────────


def _make_recently_played_page() -> tuple[RecentlyPlayedPage, dict[str, MagicMock]]:
    """Build a ``RecentlyPlayedPage`` with its widget queries stubbed.

    Returns the page plus a dict of the mocked widgets keyed by their
    ``query_one`` selector so tests can introspect ``.update`` calls
    and ``.display`` flips.
    """
    page = RecentlyPlayedPage(active_tab=_TAB_LOCAL)

    loading = MagicMock(name="recent-loading")
    table = MagicMock(name="recent-table")
    table.row_count = 0
    table.cursor_row = None
    footer = MagicMock(name="recent-footer")

    widgets: dict[str, MagicMock] = {
        "#recent-loading": loading,
        "#recent-table": table,
        "#recent-footer": footer,
    }

    def fake_query_one(selector: str, _expected_type: type | None = None) -> MagicMock:
        return widgets[selector]

    # Bypass Textual's DOMNode.query_one — we don't have a mounted DOM here.
    object.__setattr__(page, "query_one", fake_query_one)
    return page, widgets


async def test_recently_played_shows_error_fallback_when_history_raises_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``HistoryManager.get_recently_played`` raises an OSError
    (DB file unreadable, disk full, etc.), the loading label must
    update to the new error message — NOT to "No play history yet.
    Start listening!" which is the genuine empty-state message and
    would mislead the user into thinking nothing went wrong.
    """
    page, widgets = _make_recently_played_page()

    fake_history = MagicMock()
    fake_history.get_recently_played = AsyncMock(side_effect=OSError("disk full"))
    fake_app = MagicMock()
    fake_app.history = fake_history
    _attach_fake_app(page, fake_app, monkeypatch)

    await page._load_history()

    # Table must be cleared so hidden stale rows can't back actions.
    widgets["#recent-table"].load_tracks.assert_called_once_with([])

    # Loading label received the failure message and stays visible
    # so the user can read it.
    update_calls = [c.args[0] for c in widgets["#recent-loading"].update.call_args_list]
    assert any("Couldn't load history" in msg and "ytm.log" in msg for msg in update_calls), (
        f"loading label must show the failure message, got: {update_calls!r}"
    )

    # Empty-state message must NOT be shown for the failure path —
    # that would lie to the user about why the table is empty.
    assert not any("Start listening" in msg for msg in update_calls), (
        f"loading label leaked the empty-state message on failure: {update_calls!r}"
    )

    # Failure must NOT be cached as an empty history — a retry (tab
    # re-entry) must refetch.
    assert page._get_cache(_TAB_LOCAL) is None


async def test_recently_played_shows_error_fallback_when_history_raises_sqlite_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``aiosqlite`` errors inherit from ``sqlite3.Error``; the contract
    must catch those too (DB locked, schema mismatch, corrupt file)."""
    page, widgets = _make_recently_played_page()

    fake_history = MagicMock()
    fake_history.get_recently_played = AsyncMock(
        side_effect=sqlite3.OperationalError("database is locked")
    )
    fake_app = MagicMock()
    fake_app.history = fake_history
    _attach_fake_app(page, fake_app, monkeypatch)

    await page._load_history()

    widgets["#recent-table"].load_tracks.assert_called_once_with([])
    update_calls = [c.args[0] for c in widgets["#recent-loading"].update.call_args_list]
    assert any("Couldn't load history" in msg for msg in update_calls)
    # Failure must NOT be cached as an empty history — a retry (tab
    # re-entry) must refetch.
    assert page._get_cache(_TAB_LOCAL) is None


async def test_recently_played_keeps_empty_state_message_for_genuine_empty_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity check: the genuine empty-state ("No play history yet")
    message MUST still render when the history call returns an empty
    list. The fix only changes the failure path.
    """
    page, widgets = _make_recently_played_page()

    fake_history = MagicMock()
    fake_history.get_recently_played = AsyncMock(return_value=[])
    fake_app = MagicMock()
    fake_app.history = fake_history
    _attach_fake_app(page, fake_app, monkeypatch)

    await page._load_history()

    update_calls = [c.args[0] for c in widgets["#recent-loading"].update.call_args_list]
    assert any("No play history yet" in msg for msg in update_calls), (
        f"empty-state message must still render for legitimate empty history: {update_calls!r}"
    )
    assert not any("Couldn't load history" in msg for msg in update_calls)


async def test_recently_played_propagates_programming_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``TypeError`` from the history method indicates a bug in the
    code, not a runtime failure — it must propagate so it surfaces in
    development rather than getting silently swallowed under the
    graceful-degrade path. (CLAUDE.md error-handling architecture.)"""
    page, _ = _make_recently_played_page()

    fake_history = MagicMock()
    fake_history.get_recently_played = AsyncMock(side_effect=TypeError("bug: wrong argument type"))
    fake_app = MagicMock()
    fake_app.history = fake_history
    _attach_fake_app(page, fake_app, monkeypatch)

    with pytest.raises(TypeError, match="wrong argument type"):
        await page._load_history()


# ── context.py ──────────────────────────────────────────────────────


def _make_context_page(context_type: str = "album") -> ContextPage:
    """Build a ``ContextPage`` without mounting it. The ``loading`` /
    ``error_message`` reactives still work as plain attributes for the
    contract checks.
    """
    return ContextPage(context_type=context_type, context_id="MPREb_test")


async def test_context_page_records_friendly_error_on_request_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the underlying ytmusic call raises a ``RequestException``
    (network down, timeout, HTTP 5xx), ``_fetch_data`` must catch it,
    log via ``logger.exception``, and return an empty payload that
    flips the page's ``_load_failed`` flag. The worker-state handler
    then renders a user-facing error message that points at the log.
    """
    page = _make_context_page("album")

    fake_ytmusic = MagicMock()
    fake_ytmusic.get_album = AsyncMock(side_effect=requests.exceptions.ConnectionError("offline"))
    fake_app = MagicMock()
    fake_app.ytmusic = fake_ytmusic
    _attach_fake_app(page, fake_app, monkeypatch)

    result = await page._fetch_data()

    # Falsy result trips the existing ``if not self._data`` branch in
    # ``on_worker_state_changed`` so the page renders an empty/error
    # state instead of trying to build content from nothing.
    assert not result

    # The page exposes a flag for the worker-state handler to read
    # so it can surface the richer error message.
    assert page._load_failed is True


async def test_context_page_records_friendly_error_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``asyncio.TimeoutError`` is the second arm of
    ``_EXPECTED_API_EXCEPTIONS`` — same fallback contract."""
    page = _make_context_page("artist")

    fake_ytmusic = MagicMock()
    fake_ytmusic.get_artist = AsyncMock(side_effect=asyncio.TimeoutError())
    fake_app = MagicMock()
    fake_app.ytmusic = fake_ytmusic
    _attach_fake_app(page, fake_app, monkeypatch)

    result = await page._fetch_data()

    assert not result
    assert page._load_failed is True


async def test_context_page_propagates_programming_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ``AttributeError`` (e.g. wrong API shape, missing key) is a
    bug — it must propagate, not get swallowed under graceful-degrade."""
    page = _make_context_page("playlist")

    fake_ytmusic = MagicMock()
    fake_ytmusic.get_playlist = AsyncMock(
        side_effect=AttributeError("'NoneType' has no attribute 'get'")
    )
    fake_app = MagicMock()
    fake_app.ytmusic = fake_ytmusic
    _attach_fake_app(page, fake_app, monkeypatch)

    with pytest.raises(AttributeError):
        await page._fetch_data()


async def test_context_page_worker_error_branch_surfaces_log_pointer() -> None:
    """When the worker raises (programming error path or any other
    unexpected error caught at the worker boundary), the
    ``WorkerState.ERROR`` branch must update ``error_message`` to the
    new richer text that points the user at the log file — the bare
    "Failed to load <type>." message left users guessing.
    """
    page = _make_context_page("album")

    # Suppress Textual's reactive-watcher side effects (no DOM mounted).
    object.__setattr__(page, "query_one", lambda *_a, **_kw: MagicMock())

    fake_worker = MagicMock(spec=Worker)
    fake_worker.name = "fetch_context"
    fake_worker.result = None
    event = MagicMock()
    event.worker = fake_worker
    event.state = WorkerState.ERROR

    page.on_worker_state_changed(event)

    assert page.loading is False
    assert "ytm.log" in page.error_message
    assert "album" in page.error_message.lower()


async def test_context_page_fetch_data_success_path_unaffected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity check: success path still returns the data dict so
    ``_build_content`` can render normally."""
    page = _make_context_page("album")

    payload = {"title": "Some Album", "tracks": []}
    fake_ytmusic = MagicMock()
    fake_ytmusic.get_album = AsyncMock(return_value=payload)
    fake_app = MagicMock()
    fake_app.ytmusic = fake_ytmusic
    _attach_fake_app(page, fake_app, monkeypatch)

    result = await page._fetch_data()
    assert result == payload
    assert page._load_failed is False


# ── liked_songs.py ──────────────────────────────────────────────────


def _make_liked_songs_page() -> tuple["LikedSongsPage", dict[str, MagicMock]]:
    page = LikedSongsPage()

    loading = MagicMock(name="liked-loading")
    table = MagicMock(name="liked-table")
    table.row_count = 0

    widgets: dict[str, MagicMock] = {
        "#liked-loading": loading,
        "#liked-table": table,
    }

    def fake_query_one(selector: str, _expected_type: type | None = None) -> MagicMock:
        return widgets[selector]

    object.__setattr__(page, "query_one", fake_query_one)
    return page, widgets


async def test_liked_songs_failure_shows_error_not_empty_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``get_liked_songs`` returns ``None`` on failure (it never raises —
    graceful-degrade contract). The page must render the check-the-log
    failure message, NOT the misleading "No liked songs found."."""
    page, widgets = _make_liked_songs_page()

    fake_ytmusic = MagicMock()
    fake_ytmusic.get_liked_songs = AsyncMock(return_value=None)
    fake_app = MagicMock()
    fake_app.ytmusic = fake_ytmusic
    _attach_fake_app(page, fake_app, monkeypatch)

    await page._load_liked_songs()

    assert page._load_failed is True
    update_calls = [c.args[0] for c in widgets["#liked-loading"].update.call_args_list]
    assert any("Couldn't load liked songs" in msg for msg in update_calls), (
        f"loading label must show the failure message, got: {update_calls!r}"
    )
    assert not any("No liked songs found" in msg for msg in update_calls), (
        f"loading label leaked the empty-state message on failure: {update_calls!r}"
    )


async def test_liked_songs_empty_playlist_shows_empty_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuinely empty Liked Music playlist ([]) keeps the friendly
    empty-state copy."""
    page, widgets = _make_liked_songs_page()

    fake_ytmusic = MagicMock()
    fake_ytmusic.get_liked_songs = AsyncMock(return_value=[])
    fake_app = MagicMock()
    fake_app.ytmusic = fake_ytmusic
    _attach_fake_app(page, fake_app, monkeypatch)

    await page._load_liked_songs()

    assert page._load_failed is False
    update_calls = [c.args[0] for c in widgets["#liked-loading"].update.call_args_list]
    assert any("No liked songs found" in msg for msg in update_calls), (
        f"loading label must show the empty-state message, got: {update_calls!r}"
    )


# ── browse.py (For You / Charts) ────────────────────────────────────


async def test_foryou_service_failure_shows_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_home returns None on any fetch failure — the section must show
    the retryable error copy instead of rendering an empty shelf list."""
    from ytm_player.ui.pages.browse import ForYouSection

    section = ForYouSection()
    fake_app = MagicMock()
    fake_app.ytmusic.get_home = AsyncMock(return_value=None)
    _attach_fake_app(section, fake_app, monkeypatch)
    shown = MagicMock()
    monkeypatch.setattr(section, "_show_error", shown)

    await section.load_data()

    shown.assert_called_once_with("Failed to load recommendations.")
    assert section.is_loading is False


async def test_charts_service_failure_shows_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_charts returns None on any fetch failure — same contract."""
    from ytm_player.ui.pages.browse import ChartsSection

    section = ChartsSection()
    fake_app = MagicMock()
    fake_app.ytmusic.get_charts = AsyncMock(return_value=None)
    _attach_fake_app(section, fake_app, monkeypatch)
    shown = MagicMock()
    monkeypatch.setattr(section, "_show_error", shown)

    await section.load_data(country="GB")

    shown.assert_called_once_with("Failed to load charts for GB — try again later.")
    assert section.is_loading is False
