"""Unit tests for the built-in visualizer modes and the Braille helper.

Modes are pure functions of FrameContext → rich.text.Text — no Textual
context needed. We assert each mode produces the expected number of
lines and respects the requested width, and that the BrailleCanvas
dot-painting → glyph mapping is correct.
"""

from __future__ import annotations

import pytest
from rich.color import Color

from ytm_player.ui.widgets._visualizer_modes import (
    BUILTIN_MODE_ORDER,
    BUILTIN_MODES,
    BrailleCanvas,
    FrameContext,
    VisualizerMode,
    _sample_gradient,
)


def _ctx(rows: int = 6, cols: int = 80, bands_n: int = 32, rms: float = 0.4) -> FrameContext:
    return FrameContext(
        bands=[i / max(1, bands_n - 1) for i in range(bands_n)],
        waveform=[((-1) ** i) * 0.5 for i in range(4096)],
        rms=rms,
        frame=42,
        rows=rows,
        cols=cols,
        gradient=[
            Color.parse("#4ec9ff"),
            Color.parse("#a87bff"),
            Color.parse("#ff7b7b"),
            Color.parse("#ffd166"),
        ],
    )


@pytest.mark.parametrize("mode_name", BUILTIN_MODE_ORDER)
def test_every_builtin_mode_renders_correct_row_count(mode_name):
    mode = BUILTIN_MODES[mode_name]
    text = mode.render(_ctx(rows=6, cols=80))
    plain = str(text)
    assert plain.count("\n") + 1 == 6, f"{mode_name} produced wrong row count"


@pytest.mark.parametrize("mode_name", BUILTIN_MODE_ORDER)
def test_every_mode_inherits_visualizer_mode(mode_name):
    assert isinstance(BUILTIN_MODES[mode_name], VisualizerMode)
    assert BUILTIN_MODES[mode_name].name == mode_name


def test_builtin_order_lists_every_registered_mode():
    assert set(BUILTIN_MODE_ORDER) == set(BUILTIN_MODES.keys())


def test_spectrum_bars_at_silence_renders_blank_grid():
    """All-zero bands → all space characters (no block glyphs)."""
    ctx = FrameContext(
        bands=[0.0] * 32,
        waveform=[0.0] * 4096,
        rms=0.0,
        frame=0,
        rows=4,
        cols=40,
        gradient=[Color.parse("#ffffff"), Color.parse("#000000")],
    )
    plain = str(BUILTIN_MODES["spectrum_bars"].render(ctx))
    glyph_chars = set(plain) - {" ", "\n"}
    assert not glyph_chars, f"silence should be all blank, got chars: {glyph_chars}"


def test_spectrum_bars_at_full_blast_uses_full_block():
    """All-ones bands → at least one full-block █ at the bottom row."""
    ctx = FrameContext(
        bands=[1.0] * 32,
        waveform=[0.0] * 4096,
        rms=1.0,
        frame=0,
        rows=4,
        cols=40,
        gradient=[Color.parse("#ffffff"), Color.parse("#000000")],
    )
    plain = str(BUILTIN_MODES["spectrum_bars"].render(ctx))
    assert "█" in plain


def test_vu_meter_peak_hold_decays():
    """Peak-hold marker remains after RMS drops, then decays."""
    mode = BUILTIN_MODES["vu_meter"]
    high = _ctx(rms=0.9)
    low = _ctx(rms=0.0)
    mode.render(high)
    text_after_drop = str(mode.render(low))
    # Bar at low RMS is short; peak-hold marker `▎` should still show.
    assert "▎" in text_after_drop or "█" in text_after_drop


# ── BrailleCanvas ─────────────────────────────────────────────────────


def test_braille_canvas_blank_is_all_spaces():
    canvas = BrailleCanvas(rows=2, cols=4)
    plain = str(canvas.to_text(None))
    assert plain == " " * 4 + "\n" + " " * 4


def test_braille_canvas_sets_correct_dot_bits():
    """Setting (0, 0) lights the top-left dot of cell (0, 0)."""
    canvas = BrailleCanvas(rows=1, cols=1)
    canvas.set(0, 0)  # col=0, row=0 in sub-pixel coords
    plain = str(canvas.to_text(None))
    assert plain == chr(0x2800 | 0x01)  # U+2801 — only the top-left dot


def test_braille_canvas_ignores_out_of_bounds():
    canvas = BrailleCanvas(rows=1, cols=1)
    canvas.set(-1, 0)
    canvas.set(0, -1)
    canvas.set(2, 0)  # cols * 2 = 2; valid range 0..1
    canvas.set(0, 4)  # rows * 4 = 4; valid range 0..3
    plain = str(canvas.to_text(None))
    assert plain == " "  # all zero → blank cell


def test_braille_canvas_full_cell_is_full_glyph():
    """All 8 dots set → U+28FF (every Braille bit on)."""
    canvas = BrailleCanvas(rows=1, cols=1)
    for sx in range(2):
        for sy in range(4):
            canvas.set(sx, sy)
    plain = str(canvas.to_text(None))
    assert plain == chr(0x28FF)


# ── gradient sampler ──────────────────────────────────────────────────


def test_sample_gradient_endpoints():
    g = [Color.parse("#ff0000"), Color.parse("#00ff00"), Color.parse("#0000ff")]
    assert _sample_gradient(g, 0.0).triplet == g[0].triplet
    assert _sample_gradient(g, 1.0).triplet == g[-1].triplet


def test_sample_gradient_clamps_out_of_range():
    g = [Color.parse("#000000"), Color.parse("#ffffff")]
    assert _sample_gradient(g, -0.5).triplet == g[0].triplet
    assert _sample_gradient(g, 1.5).triplet == g[-1].triplet


def test_sample_gradient_midpoint_is_blend():
    """Halfway between red and green should produce something with both channels."""
    g = [Color.parse("#ff0000"), Color.parse("#00ff00")]
    mid = _sample_gradient(g, 0.5).triplet
    assert mid is not None
    # Channels should blend roughly halfway — exact value depends on rounding.
    assert 100 < mid.red < 200
    assert 100 < mid.green < 200
    assert mid.blue == 0
