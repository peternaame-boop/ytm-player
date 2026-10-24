"""Scroll bars about 8 px thick in kitty.

A terminal cell here is about 9 px wide and 20 px tall. A vertical bar one
cell wide is already about 9 px. A horizontal bar one cell tall is 20 px, so
it is drawn with the lower three-eighths block instead, about 7.5 px high.
"""

from __future__ import annotations

from math import ceil

from rich.color import Color
from rich.segment import Segment, Segments
from rich.style import Style
from textual.scrollbar import ScrollBar, ScrollBarRender

_THIN = "▃"  # lower three-eighths block: the horizontal thumb
_FULL = "█"  # the vertical thumb


class ThinScrollBarRender(ScrollBarRender):
    """Scroll bars drawn as a thin line (horizontal) or a one-cell bar (vertical)."""

    @classmethod
    def render_bar(
        cls,
        size: int = 25,
        virtual_size: float = 50,
        window_size: float = 20,
        position: float = 0,
        thickness: int = 1,
        vertical: bool = True,
        back_color: Color = Color.parse("#555555"),
        bar_color: Color = Color.parse("bright_magenta"),
    ) -> Segments:
        upper = Segment(" ", Style(bgcolor=back_color, meta={"@mouse.down": "scroll_up"}))
        lower = Segment(" ", Style(bgcolor=back_color, meta={"@mouse.down": "scroll_down"}))
        if not (window_size and size and virtual_size and size != virtual_size):
            return _segments([upper] * int(size), vertical)
        thumb_size = max(1.0, window_size / (virtual_size / size))
        start = int((size - thumb_size) * position / (virtual_size - window_size))
        end = min(int(size), start + ceil(thumb_size))
        style = Style(color=bar_color, bgcolor=back_color, meta={"@mouse.down": "grab"})
        thumb = [Segment(_FULL if vertical else _THIN, style)] * (end - start)
        return _segments([upper] * start + thumb + [lower] * (int(size) - end), vertical)


def _segments(cells: list[Segment], vertical: bool) -> Segments:
    """Package the cells the way Textual's own renderer does."""
    if vertical:
        return Segments(cells, new_lines=True)
    return Segments(cells + [Segment.line()], new_lines=False)


def install() -> None:
    """Use the thin renderer for every scroll bar in the app."""
    ScrollBar.renderer = ThinScrollBarRender
