"""Session renewal driven from YTMusicService: sorted playlist loads and the cooldown.

Sorted playlist loads (the Library page's ``order="recently_added"``) run
inside the sort-param patch window and used to bypass the renewal in
``_call``; and after a failed renewal every waiting expired call repeated
the browser cookie extraction and account probes.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest
from ytmusicapi.exceptions import YTMusicServerError

from tests.conftest import make_ytmusic_service
from ytm_player.services import ytmusic as ytmusic_module

CHANNEL = "UCa1b2c3d4e5f6g7h8i9j0k1"
OTHER = "UCz9y8x7w6v5u4t3s2r1q0p9"
EXPIRED = YTMusicServerError("Server returned HTTP 401: Unauthorized.\n")
RECENTLY_ADDED = ytmusic_module.YTMusicService._ORDER_PARAMS["recently_added"]
A_TO_Z = ytmusic_module.YTMusicService._ORDER_PARAMS["a_to_z"]


class _Client:
    """A real class so bound methods carry ``__name__``; records every request body it sends."""

    def __init__(self, expired: bool = False, delay: float = 0.0, error: Exception = EXPIRED):
        self.expired = expired
        self.delay = delay
        self.error = error
        self.requests: list[tuple[str, dict]] = []
        self.playlist_calls: list[dict] = []

    def _send_request(self, endpoint, body, *a, **kw):
        self.requests.append((endpoint, dict(body)))
        return {}

    def _finish(self):
        if self.delay:
            time.sleep(self.delay)
        if self.expired:
            raise self.error

    def get_playlist(self, playlist_id, limit=None):
        self.playlist_calls.append({"playlist_id": playlist_id, "limit": limit})
        self._send_request("browse", {"browseId": playlist_id})
        self._finish()
        return {"id": playlist_id, "tracks": [{"videoId": "v1"}]}

    def get_song(self, video_id):
        self._send_request("player", {"videoId": video_id})
        self._finish()
        return {"videoDetails": {"videoId": video_id}}

    def rate_song(self, video_id, rating):
        self._finish()
        return {}

    def browse_bodies(self, playlist_id):
        return [
            body
            for endpoint, body in self.requests
            if endpoint == "browse" and body.get("browseId") == playlist_id
        ]


def _send_is_original(client) -> bool:
    """True when ``client._send_request`` is the class method again, not a sort-param patch."""
    return getattr(client._send_request, "__func__", None) is _Client._send_request


def _service(client, *, renew=True, replacement=None, replacement_identity=CHANNEL):
    """Service whose current client is *client* (account CHANNEL); renewal yields *replacement*."""
    manager = MagicMock()
    manager.try_auto_refresh.return_value = renew
    replacement = _Client() if replacement is None else replacement
    manager.create_bound_client.return_value = (replacement, replacement_identity)
    svc = make_ytmusic_service(_ytm=client, _auth_manager=manager)
    svc._client_identities[client] = CHANNEL
    return svc, manager, replacement


class _Clock:
    """Stand-in for the ``time`` module inside the service; only ``monotonic`` is used there."""

    def __init__(self):
        self.now = 1_000.0

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(ytmusic_module, "time", fake)
    return fake


# ── Sorted playlist loads ────────────────────────────────────────────────────


class TestSortedPlaylistRenewal:
    async def test_sorted_load_renews_and_retries_with_the_same_sort(self):
        expired = _Client(expired=True)
        svc, manager, replacement = _service(expired)

        with patch.object(svc, "_run", wraps=svc._run) as run:
            result = await svc.get_playlist("PL1", limit=25, order="recently_added", timeout=7)

        assert result["tracks"] == [{"videoId": "v1"}]
        manager.try_auto_refresh.assert_called_once_with(CHANNEL)
        assert expired.browse_bodies("PL1") == [{"browseId": "PL1", "params": RECENTLY_ADDED}]
        assert replacement.browse_bodies("PL1") == [{"browseId": "PL1", "params": RECENTLY_ADDED}]
        assert replacement.playlist_calls == [{"playlist_id": "PL1", "limit": 25}]
        assert [call.kwargs["timeout"] for call in run.call_args_list] == [7, 7]

    async def test_sorted_load_failed_renewal_returns_empty_after_one_attempt(self):
        expired = _Client(expired=True)
        svc, manager, replacement = _service(expired, renew=False)

        result = await svc.get_playlist("PL1", order="recently_added")

        assert result == {}
        manager.try_auto_refresh.assert_called_once_with(CHANNEL)
        assert len(expired.playlist_calls) == 1
        assert replacement.playlist_calls == []

    async def test_sorted_load_not_replayed_on_another_account(self):
        expired = _Client(expired=True)
        svc, manager, replacement = _service(expired, replacement_identity=OTHER)

        result = await svc.get_playlist("PL1", order="recently_added")

        assert result == {}
        manager.try_auto_refresh.assert_called_once_with(CHANNEL)
        assert replacement.playlist_calls == []

    async def test_sorted_load_pending_across_account_switch_is_not_replayed(self):
        expired = _Client(expired=True)
        svc, manager, _ = _service(expired)
        switched = _Client()

        def swap_then_fail(*args, **kwargs):
            # `ytm setup` for another account landed while the sorted load was in flight.
            svc._ytm = switched
            svc._client_identities[switched] = OTHER
            raise EXPIRED

        expired.get_playlist = swap_then_fail

        result = await svc.get_playlist("PL1", order="recently_added")

        assert result == {}
        manager.try_auto_refresh.assert_not_called()
        assert switched.playlist_calls == []

    async def test_renewal_runs_outside_the_patch_window(self):
        expired = _Client(expired=True)
        svc, manager, _ = _service(expired)
        seen = {}

        def renew(identity):
            seen["gate_open"] = svc._no_patch.is_set()
            seen["order_lock_free"] = not svc._order_lock.locked()
            seen["send_restored"] = _send_is_original(expired)
            return True

        manager.try_auto_refresh.side_effect = renew

        await svc.get_playlist("PL1", order="recently_added")

        assert seen == {"gate_open": True, "order_lock_free": True, "send_restored": True}


# ── Cooldown after a failed renewal ──────────────────────────────────────────


class TestRenewalCooldown:
    async def test_failed_burst_makes_one_renewal_attempt(self, clock):
        expired = _Client(expired=True)
        svc, manager, _ = _service(expired, renew=False)

        results = await asyncio.gather(*(svc.rate_song(f"v{i}", "LIKE") for i in range(5)))

        assert results == ["auth_expired"] * 5
        manager.try_auto_refresh.assert_called_once_with(CHANNEL)

    async def test_failed_burst_across_client_reset_still_one_attempt(self, clock):
        # The KeyError form of an expired session (a sign-in endpoint where
        # data was expected) is the one that also feeds _run's consecutive-
        # failure counter, so a burst of them replaces the client mid-way.
        # Clients rebuilt from the same expired files fail the same way;
        # which client each task binds to depends on task start order,
        # which asyncio doesn't fix (it differs between 3.10 and 3.14).
        expired_keyerror = {"expired": True, "error": KeyError("signInEndpoint")}
        expired = _Client(**expired_keyerror)
        svc, manager, _ = _service(expired, renew=False)
        manager.create_bound_client.side_effect = lambda user=None: (
            _Client(**expired_keyerror),
            CHANNEL,
        )
        burst = ytmusic_module._MAX_API_FAILURES_BEFORE_REINIT * 3

        results = await asyncio.gather(*(svc.rate_song(f"v{i}", "LIKE") for i in range(burst)))

        assert results == ["server_error"] * burst  # how rate_song labels a KeyError
        assert svc._ytm is not expired  # the consecutive-failure reinit replaced the client
        manager.try_auto_refresh.assert_called_once_with(CHANNEL)

    async def test_attempt_allowed_again_after_cooldown(self, clock):
        expired = _Client(expired=True)
        svc, manager, _ = _service(expired, renew=False)

        assert await svc.rate_song("v1", "LIKE") == "auth_expired"
        clock.advance(59)
        assert await svc.rate_song("v2", "LIKE") == "auth_expired"
        assert manager.try_auto_refresh.call_count == 1
        clock.advance(2)
        assert await svc.rate_song("v3", "LIKE") == "auth_expired"
        assert manager.try_auto_refresh.call_count == 2

    async def test_successful_renewal_clears_cooldown(self, clock):
        expired = _Client(expired=True)
        svc, manager, replacement = _service(expired)
        later = _Client()
        manager.try_auto_refresh.side_effect = [False, True, True]
        manager.create_bound_client.side_effect = [(replacement, CHANNEL), (later, CHANNEL)]

        assert await svc.rate_song("v1", "LIKE") == "auth_expired"
        clock.advance(61)
        assert await svc.rate_song("v2", "LIKE") == "success"
        replacement.expired = True
        assert await svc.rate_song("v3", "LIKE") == "success"  # no cooldown after the success

        assert manager.try_auto_refresh.call_count == 3
        assert svc._ytm is later

    async def test_cooldown_does_not_block_ordinary_or_sorted_calls(self, clock):
        client = _Client()
        svc, manager, _ = _service(client)
        svc._renewal_retry_after = clock.monotonic() + 60

        song = await svc.get_song("v1")
        playlist = await svc.get_playlist("PL1", order="recently_added")

        assert song == {"videoDetails": {"videoId": "v1"}}
        assert playlist["tracks"] == [{"videoId": "v1"}]
        manager.try_auto_refresh.assert_not_called()

    async def test_cooldown_survives_a_client_reset(self, clock):
        expired = _Client(expired=True)
        svc, manager, _ = _service(expired, renew=False)
        manager.create_bound_client.side_effect = lambda user=None: (_Client(expired=True), CHANNEL)

        assert await svc.rate_song("v1", "LIKE") == "auth_expired"
        with svc._client_init_lock:
            svc._ytm = None  # what _run does after consecutive failures
        assert await svc.rate_song("v2", "LIKE") == "auth_expired"

        manager.try_auto_refresh.assert_called_once_with(CHANNEL)


# ── Patch window safety ──────────────────────────────────────────────────────


class TestPatchWindowSafety:
    async def test_concurrent_sorted_and_ordinary_calls_renew_without_deadlock_or_leak(self):
        expired = _Client(expired=True, delay=0.05)
        svc, manager, replacement = _service(expired, replacement=_Client(delay=0.05))

        results = await asyncio.wait_for(
            asyncio.gather(
                svc.get_playlist("PL1", order="recently_added"),
                svc.get_song("v1"),
                svc.get_playlist("PL2"),
                svc.get_playlist("PL3", order="a_to_z"),
            ),
            timeout=10,
        )

        assert [bool(r) for r in results] == [True, True, True, True]
        manager.try_auto_refresh.assert_called_once_with(CHANNEL)
        # Which client a task bound to depends on task start order; the
        # invariant is that every request either client saw carried exactly
        # its own sort params, and each request reached the replacement once.
        expected = {
            ("browse", "PL1"): {"browseId": "PL1", "params": RECENTLY_ADDED},
            ("browse", "PL2"): {"browseId": "PL2"},
            ("browse", "PL3"): {"browseId": "PL3", "params": A_TO_Z},
            ("player", "v1"): {"videoId": "v1"},
        }
        for client in (expired, replacement):
            for endpoint, body in client.requests:
                key = (endpoint, body.get("browseId") or body.get("videoId"))
                assert body == expected[key], (client is expired, endpoint, body)
        assert sorted(
            (e, b.get("browseId") or b.get("videoId")) for e, b in replacement.requests
        ) == sorted(expected)
        assert _send_is_original(expired) and _send_is_original(replacement)
        assert svc._no_patch.is_set() and not svc._order_lock.locked()

    async def test_cancelled_sorted_load_restores_send_and_reopens_gate(self):
        client = _Client(delay=0.3)
        svc, manager, _ = _service(client)

        load = asyncio.create_task(svc.get_playlist("PL1", order="recently_added"))
        await asyncio.sleep(0.05)
        assert not _send_is_original(client) and not svc._no_patch.is_set()  # window open
        load.cancel()
        with pytest.raises(asyncio.CancelledError):
            await load

        assert _send_is_original(client)
        assert svc._no_patch.is_set() and not svc._order_lock.locked()
        song = await asyncio.wait_for(svc.get_song("v1"), timeout=5)
        assert song == {"videoDetails": {"videoId": "v1"}}
        manager.try_auto_refresh.assert_not_called()

    async def test_failed_sorted_load_restores_send_and_reopens_gate(self):
        client = _Client()
        svc, manager, _ = _service(client)
        client.get_playlist = MagicMock(side_effect=RuntimeError("boom"))

        assert await svc.get_playlist("PL1", order="recently_added") == {}

        assert _send_is_original(client)
        assert svc._no_patch.is_set() and not svc._order_lock.locked()
        manager.try_auto_refresh.assert_not_called()
