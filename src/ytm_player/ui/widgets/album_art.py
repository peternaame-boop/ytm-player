"""Album art display widget for the terminal.

Downloads thumbnails and renders them as colored Unicode half-blocks (▀).
Falls back to a styled placeholder when Pillow is not installed or the
image cannot be fetched.
"""

from __future__ import annotations

import logging
import urllib.request
from collections import OrderedDict

from textual.events import Resize
from textual.reactive import reactive
from textual.widget import Widget
from rich.style import Style
from rich.color import Color
from rich.text import Text

logger = logging.getLogger(__name__)

try:
    from PIL import Image
    import io
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

# Unicode block characters.
_BLOCK_TOP = "\u2580"     # Upper half block ▀
_BLOCK_BOTTOM = "\u2584"  # Lower half block ▄
_BLOCK_FULL = "\u2588"    # Full block █
_NOTE = "\u266b"          # Beamed eighth notes ♫

_LRU_MAX = 20
_DOWNLOAD_TIMEOUT = 5


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
        accent_color: str = "#ff0000",
        bg_color: str = "#1a1a1a",
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._accent_color = accent_color
        self._bg_color = bg_color
        self._rendered: Text | None = None
        self._cache: OrderedDict[str, bytes] = OrderedDict()

    # ── Reactive watchers ─────────────────────────────────────────────

    def watch_has_track(self, value: bool) -> None:
        if not value:
            self._rendered = None
        self.refresh()

    def watch_thumbnail_url(self, url: str) -> None:
        self._rendered = None
        if url and HAS_PILLOW:
            self.run_worker(self._load_thumbnail(url), exclusive=True)
        else:
            self.refresh()

    # ── Thumbnail loading ─────────────────────────────────────────────

    async def _load_thumbnail(self, url: str) -> None:
        import asyncio

        # Check LRU cache.
        if url in self._cache:
            self._cache.move_to_end(url)
            img_bytes = self._cache[url]
        else:
            try:
                img_bytes = await asyncio.to_thread(self._download, url)
            except Exception:
                logger.debug("Failed to download thumbnail: %s", url, exc_info=True)
                return

            self._cache[url] = img_bytes
            if len(self._cache) > _LRU_MAX:
                self._cache.popitem(last=False)

        w = self.size.width
        h = self.size.height
        if w < 3 or h < 1:
            return

        try:
            self._rendered = self._image_to_half_blocks(img_bytes, w, h)
        except Exception:
            logger.debug("Failed to render thumbnail", exc_info=True)
            self._rendered = None

        self.refresh()

    @staticmethod
    def _download(url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "ytm-player/1.0"})
        with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT) as resp:
            return resp.read()

    # ── Half-block rendering ──────────────────────────────────────────

    @staticmethod
    def _image_to_half_blocks(img_bytes: bytes, width: int, height: int) -> Text:
        """Convert image bytes to a Rich Text of colored half-block characters.

        Each character cell maps to two vertical pixels: the top pixel
        colour becomes the foreground (▀) and the bottom pixel colour
        becomes the background.
        """
        img = Image.open(io.BytesIO(img_bytes))
        # Resize: width pixels across, height*2 pixels tall (2 pixel rows per char row).
        img = img.convert("RGB").resize((width, height * 2), Image.LANCZOS)

        result = Text()
        pixels = img.load()
        for row in range(height):
            if row > 0:
                result.append("\n")
            for col in range(width):
                top_r, top_g, top_b = pixels[col, row * 2]
                bot_r, bot_g, bot_b = pixels[col, row * 2 + 1]
                style = Style(
                    color=Color.from_rgb(top_r, top_g, top_b),
                    bgcolor=Color.from_rgb(bot_r, bot_g, bot_b),
                )
                result.append(_BLOCK_TOP, style=style)
        return result

    # ── Rendering ─────────────────────────────────────────────────────

    def render(self) -> Text:
        w = self.size.width
        h = self.size.height

        if not self.has_track or w < 3 or h < 1:
            return self._render_empty(w, h)

        if self._rendered is not None:
            return self._rendered

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

        for row in range(h):
            if row > 0:
                result.append("\n")

            if row == 0:
                result.append(_BLOCK_BOTTOM * w, style=f"{accent} on {self._bg_color}")
            elif row == h - 1:
                result.append(_BLOCK_TOP * w, style=f"{accent} on {self._bg_color}")
            elif row == h // 2:
                pad = (w - 1) // 2
                result.append(_BLOCK_FULL * pad, style=accent)
                result.append(_NOTE, style=f"bold white on {accent}")
                result.append(_BLOCK_FULL * (w - pad - 1), style=accent)
            else:
                result.append(_BLOCK_FULL * w, style=accent)

        return result

    def on_resize(self, event: Resize) -> None:
        """Re-render cached image at the new widget dimensions."""
        if not HAS_PILLOW or not self.has_track or not self.thumbnail_url:
            return
        url = self.thumbnail_url
        if url in self._cache:
            w = event.size.width
            h = event.size.height
            if w >= 3 and h >= 1:
                try:
                    self._rendered = self._image_to_half_blocks(self._cache[url], w, h)
                except Exception:
                    logger.debug("Failed to re-render thumbnail on resize", exc_info=True)
                    self._rendered = None

    def set_track(self, thumbnail_url: str = "") -> None:
        """Update the widget for a new track."""
        self.thumbnail_url = thumbnail_url
        self.has_track = True

    def clear_track(self) -> None:
        """Clear the current track display."""
        self.has_track = False
        self.thumbnail_url = ""
