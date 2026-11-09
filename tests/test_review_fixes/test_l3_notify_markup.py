"""Toasts show outside text as typed; they never parse it as markup (U1/U2/S1).

Titles, artist / album / playlist names, ids inside links, error text, file
names, the theme name and the version reported by PyPI all reach
``notify()``. With markup on, ``Song [live]`` lost its bracketed word,
``Song [/x]`` raised ``MarkupError`` while the toast rendered, and
``[@click=app.quit]x[/]`` became a clickable action.

Every such notification in ``app/_track_actions.py``, ``app/_sidebar.py`` and
``app/_app.py`` is raised here through its real code path, then shown by a
real Textual app and read back from the rendered toast.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from textual.app import App
from textual.widgets._toast import Toast

from tests.test_app.test_playback import _fresh_playback_host
from ytm_player.app._app import YTMPlayerApp
from ytm_player.app._playback import PlaybackMixin
from ytm_player.app._sidebar import SidebarMixin
from ytm_player.app._track_actions import TrackActionsMixin
from ytm_player.services.queue import QueueManager

# Bracket text, a closing tag with nothing to close, and a click action.
_HOSTILE = "Song [live] [/x] [@click=app.quit]quit me[/]"


# ── Showing a notification for real ──────────────────────────────────


class _ToastApp(App[None]):
    """A bare app that records a quit instead of quitting."""

    quit_requested = False

    async def action_quit(self) -> None:
        self.quit_requested = True


async def _wait_for_toasts(pilot: Any, count: int) -> list[Toast]:
    app = pilot.app
    for _ in range(50):
        await pilot.pause()
        if len(app.screen.query(Toast)) >= count:
            break
    await pilot.pause()
    toasts = list(app.screen.query(Toast))
    assert len(toasts) == count
    return toasts


def _click_action_cells(app: App[Any], toast: Toast) -> list[tuple[int, int]]:
    """Screen cells of *toast* that run an action when clicked."""
    region = toast.region
    return [
        (x, y)
        for y in range(region.y, region.bottom)
        for x in range(region.x, region.right)
        if "@click" in app.screen.get_style_at(x, y).meta
    ]


async def _show(calls: list[Any]) -> list[tuple[str, list[tuple[int, int]]]]:
    """Replay recorded ``notify`` calls in a real app: (text shown, action cells)."""
    app = _ToastApp()
    async with app.run_test(size=(160, 60), notifications=True) as pilot:
        for call in calls:
            app.notify(*call.args, **call.kwargs)
        toasts = await _wait_for_toasts(pilot, len(calls))
        return [(toast.render().plain, _click_action_cells(app, toast)) for toast in toasts]


# ── The real mixin in a real app, one case per kind of bad text ──────


class _QueueApp(TrackActionsMixin, _ToastApp):
    def __init__(self) -> None:
        super().__init__()
        self.queue = QueueManager()


@pytest.mark.parametrize(
    "title",
    ["Song [live]", "Song [/x]", "[@click=app.quit]quit me[/]"],
    ids=["bracket-text", "malformed-closing-tag", "click-action"],
)
async def test_added_to_queue_toast_shows_the_title_as_typed(title):
    app = _QueueApp()
    async with app.run_test(size=(100, 30), notifications=True) as pilot:
        app._enqueue_track({"video_id": "v", "title": title})
        (toast,) = await _wait_for_toasts(pilot, 1)

        shown = toast.render().plain
        action_cells = _click_action_cells(app, toast)
        # Click inside the title itself, where an injected action would sit.
        content = toast.content_region
        await pilot.click(offset=(content.x + len("Added to queue: ") + 1, content.y))
        await pilot.pause()

        assert shown == f"Added to queue: {title}"
        assert action_cells == []
        assert not app.quit_requested


# ── Every sink, through its own code path ────────────────────────────


class _Host(TrackActionsMixin, SidebarMixin, PlaybackMixin):
    pass


def _host() -> _Host:
    host = _Host()
    host.__dict__.update(vars(_fresh_playback_host()))
    host.queue = QueueManager()
    host.play_track = AsyncMock()
    host._refresh_queue_page = MagicMock()
    host._sync_shuffle_bar = MagicMock()
    host.shuffle_prefs = {}
    host.ytmusic = MagicMock()
    host.push_screen = MagicMock()
    host.query_one = MagicMock()
    host.focused = None
    host.navigate_to = AsyncMock()
    host._current_page = "search"
    host._current_page_kwargs = {}
    host._active_library_playlist_id = None
    host._nav_idle = asyncio.Event()
    host._nav_idle.set()
    host._purge_playlist_nav_state = MagicMock()
    host._apply_playlist_edit_to_ui = AsyncMock()
    return host


def _raw(video_id: str = "v") -> dict:
    return {"videoId": video_id, "title": "T", "artists": [{"name": "A", "id": "a"}]}


def _track(**fields: Any) -> dict:
    return {"video_id": "v", "title": "T", "duration": 180, **fields}


def _pick(host: _Host, action_id: str) -> None:
    """Choose *action_id* in the popup the host just pushed."""
    with patch("ytm_player.app._track_actions.copy_to_clipboard", return_value=False):
        host.push_screen.call_args.args[1](action_id)


async def _added_to_queue(host: _Host) -> None:
    host._enqueue_track(_track(title=_HOSTILE))


async def _playing_next(host: _Host) -> None:
    host._play_track_next(_track(title=_HOSTILE))


async def _track_link(host: _Host) -> None:
    host._open_actions_for_track(_track(video_id=_HOSTILE))
    _pick(host, "copy_link")


async def _added_collection(host: _Host) -> None:
    host._append_to_queue([_track()], _HOSTILE)


async def _collection_next(host: _Host) -> None:
    host._play_next_multiple([_track()], _HOSTILE)


async def _artist_link(host: _Host) -> None:
    host._show_artist_actions({"id": _HOSTILE, "name": "A"})
    _pick(host, "copy_link")


async def _album_link(host: _Host) -> None:
    host._open_actions_for_album(_track(album="Album", album_id=_HOSTILE))
    _pick(host, "copy_link")


async def _artist_radio(host: _Host) -> None:
    host.ytmusic.get_artist = AsyncMock(return_value={"name": _HOSTILE, "radioId": "R"})
    host.ytmusic.get_watch_playlist = AsyncMock(return_value=[_raw()])
    await host._start_artist_radio("ARTIST")


async def _artist_top_songs(host: _Host) -> None:
    host.ytmusic.get_artist = AsyncMock(
        return_value={"name": _HOSTILE, "songs": {"results": [_raw()]}}
    )
    await host._play_artist_top_songs("ARTIST")


async def _album_to_library(host: _Host) -> None:
    host.ytmusic.get_album = AsyncMock(return_value={"audioPlaylistId": "PL"})
    host.ytmusic.add_to_library = AsyncMock(return_value="success")
    await host._add_album_to_library("ALBUM", _HOSTILE)


async def _play_album(host: _Host) -> None:
    host.ytmusic.get_album = AsyncMock(return_value={"tracks": [_raw()]})
    await host._play_album("ALBUM", _HOSTILE)


async def _play_playlist(host: _Host) -> None:
    host.ytmusic.get_playlist = AsyncMock(return_value={"tracks": [_raw()], "trackCount": 1})
    await host._play_playlist("PL", _HOSTILE)


async def _playlist_radio(host: _Host) -> None:
    host.ytmusic.get_playlist_radio = AsyncMock(return_value=[_raw()])
    await host._start_playlist_radio({"playlistId": "PL", "title": _HOSTILE})


async def _playlist_created(host: _Host) -> None:
    host.ytmusic.create_playlist = AsyncMock(return_value=("success", "PL"))
    await host._create_sidebar_playlist(_HOSTILE)


async def _playlist_updated(host: _Host) -> None:
    host.ytmusic.edit_playlist = AsyncMock(return_value="success")
    await host._edit_sidebar_playlist({"playlistId": "PL"}, _HOSTILE, "", "PRIVATE")


async def _playlist_removed(host: _Host) -> None:
    host.ytmusic.delete_playlist = AsyncMock(return_value="success")
    await host._delete_sidebar_playlist({"playlistId": "PL", "title": _HOSTILE})


_Sink = Callable[[_Host], Awaitable[None]]

# (code path, number of notifications carrying the outside text)
_MIXIN_SINKS: list[tuple[_Sink, int]] = [
    (_added_to_queue, 1),
    (_playing_next, 1),
    (_track_link, 1),
    (_added_collection, 1),
    (_collection_next, 1),
    (_artist_link, 1),
    (_album_link, 1),
    (_artist_radio, 1),
    (_artist_top_songs, 1),
    (_album_to_library, 1),
    (_play_album, 1),
    (_play_playlist, 1),
    (_playlist_radio, 2),
    (_playlist_created, 1),
    (_playlist_updated, 1),
    (_playlist_removed, 1),
]


def _carrying_outside_text(notify: MagicMock) -> list[Any]:
    return [call for call in notify.call_args_list if call.args and _HOSTILE in call.args[0]]


async def _assert_shown_as_typed(calls: list[Any], expected: int) -> None:
    assert len(calls) == expected, "the code path did not raise its notification"
    shown = await _show(calls)
    assert [text for text, _ in shown] == [call.args[0] for call in calls]
    assert [cells for _, cells in shown] == [[] for _ in calls]


@pytest.mark.parametrize(
    ("sink", "expected"), _MIXIN_SINKS, ids=[sink.__name__.lstrip("_") for sink, _ in _MIXIN_SINKS]
)
async def test_track_action_and_sidebar_toasts_show_outside_text_as_typed(sink, expected):
    host = _host()

    await sink(host)

    await _assert_shown_as_typed(_carrying_outside_text(host.notify), expected)


# ── app/_app.py ──────────────────────────────────────────────────────


async def test_saved_theme_toast_shows_the_theme_name_as_typed():
    host = MagicMock()
    host.theme = _HOSTILE

    YTMPlayerApp.action_set_current_theme_as_default(host)

    await _assert_shown_as_typed(_carrying_outside_text(host.notify), 1)


async def test_background_error_toast_shows_the_crash_file_name_as_typed():
    host = MagicMock()
    crash_file = SimpleNamespace(name=_HOSTILE)

    with patch("ytm_player.utils.logging.write_crash_file", return_value=crash_file):
        YTMPlayerApp._handle_exception(host, RuntimeError("synthetic"))

    await _assert_shown_as_typed(_carrying_outside_text(host.notify), 1)


async def _mount(**overrides: Any) -> MagicMock:
    """Run the real ``on_mount`` against a mock host with fake services."""
    host = MagicMock()
    host.settings.mpris.enabled = False
    host.settings.discord.enabled = False
    host.settings.lastfm.enabled = False
    host.settings.general.startup_page = "library"
    host._first_run_hint_shown = True
    host._restore_session_state = AsyncMock()
    host.navigate_to = AsyncMock()
    auth = MagicMock()
    auth.is_authenticated.return_value = True
    auth.validate.return_value = True
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    with (
        patch("ytm_player.app._app.AuthManager", return_value=auth),
        patch("ytm_player.config.paths.ensure_dirs"),
        patch("ytm_player.app._app.Player"),
        patch("ytm_player.app._app.YTMusicService", **overrides.get("ytmusic", {})),
        patch("ytm_player.app._app.StreamResolver"),
        patch("ytm_player.app._app.HistoryManager") as history,
        patch("ytm_player.app._app.CacheManager") as cache,
        patch("ytm_player.app._app.IPCServer") as ipc,
        patch("ytm_player.app._app.HISTORY_DB", overrides.get("history_db", "history.db")),
    ):
        ipc.return_value.start = AsyncMock()
        history.return_value.init = overrides.get("history_init", AsyncMock())
        cache.return_value.init = overrides.get("cache_init", AsyncMock())
        try:
            await YTMPlayerApp.on_mount(host)
        finally:
            loop.set_exception_handler(previous)
    return host


@pytest.mark.parametrize(
    "overrides",
    [
        {"ytmusic": {"side_effect": RuntimeError(_HOSTILE)}},
        {"history_db": _HOSTILE, "history_init": AsyncMock(side_effect=RuntimeError("unreadable"))},
        {"cache_init": AsyncMock(side_effect=RuntimeError(_HOSTILE))},
    ],
    ids=["service-error-text", "history-db-path", "cache-error-text"],
)
async def test_startup_failure_toasts_show_error_text_and_paths_as_typed(overrides):
    host = await _mount(**overrides)

    await _assert_shown_as_typed(_carrying_outside_text(host.notify), 1)


async def test_update_toast_shows_the_reported_version_as_typed():
    host = MagicMock()
    host.settings.general.check_for_updates = True

    YTMPlayerApp._start_update_check(host)
    check = host.run_worker.call_args.args[0]
    with patch("ytm_player.services.update_check.check_for_update", return_value=_HOSTILE):
        await check

    await _assert_shown_as_typed(_carrying_outside_text(host.notify), 1)
