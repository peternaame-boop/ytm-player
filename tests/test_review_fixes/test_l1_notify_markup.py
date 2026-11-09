"""Toasts from the playback mixin show outside text as typed (review U1/U2/S1).

``App.notify`` parses its message as Textual markup unless told not to.
Track titles, radio labels, the user's notification format and download
errors went in unescaped: ``[live]`` vanished from the toast, ``[/x]``
raised MarkupError while it rendered, and ``[@click=app.quit]`` became a
clickable action. Every toast here is produced by the real mixin method
and rendered by a real Textual ``Toast``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from textual.app import App
from textual.pilot import Pilot
from textual.widgets._toast import Toast

from ytm_player.app._playback import PlaybackMixin
from ytm_player.services.queue import QueueManager

# Shown as typed / must not raise / must not become an action.
_BRACKETED = "Song [live]"
_CLOSING_TAG = "Song [/x]"
_CLICK_ACTION = "[@click=app.quit]x[/]"
_OUTSIDE_TEXT = [_BRACKETED, _CLOSING_TAG, _CLICK_ACTION]


class _PlaybackApp(PlaybackMixin, App[None]):
    """The real playback mixin over a bare app; services are stand-ins."""

    def __init__(self) -> None:
        super().__init__()
        self.quit_actions = 0
        self.player = MagicMock()
        self.player.play = AsyncMock()
        self.player.current_track = None
        self.player.position = 0.0
        self.stream_resolver = MagicMock()
        self.stream_resolver.resolve = AsyncMock(return_value=None)
        self.queue = QueueManager()
        self.settings = MagicMock()
        self.settings.notifications.enabled = True
        self.settings.notifications.format = "{title} — {artist}"
        self.settings.notifications.timeout_seconds = 5
        self.settings.playback.autoplay = False
        self.downloader = MagicMock()
        self.history = None
        self.cache = None
        self.discord = None
        self.lastfm = None
        self.mpris = None
        self.mac_media = None
        self.ytmusic = None
        self._last_play_video_id = ""
        self._last_play_time = 0.0
        self._consecutive_failures = 0
        self._track_start_position = 0.0
        self._pending_resume_video_id = None
        self._pending_resume_position = 0.0
        self._play_generation = 0
        self._recovery_generation = None
        self._handled_error_attempt = 0
        self._local_history_claim = None
        self._play_lock = asyncio.Lock()

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    async def action_quit(self) -> None:
        # What a "[@click=app.quit]" link in a toast would run.
        self.quit_actions += 1

    def _get_current_page(self) -> None:
        return None

    def _refresh_queue_page(self) -> None:
        pass


# Each case drives one notify sink with *text* as the outside value and
# returns the toast the user should read.


async def _skipped_without_video_id(app: _PlaybackApp, text: str) -> str:
    await app.play_track({"title": text})
    return f'Skipping "{text}" — no video ID (AI-generated streams are not supported).'


async def _stream_unavailable(app: _PlaybackApp, text: str) -> str:
    await app.play_track({"video_id": "vid", "title": text})
    return f'Couldn\'t play "{text}" — track may be unavailable or region-locked. Playback failed.'


async def _stream_failed_twice(app: _PlaybackApp, text: str) -> str:
    app._play_generation = 3
    app._recovery_generation = 3
    await app._on_player_error(
        {"attempt": 3, "track": {"video_id": "vid", "title": text}, "error": "boom"}
    )
    return f'Couldn\'t play "{text}" — stream failed twice.'


async def _radio_started(app: _PlaybackApp, text: str) -> str:
    app.ytmusic = MagicMock()
    app.ytmusic.get_radio = AsyncMock(return_value=[{"video_id": "r1", "title": "Radio 1"}])
    app.play_track = AsyncMock()  # type: ignore[method-assign]
    await app._fetch_and_play_radio({"video_id": "seed", "title": text})
    return f"Playing: Radio from {text}"


async def _track_changed(app: _PlaybackApp, text: str) -> str:
    app._on_track_change({"video_id": "vid", "title": text, "artist": text})
    return f"{text} — {text}"


async def _already_downloading(app: _PlaybackApp, text: str) -> str:
    app.downloader.is_downloading.return_value = True
    await app._download_track({"video_id": "vid", "title": text})
    return f"Already downloading: {text}"


def _download_result(app: _PlaybackApp, **result: object) -> None:
    app.downloader.is_downloading.return_value = False
    app.downloader.is_downloaded.return_value = False
    app.downloader.download = AsyncMock(return_value=SimpleNamespace(**result))


async def _download_started(app: _PlaybackApp, text: str) -> str:
    _download_result(app, success=True, file_path=None, error=None)
    await app._download_track({"video_id": "vid", "title": text})
    return f"Downloading: {text}"


async def _download_finished(app: _PlaybackApp, text: str) -> str:
    _download_result(app, success=True, file_path=None, error=None)
    await app._download_track({"video_id": "vid", "title": text})
    return f"Downloaded: {text}"


async def _download_not_retained(app: _PlaybackApp, text: str) -> str:
    file_path = MagicMock()
    file_path.suffix = ".opus"
    _download_result(app, success=True, file_path=file_path, error=None)
    app.cache = MagicMock()
    app.cache.put_file = AsyncMock()
    app.cache.has = AsyncMock(return_value=False)
    await app._download_track({"video_id": "vid", "title": text})
    return f"Not retained in cache: {text} doesn't fit within the cache size limit."


async def _download_failed(app: _PlaybackApp, text: str) -> str:
    _download_result(app, success=False, file_path=None, error=text)
    await app._download_track({"video_id": "vid", "title": "Plain"})
    return f"Download failed: {text}"


_SINKS = {
    "skipped-without-video-id": _skipped_without_video_id,
    "stream-unavailable": _stream_unavailable,
    "stream-failed-twice": _stream_failed_twice,
    "radio-started": _radio_started,
    "track-changed": _track_changed,
    "already-downloading": _already_downloading,
    "download-started": _download_started,
    "download-finished": _download_finished,
    "download-not-retained": _download_not_retained,
    "download-failed": _download_failed,
}


async def _toasts(pilot: Pilot) -> list[Toast]:
    """The toasts on screen once the posted notifications are mounted."""
    await pilot.pause()
    await pilot.pause()
    return list(pilot.app.screen.query(Toast))


@pytest.mark.parametrize("text", _OUTSIDE_TEXT, ids=["bracketed", "closing-tag", "click-action"])
@pytest.mark.parametrize("sink", list(_SINKS))
async def test_toast_shows_outside_text_as_typed(sink, text):
    app = _PlaybackApp()
    async with app.run_test(size=(120, 40), notifications=True) as pilot:
        expected = await _SINKS[sink](app, text)

        toasts = await _toasts(pilot)
        # render() is where "[/x]" raised MarkupError on master.
        rendered = [toast.render() for toast in toasts]

        assert expected in [content.plain for content in rendered]
        # No span at all: nothing in the text was taken as a style, a link
        # or an "@click" action.
        assert [content.spans for content in rendered] == [[] for _ in rendered]


async def test_clicking_a_click_action_title_in_a_toast_runs_no_action():
    app = _PlaybackApp()
    async with app.run_test(size=(120, 40), notifications=True) as pilot:
        await _radio_started(app, _CLICK_ACTION)
        (toast,) = [t for t in await _toasts(pilot) if t.render().plain.startswith("Playing:")]

        # Click the "x" the payload wraps; on master it ran app.quit.
        column = toast.render().plain.rindex("x")
        content, widget = toast.content_region, toast.region
        await pilot.click(toast, offset=(content.x - widget.x + column, content.y - widget.y))
        await pilot.pause()

        assert app.quit_actions == 0


async def test_toast_keeps_the_apps_own_wording_and_severity():
    """Guard for the fix (passes on master too): what the app itself
    contributes to these toasts still renders, namely the fixed wording
    around the outside text and the severity styling. None of these sinks
    mixes app markup with outside text."""
    app = _PlaybackApp()
    async with app.run_test(size=(120, 40), notifications=True) as pilot:
        await _download_failed(app, "disk full")
        await _download_not_retained(app, "Song")
        toasts = {toast.render().plain: toast for toast in await _toasts(pilot)}

        assert toasts["Download failed: disk full"].has_class("-error")
        assert toasts[
            "Not retained in cache: Song doesn't fit within the cache size limit."
        ].has_class("-warning")
        assert toasts["Downloading: Plain"].has_class("-information")
