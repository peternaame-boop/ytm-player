"""Concurrency tests for YTMusicService.

The get_playlist(order=...) path monkey-patches client._send_request to
inject sort params.  Two concurrent calls would stack patches and fail to
fully restore the original — meaning a third call could see a stale
patched _send_request.
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def ytmusic_service():
    """Construct YTMusicService with a fake client (bypasses __init__)."""
    from ytm_player.services.ytmusic import YTMusicService

    svc = YTMusicService.__new__(YTMusicService)
    svc._auth_path = MagicMock()
    svc._auth_manager = None
    svc._user = None
    svc._consecutive_api_failures = 0
    svc._order_lock = asyncio.Lock()  # needed since we bypass __init__
    svc._no_patch = asyncio.Event()
    svc._no_patch.set()
    svc._inflight = 0
    svc._no_inflight = asyncio.Event()
    svc._no_inflight.set()

    fake_client = MagicMock()
    # Real send_request impl we want to be restored.
    fake_client._original_send = lambda endpoint, body, *a, **kw: {"contents": []}
    fake_client._send_request = fake_client._original_send

    # get_playlist returns minimal valid response.
    def fake_get_playlist(playlist_id, **kwargs):
        return {"tracks": []}

    fake_client.get_playlist = fake_get_playlist
    svc._ytm = fake_client
    return svc


class TestSendRequestRaceCondition:
    """C3: concurrent get_playlist(order=...) must not corrupt _send_request."""

    async def test_concurrent_order_calls_restore_original_send_request(self, ytmusic_service):
        """Run two get_playlist(order=...) calls concurrently and assert
        client._send_request is the ORIGINAL function after both complete.

        The bug: each call captures original_send = client._send_request
        BEFORE patching.  If call A patches first, call B captures the
        patched function as 'original'.  When both finish, the restore
        in B's finally block sets _send_request to A's patched function,
        not the true original.
        """
        client = ytmusic_service._ytm
        true_original = client._send_request

        # Make get_playlist take a measurable amount of time so calls overlap.
        # (Patch _run — the ordered path bypasses the gated _call.)
        async def fake_call(func, *args, **kwargs):
            # Simulate the work the real _run would do on a thread.
            await asyncio.sleep(0.05)
            return {"tracks": []}

        with patch.object(ytmusic_service, "_run", side_effect=fake_call):
            # Run two concurrent calls with order= so both trigger the
            # monkey-patch path.
            await asyncio.gather(
                ytmusic_service.get_playlist("PL1", order="a_to_z"),
                ytmusic_service.get_playlist("PL2", order="recently_added"),
            )

        assert client._send_request is true_original, (
            "After concurrent get_playlist(order=...) calls, client._send_request "
            "must be restored to the true original. Got a stacked/leaked patch instead."
        )


class TestPatchWindowIsolation:
    """A patch window must be exclusive against ALL other client calls —
    a concurrent "browse" request would otherwise get the sort params
    injected into its body."""

    async def test_normal_call_during_patch_window_is_not_polluted(self, ytmusic_service):
        """A normal call issued WHILE an ordered fetch is mid-flight must
        wait out the window and send an un-patched body."""
        client = ytmusic_service._ytm
        recorded: list[tuple[str, dict]] = []

        def real_send(endpoint, body, *a, **kw):
            recorded.append((endpoint, dict(body)))
            return {"contents": []}

        client._send_request = real_send

        entered = threading.Event()
        release = threading.Event()

        def fake_get_playlist(playlist_id, **kwargs):
            # Emulate ytmusicapi: route a browse request through _send_request,
            # then hold the window open until the test releases it.
            client._send_request("browse", {"browseId": playlist_id})
            entered.set()
            release.wait(timeout=5)
            return {"tracks": []}

        client.get_playlist = fake_get_playlist

        def normal_browse():
            client._send_request("browse", {"browseId": "NORMAL"})

        ordered = asyncio.create_task(ytmusic_service.get_playlist("PL1", order="a_to_z"))
        await asyncio.to_thread(entered.wait, 5)  # window open, fetch mid-flight
        normal = asyncio.create_task(ytmusic_service._call(normal_browse))
        await asyncio.sleep(0.1)  # pre-fix, the normal call runs here — patched
        release.set()
        await asyncio.gather(ordered, normal)

        ordered_bodies = [b for e, b in recorded if b.get("browseId") == "PL1"]
        normal_bodies = [b for e, b in recorded if b.get("browseId") == "NORMAL"]
        assert ordered_bodies and "params" in ordered_bodies[0]  # sanity: patch worked
        assert normal_bodies == [{"browseId": "NORMAL"}], (
            "sort params leaked into a concurrent unrelated browse request"
        )

    async def test_patch_window_waits_for_inflight_calls(self, ytmusic_service):
        """An ordered fetch must not patch _send_request while a normal call
        that captured the un-patched client is still in flight."""
        client = ytmusic_service._ytm
        recorded: list[tuple[str, dict]] = []

        def real_send(endpoint, body, *a, **kw):
            recorded.append((endpoint, dict(body)))
            return {"contents": []}

        client._send_request = real_send

        started = threading.Event()
        normal_release = threading.Event()
        ordered_release = threading.Event()

        def fake_get_playlist(playlist_id, **kwargs):
            client._send_request("browse", {"browseId": playlist_id})
            ordered_release.wait(timeout=5)  # hold the window open
            return {"tracks": []}

        client.get_playlist = fake_get_playlist

        def slow_normal():
            started.set()
            normal_release.wait(timeout=5)  # ordered call arrives while we sleep
            client._send_request("browse", {"browseId": "NORMAL"})

        normal = asyncio.create_task(ytmusic_service._call(slow_normal))
        await asyncio.to_thread(started.wait, 5)
        ordered = asyncio.create_task(ytmusic_service.get_playlist("PL1", order="a_to_z"))
        # Pre-fix the patch lands here, mid-normal-call; post-fix the ordered
        # call is parked waiting for the normal call to drain.
        await asyncio.sleep(0.1)
        normal_release.set()
        await normal  # normal sends its browse body now
        ordered_release.set()
        await ordered

        normal_bodies = [b for e, b in recorded if b.get("browseId") == "NORMAL"]
        assert normal_bodies == [{"browseId": "NORMAL"}], (
            "patch window opened while a normal call was in flight"
        )
