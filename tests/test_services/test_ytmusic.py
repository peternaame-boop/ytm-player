"""Tests for YTMusicService._call() error handling and client thread-safety.

These tests cover Tasks 4.1 + 4.2 of the audit-driven cleanup:

- 4.1: ``_call()`` narrows its outer ``except`` so that programming-error
  exceptions (TypeError, AttributeError, etc.) propagate without bumping the
  consecutive-failure counter or triggering a spurious client reinit.
- 4.2: ``YTMusicService.client`` lazy init is guarded by a ``threading.Lock``
  with double-checked locking so concurrent first-accesses from
  ``asyncio.to_thread`` workers don't both build a fresh ``YTMusic`` instance.
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock

import pytest
import requests


@pytest.fixture
def ytmusic_service():
    """Construct YTMusicService with a fake client (bypasses __init__)."""
    from ytm_player.services.ytmusic import YTMusicService

    svc = YTMusicService.__new__(YTMusicService)
    svc._auth_path = MagicMock()
    svc._auth_manager = None
    svc._user = None
    svc._consecutive_api_failures = 0
    svc._client_init_lock = threading.Lock()
    svc._order_lock = asyncio.Lock()
    svc._ytm = MagicMock(name="fake-ytm-client")
    return svc


class TestCallNarrowedCatch:
    """Task 4.1: ``_call()`` outer except is narrowed to expected types."""

    async def test_call_propagates_unexpected_exceptions_unmasked(self, ytmusic_service):
        """A TypeError (programming bug) must propagate without bumping the
        failure counter or clearing _ytm.
        """

        def boom(*_args, **_kwargs):
            raise TypeError("expected str, got None")

        original_client = ytmusic_service._ytm

        with pytest.raises(TypeError, match="expected str"):
            await ytmusic_service._call(boom)

        assert ytmusic_service._consecutive_api_failures == 0, (
            "Programming-error exceptions must NOT increment the failure counter."
        )
        assert ytmusic_service._ytm is original_client, (
            "Programming-error exceptions must NOT trigger a client reinit."
        )

    async def test_call_increments_counter_only_on_expected_api_errors(self, ytmusic_service):
        """A requests.ConnectionError IS expected — it bumps the counter, but
        below threshold the client is NOT cleared.
        """

        def network_failure(*_args, **_kwargs):
            raise requests.ConnectionError("network unreachable")

        original_client = ytmusic_service._ytm

        for _ in range(2):
            with pytest.raises(requests.ConnectionError):
                await ytmusic_service._call(network_failure)

        assert ytmusic_service._consecutive_api_failures == 2
        assert ytmusic_service._ytm is original_client, (
            "Below the reinit threshold, the client must not be cleared."
        )

    async def test_call_reinits_client_after_threshold(self, ytmusic_service):
        """After 3 consecutive expected failures, _ytm is cleared (reinit
        signal) and the counter is reset.
        """

        def timeout_failure(*_args, **_kwargs):
            raise asyncio.TimeoutError("api timed out")

        for _ in range(3):
            with pytest.raises(asyncio.TimeoutError):
                await ytmusic_service._call(timeout_failure)

        assert ytmusic_service._ytm is None, (
            "After the failure threshold, _ytm must be cleared so the next "
            ".client access rebuilds it."
        )
        assert ytmusic_service._consecutive_api_failures == 0, (
            "The failure counter must be reset after a reinit."
        )


class TestClientThreadSafety:
    """Task 4.2: ``client`` property is thread-safe under concurrent first-access."""

    def test_client_property_is_thread_safe_under_concurrent_first_access(self, monkeypatch):
        """Four threads call ``service.client`` simultaneously when ``_ytm`` is
        ``None``. Only one ``YTMusic`` instance must be created and all four
        threads must see the same instance.
        """
        from ytm_player.services.ytmusic import YTMusicService

        construction_count = 0
        construction_lock = threading.Lock()

        def fake_ytmusic_ctor(*_args, **_kwargs):
            nonlocal construction_count
            with construction_lock:
                construction_count += 1
            # Sleep briefly so a second thread is very likely to enter the
            # outer ``if self._ytm is None`` check while the first is still
            # constructing — this is exactly the race the lock prevents.
            import time

            time.sleep(0.05)
            return MagicMock(name=f"ytm-mock-{construction_count}")

        monkeypatch.setattr("ytm_player.services.ytmusic.YTMusic", fake_ytmusic_ctor)

        svc = YTMusicService.__new__(YTMusicService)
        svc._auth_path = MagicMock()
        svc._auth_manager = None
        svc._user = None
        svc._ytm = None
        svc._consecutive_api_failures = 0
        svc._client_init_lock = threading.Lock()
        svc._order_lock = asyncio.Lock()

        n_threads = 4
        barrier = threading.Barrier(n_threads)
        results: list[int] = []
        results_lock = threading.Lock()

        def worker():
            barrier.wait()  # all threads punch through together
            client = svc.client
            with results_lock:
                results.append(id(client))

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == n_threads
        assert len(set(results)) == 1, (
            f"All {n_threads} threads must see the same client instance; "
            f"got {len(set(results))} distinct instances."
        )
        assert construction_count == 1, (
            f"YTMusic constructor must be called exactly once under the lock; "
            f"was called {construction_count} times."
        )


class TestMutationMethodsReturnTypedResult:
    """Tasks 4.3 + 4.11: rate_song / add_playlist_items / remove_playlist_items
    return a MutationResult Literal so callers can branch on the failure cause.

    Task 4.3 originally returned bool; Task 4.11 refined this to a typed
    Literal (``"success"`` | ``"auth_required"`` | ``"auth_expired"`` |
    ``"network"`` | ``"server_error"``) so the UI can show per-cause toasts.
    """

    async def test_rate_song_returns_success_on_ok(self, ytmusic_service, monkeypatch):
        async def fake_call(func, *_args, **_kwargs):
            return None  # ytmusicapi.rate_song's actual return is irrelevant

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        result = await ytmusic_service.rate_song("abc123", "LIKE")
        assert result == "success"

    async def test_rate_song_returns_network_on_connection_error(
        self, ytmusic_service, monkeypatch
    ):
        async def fake_call(func, *_args, **_kwargs):
            raise requests.ConnectionError("network down")

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        result = await ytmusic_service.rate_song("abc123", "LIKE")
        assert result == "network"

    async def test_rate_song_returns_network_on_timeout(self, ytmusic_service, monkeypatch):
        async def fake_call(func, *_args, **_kwargs):
            raise asyncio.TimeoutError("api timed out")

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        result = await ytmusic_service.rate_song("abc123", "LIKE")
        assert result == "network"

    async def test_rate_song_returns_auth_expired_on_http_401(self, ytmusic_service, monkeypatch):
        from ytmusicapi.exceptions import YTMusicServerError

        async def fake_call(func, *_args, **_kwargs):
            raise YTMusicServerError(
                "Server returned HTTP 401: Unauthorized.\nRequest had invalid authentication credentials."
            )

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        result = await ytmusic_service.rate_song("abc123", "LIKE")
        assert result == "auth_expired"

    async def test_rate_song_returns_auth_expired_on_http_403(self, ytmusic_service, monkeypatch):
        from ytmusicapi.exceptions import YTMusicServerError

        async def fake_call(func, *_args, **_kwargs):
            raise YTMusicServerError(
                "Server returned HTTP 403: Forbidden.\nThe request is missing a valid API key."
            )

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        result = await ytmusic_service.rate_song("abc123", "LIKE")
        assert result == "auth_expired"

    async def test_rate_song_returns_server_error_on_http_500(self, ytmusic_service, monkeypatch):
        from ytmusicapi.exceptions import YTMusicServerError

        async def fake_call(func, *_args, **_kwargs):
            raise YTMusicServerError(
                "Server returned HTTP 500: Internal Server Error.\nbackend exploded"
            )

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        result = await ytmusic_service.rate_song("abc123", "LIKE")
        assert result == "server_error"

    async def test_rate_song_returns_server_error_on_unparseable_message(
        self, ytmusic_service, monkeypatch
    ):
        """If ytmusicapi changes the message format, fall through to server_error."""
        from ytmusicapi.exceptions import YTMusicServerError

        async def fake_call(func, *_args, **_kwargs):
            raise YTMusicServerError("totally different format")

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        result = await ytmusic_service.rate_song("abc123", "LIKE")
        assert result == "server_error"

    async def test_rate_song_returns_auth_required_on_user_error(
        self, ytmusic_service, monkeypatch
    ):
        from ytmusicapi.exceptions import YTMusicUserError

        async def fake_call(func, *_args, **_kwargs):
            raise YTMusicUserError("Please provide authentication before using this function")

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        result = await ytmusic_service.rate_song("abc123", "LIKE")
        assert result == "auth_required"

    async def test_rate_song_propagates_unexpected_exceptions(self, ytmusic_service, monkeypatch):
        async def fake_call(func, *_args, **_kwargs):
            raise TypeError("programming bug")

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        with pytest.raises(TypeError, match="programming bug"):
            await ytmusic_service.rate_song("abc123", "LIKE")

    async def test_add_playlist_items_returns_success_on_ok(self, ytmusic_service, monkeypatch):
        async def fake_call(func, *_args, **_kwargs):
            return None

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        result = await ytmusic_service.add_playlist_items("PL_test", ["v1", "v2"])
        assert result == "success"

    async def test_add_playlist_items_returns_success_on_succeeded_dict(
        self, ytmusic_service, monkeypatch
    ):
        async def fake_call(func, *_args, **_kwargs):
            return {"status": "STATUS_SUCCEEDED", "playlistEditResults": []}

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        result = await ytmusic_service.add_playlist_items("PL_test", ["v1"])
        assert result == "success"

    async def test_add_playlist_items_captures_set_video_ids(self, ytmusic_service, monkeypatch):
        async def fake_call(func, *_args, **_kwargs):
            return {
                "status": "STATUS_SUCCEEDED",
                "playlistEditResults": [
                    {"videoId": "v1", "setVideoId": "s1"},
                    {"videoId": "v2", "setVideoId": "s2"},
                ],
            }

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        result = await ytmusic_service.add_playlist_items("PL_test", ["v1", "v2"])
        assert result == "success"
        assert ytmusic_service.last_added_set_video_ids == {"v1": "s1", "v2": "s2"}

    async def test_add_playlist_items_resets_set_video_ids_on_duplicate(
        self, ytmusic_service, monkeypatch
    ):
        ytmusic_service.last_added_set_video_ids = {"stale": "x"}

        async def fake_call(func, *_args, **_kwargs):
            return {"status": "STATUS_FAILED"}

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        result = await ytmusic_service.add_playlist_items("PL_test", ["v1"])
        assert result == "duplicate"
        assert ytmusic_service.last_added_set_video_ids == {}

    async def test_add_playlist_items_returns_duplicate_on_failed_status(
        self, ytmusic_service, monkeypatch
    ):
        # YTM returns HTTP 200 + STATUS_FAILED when a duplicate add is rejected.
        async def fake_call(func, *_args, **_kwargs):
            return {"status": "STATUS_FAILED"}

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        result = await ytmusic_service.add_playlist_items("PL_test", ["v1"])
        assert result == "duplicate"

    async def test_add_playlist_items_returns_server_error_on_unexpected_status(
        self, ytmusic_service, monkeypatch
    ):
        # An exotic non-success status must not be mislabeled as a duplicate.
        async def fake_call(func, *_args, **_kwargs):
            return {"status": "STATUS_SOMETHING_ELSE"}

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        result = await ytmusic_service.add_playlist_items("PL_test", ["v1"])
        assert result == "server_error"

    async def test_add_playlist_items_forwards_duplicates_flag(self, ytmusic_service, monkeypatch):
        seen = {}

        async def fake_call(func, *_args, **kwargs):
            seen.update(kwargs)
            return None

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        await ytmusic_service.add_playlist_items("PL_test", ["v1"], duplicates=True)
        assert seen.get("duplicates") is True

    async def test_add_playlist_items_returns_network_on_connection_error(
        self, ytmusic_service, monkeypatch
    ):
        async def fake_call(func, *_args, **_kwargs):
            raise requests.ConnectionError("api down")

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        result = await ytmusic_service.add_playlist_items("PL_test", ["v1"])
        assert result == "network"

    async def test_add_playlist_items_returns_auth_expired_on_http_401(
        self, ytmusic_service, monkeypatch
    ):
        from ytmusicapi.exceptions import YTMusicServerError

        async def fake_call(func, *_args, **_kwargs):
            raise YTMusicServerError("Server returned HTTP 401: Unauthorized.\n")

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        result = await ytmusic_service.add_playlist_items("PL_test", ["v1"])
        assert result == "auth_expired"

    async def test_remove_playlist_items_returns_success_on_ok(self, ytmusic_service, monkeypatch):
        async def fake_call(func, *_args, **_kwargs):
            return None

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        result = await ytmusic_service.remove_playlist_items(
            "PL_test", [{"videoId": "v1", "setVideoId": "s1"}]
        )
        assert result == "success"

    async def test_remove_playlist_items_returns_network_on_timeout(
        self, ytmusic_service, monkeypatch
    ):
        async def fake_call(func, *_args, **_kwargs):
            raise asyncio.TimeoutError("timed out")

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        result = await ytmusic_service.remove_playlist_items(
            "PL_test", [{"videoId": "v1", "setVideoId": "s1"}]
        )
        assert result == "network"

    async def test_remove_playlist_items_returns_server_error_on_http_503(
        self, ytmusic_service, monkeypatch
    ):
        from ytmusicapi.exceptions import YTMusicServerError

        async def fake_call(func, *_args, **_kwargs):
            raise YTMusicServerError("Server returned HTTP 503: Service Unavailable.\n")

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        result = await ytmusic_service.remove_playlist_items(
            "PL_test", [{"videoId": "v1", "setVideoId": "s1"}]
        )
        assert result == "server_error"

    async def test_edit_playlist_returns_success_on_ok(self, ytmusic_service, monkeypatch):
        async def fake_call(func, *_args, **_kwargs):
            return "STATUS_SUCCEEDED"

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        result = await ytmusic_service.edit_playlist("PL_test", title="New Name")
        assert result == "success"

    async def test_edit_playlist_returns_success_on_truthy_result(
        self, ytmusic_service, monkeypatch
    ):
        async def fake_call(func, *_args, **_kwargs):
            return True

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        result = await ytmusic_service.edit_playlist("PL_test", description="desc")
        assert result == "success"

    async def test_edit_playlist_returns_server_error_on_non_success(
        self, ytmusic_service, monkeypatch
    ):
        async def fake_call(func, *_args, **_kwargs):
            return "SOME_OTHER_STATUS"

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        result = await ytmusic_service.edit_playlist("PL_test", privacy_status="PUBLIC")
        assert result == "server_error"

    async def test_edit_playlist_returns_network_on_timeout(self, ytmusic_service, monkeypatch):
        async def fake_call(func, *_args, **_kwargs):
            raise asyncio.TimeoutError("timed out")

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        result = await ytmusic_service.edit_playlist("PL_test", title="Name")
        assert result == "network"

    async def test_edit_playlist_returns_auth_expired_on_http_401(
        self, ytmusic_service, monkeypatch
    ):
        from ytmusicapi.exceptions import YTMusicServerError

        async def fake_call(func, *_args, **_kwargs):
            raise YTMusicServerError("Server returned HTTP 401: Unauthorized.\n")

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        result = await ytmusic_service.edit_playlist("PL_test", title="Name")
        assert result == "auth_expired"

    async def test_edit_playlist_propagates_unexpected_exceptions(
        self, ytmusic_service, monkeypatch
    ):
        async def fake_call(func, *_args, **_kwargs):
            raise TypeError("programming bug")

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        with pytest.raises(TypeError, match="programming bug"):
            await ytmusic_service.edit_playlist("PL_test", title="Name")

    async def test_edit_playlist_sends_all_kwargs(self, ytmusic_service, monkeypatch):
        calls = []

        async def fake_call(func, playlist_id, **kwargs):
            calls.append((func, playlist_id, kwargs))
            return "STATUS_SUCCEEDED"

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        result = await ytmusic_service.edit_playlist(
            "PL_test", title="T", description="D", privacy_status="PUBLIC"
        )
        assert result == "success"
        assert len(calls) == 1
        _func, pid, kwargs = calls[0]
        assert pid == "PL_test"
        assert kwargs == {"title": "T", "description": "D", "privacyStatus": "PUBLIC"}

    async def test_edit_playlist_omits_none_kwargs(self, ytmusic_service, monkeypatch):
        calls = []

        async def fake_call(func, playlist_id, **kwargs):
            calls.append(kwargs)
            return "STATUS_SUCCEEDED"

        monkeypatch.setattr(ytmusic_service, "_call", fake_call)
        result = await ytmusic_service.edit_playlist("PL_test", title="T")
        assert result == "success"
        assert calls == [{"title": "T"}]


class TestMutationFailureSuffix:
    """Task 4.11: cascade sites compose toast text via mutation_failure_suffix."""

    def test_suffix_is_empty_for_success(self):
        from ytm_player.services.ytmusic import mutation_failure_suffix

        assert mutation_failure_suffix("success") == ""

    def test_suffix_distinct_per_kind(self):
        """Each non-success kind has a non-empty, distinct suffix so users
        can tell them apart in the UI.
        """
        from ytm_player.services.ytmusic import mutation_failure_suffix

        kinds = ("auth_required", "auth_expired", "network", "server_error")
        suffixes = [mutation_failure_suffix(k) for k in kinds]  # type: ignore[arg-type]
        assert all(s for s in suffixes), "every non-success kind needs text"
        assert len(set(suffixes)) == len(kinds), "suffixes must be distinct"

    def test_auth_suffixes_mention_setup(self):
        """Auth failures must point users at `ytm setup` so they know how
        to fix it.
        """
        from ytm_player.services.ytmusic import mutation_failure_suffix

        assert "setup" in mutation_failure_suffix("auth_required").lower()
        assert "setup" in mutation_failure_suffix("auth_expired").lower()
