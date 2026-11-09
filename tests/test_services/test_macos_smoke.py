"""Real-framework smoke test for the macOS integrations (runs on macOS only).

Every other macOS test mocks AppKit, Foundation and MediaPlayer. On macOS
this module exercises the new code paths against the frameworks as installed,
each case in its own child process (``_macos_smoke_child.py``) with a
wall-clock timeout enforced by killing the child: the framework calls are
synchronous, so no in-process timeout could interrupt them, and a fresh
process keeps AppKit on a main thread. Each child restores what it changed in
``finally``; after a forced kill that cleanup has not run.

What it proves: the process can be demoted to an accessory app and restored,
the media service starts against the real MPRemoteCommandCenter, publishes
metadata and playback state that MPNowPlayingInfoCenter reads back, services
the main run loop alongside asyncio, and tears down. What it cannot prove:
that the Dock tile, Control Center or the media keys behave as a user sees
them. Those stay unverified until someone runs the branch on a desktop.

Deliberately NOT exercised: the media-key event tap. Creating a CGEventTap
needs Input Monitoring / Accessibility permission and captures keyboard events
system-wide; ``test_media_key_event_tap_is_not_exercised`` records that gap
as an explicit skip instead of pretending to cover it.

A framework missing on macOS is a failure, not a skip: pyproject declares
them for ``sys_platform == 'darwin'``, so absence means a packaging problem
that must not pass as validated. A refused activation policy fails too; on a
CI runner that warrants investigation, it is not by itself a regression.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

_CHILD = Path(__file__).with_name("_macos_smoke_child.py")
_ACCESSORY = 1
_CASE_TIMEOUT = 30.0

darwin_only = pytest.mark.skipif(sys.platform != "darwin", reason="macOS frameworks only")


def _run_child(case: str, timeout: float = _CASE_TIMEOUT) -> dict[str, Any]:
    """Run one smoke case in a child process; kill it if it exceeds *timeout*."""
    try:
        proc = subprocess.run(
            [sys.executable, str(_CHILD), case],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ,
            cwd=_CHILD.parents[2],
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"{case}: child exceeded {timeout:.0f}s and was killed; the framework call "
            "blocked and the child's cleanup did not run"
        )
    if proc.returncode == 2:
        pytest.fail(
            f"{case}: {proc.stderr.strip()} -- pyproject declares the pyobjc frameworks "
            "for darwin, so this install cannot validate the macOS integration"
        )
    if proc.returncode != 0:
        pytest.fail(f"{case}: child failed (exit {proc.returncode})\n{proc.stderr}")
    return json.loads(proc.stdout)


def test_blocking_child_is_killed_within_the_timeout() -> None:
    """The bound is enforced by the parent, not by anything the child awaits."""
    started = time.monotonic()
    with pytest.raises(pytest.fail.Exception, match="exceeded 1s and was killed"):
        _run_child("hang", timeout=1.0)
    assert time.monotonic() - started < 5.0


@darwin_only
def test_accessory_policy_is_applied_and_restored() -> None:
    r = _run_child("dock")

    assert r["appkit_available"], "macos_app did not pick up AppKit at import"
    assert r["main_thread"], "the child did not run on its main thread"
    assert r["switched"] is True, (
        f"AppKit refused the accessory activation policy on this host (policy after: "
        f"{r['policy_after']}); investigate the runner before reading this as a regression"
    )
    assert r["policy_after"] == _ACCESSORY
    assert r["restored"] is True, f"could not restore activation policy {r['previous']}"
    assert r["policy_final"] == r["previous"]


@darwin_only
def test_media_service_start_publish_pump_and_stop() -> None:
    from ytm_player.services import macos_media

    r = _run_child("media")

    assert r["media_player_available"], "macos_media did not pick up MediaPlayer"
    assert r["foundation_available"], "macos_media did not pick up Foundation"
    assert r["main_thread"] and r["main_run_loop"]
    assert r["running"] is True
    assert r["registered"] == 5, "not every remote command registered"
    assert r["pump_started"], "run-loop pump was not started"

    published = r["published"]
    assert published is not None, "MPNowPlayingInfoCenter has no now-playing info"
    assert published[macos_media._TITLE_KEY] == "Smoke title"
    assert published[macos_media._ARTIST_KEY] == "Smoke artist"
    assert float(published[macos_media._DURATION_KEY]) == 90.0

    assert r["rate_after_playing"] == 1.0, "playback rate not published for 'playing'"
    assert r["state_playing_const"] is not None, (
        "MPNowPlayingPlaybackStatePlaying is unavailable in this MediaPlayer; the playback "
        "state cannot be validated here"
    )
    assert r["state_after_playing"] is not None, (
        "MPNowPlayingInfoCenter.playbackState is unavailable here; the playback state "
        "cannot be validated"
    )
    assert r["state_after_playing"] == r["state_playing_const"], (
        "playback state not published for 'playing'"
    )

    assert r["pump_alive_after_pumping"], (
        "run-loop pump stopped on its own — check the child's stderr for an exception"
    )
    assert r["pump_finished_after_stop"], "run-loop pump did not finish after stop()"
    assert r["running_after_stop"] is False
    assert r["targets_after_stop"] == 0
    assert r["info_after_stop"] is False, "now-playing info not cleared on stop"


@darwin_only
def test_media_key_event_tap_is_not_exercised() -> None:
    """Recorded gap, on purpose: no CGEventTap is created in this suite."""
    r = _run_child("tap_constants")
    assert r["has_timeout_const"] and r["has_user_input_const"]
    pytest.skip(
        "media-key event tap not exercised: creating a CGEventTap needs Input Monitoring / "
        "Accessibility and captures keyboard events system-wide; media keys stay unverified"
    )
