"""Playback progress bar widget with click-to-seek and scroll-to-seek."""

from __future__ import annotations

from textual.events import Click, MouseScrollDown, MouseScrollUp
from textual.reactive import reactive
from textual.timer import Timer
from textual.widget import Widget
from rich.text import Text

from ytm_player.utils.formatting import format_duration

# Seconds to jump per scroll tick.
_SCROLL_STEP = 3.0

# Delay (seconds) after the last scroll event before the seek fires.
_SCROLL_COMMIT_DELAY = 0.6


class PlaybackProgress(Widget):
    """Displays a playback progress bar with elapsed/total time.

    Renders as: 1:23 [==========>-----------] 4:56

    Supports block style (default) and line style via the ``style`` parameter.
    Clicking anywhere on the bar seeks to that position.  Scrolling up/down
    adjusts a preview marker that commits after a short pause.
    """

    DEFAULT_CSS = """
    PlaybackProgress {
        height: 1;
        width: 1fr;
    }
    """

    position: reactive[float] = reactive(0.0)
    duration: reactive[float] = reactive(0.0)

    BLOCK_FILLED = "\u2588"  # Full block
    BLOCK_EMPTY = "\u2591"  # Light shade
    MARKER = "\u2503"        # Heavy vertical bar (seek preview)
    LINE_FILLED = "\u2501"  # Box heavy horizontal
    LINE_HEAD = "\u25cf"    # Black circle
    LINE_EMPTY = "\u2500"   # Box light horizontal

    def __init__(
        self,
        *,
        bar_style: str = "block",
        filled_color: str = "#ff0000",
        empty_color: str = "#404040",
        time_color: str = "#aaaaaa",
        marker_color: str = "#ffffff",
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._bar_style = bar_style
        self._filled_color = filled_color
        self._empty_color = empty_color
        self._time_color = time_color
        self._marker_color = marker_color

        # Scroll-seek preview state.
        self._preview_position: float | None = None
        self._scroll_timer: Timer | None = None

    @property
    def progress(self) -> float:
        """Progress as a fraction between 0.0 and 1.0."""
        if self.duration <= 0:
            return 0.0
        return min(1.0, max(0.0, self.position / self.duration))

    # ── Rendering ─────────────────────────────────────────────────

    def _bar_metrics(self) -> tuple[str, str, int]:
        """Return (time_prefix, time_suffix, bar_width)."""
        pos = self.position if self._preview_position is None else self._preview_position
        elapsed_str = format_duration(int(pos))
        total_str = format_duration(int(self.duration))
        time_prefix = f" {elapsed_str} "
        time_suffix = f" {total_str} "
        reserved = len(time_prefix) + len(time_suffix)
        bar_width = max(0, self.size.width - reserved)
        return time_prefix, time_suffix, bar_width

    def render(self) -> Text:
        time_prefix, time_suffix, bar_width = self._bar_metrics()
        filled_count = int(bar_width * self.progress)
        empty_count = bar_width - filled_count

        result = Text()
        result.append(time_prefix, style=self._time_color)

        if self._preview_position is not None and bar_width > 0:
            # Show a marker at the preview position.
            preview_frac = min(1.0, max(0.0, self._preview_position / self.duration)) if self.duration > 0 else 0.0
            marker_col = min(int(bar_width * preview_frac), bar_width - 1)

            for i in range(bar_width):
                if i == marker_col:
                    result.append(self.MARKER, style=f"bold {self._marker_color}")
                elif i < filled_count:
                    result.append(self.BLOCK_FILLED if self._bar_style == "block" else self.LINE_FILLED, style=self._filled_color)
                else:
                    result.append(self.BLOCK_EMPTY if self._bar_style == "block" else self.LINE_EMPTY, style=self._empty_color)
        else:
            # Normal rendering (no preview).
            if self._bar_style == "line":
                if filled_count > 0:
                    filled_str = self.LINE_FILLED * (filled_count - 1) + self.LINE_HEAD
                else:
                    filled_str = ""
                empty_str = self.LINE_EMPTY * empty_count
            else:
                filled_str = self.BLOCK_FILLED * filled_count
                empty_str = self.BLOCK_EMPTY * empty_count

            result.append(filled_str, style=self._filled_color)
            result.append(empty_str, style=self._empty_color)

        result.append(time_suffix, style=self._time_color)
        return result

    # ── Click to seek ─────────────────────────────────────────────

    def on_click(self, event: Click) -> None:
        """Seek to the clicked position on the bar."""
        event.stop()
        target = self._x_to_seconds(event.x)
        if target is not None:
            self._seek_to(target)

    # ── Scroll to seek (with preview marker) ──────────────────────

    def on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        """Scroll up → fast-forward preview."""
        event.stop()
        self._scroll_adjust(_SCROLL_STEP)

    def on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        """Scroll down → rewind preview."""
        event.stop()
        self._scroll_adjust(-_SCROLL_STEP)

    def _scroll_adjust(self, delta: float) -> None:
        """Move the preview marker by *delta* seconds and reset the commit timer."""
        if self.duration <= 0:
            return

        # Initialise preview from current position on first scroll tick.
        if self._preview_position is None:
            self._preview_position = self.position

        self._preview_position = max(0.0, min(self.duration, self._preview_position + delta))
        self.refresh()

        # Reset the commit timer.
        if self._scroll_timer is not None:
            self._scroll_timer.stop()
        self._scroll_timer = self.set_timer(_SCROLL_COMMIT_DELAY, self._commit_scroll)

    def _commit_scroll(self) -> None:
        """Called when scrolling pauses — seek to the preview position."""
        self._scroll_timer = None
        if self._preview_position is not None:
            target = self._preview_position
            self._preview_position = None
            self._seek_to(target)
        self.refresh()

    # ── Helpers ────────────────────────────────────────────────────

    def _x_to_seconds(self, x: int) -> float | None:
        """Map a widget-relative x coordinate to a position in seconds."""
        if self.duration <= 0:
            return None
        _, _, bar_width = self._bar_metrics()
        if bar_width <= 0:
            return None

        # The bar starts after the time prefix.
        pos = self.position if self._preview_position is None else self._preview_position
        elapsed_str = format_duration(int(pos))
        prefix_len = len(f" {elapsed_str} ")

        bar_x = x - prefix_len
        if bar_x < 0:
            bar_x = 0
        if bar_x >= bar_width:
            bar_x = bar_width - 1

        fraction = bar_x / bar_width
        return fraction * self.duration

    def _seek_to(self, seconds: float) -> None:
        """Tell the app player to seek to an absolute position."""
        app = self.app
        if hasattr(app, "player") and app.player:
            self.call_later(lambda: app.run_worker(app.player.seek_absolute(seconds)))

    # ── Public API (unchanged) ────────────────────────────────────

    def update_position(self, position: float, duration: float | None = None) -> None:
        """Update the position and optionally the duration."""
        self.position = position
        if duration is not None:
            self.duration = duration

