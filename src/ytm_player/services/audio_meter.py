"""Realtime audio analysis for the visualizer widget.

Captures from the system's default-sink monitor via PulseAudio/PipeWire
(soundcard library) on a background thread, runs a Hann-windowed FFT, and
exposes log-binned, attack/release-smoothed bands plus a raw waveform tail.

The pipeline is opt-in: `pip install ytm-player[viz]` adds soundcard and
numpy. With the extras missing, AudioMeter falls back to a no-op shape
that returns flat-zero arrays — visualizers render blank instead of
crashing.

Why default-sink monitor: mpv hands audio to PipeWire; we tap the loopback
of whatever sink mpv plays to. One pipeline covers YT Music, internet
stations, and local files. Trade-off: notifications and other apps mixed
into the same sink will show up in the visualizer. To isolate mpv only,
load a module-combine-sink — see docs/configuration.md.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import TYPE_CHECKING, Any

from ytm_player.config.settings import get_settings

if TYPE_CHECKING:
    import numpy as np


logger = logging.getLogger(__name__)

# Soundcard 0.4.x crashes at import time if sys.argv has no script slot
# (which happens under `python -c` and some embed scenarios). Patch before
# importing so the module loads cleanly inside Textual.
if len(sys.argv) < 2:  # pragma: no cover - environmental
    sys.argv.append(__file__)

try:
    import numpy as np  # type: ignore[import-not-found]
    import soundcard as sc  # type: ignore[import-not-found]

    _VIZ_AVAILABLE = True
    _VIZ_IMPORT_ERROR: Exception | None = None
except Exception as _exc:  # broad: numpy / soundcard / PulseAudio backend
    _VIZ_AVAILABLE = False
    _VIZ_IMPORT_ERROR = _exc
    np = None  # type: ignore[assignment]
    sc = None  # type: ignore[assignment]


SAMPLE_RATE = 44100
BLOCK_SIZE = 1024  # ~23 ms per block @ 44.1 kHz
FFT_SIZE = 2048  # zero-pad blocks for finer bin resolution
WAVEFORM_TAIL_BLOCKS = 4  # ~92 ms of mono samples retained for waveform mode
FREQ_LO = 50.0  # Hz, lower bound of log-binned spectrum
FREQ_HI = 18000.0  # Hz, upper bound (humans tap out earlier; 18k is plenty)
# Bandpass display range in dBFS — values below FLOOR_DB render as silent,
# values above CEIL_DB clip to 1.0. Tuned so a typical pop master sits in
# the middle of the dynamic range without the visualizer pegging.
FLOOR_DB = -70.0
CEIL_DB = -10.0


class AudioMeter:
    """Singleton background audio analyzer for the visualizer widget.

    Lifecycle:
        meter = AudioMeter()
        meter.start()              # spawns capture thread
        bands = meter.bands        # 32-float list in [0, 1], lock-free read
        wave = meter.waveform      # ~4 KB float32 mono tail in [-1, 1]
        meter.stop()               # joins capture thread

    The capture thread is daemonised; if you forget to call stop() the
    process can still exit cleanly.
    """

    _instance: AudioMeter | None = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> AudioMeter:
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True

        settings = get_settings().visualizer
        self._bands_count = max(8, min(128, settings.bands))
        self._smoothing = max(0.0, min(0.95, settings.smoothing))
        self._capture_device = settings.capture_device

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        # Lock-free reads: replace the list/array pointer atomically each block.
        # Python list assignment is atomic under the GIL — readers always see
        # either the old or the new array, never a torn intermediate.
        self._bands: list[float] = [0.0] * self._bands_count
        self._waveform: list[float] = [0.0] * (BLOCK_SIZE * WAVEFORM_TAIL_BLOCKS)
        self._rms: float = 0.0
        self._frame_counter: int = 0
        self._last_block_at: float = 0.0

        # Pre-computed FFT scaffolding (only when viz extras present).
        self._hann: Any = None
        self._bin_edges: Any = None
        self._mono_buf: Any = None

        if _VIZ_AVAILABLE:
            self._hann = np.hanning(FFT_SIZE).astype(np.float32)
            self._bin_edges = self._compute_log_bin_edges()
            self._mono_buf = np.zeros(FFT_SIZE, dtype=np.float32)

    # ── public API ────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        """True when the viz extras imported and capture can be attempted."""
        return _VIZ_AVAILABLE

    @property
    def bands(self) -> list[float]:
        """Latest log-binned, normalized spectrum bands in [0, 1].

        Lock-free read. Always returns a list of length self._bands_count.
        """
        return self._bands

    @property
    def waveform(self) -> list[float]:
        """Latest mono PCM tail (~4 blocks) in [-1, 1]. Lock-free read."""
        return self._waveform

    @property
    def rms(self) -> float:
        """Smoothed RMS of the latest block in [0, 1]."""
        return self._rms

    @property
    def frame_counter(self) -> int:
        """Monotonic counter incremented per FFT block — useful for plugins."""
        return self._frame_counter

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Begin capturing on a background thread. Idempotent."""
        if not _VIZ_AVAILABLE:
            logger.warning(
                "Visualizer extras not installed (%s) — `pip install ytm-player[viz]` "
                "to enable. Falling back to silent meter.",
                _VIZ_IMPORT_ERROR,
            )
            return
        if self.is_running():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._capture_loop,
            name="ytm-audio-meter",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Audio meter started (bands=%d, smoothing=%.2f)", self._bands_count, self._smoothing
        )

    def stop(self) -> None:
        """Signal the capture thread to exit and join briefly."""
        if not self.is_running():
            return
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.5)
            self._thread = None
        logger.info("Audio meter stopped")

    # ── capture loop ──────────────────────────────────────────────────

    def _capture_loop(self) -> None:
        try:
            mic = self._open_capture()
        except Exception:
            logger.exception("Audio meter: failed to open capture device")
            return

        logger.info("Audio meter capturing from %r", getattr(mic, "name", "?"))
        try:
            with mic.recorder(samplerate=SAMPLE_RATE, channels=2, blocksize=BLOCK_SIZE) as recorder:
                while not self._stop_event.is_set():
                    try:
                        samples = recorder.record(numframes=BLOCK_SIZE)
                    except Exception:
                        logger.exception("Audio meter: record() failed; sleeping then retrying")
                        time.sleep(0.25)
                        continue
                    self._on_block(samples)
        except Exception:
            logger.exception("Audio meter: capture loop crashed")

    def _open_capture(self) -> Any:
        """Find the loopback monitor for the default sink (or override)."""
        if self._capture_device and self._capture_device.lower() != "auto":
            # User override — soundcard accepts either node name or id.
            return sc.get_microphone(self._capture_device, include_loopback=True)

        default_speaker = sc.default_speaker()
        default_name = default_speaker.name

        # Strategy: look for a loopback whose id matches the default sink name
        # (PipeWire), or whose human name contains the speaker name. Most
        # systems expose `<sink_name>.monitor`.
        loopbacks = [
            m for m in sc.all_microphones(include_loopback=True) if getattr(m, "isloopback", False)
        ]
        if not loopbacks:
            raise RuntimeError(
                "No loopback monitor devices found. Check PipeWire/PulseAudio is running."
            )

        for m in loopbacks:
            mid = getattr(m, "id", "") or ""
            if default_name in m.name or default_name in mid:
                return m

        # Fall back to the first loopback so the user still sees *something*
        # rather than complete silence.
        logger.warning(
            "Audio meter: default speaker %r has no matching monitor; falling back to %r",
            default_name,
            loopbacks[0].name,
        )
        return loopbacks[0]

    # ── per-block processing ──────────────────────────────────────────

    def _on_block(self, samples: Any) -> None:
        # samples shape: (BLOCK_SIZE, 2). Average to mono float32.
        mono = samples.mean(axis=1).astype(np.float32)

        # Slide waveform tail by one block.
        wf = self._mono_buf  # reuse pre-allocated buffer (FFT_SIZE)
        wf[:-BLOCK_SIZE] = wf[BLOCK_SIZE:]
        wf[-BLOCK_SIZE:] = mono

        # RMS for VU mode + peak indicator.
        block_rms = float(np.sqrt(np.mean(mono * mono))) if mono.size else 0.0
        self._rms = self._smoothing * self._rms + (1 - self._smoothing) * block_rms

        # Hann-windowed FFT on the full FFT_SIZE buffer (BLOCK_SIZE most recent +
        # the prior block worth of samples zero-padded into the older slots).
        windowed = wf * self._hann
        spectrum = np.fft.rfft(windowed)
        mag = np.abs(spectrum).astype(np.float32)

        # Log-binned aggregation: each output band is the max magnitude across
        # its bin edge range. max() gives a punchier visualization than mean()
        # because it preserves transients.
        bands = np.zeros(self._bands_count, dtype=np.float32)
        for i in range(self._bands_count):
            lo, hi = int(self._bin_edges[i]), int(self._bin_edges[i + 1])
            if hi <= lo:
                hi = lo + 1
            bands[i] = mag[lo:hi].max() if hi <= mag.size else 0.0

        # Convert to dB, clamp to display range, normalize to [0, 1].
        # Floor at ~1e-7 to avoid log(0); offset by FFT_SIZE for sane scaling.
        bands = 20.0 * np.log10(np.maximum(bands / FFT_SIZE, 1e-7))
        bands = (bands - FLOOR_DB) / (CEIL_DB - FLOOR_DB)
        bands = np.clip(bands, 0.0, 1.0)

        # Attack/release smoothing: snap up fast, decay slow.
        smoothed = self._apply_smoothing(np.asarray(self._bands, dtype=np.float32), bands)

        # Atomic pointer swap — readers see the new list immediately.
        self._bands = smoothed.tolist()
        # Waveform readers see the latest tail (~92ms). Down-cast slice to list
        # for tooling that can't import numpy.
        self._waveform = wf[-(BLOCK_SIZE * WAVEFORM_TAIL_BLOCKS) :].tolist()
        self._frame_counter += 1
        self._last_block_at = time.monotonic()

    def _apply_smoothing(self, prev: Any, current: Any) -> Any:
        """Attack/release smoothing: rises with attack weight, falls with release weight."""
        # Higher smoothing setting → more inertia on the way down only.
        # On the way up we use a fixed snappy weight so transients register.
        attack = 0.35
        release = self._smoothing
        rising = current > prev
        out = np.where(
            rising,
            attack * current + (1 - attack) * prev,
            release * prev + (1 - release) * current,
        )
        return out.astype(np.float32)

    def _compute_log_bin_edges(self) -> Any:
        """Pre-compute FFT bin indices at log-spaced frequencies."""
        bin_hz = SAMPLE_RATE / FFT_SIZE
        log_lo = np.log10(FREQ_LO)
        log_hi = np.log10(FREQ_HI)
        edges_hz = np.logspace(log_lo, log_hi, self._bands_count + 1)
        edges_bin = (edges_hz / bin_hz).astype(np.int32)
        # Cap upper edge to nyquist bin to avoid out-of-range slicing.
        nyquist_bin = FFT_SIZE // 2
        edges_bin = np.clip(edges_bin, 0, nyquist_bin)
        return edges_bin
