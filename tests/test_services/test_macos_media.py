"""Tests for ytm_player.services.macos_media."""

# ruff: noqa: N802

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import ytm_player.services.macos_media as macos_media
from ytm_player.services import _dispatch
from ytm_player.services.macos_media import MacOSMediaService


class _FakeCommand:
    def __init__(self) -> None:
        self.enabled = False
        self.handler = None
        self.removed: list[object] = []

    def setEnabled_(self, enabled: bool) -> None:
        self.enabled = enabled

    def addTargetWithHandler_(self, handler):
        self.handler = handler
        return handler

    def removeTarget_(self, target) -> None:
        self.removed.append(target)


class _FakeRemoteCommandCenter:
    def __init__(self) -> None:
        self.play = _FakeCommand()
        self.pause = _FakeCommand()
        self.toggle = _FakeCommand()
        self.next = _FakeCommand()
        self.previous = _FakeCommand()

    def playCommand(self):
        return self.play

    def pauseCommand(self):
        return self.pause

    def togglePlayPauseCommand(self):
        return self.toggle

    def nextTrackCommand(self):
        return self.next

    def previousTrackCommand(self):
        return self.previous


class _FakeNowPlayingInfoCenter:
    def __init__(self) -> None:
        self.info = None
        self.playback_state = None

    def setNowPlayingInfo_(self, info) -> None:
        self.info = info

    def setPlaybackState_(self, state) -> None:
        self.playback_state = state


class _FakeMediaPlayerModule:
    MPRemoteCommandHandlerStatusSuccess = 1
    MPRemoteCommandHandlerStatusCommandFailed = 2
    MPNowPlayingPlaybackStatePlaying = 10
    MPNowPlayingPlaybackStatePaused = 11
    MPNowPlayingPlaybackStateStopped = 12
    MPMediaItemPropertyTitle = "title"
    MPMediaItemPropertyArtist = "artist"
    MPMediaItemPropertyAlbumTitle = "albumTitle"
    MPMediaItemPropertyPlaybackDuration = "playbackDuration"
    MPNowPlayingInfoPropertyElapsedPlaybackTime = "elapsedPlaybackTime"
    MPNowPlayingInfoPropertyPlaybackRate = "playbackRate"

    _remote = _FakeRemoteCommandCenter()
    _now = _FakeNowPlayingInfoCenter()

    class MPRemoteCommandCenter:
        @staticmethod
        def sharedCommandCenter():
            return _FakeMediaPlayerModule._remote

    class MPNowPlayingInfoCenter:
        @staticmethod
        def defaultCenter():
            return _FakeMediaPlayerModule._now


def _reset_fake_media_player() -> None:
    _FakeMediaPlayerModule._remote = _FakeRemoteCommandCenter()
    _FakeMediaPlayerModule._now = _FakeNowPlayingInfoCenter()


class _FakeRunLoop:
    def __init__(self) -> None:
        self.calls = 0

    def runMode_beforeDate_(self, _mode, _date) -> None:
        self.calls += 1


class _FakeFoundationModule:
    NSDefaultRunLoopMode = "default"
    _run_loop = _FakeRunLoop()

    class NSRunLoop:
        @staticmethod
        def mainRunLoop():
            return _FakeFoundationModule._run_loop

    class NSDate:
        @staticmethod
        def date():
            return object()


