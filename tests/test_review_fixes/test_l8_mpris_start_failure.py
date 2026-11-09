"""T15 (C2): a D-Bus failure after the bus connects must not escape start().

The app awaits ``MPRISService.start`` unguarded in ``on_mount``. A session bus
that accepts the connection but refuses the export or the name (a sandbox
D-Bus proxy, for example) used to abort startup half-way. start() now logs,
disconnects, clears the partial state and leaves the app running without MPRIS.
"""

from __future__ import annotations

import logging

import pytest


@pytest.fixture
def mpris_mod():
    """The mpris module, skipping where MPRIS cannot run (off Linux, no dbus-fast)."""
    import ytm_player.services.mpris as mpris_mod

    if not mpris_mod._DBUS_AVAILABLE:
        pytest.skip("MPRIS (dbus-fast) not available on this platform")
    return mpris_mod


class _FakeBus:
    """Session bus that connects, then fails at the configured registration step."""

    def __init__(self, *, fail_export_number: int | None = None, fail_request_name: bool = False):
        self._fail_export_number = fail_export_number
        self._fail_request_name = fail_request_name
        self.exported: list[object] = []
        self.name_requests: list[str] = []
        self.disconnect_calls = 0

    async def connect(self) -> _FakeBus:
        return self

    def export(self, path: str, interface: object) -> None:
        if len(self.exported) + 1 == self._fail_export_number:
            raise ValueError(f"cannot export interface at {path}")
        self.exported.append(interface)

    async def request_name(self, name: str) -> None:
        self.name_requests.append(name)
        if self._fail_request_name:
            from dbus_fast import DBusError, ErrorType

            raise DBusError(
                ErrorType.ACCESS_DENIED,
                f'Connection is not allowed to own the service "{name}" due to security policies',
            )

    def disconnect(self) -> None:
        self.disconnect_calls += 1


def _install(mpris_mod, monkeypatch, bus: _FakeBus) -> None:
    monkeypatch.setattr(mpris_mod, "MessageBus", lambda: bus)


async def _assert_runs_without_mpris(mpris_mod, svc, bus: _FakeBus, caplog) -> None:
    assert svc._running is False
    # The connection the failed registration left open is closed, once.
    assert bus.disconnect_calls == 1
    # No partial state survives.
    assert svc._bus is None
    assert svc._root_iface is None
    assert svc._player_iface is None
    # The failure is reported in the log.
    assert any(
        r.levelno >= logging.WARNING and r.name == mpris_mod.__name__ and "MPRIS" in r.getMessage()
        for r in caplog.records
    )
    # The rest of the app keeps calling the service; every call is a quiet no-op.
    await svc.update_metadata("Title", "Artist", "Album", "", 1_000_000)
    await svc.update_playback_status("Playing")
    svc.update_position(5)
    svc.update_volume(0.3)
    svc.emit_seeked(1)
    await svc.stop()
    assert bus.disconnect_calls == 1


async def test_name_request_failure_leaves_app_running_without_mpris(
    mpris_mod, monkeypatch, caplog
):
    bus = _FakeBus(fail_request_name=True)
    _install(mpris_mod, monkeypatch, bus)
    svc = mpris_mod.MPRISService()

    with caplog.at_level(logging.WARNING, logger=mpris_mod.__name__):
        await svc.start({})  # must not raise

    assert bus.name_requests == [mpris_mod.BUS_NAME]
    await _assert_runs_without_mpris(mpris_mod, svc, bus, caplog)


@pytest.mark.parametrize("failing_export", [1, 2])
async def test_export_failure_leaves_app_running_without_mpris(
    mpris_mod, monkeypatch, caplog, failing_export
):
    bus = _FakeBus(fail_export_number=failing_export)
    _install(mpris_mod, monkeypatch, bus)
    svc = mpris_mod.MPRISService()

    with caplog.at_level(logging.WARNING, logger=mpris_mod.__name__):
        await svc.start({})  # must not raise

    assert len(bus.exported) == failing_export - 1
    assert bus.name_requests == []
    await _assert_runs_without_mpris(mpris_mod, svc, bus, caplog)


async def test_successful_start_still_registers(mpris_mod, monkeypatch):
    """Guard for the fix itself: the success path is unchanged."""
    bus = _FakeBus()
    _install(mpris_mod, monkeypatch, bus)
    svc = mpris_mod.MPRISService()

    await svc.start({})

    assert svc._running is True
    assert svc._bus is bus
    assert bus.exported == [svc._root_iface, svc._player_iface]
    assert bus.name_requests == [mpris_mod.BUS_NAME]
    assert bus.disconnect_calls == 0
