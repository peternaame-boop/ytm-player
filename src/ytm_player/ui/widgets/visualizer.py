"""Cliamp-style audio visualizer widget.

A thin Textual widget that pulls log-binned FFT bands from AudioMeter at
display rate and renders a chosen mode. Modes are plug-in friendly: the
built-in set lives in `ui/widgets/_visualizer_modes.py` (added in Phase 2);
Phase 1 ships only `spectrum_bars` inline so audio capture can be verified
end-to-end before broadening.

Render contract for modes (informational — interface formalised in Phase 2):

    mode_fn(bands, waveform, rms, frame, rows, cols, theme) -> rich.text.Text

The widget keeps its own redraw cadence (settings.visualizer.fps) and
polls the meter's lock-free latest arrays each tick. The audio thread runs
independently at ~43 Hz; the widget just samples whatever's freshest.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rich.color import Color
from rich.style import Style
from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

from ytm_player.config.settings import get_settings
from ytm_player.services.audio_meter import AudioMeter

if TYPE_CHECKING:
    from textual.timer import Timer

logger = logging.getLogger(__name__)

# Unicode block ramps for vertical bars.
# 9 levels: 0 (space) through 8 (full block ▏▎▍▌▋▊▉█-equivalent vertically).
_BAR_LEVELS = (" ", "▁", "▂", "▃", "▄", "▅", "▆", "▇", "█")
# Half-blocks for the "pixel" gradient mode (Phase 2 ships variants).
_BLOCK_TOP = "▀"  # ▀
_BLOCK_BOT = "▄"  # ▄

# Gradient endpoints used when a theme doesn't supply a custom accent ramp.
# Cool-blue → magenta → warm-orange, classic Winamp coloring.
_FALLBACK_GRADIENT = ("#4ec9ff", "#a87bff", "#ff7b7b", "#ffd166")


class Visualizer(Widget):
    """Audio visualizer for the playback area.

    Sits between the page area and the playback bar. Hidden by default
    (settings.visualizer.enabled = false), toggled via the `v` keybinding.

    Lifecycle:
        on_mount    → start AudioMeter + set_interval
        on_unmount  → stop AudioMeter + remove interval
    """

    DEFAULT_CSS = """
    Visualizer {
        height: auto;
        width: 100%;
        background: transparent;
        padding: 0 1;
    }
    Visualizer.-hidden {
        display: none;
    }
    """

    mode: reactive[str] = reactive("spectrum_bars")
    enabled: reactive[bool] = reactive(False)

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        settings = get_settings().visualizer
        self.enabled = settings.enabled
        self.mode = settings.mode
        self._fps = max(15, min(60, settings.fps))
        self._height_rows = max(2, min(20, settings.height))
        self._frame: int = 0
        self._meter = AudioMeter()
        self._timer: Timer | None = None
        # Pre-render the fallback gradient as Rich Colors once, not per frame.
        self._gradient: list[Color] = [Color.parse(c) for c in _FALLBACK_GRADIENT]
        # Reflect height into styles immediately so layout reserves the space.
        self.styles.height = self._height_rows
        if not self.enabled:
            self.add_class("-hidden")

    # ── lifecycle ─────────────────────────────────────────────────────

    def on_mount(self) -> None:
        if self.enabled:
            self._start()

    def on_unmount(self) -> None:
        self._stop()

    def _start(self) -> None:
        if not self._meter.available:
            logger.info(
                "Visualizer enabled but viz extras missing — see `pip install ytm-player[viz]`"
            )
        self._meter.start()
        if self._timer is None:
            self._timer = self.set_interval(1 / self._fps, self._tick, pause=False)

    def _stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._meter.stop()

    # ── reactives ─────────────────────────────────────────────────────

    def watch_enabled(self, value: bool) -> None:
        if value:
            self.remove_class("-hidden")
            self._start()
        else:
            self.add_class("-hidden")
            self._stop()
            self.refresh()

    def watch_mode(self, _value: str) -> None:
        self.refresh()

    # ── per-frame tick ────────────────────────────────────────────────

    def _tick(self) -> None:
        self._frame += 1
        self.refresh()

    # ── render ────────────────────────────────────────────────────────

    def render(self) -> Text:
        if not self.enabled:
            return Text("")
        cols = max(8, self.size.width - 2)  # account for padding
        rows = max(2, min(self._height_rows, self.size.height or self._height_rows))
        bands = self._meter.bands
        rms = self._meter.rms
        waveform = self._meter.waveform

        try:
            return self._render_mode(self.mode, bands, waveform, rms, self._frame, rows, cols)
        except Exception:
            logger.exception("Visualizer render failed (mode=%s); blanking", self.mode)
            return Text("\n" * (rows - 1))

    def _render_mode(
        self,
        mode: str,
        bands: list[float],
        waveform: list[float],
        rms: float,
        frame: int,
        rows: int,
        cols: int,
    ) -> Text:
        # Phase 1 ships one mode inline. Phase 2 swaps this dispatch for a
        # registry populated by _visualizer_modes + the plugin loader.
        if mode == "spectrum_bars":
            return self._render_spectrum_bars(bands, rows, cols)
        # Unknown / future-Phase modes render an informational placeholder
        # rather than blanking entirely.
        return self._render_placeholder(mode, rows, cols)

    # ── built-in mode: spectrum_bars ──────────────────────────────────

    def _render_spectrum_bars(self, bands: list[float], rows: int, cols: int) -> Text:
        """Classic FFT spectrum: one column per band, vertical block stack.

        Each band occupies a column. Height is bands[i] * (rows * 8) so we
        get 8 sub-cell vertical resolution via the lower-block ramp.
        """
        band_count = len(bands) or 1
        # If there are more cols than bands, widen each band by repeating its
        # column. If more bands than cols, sample-and-hold downsample.
        cols_per_band = max(1, cols // band_count)
        # Recompute effective column count to keep the bar block aligned.
        used_cols = cols_per_band * band_count
        if used_cols > cols:
            cols_per_band = max(1, cols_per_band - 1)
            used_cols = cols_per_band * band_count

        # Per-band height in 8-level sub-cell units.
        sub_levels = rows * 8
        heights = [int(round(b * sub_levels)) for b in bands]

        # Build the canvas row by row, top to bottom.
        lines: list[Text] = []
        for row_idx in range(rows):
            # Each row covers 8 sub-cell levels; row 0 is topmost.
            row_top_sub = (rows - row_idx) * 8
            row_bot_sub = row_top_sub - 8
            line = Text()
            for i, h in enumerate(heights):
                # Pick the ramp char for this band at this row.
                if h <= row_bot_sub:
                    glyph = _BAR_LEVELS[0]
                elif h >= row_top_sub:
                    glyph = _BAR_LEVELS[8]
                else:
                    glyph = _BAR_LEVELS[h - row_bot_sub]
                color = self._gradient_color(i / max(1, band_count - 1))
                style = Style(color=color)
                line.append(glyph * cols_per_band, style=style)
            # Pad to true visible width so right-edge artifacts don't bleed
            # the prior frame.
            pad = cols - used_cols
            if pad > 0:
                line.append(" " * pad)
            lines.append(line)

        out = Text()
        for i, line in enumerate(lines):
            if i > 0:
                out.append("\n")
            out.append_text(line)
        return out

    def _render_placeholder(self, mode: str, rows: int, cols: int) -> Text:
        msg = f"visualizer mode '{mode}' not in Phase 1 build"
        msg = msg[:cols]
        out = Text()
        blank = rows // 2
        for _ in range(blank):
            out.append("\n")
        out.append(msg.center(cols), style=Style(color="grey50"))
        for _ in range(rows - blank - 1):
            out.append("\n")
        return out

    # ── color helpers ─────────────────────────────────────────────────

    def _gradient_color(self, t: float) -> Color:
        """Sample the 4-stop fallback gradient at t in [0, 1]."""
        t = max(0.0, min(1.0, t))
        stops = self._gradient
        if t >= 1.0:
            return stops[-1]
        scaled = t * (len(stops) - 1)
        i = int(scaled)
        frac = scaled - i
        c0 = stops[i].triplet
        c1 = stops[i + 1].triplet
        if c0 is None or c1 is None:
            return stops[i]
        r = int(c0.red + (c1.red - c0.red) * frac)
        g = int(c0.green + (c1.green - c0.green) * frac)
        b = int(c0.blue + (c1.blue - c0.blue) * frac)
        return Color.from_rgb(r, g, b)

    # ── public actions ────────────────────────────────────────────────

    def toggle(self) -> None:
        """Flip enabled. Called by the 'v' keybinding."""
        self.enabled = not self.enabled

    def set_mode(self, mode: str) -> None:
        self.mode = mode
