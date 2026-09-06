"""The Browse page's service wrappers tell a failed fetch (None) from an
empty result ([]), so the Subscriptions and Releases tabs can show a failure
instead of "nothing here"."""

from __future__ import annotations

from unittest.mock import MagicMock

from tests.conftest import make_ytmusic_service


async def test_get_library_artists_returns_none_when_the_client_fails() -> None:
    svc = make_ytmusic_service()
    svc._ytm.get_library_subscriptions = MagicMock(side_effect=RuntimeError("down"))

    assert await svc.get_library_artists(limit=None) is None


async def test_get_library_artists_passes_the_limit_through_and_returns_the_list() -> None:
    svc = make_ytmusic_service()
    rows = [{"artist": "Sia", "browseId": "UCsia"}]
    svc._ytm.get_library_subscriptions = MagicMock(return_value=rows)

    assert await svc.get_library_artists(limit=None) == rows
    svc._ytm.get_library_subscriptions.assert_called_once_with(limit=None)
    assert await svc.get_library_artists() == rows
    svc._ytm.get_library_subscriptions.assert_called_with(limit=25)


async def test_get_new_releases_returns_none_when_the_client_fails() -> None:
    svc = make_ytmusic_service()
    svc._ytm.get_explore = MagicMock(side_effect=RuntimeError("down"))

    assert await svc.get_new_releases() is None


async def test_get_new_releases_returns_the_list_or_empty_for_a_feed_without_releases() -> None:
    svc = make_ytmusic_service()
    albums = [{"title": "Fresh", "browseId": "MPREb_x", "audioPlaylistId": "OLAK5uy_x"}]
    svc._ytm.get_explore = MagicMock(return_value={"new_releases": albums})
    assert await svc.get_new_releases() == albums

    svc._ytm.get_explore = MagicMock(return_value={"top_songs": []})
    assert await svc.get_new_releases() == []