class TestStart:
    async def test_noop_when_framework_missing(self, caplog) -> None:
        svc = MacOSMediaService()
        with (
            patch("ytm_player.services.macos_media._MEDIA_PLAYER_AVAILABLE", False),
            caplog.at_level(logging.INFO),
        ):
            await svc.start({}, asyncio.get_running_loop())

        assert svc._running is False
        assert "MediaPlayer framework bindings not installed" in caplog.text

    async def test_registers_handlers_and_dispatches_callback(self) -> None:
        _reset_fake_media_player()
        callbacks = {
            "play": AsyncMock(),
            "pause": AsyncMock(),
            "play_pause": AsyncMock(),
            "next": AsyncMock(),
            "previous": AsyncMock(),
        }
        svc = MacOSMediaService()

        with (
            patch("ytm_player.services.macos_media._MEDIA_PLAYER_AVAILABLE", True),
            patch("ytm_player.services.macos_media._MP", _FakeMediaPlayerModule),
        ):
            await svc.start(callbacks, asyncio.get_running_loop())
            assert svc._running is True
            assert _FakeMediaPlayerModule._remote.play.enabled is True

            handler = _FakeMediaPlayerModule._remote.play.handler
            assert handler is not None
            status = handler(None)
            assert status == macos_media._STATUS_SUCCESS

            await asyncio.sleep(0)
            await asyncio.sleep(0)
            callbacks["play"].assert_awaited_once()
            svc.stop()

    async def test_start_captures_dispatch_context(self) -> None:
        """start() runs on the loop thread and must snapshot its context so
        dispatched Now-Playing callbacks keep Textual's active_app ContextVar."""
        _reset_fake_media_player()
        _dispatch._dispatch_context = None
        svc = MacOSMediaService()
        try:
            with (
                patch("ytm_player.services.macos_media._MEDIA_PLAYER_AVAILABLE", True),
                patch("ytm_player.services.macos_media._MP", _FakeMediaPlayerModule),
            ):
                await svc.start({}, asyncio.get_running_loop())
            assert _dispatch._dispatch_context is not None
        finally:
            svc.stop()
            _dispatch._dispatch_context = None

    async def test_pumps_cocoa_run_loop_until_stopped(self) -> None:
        _reset_fake_media_player()
        _FakeFoundationModule._run_loop = _FakeRunLoop()
        svc = MacOSMediaService()

        with (
            patch("ytm_player.services.macos_media._MEDIA_PLAYER_AVAILABLE", True),
            patch("ytm_player.services.macos_media._MP", _FakeMediaPlayerModule),
            patch("ytm_player.services.macos_media._FOUNDATION", _FakeFoundationModule),
        ):
            await svc.start({}, asyncio.get_running_loop())
            await asyncio.sleep(0)
            task = svc._run_loop_task
            assert task is not None
            assert _FakeFoundationModule._run_loop.calls == 1

            await svc.start({}, asyncio.get_running_loop())
            assert svc._run_loop_task is task

            calls_before_stop = _FakeFoundationModule._run_loop.calls
            svc.stop()
            await asyncio.sleep(0)
            assert task.done()
            assert _FakeFoundationModule._run_loop.calls == calls_before_stop


class TestNowPlayingUpdates:
    async def test_updates_metadata_status_and_position(self) -> None:
        _reset_fake_media_player()
        callbacks = {
            "play": AsyncMock(),
            "pause": AsyncMock(),
            "play_pause": AsyncMock(),
            "next": AsyncMock(),
            "previous": AsyncMock(),
        }
        svc = MacOSMediaService()

        with (
            patch("ytm_player.services.macos_media._MEDIA_PLAYER_AVAILABLE", True),
            patch("ytm_player.services.macos_media._MP", _FakeMediaPlayerModule),
        ):
            await svc.start(callbacks, asyncio.get_running_loop())
            await svc.update_metadata("Song", "Artist", "Album", 180_000_000)
            await svc.update_playback_status("Playing")
            svc.update_position(32_000_000)

            info = _FakeMediaPlayerModule._now.info
            assert info is not None
            assert info[macos_media._TITLE_KEY] == "Song"
            assert info[macos_media._ARTIST_KEY] == "Artist"
            assert info[macos_media._ALBUM_KEY] == "Album"
            assert info[macos_media._DURATION_KEY] == 180.0
            assert info[macos_media._ELAPSED_KEY] == 32.0
            assert info[macos_media._RATE_KEY] == 1.0
            assert _FakeMediaPlayerModule._now.playback_state == macos_media._PLAYBACK_STATE_PLAYING

            svc.stop()
            assert _FakeMediaPlayerModule._remote.play.removed
            assert _FakeMediaPlayerModule._now.info is None
