"""Tests for artist subscribe/unsubscribe service methods."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock

import pytest

from ytm_player.services.ytmusic import YTMusicService


@pytest.fixture
def ytmusic_service():
    """Create a YTMusicService with a mocked YTMusic client."""
    service = YTMusicService.__new__(YTMusicService)
    service._ytm = MagicMock()
    service._consecutive_api_failures = 0
    service._client_init_lock = threading.Lock()
    service._order_lock = asyncio.Lock()
    service._last_discovery_source = -1
    return service


async def test_subscribe_artist_success(ytmusic_service: YTMusicService):
    ytmusic_service.client.subscribe_artists = MagicMock(return_value=None)
    result = await ytmusic_service.subscribe_artist("UC_channel_123")
    assert result == "success"
    ytmusic_service.client.subscribe_artists.assert_called_once_with(["UC_channel_123"])


async def test_subscribe_artist_network_failure(ytmusic_service: YTMusicService):
    import requests.exceptions

    ytmusic_service.client.subscribe_artists = MagicMock(
        side_effect=requests.exceptions.ConnectionError("no network")
    )
    result = await ytmusic_service.subscribe_artist("UC_channel_123")
    assert result == "network"


async def test_subscribe_artist_server_error(ytmusic_service: YTMusicService):
    from ytmusicapi.exceptions import YTMusicServerError

    ytmusic_service.client.subscribe_artists = MagicMock(
        side_effect=YTMusicServerError("Server returned HTTP 500: Internal Server Error")
    )
    result = await ytmusic_service.subscribe_artist("UC_channel_123")
    assert result == "server_error"


async def test_subscribe_artist_auth_expired(ytmusic_service: YTMusicService):
    from ytmusicapi.exceptions import YTMusicServerError

    ytmusic_service.client.subscribe_artists = MagicMock(
        side_effect=YTMusicServerError("Server returned HTTP 401: Unauthorized")
    )
    result = await ytmusic_service.subscribe_artist("UC_channel_123")
    assert result == "auth_expired"


async def test_unsubscribe_artist_success(ytmusic_service: YTMusicService):
    ytmusic_service.client.unsubscribe_artists = MagicMock(return_value=None)
    result = await ytmusic_service.unsubscribe_artist("UC_channel_123")
    assert result == "success"
    ytmusic_service.client.unsubscribe_artists.assert_called_once_with(["UC_channel_123"])


async def test_unsubscribe_artist_network_failure(ytmusic_service: YTMusicService):
    import requests.exceptions

    ytmusic_service.client.unsubscribe_artists = MagicMock(
        side_effect=requests.exceptions.ConnectionError("no network")
    )
    result = await ytmusic_service.unsubscribe_artist("UC_channel_123")
    assert result == "network"


async def test_unsubscribe_artist_server_error(ytmusic_service: YTMusicService):
    from ytmusicapi.exceptions import YTMusicServerError

    ytmusic_service.client.unsubscribe_artists = MagicMock(
        side_effect=YTMusicServerError("Server returned HTTP 500: Internal Server Error")
    )
    result = await ytmusic_service.unsubscribe_artist("UC_channel_123")
    assert result == "server_error"


async def test_unsubscribe_artist_auth_expired(ytmusic_service: YTMusicService):
    from ytmusicapi.exceptions import YTMusicServerError

    ytmusic_service.client.unsubscribe_artists = MagicMock(
        side_effect=YTMusicServerError("Server returned HTTP 401: Unauthorized")
    )
    result = await ytmusic_service.unsubscribe_artist("UC_channel_123")
    assert result == "auth_expired"
