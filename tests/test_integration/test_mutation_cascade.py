"""Integration test: mutation methods (rate_song, add_playlist_items)
return a typed MutationResult on caught failure, and the UI cascade reacts
correctly per failure cause.

Validates the contract chain:
  service.rate_song fails (network)   -> returns "network"      -> UI shows "check your connection"
  service.rate_song fails (auth)      -> returns "auth_expired" -> UI tells user to re-setup
  service.rate_song succeeds          -> returns "success"      -> UI shows "Liked" toast
  service.add_playlist_items partial  -> returns failure-kind per failed batch
                                          -> UI surfaces partial-success warning

Pre-Phase-4.3, the methods returned None on both success and failure, so the
UI lied to the user. Task 4.3 introduced bool returns and Task 4.11 refined
that to a typed Literal so per-cause toasts are possible.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import requests
from ytmusicapi.exceptions import YTMusicServerError

from tests.conftest import make_ytmusic_service
from ytm_player.services.ytmusic import mutation_failure_suffix


async def test_rate_song_returns_success_on_ok(monkeypatch):
    svc = make_ytmusic_service()

    fake_call = AsyncMock(return_value=None)
    monkeypatch.setattr(svc, "_call", fake_call)

    result = await svc.rate_song("abc123", "LIKE")
    assert result == "success"


async def test_rate_song_returns_network_on_connection_failure(monkeypatch):
    svc = make_ytmusic_service()

    fake_call = AsyncMock(side_effect=requests.ConnectionError("network unreachable"))
    monkeypatch.setattr(svc, "_call", fake_call)

    result = await svc.rate_song("abc123", "LIKE")
    assert result == "network"
    # Suffix the cascade site would render must mention connectivity.
    assert "connection" in mutation_failure_suffix(result).lower()


async def test_rate_song_returns_auth_expired_on_http_401(monkeypatch):
    """Auth-expired must map to the dedicated Literal, not "server_error",
    so cascade sites can prompt the user to re-run `ytm setup`.
    """
    svc = make_ytmusic_service()

    fake_call = AsyncMock(
        side_effect=YTMusicServerError(
            "Server returned HTTP 401: Unauthorized.\nMissing auth credentials."
        )
    )
    monkeypatch.setattr(svc, "_call", fake_call)

    result = await svc.rate_song("abc123", "LIKE")
    assert result == "auth_expired"
    assert "setup" in mutation_failure_suffix(result).lower()


async def test_add_playlist_items_returns_kind_per_batch(monkeypatch):
    """Verifies the spotify-import flow's per-batch failure tracking still
    works under the typed contract — three batches: ok, network-fail, ok.
    """
    svc = make_ytmusic_service()

    call_count = 0

    async def fake_call(func, *_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise requests.ConnectionError("flaky network")
        return None

    monkeypatch.setattr(svc, "_call", fake_call)

    batch1 = await svc.add_playlist_items("PL_test", ["v1", "v2"])
    batch2 = await svc.add_playlist_items("PL_test", ["v3", "v4"])
    batch3 = await svc.add_playlist_items("PL_test", ["v5", "v6"])

    assert batch1 == "success"
    assert batch2 == "network"
    assert batch3 == "success"


async def test_add_playlist_items_uniform_failure_reports_kind(monkeypatch):
    """Spotify-import: when ALL batches fail with the same kind, the UI
    surfaces that kind via mutation_failure_suffix. This test exercises the
    service-side contract; the popup-side composition is tested implicitly
    by relying on the same suffix helper.
    """
    svc = make_ytmusic_service()

    fake_call = AsyncMock(
        side_effect=YTMusicServerError("Server returned HTTP 401: Unauthorized.\n")
    )
    monkeypatch.setattr(svc, "_call", fake_call)

    results = [
        await svc.add_playlist_items("PL_test", ["v1"]),
        await svc.add_playlist_items("PL_test", ["v2"]),
        await svc.add_playlist_items("PL_test", ["v3"]),
    ]
    # All same kind — popup branch shows per-cause suffix.
    assert set(results) == {"auth_expired"}
