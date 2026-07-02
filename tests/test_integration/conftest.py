"""Shared fixtures for integration tests.

Pattern: real services wired together, mocked at the outermost boundary.

- ytmusicapi mocked per-test at the service boundary (monkeypatch)
- mpv at the FFI boundary via existing test stubs
- Disk via tmp_path
- Singletons reset between tests so parallel runs don't collide

Why this layer exists: unit tests in tests/test_services/ mock at the
service boundary (e.g., they pass a fake YTMusic to the service). Integration
tests at this layer build real services wired together and only mock the
external systems (HTTP, FFI, disk) so the cross-service contracts are
exercised end-to-end.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest

from ytm_player.services.player import Player
from ytm_player.services.queue import QueueManager
from ytm_player.services.ytmusic import YTMusicService


@pytest.fixture(autouse=True)
def _reset_singletons() -> Iterator[None]:
    """Reset class-level singleton state between integration tests.

    Only ``Player._instance`` is a class-level singleton in this codebase;
    ``YTMusicService`` and ``QueueManager`` are constructed per test via
    their respective fixtures so their instance state doesn't leak.
    Without resetting Player here, parallel test runs collide on shared
    state.
    """
    yield
    if getattr(Player, "_instance", None) is not None:
        Player._instance = None


@pytest.fixture
def fresh_ytmusic() -> YTMusicService:
    """A YTMusicService with its lazy ``_ytm`` cleared so tests can stub.

    Tests typically follow this pattern::

        def test_something(fresh_ytmusic, monkeypatch):
            monkeypatch.setattr(fresh_ytmusic, "search", lambda *a, **kw: [...])
            # ...
    """
    svc = YTMusicService()
    svc._ytm = None
    return svc


@pytest.fixture
def fresh_queue() -> QueueManager:
    """A clean QueueManager instance per test."""
    qm = QueueManager()
    qm.clear()
    return qm


@pytest.fixture
def mock_mpv(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace ``services.player.mpv`` with a MagicMock for tests that
    exercise Player. Player's services.player module already substitutes
    a stub at import time when libmpv is missing; this fixture is for
    tests that want to assert specific mpv calls were made.
    """
    fake = MagicMock(name="fake_mpv_module")
    fake.MPV.return_value = MagicMock(name="fake_MPV_instance")
    monkeypatch.setattr("ytm_player.services.player.mpv", fake)
    return fake
