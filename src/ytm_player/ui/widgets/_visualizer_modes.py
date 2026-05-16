"""Built-in visualizer modes and the contract for user plugins.

A "mode" is a class with a ``render(ctx) -> Text`` method that turns a
frame's worth of audio analysis data into a multi-line Rich Text. The
Visualizer widget calls render() once per redraw at settings.visualizer.fps.

Shipped modes (port of cliamp's most-loved set):
    - spectrum_bars   ▁▂▃▄▅▆▇█ classic FFT bins, top-aligned
    - mirror_bars     bars mirrored around the centre row
    - pixel_spectrum  half-block colour gradient (two bins per cell)
    - waveform        time-domain via Braille (2x4 sub-pixel)
    - oscilloscope    XY Lissajous via Braille
    - vu_meter        smoothed peak meter with decay

Plugin contract — drop a `.py` file under ~/.config/ytm-player/visualizers/
defining a subclass of VisualizerMode with a unique `name` ClassVar:

    from ytm_player.ui.widgets._visualizer_modes import VisualizerMode, FrameContext
    from rich.text import Text

    class MyMode(VisualizerMode):
        name = "my_mode"

        def render(self, ctx: FrameContext) -> Text:
            return Text(f"frame {ctx.frame}: rms={ctx.rms:.2f}")

User plugins appear in the cycle alongside built-ins. Errors raised by
plugins are caught by the widget and logged — they never bring down the
TUI.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from rich.color import Color
from rich.style import Style
from rich.text import Text

# 8-level vertical ramp used by spectrum_bars and mirror_bars.
_BAR_LEVELS = (" ", "▁", "▂", "▃", "▄", "▅", "▆", "▇", "█")

# Half-blocks for pixel_spectrum / waveform fills.
_BLOCK_UPPER = "▀"  # foreground colour = top half, background colour = bottom half

# Braille glyph base + dot bitmap.
# Each character cell is a 2-column × 4-row sub-pixel grid; dots are bits.
_BRAILLE_BASE = 0x2800
_BRAILLE_DOT = (
    (0x01, 0x02, 0x04, 0x40),  # col 0, rows 0..3
    (0x08, 0x10, 0x20, 0x80),  # col 1, rows 0..3
)


@dataclass
class FrameContext:
    """Snapshot of audio + render geometry passed to each mode per frame."""

    bands: list[float]
    waveform: list[float]
    rms: float
    frame: int
    rows: int
    cols: int
    # Gradient stops (left-to-right). At least 2 colors. Themes can swap
    # this; built-in fallback is a four-stop Winamp ramp.
    gradient: list[Color]


class VisualizerMode(ABC):
    """Abstract base for built-in modes and user plugins.

    Subclasses set `name` and implement `render(ctx)`. Optional hooks
    `init(rows, cols)` and `destroy()` exist for plugins that need
    persistent state (particle systems, etc.).
    """

    name: ClassVar[str] = ""

    def init(self, rows: int, cols: int) -> None:
        """Optional one-time setup when the mode becomes active."""

    def destroy(self) -> None:
        """Optional teardown when the mode is switched away from."""

    @abstractmethod
    def render(self, ctx: FrameContext) -> Text:
        """Render one frame. Returns a multi-line Rich Text."""


# ── helper: Braille canvas ───────────────────────────────────────────


class BrailleCanvas:
    """Tiny 2x4-sub-pixel painter that flattens to Braille glyphs.

    Used by waveform and oscilloscope. Plot dots at sub-pixel coords,
    then call `to_text(style)` to render the result.
    """

    __slots__ = ("rows", "cols", "_grid")

    def __init__(self, rows: int, cols: int) -> None:
        self.rows = rows
        self.cols = cols
        self._grid = bytearray(rows * cols)

    def set(self, sub_x: int, sub_y: int) -> None:
        if not (0 <= sub_x < self.cols * 2 and 0 <= sub_y < self.rows * 4):
            return
        cell_x = sub_x // 2
        cell_y = sub_y // 4
        col_in_cell = sub_x & 1
        row_in_cell = sub_y & 3
        self._grid[cell_y * self.cols + cell_x] |= _BRAILLE_DOT[col_in_cell][row_in_cell]

    def to_text(self, style: Style) -> Text:
        out = Text()
        cols = self.cols
        for r in range(self.rows):
            if r > 0:
                out.append("\n")
            row_offset = r * cols
            row_chars: list[str] = []
            for c in range(cols):
                bits = self._grid[row_offset + c]
                row_chars.append(chr(_BRAILLE_BASE + bits) if bits else " ")
            out.append("".join(row_chars), style=style)
        return out


# ── gradient sampling helper (shared by colour modes) ────────────────


def _sample_gradient(stops: list[Color], t: float) -> Color:
    t = max(0.0, min(1.0, t))
    if t >= 1.0:
        return stops[-1]
    scaled = t * (len(stops) - 1)
    i = int(scaled)
    frac = scaled - i
    a = stops[i].triplet
    b = stops[i + 1].triplet
    if a is None or b is None:
        return stops[i]
    return Color.from_rgb(
        int(a.red + (b.red - a.red) * frac),
        int(a.green + (b.green - a.green) * frac),
        int(a.blue + (b.blue - a.blue) * frac),
    )


def _glyph_at_row(height_subcells: int, row_top: int, row_bot: int) -> str:
    if height_subcells <= row_bot:
        return _BAR_LEVELS[0]
    if height_subcells >= row_top:
        return _BAR_LEVELS[8]
    return _BAR_LEVELS[height_subcells - row_bot]


# ── built-in modes ───────────────────────────────────────────────────


class SpectrumBars(VisualizerMode):
    """Classic FFT bins, one column-block per band, bottom-aligned."""

    name = "spectrum_bars"

    def render(self, ctx: FrameContext) -> Text:
        bands, rows, cols = ctx.bands, ctx.rows, ctx.cols
        n = len(bands) or 1
        cols_per_band = max(1, cols // n)
        used = cols_per_band * n
        if used > cols:
            cols_per_band = max(1, cols_per_band - 1)
            used = cols_per_band * n
        sub_levels = rows * 8
        heights = [int(round(b * sub_levels)) for b in bands]
        colors = [_sample_gradient(ctx.gradient, i / max(1, n - 1)) for i in range(n)]
        pad = cols - used
        out = Text()
        for r in range(rows):
            if r > 0:
                out.append("\n")
            row_top = (rows - r) * 8
            row_bot = row_top - 8
            for i, h in enumerate(heights):
                glyph = _glyph_at_row(h, row_top, row_bot)
                out.append(glyph * cols_per_band, style=Style(color=colors[i]))
            if pad > 0:
                out.append(" " * pad)
        return out


class MirrorBars(VisualizerMode):
    """Spectrum mirrored around the horizontal midline.

    Top half is inverted (full-block at the middle row, descending up);
    bottom half is normal (full-block at the middle row, descending down).
    Produces the classic "audio wave" silhouette look.
    """

    name = "mirror_bars"

    def render(self, ctx: FrameContext) -> Text:
        bands, rows, cols = ctx.bands, ctx.rows, ctx.cols
        n = len(bands) or 1
        # Each half gets rows//2 cells. Reserve middle row for "always on".
        half = max(1, rows // 2)
        cols_per_band = max(1, cols // n)
        used = cols_per_band * n
        if used > cols:
            cols_per_band = max(1, cols_per_band - 1)
            used = cols_per_band * n
        sub_levels = half * 8
        heights = [int(round(b * sub_levels)) for b in bands]
        colors = [_sample_gradient(ctx.gradient, i / max(1, n - 1)) for i in range(n)]
        pad = cols - used
        out = Text()
        # Top half — invert: row 0 is "highest", growing downward into the middle.
        for r in range(half):
            if r > 0:
                out.append("\n")
            row_top = (r + 1) * 8
            row_bot = r * 8
            for i, h in enumerate(heights):
                glyph = _glyph_at_row(h, row_top, row_bot)
                # Vertically flip the glyph for the top half — easier than
                # building a separate flipped ramp.
                glyph = _flip_block(glyph)
                out.append(glyph * cols_per_band, style=Style(color=colors[i]))
            if pad > 0:
                out.append(" " * pad)
        # Bottom half — standard descending bars.
        for r in range(rows - half):
            out.append("\n")
            row_top = (half - r) * 8
            row_bot = row_top - 8
            for i, h in enumerate(heights):
                glyph = _glyph_at_row(h, row_top, row_bot)
                out.append(glyph * cols_per_band, style=Style(color=colors[i]))
            if pad > 0:
                out.append(" " * pad)
        return out


def _flip_block(glyph: str) -> str:
    """Vertical mirror of the 8-level lower-block ramp."""
    # Upper-block ramp matching _BAR_LEVELS' coverage.
    flip_map = {
        " ": " ",
        "▁": "▔",
        "▂": "🮂",
        "▃": "🮃",
        "▄": "▀",
        "▅": "🮄",
        "▆": "🮅",
        "▇": "🮆",
        "█": "█",
    }
    return flip_map.get(glyph, glyph)


class PixelSpectrum(VisualizerMode):
    """Pixel-style FFT bars using half-blocks for 2x vertical resolution.

    Each cell encodes two sub-rows: foreground colour = top half (or "lit"),
    background colour = bottom half. Result: same nominal rows count
    visualizes 2*rows pixel rows with smooth colour transitions.
    """

    name = "pixel_spectrum"

    def render(self, ctx: FrameContext) -> Text:
        bands, rows, cols = ctx.bands, ctx.rows, ctx.cols
        n = len(bands) or 1
        # Half-block doubles vertical resolution: 2*rows pixel rows.
        pixel_rows = rows * 2
        cols_per_band = max(1, cols // n)
        used = cols_per_band * n
        if used > cols:
            cols_per_band = max(1, cols_per_band - 1)
            used = cols_per_band * n
        # Per-band pixel height.
        heights = [int(round(b * pixel_rows)) for b in bands]
        # Per-pixel-row colours: gradient bottom→top so the bar gets warmer
        # the higher it climbs.
        row_colors = [
            _sample_gradient(ctx.gradient, 1.0 - i / max(1, pixel_rows - 1))
            for i in range(pixel_rows)
        ]
        pad = cols - used
        out = Text()
        for cell_row in range(rows):
            if cell_row > 0:
                out.append("\n")
            top_pixel = cell_row * 2
            bot_pixel = top_pixel + 1
            for i, h in enumerate(heights):
                # "lit" if the pixel row is within bar height (measured from bottom).
                lit_top = (pixel_rows - top_pixel) <= h
                lit_bot = (pixel_rows - bot_pixel) <= h
                if lit_top and lit_bot:
                    glyph = _BLOCK_UPPER  # ▀
                    style = Style(color=row_colors[top_pixel], bgcolor=row_colors[bot_pixel])
                elif lit_top:
                    glyph = _BLOCK_UPPER
                    style = Style(color=row_colors[top_pixel])
                elif lit_bot:
                    glyph = "▄"
                    style = Style(color=row_colors[bot_pixel])
                else:
                    glyph = " "
                    style = Style()
                out.append(glyph * cols_per_band, style=style)
            if pad > 0:
                out.append(" " * pad)
        return out


class Waveform(VisualizerMode):
    """Time-domain oscilloscope with Braille sub-pixel resolution."""

    name = "waveform"

    def render(self, ctx: FrameContext) -> Text:
        wf = ctx.waveform
        rows, cols = ctx.rows, ctx.cols
        canvas = BrailleCanvas(rows, cols)
        if not wf:
            return canvas.to_text(Style(color=ctx.gradient[len(ctx.gradient) // 2]))
        sub_cols = cols * 2
        sub_rows = rows * 4
        n = len(wf)
        mid = (sub_rows - 1) / 2.0
        scale = mid * 0.95  # leave 5% headroom so peaks don't clip the canvas
        # Walk waveform left-to-right, sample-and-hold to sub-columns.
        for sx in range(sub_cols):
            t = sx / max(1, sub_cols - 1)
            idx = int(t * (n - 1))
            v = wf[idx]
            sy = int(round(mid - v * scale))
            canvas.set(sx, sy)
        # Connect consecutive samples with vertical fills for a clean line.
        # Without this we get a dotted/scattered trace at high amplitudes.
        last_sy = int(round(mid - wf[0] * scale))
        for sx in range(1, sub_cols):
            t = sx / max(1, sub_cols - 1)
            idx = int(t * (n - 1))
            v = wf[idx]
            sy = int(round(mid - v * scale))
            lo, hi = (last_sy, sy) if last_sy < sy else (sy, last_sy)
            for fill in range(lo, hi + 1):
                canvas.set(sx, fill)
            last_sy = sy
        accent = _sample_gradient(ctx.gradient, 0.55)
        return canvas.to_text(Style(color=accent))


class Oscilloscope(VisualizerMode):
    """Lissajous XY scatter — sample N pairs (x[i], x[i+1]) → (x, y)."""

    name = "oscilloscope"

    def render(self, ctx: FrameContext) -> Text:
        wf = ctx.waveform
        rows, cols = ctx.rows, ctx.cols
        canvas = BrailleCanvas(rows, cols)
        if len(wf) < 2:
            return canvas.to_text(Style(color=ctx.gradient[-1]))
        sub_cols = cols * 2
        sub_rows = rows * 4
        cx = (sub_cols - 1) / 2.0
        cy = (sub_rows - 1) / 2.0
        scale_x = cx * 0.95
        scale_y = cy * 0.95
        # Downsample so we don't spam dots — at 4096 samples × full plot,
        # most cells fill solid. Stride based on cell area.
        target = sub_cols * sub_rows // 4  # average density
        stride = max(1, (len(wf) - 1) // target)
        for i in range(0, len(wf) - 1, stride):
            x = wf[i]
            y = wf[i + 1]
            sx = int(round(cx + x * scale_x))
            sy = int(round(cy - y * scale_y))
            canvas.set(sx, sy)
        accent = _sample_gradient(ctx.gradient, 0.8)
        return canvas.to_text(Style(color=accent))


class VuMeter(VisualizerMode):
    """Smoothed peak meter with peak-hold dot.

    Renders a single horizontal bar centred vertically. Width is RMS scaled
    to ~80% of the available columns, leaving headroom for visible peak.
    Peak-hold marker decays slowly.
    """

    name = "vu_meter"

    def __init__(self) -> None:
        self._peak_hold = 0.0
        self._peak_hold_ttl = 0  # frames before decay
        self._peak_hold_decay = 0.97

    def render(self, ctx: FrameContext) -> Text:
        rows, cols = ctx.rows, ctx.cols
        # rms is smoothed already by AudioMeter — square-root it for VU-style
        # response (more perceptually linear than RMS or peak).
        level = math.sqrt(max(0.0, min(1.0, ctx.rms * 4.0)))  # boost dB floor
        level = max(0.0, min(1.0, level))
        if level > self._peak_hold:
            self._peak_hold = level
            self._peak_hold_ttl = 30
        elif self._peak_hold_ttl > 0:
            self._peak_hold_ttl -= 1
        else:
            self._peak_hold *= self._peak_hold_decay

        # Inner column span is cols-2 cells (after the │ │ borders) — peak
        # marker must land at most at the last inner column, not one past it.
        inner = max(0, cols - 2)
        bar_cols = max(0, min(inner, int(round(level * inner))))
        peak_col = max(0, min(inner - 1, int(round(self._peak_hold * inner)))) if inner else 0
        mid_row = rows // 2
        out = Text()
        for r in range(rows):
            if r > 0:
                out.append("\n")
            if r != mid_row and r != mid_row - 1 and r != mid_row + 1:
                out.append(" " * cols)
                continue
            line = Text()
            line.append("│", style=Style(color="grey50"))
            for c in range(cols - 2):
                t = c / max(1, cols - 3)
                color = _sample_gradient(ctx.gradient, t)
                if c < bar_cols:
                    line.append("█", style=Style(color=color))
                elif c == peak_col and self._peak_hold > 0.01:
                    line.append("▎", style=Style(color="white"))
                else:
                    line.append(" ")
            line.append("│", style=Style(color="grey50"))
            out.append_text(line)
        return out


# ── registry ─────────────────────────────────────────────────────────


def _builtin_instances() -> dict[str, VisualizerMode]:
    return {
        SpectrumBars.name: SpectrumBars(),
        MirrorBars.name: MirrorBars(),
        PixelSpectrum.name: PixelSpectrum(),
        Waveform.name: Waveform(),
        Oscilloscope.name: Oscilloscope(),
        VuMeter.name: VuMeter(),
    }


BUILTIN_MODES: dict[str, VisualizerMode] = _builtin_instances()
BUILTIN_MODE_ORDER: list[str] = [
    SpectrumBars.name,
    MirrorBars.name,
    PixelSpectrum.name,
    Waveform.name,
    Oscilloscope.name,
    VuMeter.name,
]
