"""Draw every character in the terminal font's regular face, and in capitals.

kitty draws bold and italic text with separate font faces, so a screen that
mixes them looks like it uses several fonts. RegularWeight removes both at
render time. It covers CSS text-style, inline Rich styles and Textual's own
widgets. Colour and dim still mark emphasis.

UppercaseText shows all text in capitals when [ui] uppercase is on. It only
changes what is drawn. Search queries, titles and other data keep their case.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache

from rich.segment import Segment
from rich.style import Style
from textual.color import Color
from textual.filter import LineFilter

from ytm_player.config.settings import get_settings

_REGULAR = Style(bold=False, italic=False)


@lru_cache(1024)
def _regular(style: Style) -> Style:
    return style + _REGULAR


class RegularWeight(LineFilter):
    """Replace bold and italic with the regular face."""

    def apply(self, segments: list[Segment], background: Color) -> list[Segment]:
        return [
            Segment(segment.text, _regular(segment.style), segment.control)
            if segment.style is not None and (segment.style.bold or segment.style.italic)
            else segment
            for segment in segments
        ]


@lru_cache(4096)
def _upper(text: str) -> str:
    """*text* in capitals, one character for one, so every column keeps its width.

    A letter whose capital is longer, such as ß (SS), stays as it is.
    """
    upper = text.upper()
    if len(upper) == len(text):
        return upper
    return "".join(up if len(up := char.upper()) == 1 else char for char in text)


class UppercaseText(LineFilter):
    """Draw all text in capitals."""

    def apply(self, segments: list[Segment], background: Color) -> list[Segment]:
        return [
            segment
            if segment.control or not segment.text
            else Segment(_upper(segment.text), segment.style, None)
            for segment in segments
        ]


_REGULAR_FILTER = RegularWeight()
_UPPERCASE_FILTER = UppercaseText()


class RegularWeightMixin:
    """App mixin that adds these filters after Textual's own line filters."""

    def get_line_filters(self) -> Sequence[LineFilter]:
        filters = [*super().get_line_filters(), _REGULAR_FILTER]  # type: ignore[misc]
        if get_settings().ui.uppercase:
            filters.append(_UPPERCASE_FILTER)
        return filters
