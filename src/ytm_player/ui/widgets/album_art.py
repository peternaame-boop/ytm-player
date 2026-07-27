"""Album art display widget for the terminal.

Downloads thumbnails and renders them as colored Unicode half-blocks (▀).
Falls back to a styled placeholder while loading or if the image cannot
be fetched.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from io import BytesIO
from typing import cast
from urllib.request import urlopen

from PIL import Image
from rich.color import Color
from rich.style import Style
from rich.text import Text
from textual.events import Resize
from textual.reactive import reactive
from textual.widget import Widget

from ytm_player.ui.theme import get_theme

logger = logging.getLogger(__name__)

# Unicode block characters.
_BLOCK_TOP = "\u2580"  # Upper half block ▀
_BLOCK_BOTTOM = "\u2584"  # Lower half block ▄
_BLOCK_FULL = "\u2588"  # Full block █
_NOTE = "\u266b"  # Beamed eighth notes ♫

# Module-level thumbnail cache: URL -> rendered Text.
_ART_CACHE: OrderedDict[str, Text] = OrderedDict()
_ART_CACHE_MAX = 20

_DOWNLOAD_TIMEOUT = 5


def _readable_on(bg_hex: str, *, light: str, dark: str) -> str:
    """Pick whichever of *light*/*dark* is more readable on *bg_hex*.

    The placeholder's note glyph used to hardcode ``theme.foreground`` as
    its color regardless of the background it sits on -- correct for the
    stock theme (dark red primary, near-white foreground contrasts fine),
    but the glyph goes nearly invisible on any theme with a light/pastel
    ``primary`` (foreground and background end up close in luminance).
    Relative luminance (simplified sRGB weights, WCAG-style) decides which
    of the two supplied colors actually contrasts against this specific
    background, so it stays readable regardless of the active theme.
    """
    try:
        hex_str = bg_hex.lstrip("#")
        r, g, b = (int(hex_str[i : i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return light
    luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
    return dark if luminance > 0.5 else light


class AlbumArt(Widget):
    """Displays album art in the terminal using half-block Unicode rendering.

    When Pillow is installed, thumbnails are downloaded and rendered as
    colored ``▀`` characters (two pixel rows per character row).  Without
    Pillow the widget shows a styled placeholder.
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
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._rendered: Text | None = None

    # ── Reactive watchers ─────────────────────────────────────────────

    def watch_has_track(self, value: bool) -> None:
        if not value:
            self._rendered = None
        self.refresh()

    def watch_thumbnail_url(self, value: str) -> None:
        """When thumbnail URL changes, fetch and render the image."""
        if value:
            self.run_worker(self._load_thumbnail(value), exclusive=True, group="album-art")
        else:
            self._rendered = None
            self.refresh()

    # ── Thumbnail loading ─────────────────────────────────────────────

    async def _load_thumbnail(self, url: str) -> None:
        """Download thumbnail and convert to half-block art."""
        import asyncio

        # Check cache first.
        if url in _ART_CACHE:
            _ART_CACHE.move_to_end(url)
            self._rendered = _ART_CACHE[url]
            self.refresh()
            return

        try:
            img_bytes = await asyncio.to_thread(self._download, url)
            w = self.size.width
            h = self.size.height
            if w < 2 or h < 1:
                return
            rendered = await asyncio.to_thread(self._image_to_half_blocks, img_bytes, w, h)

            # Cache it.
            _ART_CACHE[url] = rendered
            if len(_ART_CACHE) > _ART_CACHE_MAX:
                _ART_CACHE.popitem(last=False)

            self._rendered = rendered
            self.refresh()
        except Exception:
            logger.debug("Failed to load album art from %s", url, exc_info=True)
            self._rendered = None
            self.refresh()

    @staticmethod
    def _download(url: str) -> bytes:
        """Download image bytes (runs in thread)."""
        with urlopen(url, timeout=_DOWNLOAD_TIMEOUT) as resp:  # noqa: S310
            return resp.read()

    # ── Half-block rendering ──────────────────────────────────────────

    @staticmethod
    def _image_to_half_blocks(img_bytes: bytes, width: int, height: int) -> Text:
        """Convert image to colored half-block characters.

        Each character cell represents 2 vertical pixels using the upper
        half block (▀) with foreground = top pixel, background = bottom pixel.
        """
        img = Image.open(BytesIO(img_bytes)).convert("RGB")
        # Each char = 1 pixel wide, 2 pixels tall.
        pixel_w = width
        pixel_h = height * 2
        img = img.resize((pixel_w, pixel_h), Image.Resampling.LANCZOS)

        result = Text()
        for row in range(height):
            if row > 0:
                result.append("\n")
            top_y = row * 2
            bot_y = row * 2 + 1
            for col in range(pixel_w):
                # img is RGB after .convert("RGB"), so getpixel always
                # returns a 3-tuple; the cast tells Pyright that.
                tr, tg, tb = cast(tuple[int, int, int], img.getpixel((col, top_y)))
                br, bg, bb = cast(tuple[int, int, int], img.getpixel((col, bot_y)))
                style = Style(
                    color=Color.from_rgb(tr, tg, tb),
                    bgcolor=Color.from_rgb(br, bg, bb),
                )
                result.append("\u2580", style=style)
        return result

    # ── Rendering ─────────────────────────────────────────────────────

    def render(self) -> Text:
        w = self.size.width
        h = self.size.height

        if self._rendered is not None and self.has_track:
            return self._rendered

        if not self.has_track or w < 3 or h < 1:
            return self._render_empty(w, h)

        return self._render_placeholder(w, h)

    def _render_empty(self, w: int, h: int) -> Text:
        """Render an empty/muted placeholder."""
        result = Text()
        muted = get_theme().border
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
        theme = get_theme()
        accent = theme.primary
        bg = theme.playback_bar_bg

        for row in range(h):
            if row > 0:
                result.append("\n")

            if row == 0:
                result.append(_BLOCK_BOTTOM * w, style=f"{accent} on {bg}")
            elif row == h - 1:
                result.append(_BLOCK_TOP * w, style=f"{accent} on {bg}")
            elif row == h // 2:
                pad = (w - 1) // 2
                note_color = _readable_on(accent, light=theme.foreground, dark=theme.background)
                result.append(_BLOCK_FULL * pad, style=accent)
                result.append(_NOTE, style=f"bold {note_color} on {accent}")
                result.append(_BLOCK_FULL * (w - pad - 1), style=accent)
            else:
                result.append(_BLOCK_FULL * w, style=accent)

        return result

    def on_resize(self, event: Resize) -> None:
        """Re-render on resize if we have a cached image for the current URL."""
        if not self.has_track or not self.thumbnail_url:
            return
        url = self.thumbnail_url
        # If the URL is in cache, invalidate and re-fetch at new size.
        if url in _ART_CACHE:
            del _ART_CACHE[url]
        self.run_worker(self._load_thumbnail(url), exclusive=True, group="album-art")

    def set_track(self, thumbnail_url: str = "") -> None:
        """Update the widget for a new track."""
        self.thumbnail_url = thumbnail_url
        self.has_track = True

    def clear_track(self) -> None:
        """Clear the current track display."""
        self._rendered = None
        self.has_track = False
        self.thumbnail_url = ""
