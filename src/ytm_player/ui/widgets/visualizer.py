"""Cliamp-style audio visualizer widget.

A thin Textual widget that pulls log-binned FFT bands from AudioMeter at
display rate and renders the active mode. Modes are pluggable: built-ins
live in ``_visualizer_modes.py``; user drops a ``.py`` file under
``~/.config/ytm-player/visualizers/`` to add their own (see
``_visualizer_plugins.py`` for the contract).

The widget keeps its own redraw cadence (settings.visualizer.fps) and
polls the meter's lock-free latest arrays each tick. The audio thread
runs independently at ~43 Hz; the widget just samples whatever's freshest.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rich.color import Color
from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

from ytm_player.config.settings import get_settings
from ytm_player.services.audio_meter import AudioMeter
from ytm_player.ui.widgets._visualizer_modes import (
    BUILTIN_MODE_ORDER,
    BUILTIN_MODES,
    FrameContext,
    VisualizerMode,
)
from ytm_player.ui.widgets._visualizer_plugins import discover_plugins

if TYPE_CHECKING:
    from textual.timer import Timer

logger = logging.getLogger(__name__)

# Fallback gradient when the theme doesn't supply one.
# Cool-blue → magenta → warm-orange — classic Winamp coloring.
_FALLBACK_GRADIENT = ("#4ec9ff", "#a87bff", "#ff7b7b", "#ffd166")


class Visualizer(Widget):
    """Audio visualizer for the playback area.

    Sits between SelectionInfoBar and PlaybackBar in the bottom stack.
    Hidden by default (settings.visualizer.enabled = false), toggled
    via the `v` keybinding; `V` cycles through registered modes.

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
        self._gradient: list[Color] = [Color.parse(c) for c in _FALLBACK_GRADIENT]
        self._modes: dict[str, VisualizerMode] = dict(BUILTIN_MODES)
        self._mode_order: list[str] = list(BUILTIN_MODE_ORDER)
        self._load_plugins()
        # Reserve height for layout even before first paint.
        self.styles.height = self._height_rows
        if not self.enabled:
            self.add_class("-hidden")
        if self.mode not in self._modes:
            logger.info(
                "Visualizer: configured mode %r unknown — falling back to spectrum_bars",
                self.mode,
            )
            self.mode = "spectrum_bars"

    def _load_plugins(self) -> None:
        try:
            plugins = discover_plugins()
        except Exception:
            logger.exception("Visualizer: plugin discovery raised; continuing with built-ins")
            return
        for name, inst in plugins.items():
            self._modes[name] = inst
            self._mode_order.append(name)
        if plugins:
            logger.info(
                "Visualizer: loaded %d user plugin(s): %s", len(plugins), ", ".join(sorted(plugins))
            )

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
        self._init_mode(self.mode)
        if self._timer is None:
            self._timer = self.set_interval(1 / self._fps, self._tick, pause=False)

    def _stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._meter.stop()
        # Tell the active mode it's leaving so plugins can release state.
        active = self._modes.get(self.mode)
        if active is not None:
            try:
                active.destroy()
            except Exception:
                logger.exception("Visualizer: mode %r destroy() raised", self.mode)

    def _init_mode(self, name: str) -> None:
        active = self._modes.get(name)
        if active is None:
            return
        try:
            active.init(self._height_rows, max(8, self.size.width - 2))
        except Exception:
            logger.exception("Visualizer: mode %r init() raised", name)

    # ── reactives ─────────────────────────────────────────────────────

    def watch_enabled(self, value: bool) -> None:
        if value:
            self.remove_class("-hidden")
            self._start()
        else:
            self.add_class("-hidden")
            self._stop()
            self.refresh()

    def watch_mode(self, value: str) -> None:
        if value in self._modes:
            self._init_mode(value)
        self.refresh()

    # ── per-frame tick ────────────────────────────────────────────────

    def _tick(self) -> None:
        self._frame += 1
        self.refresh()

    # ── render ────────────────────────────────────────────────────────

    def render(self) -> Text:
        if not self.enabled:
            return Text("")
        cols = max(8, self.size.width - 2)  # padding
        rows = max(2, min(self._height_rows, self.size.height or self._height_rows))
        ctx = FrameContext(
            bands=self._meter.bands,
            waveform=self._meter.waveform,
            rms=self._meter.rms,
            frame=self._frame,
            rows=rows,
            cols=cols,
            gradient=self._gradient,
        )
        mode = self._modes.get(self.mode)
        if mode is None:
            return self._render_unknown(ctx)
        try:
            return mode.render(ctx)
        except Exception:
            logger.exception("Visualizer: mode %r render() raised; blanking", self.mode)
            return Text("\n" * (rows - 1))

    def _render_unknown(self, ctx: FrameContext) -> Text:
        msg = f"unknown visualizer mode '{self.mode}'"
        msg = msg[: ctx.cols]
        out = Text()
        blank = ctx.rows // 2
        for _ in range(blank):
            out.append("\n")
        out.append(msg.center(ctx.cols))
        for _ in range(ctx.rows - blank - 1):
            out.append("\n")
        return out

    # ── public actions ────────────────────────────────────────────────

    def toggle(self) -> None:
        self.enabled = not self.enabled

    def set_mode(self, mode: str) -> None:
        if mode in self._modes:
            self.mode = mode

    def cycle_mode(self) -> str:
        """Advance to the next registered mode and return its name."""
        order = self._mode_order
        if not order:
            return self.mode
        try:
            idx = order.index(self.mode)
        except ValueError:
            idx = -1
        self.mode = order[(idx + 1) % len(order)]
        return self.mode

    @property
    def available_modes(self) -> list[str]:
        """Names of modes currently registered (built-ins + plugins)."""
        return list(self._mode_order)
