"""Album art display widget for the terminal."""

from __future__ import annotations

import logging

from textual.reactive import reactive
from textual.widget import Widget
from rich.text import Text

logger = logging.getLogger(__name__)

# Unicode block characters used for the placeholder art.
_BLOCK_TOP = "\u2580"  # Upper half block
_BLOCK_BOTTOM = "\u2584"  # Lower half block
_BLOCK_FULL = "\u2588"  # Full block
_NOTE = "\u266b"  # Beamed eighth notes


class AlbumArt(Widget):
    """Displays album art in the terminal.

    For v1.0 this renders a styled placeholder using Unicode block characters
    and a music note icon. Full Kitty/sixel image protocol support is deferred
    to a future version.
    """

    DEFAULT_CSS = """
    AlbumArt {
        width: 12;
        height: 3;
        min-width: 8;
        min-height: 3;
    }
    """

    has_track: reactive[bool] = reactive(False)
    thumbnail_url: reactive[str] = reactive("")

    def __init__(
        self,
        *,
        accent_color: str = "#ff0000",
        bg_color: str = "#1a1a1a",
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._accent_color = accent_color
        self._bg_color = bg_color

    def render(self) -> Text:
        w = self.size.width
        h = self.size.height

        if not self.has_track or w < 3 or h < 1:
            return self._render_empty(w, h)

        return self._render_placeholder(w, h)

    def _render_empty(self, w: int, h: int) -> Text:
        """Render an empty/muted placeholder."""
        result = Text()
        muted = "#404040"
        for row in range(h):
            if row > 0:
                result.append("\n")
            if row == h // 2:
                pad = (w - 1) // 2
                result.append(" " * pad, style=muted)
                result.append(_NOTE, style=muted)
                result.append(" " * (w - pad - 1), style=muted)
            else:
                result.append(" " * w, style=muted)
        return result

    def _render_placeholder(self, w: int, h: int) -> Text:
        """Render a colored placeholder box with a music note."""
        result = Text()
        accent = self._accent_color
        dark = "#2a0000"

        for row in range(h):
            if row > 0:
                result.append("\n")

            if row == 0:
                # Top border
                result.append(_BLOCK_BOTTOM * w, style=f"{accent} on {self._bg_color}")
            elif row == h - 1:
                # Bottom border
                result.append(_BLOCK_TOP * w, style=f"{accent} on {self._bg_color}")
            elif row == h // 2:
                # Center row with note
                pad = (w - 1) // 2
                result.append(_BLOCK_FULL * pad, style=accent)
                result.append(_NOTE, style=f"bold white on {accent}")
                result.append(_BLOCK_FULL * (w - pad - 1), style=accent)
            else:
                # Filled row
                result.append(_BLOCK_FULL * w, style=accent)

        return result

    def set_track(self, thumbnail_url: str = "") -> None:
        """Update the widget for a new track."""
        self.thumbnail_url = thumbnail_url
        self.has_track = True

    def clear_track(self) -> None:
        """Clear the current track display."""
        self.has_track = False
        self.thumbnail_url = ""

