"""Tests for ytm_player.services.macos_media."""

# ruff: noqa: N802

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import ytm_player.services.macos_media as macos_media
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
