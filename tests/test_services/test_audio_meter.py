"""Unit tests for the AudioMeter capture/FFT pipeline.

The real soundcard backend talks to PulseAudio/PipeWire which isn't
available in CI — instead we monkeypatch the singleton's _open_capture
method to return a fake recorder that emits known samples. This lets us
assert on the FFT shape, band normalization, and lifecycle without ever
opening a real audio device.
"""

from __future__ import annotations

import time

import pytest

from ytm_player.services import audio_meter as am_module
from ytm_player.services.audio_meter import AudioMeter


@pytest.fixture(autouse=True)
def _isolate_singleton():
    AudioMeter._instance = None
    yield
    # Always stop and clear so the daemon thread doesn't bleed across tests.
    inst = AudioMeter._instance
    if inst is not None:
        inst.stop()
    AudioMeter._instance = None


def test_bands_count_clamped_into_8_to_128(monkeypatch):
    """Settings out-of-range get clamped during init."""
    from ytm_player.config.settings import Settings, VisualizerSettings

    settings = Settings(visualizer=VisualizerSettings(bands=4))  # too small
    monkeypatch.setattr(am_module, "get_settings", lambda: settings)

    m = AudioMeter()
    assert len(m.bands) == 8

    AudioMeter._instance = None
    settings2 = Settings(visualizer=VisualizerSettings(bands=512))  # too big
    monkeypatch.setattr(am_module, "get_settings", lambda: settings2)
    m2 = AudioMeter()
    assert len(m2.bands) == 128


def test_default_state_is_silence():
    """A freshly-constructed meter exposes zero bands until capture runs."""
    m = AudioMeter()
    assert all(b == 0.0 for b in m.bands)
    assert m.rms == 0.0
    assert m.frame_counter == 0
    assert m.is_running() is False


@pytest.mark.skipif(not am_module._VIZ_AVAILABLE, reason="numpy/soundcard not installed")
def test_start_stop_with_fake_capture(monkeypatch):
    """End-to-end: a fake recorder feeds samples; bands move off zero."""
    import numpy as np

    # A 440 Hz tone at amplitude 0.7 across 1024-frame blocks.
    t = np.arange(1024) / am_module.SAMPLE_RATE
    tone = 0.7 * np.sin(2 * np.pi * 440 * t)
    stereo = np.column_stack([tone, tone]).astype(np.float32)

    blocks_handed_out = {"n": 0}

    class _FakeRecorder:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def record(self, numframes):
            blocks_handed_out["n"] += 1
            return stereo

    class _FakeMic:
        name = "fake-monitor"

        def recorder(self, **_kwargs):
            return _FakeRecorder()

    m = AudioMeter()
    monkeypatch.setattr(m, "_open_capture", lambda: _FakeMic())

    m.start()
    # Wait for the capture loop to process a few blocks.
    deadline = time.monotonic() + 1.0
    while m.frame_counter < 3 and time.monotonic() < deadline:
        time.sleep(0.02)
    m.stop()

    assert m.frame_counter >= 3
    # At least some bands should register the tone — 440Hz is well within
    # our log-binned range (50–18000Hz).
    nonzero = sum(1 for b in m.bands if b > 0.05)
    assert nonzero > 0, f"expected non-zero bands, got {m.bands}"


def test_start_no_op_when_viz_unavailable(monkeypatch):
    """When the viz extras aren't installed, start() is a documented no-op."""
    monkeypatch.setattr(am_module, "_VIZ_AVAILABLE", False)
    m = AudioMeter()
    m.start()
    assert m.is_running() is False
    # bands stay flat-zero — visualizers render blank instead of crashing.
    assert all(b == 0.0 for b in m.bands)


def test_stop_is_idempotent():
    m = AudioMeter()
    m.stop()  # not running — must not raise
    m.stop()  # still not running


def test_open_capture_falls_back_when_no_match(monkeypatch):
    """If the default sink's monitor isn't found, fall back to first loopback."""
    if not am_module._VIZ_AVAILABLE:
        pytest.skip("soundcard not installed")

    class _FakeMic:
        def __init__(self, name, mid, loop):
            self.name = name
            self.id = mid
            self.isloopback = loop

    class _FakeDefaultSpeaker:
        name = "Some Unique Sink Nobody Has"

    fake_sc = type(
        "ns",
        (),
        {
            "default_speaker": staticmethod(lambda: _FakeDefaultSpeaker()),
            "all_microphones": staticmethod(
                lambda include_loopback: [
                    _FakeMic("Monitor of Random Other Sink", "id1.monitor", True),
                    _FakeMic("Monitor of Yet Another Sink", "id2.monitor", True),
                    _FakeMic("Some Real Mic", "input.dev", False),
                ]
            ),
        },
    )
    monkeypatch.setattr(am_module, "sc", fake_sc)

    m = AudioMeter()
    mic = m._open_capture()
    assert mic.name.startswith("Monitor of")
