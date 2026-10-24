"""Cover art drawn as a real image where the terminal supports one.

A cover downloads its thumbnail once into a disk cache, shrinks it to its own
pixel size in a worker thread, then draws it itself. Under kitty that is the
full picture through the kitty graphics protocol. Elsewhere it is half-blocks.

The cover draws the image in ``render()`` instead of mounting a child widget.
A mount re-lays-out the whole screen, and a page of 100 covers then blocked
the UI thread for seconds. A new image now only repaints its own cover.

textual-image has to query the terminal before Textual takes over stdin, so
``prime_image_support()`` must run before ``App.run()``. The CLI does that.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

from ytm_player.config.paths import CACHE_DIR
from ytm_player.ui.icons import icon

logger = logging.getLogger(__name__)

THUMB_DIR = CACHE_DIR.parent / "covers"  # CACHE_DIR is the audio cache
_TIMEOUT = 8
_FIRST_PAINT_DELAY = 0.12  # seconds: the page lays out and paints its text first
_IMAGE_GAP = 0.03  # seconds between kitty images, about two frames
_STALE_PART_SECONDS = 3600  # a temp file this old belongs to a killed download
# Corner radius of a cover, as a share of its shorter side.
CORNER_RATIO = 0.06
# YouTube's hqdefault video thumbnail, and where the square art sits in it.
_VIDEO_THUMB_SIZE = (480, 360)
_VIDEO_THUMB_ART = (105, 45, 375, 315)
_LH3_SIZE = re.compile(r"=w\d+-h\d+")

_renderable_cls: type | None = None
_kitty = False
_primed = False
_image_lock: asyncio.Lock | None = None


def _image_turn() -> asyncio.Lock:
    """One cover at a time sends its kitty image, so the terminal never gets a burst."""
    global _image_lock
    if _image_lock is None:
        _image_lock = asyncio.Lock()
    return _image_lock


def prime_image_support() -> None:
    """Import textual-image while the terminal can still answer its queries."""
    global _renderable_cls, _kitty, _primed
    _primed = True
    try:
        from textual_image.renderable import Image as AutoRenderable
        from textual_image.renderable.halfcell import Image as HalfcellRenderable
        from textual_image.renderable.tgp import Image as TGPRenderable

        _kitty = AutoRenderable is TGPRenderable and os.environ.get("YTM_COVERS") != "halfcell"
        _renderable_cls = TGPRenderable if _kitty else HalfcellRenderable
        if _kitty:
            _route_kitty_messages_through_textual()
    except ImportError:
        logger.debug("textual-image not installed, covers use the built-in half-blocks")
        _renderable_cls, _kitty = None, False
    except Exception:
        # Installed but broken. Say so, or covers quietly lose their images.
        logger.warning("textual-image failed to start, covers use half-blocks", exc_info=True)
        _renderable_cls, _kitty = None, False


def _route_kitty_messages_through_textual() -> None:
    """Send textual-image's kitty messages through Textual's driver, and narrow its deletes.

    textual-image writes each image straight to ``sys.__stdout__`` while it
    renders, and Textual writes frames from its own writer thread. The two
    interleaved and corrupted frames. Through the driver, each image message is
    queued just before the frame that shows it.
    """
    from textual._context import active_app
    from textual_image.renderable import tgp

    if getattr(tgp._send_tgp_message, "_routed", False):
        return
    direct = tgp._send_tgp_message

    def routed(*, payload: str | None = None, **kwargs: int | str | None) -> None:
        if kwargs.get("a") == "d" and "d" not in kwargs and kwargs.get("I") is not None:
            # textual-image deletes with ``a=d,I=<id>``. To kitty that removes every
            # image on screen. Delete only this image by its id, and free its data.
            kwargs = {"a": "d", "d": "I", "i": kwargs["I"], "q": 2}
        app = active_app.get(None)
        driver = getattr(app, "_driver", None)
        if driver is None:  # no running app: the library's own direct write
            direct(payload=payload, **kwargs)
            return
        sequence = "".join(
            [
                tgp._TGP_MESSAGE_START,
                ",".join(f"{k}={v}" for k, v in kwargs.items() if v is not None),
                f";{payload}" if payload else "",
                tgp._TGP_MESSAGE_END,
            ]
        )
        driver.write(tgp.prepare_terminal_sequence(sequence))  # type: ignore[attr-defined]

    routed._routed = True  # type: ignore[attr-defined]
    tgp._send_tgp_message = routed


def renderable_class() -> type | None:
    """The textual-image renderable covers draw with, or None for the built-in half-blocks."""
    if not _primed:
        prime_image_support()
    return _renderable_cls


def has_real_images() -> bool:
    """True when covers draw as real images (kitty graphics), not half-blocks."""
    renderable_class()
    return _kitty


def best_thumbnail(item: dict, min_width: int = 226) -> str:
    """URL of the smallest thumbnail at least *min_width* wide, else the largest."""
    thumbs = item.get("thumbnails") or item.get("thumbnail") or []
    if isinstance(thumbs, dict):
        thumbs = thumbs.get("thumbnails", [])
    if not thumbs:
        return ""
    ranked = sorted(thumbs, key=lambda t: t.get("width") or 0)
    for thumb in ranked:
        if (thumb.get("width") or 0) >= min_width:
            return thumb.get("url", "")
    # Song and artist results list 120 px at most. Their lh3 URLs carry the
    # size, so ask the image host for a larger copy.
    url = ranked[-1].get("url", "")
    return _LH3_SIZE.sub(f"=w{min_width}-h{min_width}", url, count=1)


def _temp_beside(path: Path) -> Path:
    """A temp file next to *path*, unique to this call.

    Two covers with the same URL load at the same time. With one shared temp
    name, the second rename found the file already moved and failed.
    """
    return path.with_name(f"{path.name}.{uuid.uuid4().hex}.part")


def track_thumbnail(track: dict | None) -> str:
    """A track's cover URL, else YouTube's own thumbnail for its video id.

    Radio and restored queue tracks often carry no thumbnail. YouTube serves
    one for every video, 4:3 with the square art centred, so covers crop it.
    """
    if not track:
        return ""
    url = track.get("thumbnail_url") or ""
    if url:
        return url
    video_id = track.get("video_id") or track.get("videoId") or ""
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else ""


def cached_path(url: str) -> Path:
    return THUMB_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".img")


def fetch_thumbnail(url: str) -> Path | None:
    """Download *url* into the cover cache once. Blocking. Run it in a thread."""
    path = cached_path(url)
    if path.exists() and path.stat().st_size > 0:
        _touch(path)
        return path
    try:
        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=_TIMEOUT) as resp:  # noqa: S310 - YouTube image host
            data = resp.read()
        tmp = _temp_beside(path)
        tmp.write_bytes(data)
        tmp.replace(path)
        return path
    except Exception:
        logger.debug("Cover download failed: %s", url, exc_info=True)
        return None


def _touch(path: Path) -> None:
    """Mark a cached cover as just used, so the size cap removes it last."""
    try:
        path.touch()
    except OSError:
        pass


def prune_covers(max_mb: int, now: float | None = None) -> int:
    """Remove the least recently used covers until the cache fits *max_mb*.

    Blocking. Run it in a thread. Also removes temp files left by a killed
    download. Returns the number of files removed.
    """
    now = time.time() if now is None else now
    entries: list[tuple[float, int, Path]] = []
    removed = 0
    try:
        candidates = list(THUMB_DIR.iterdir())
    except OSError:
        return 0
    for path in candidates:
        try:
            stat = path.stat()
        except OSError:
            continue
        if path.suffix == ".part":
            # A download in progress renames its temp file within seconds.
            if now - stat.st_mtime > _STALE_PART_SECONDS:
                removed += _unlink(path)
            continue
        entries.append((stat.st_mtime, stat.st_size, path))
    total = sum(size for _mtime, size, _path in entries)
    limit = max(0, max_mb) * 1024 * 1024
    for _mtime, size, path in sorted(entries):
        if total <= limit:
            break
        if _unlink(path):
            removed += 1
            total -= size
    return removed


def _unlink(path: Path) -> int:
    try:
        path.unlink()
        return 1
    except OSError:
        return 0


def shrink_to_cells(path: Path, cols: int, rows: int) -> Path:
    """A copy of *path* sized for *cols* x *rows* terminal cells, cached beside it.

    Blocking. Run it in a thread. Returns *path* unchanged when the cell size is
    unknown or the copy cannot be made.
    """
    try:
        from PIL import Image, ImageOps
        from textual_image._terminal import get_cell_size

        cell = get_cell_size()
        width, height = max(1, cols) * cell.width, max(1, rows) * cell.height
        target = path.with_name(f"{path.stem}.{width}x{height}.r2.png")
        if target.exists():
            _touch(target)
        else:
            with Image.open(path) as image:
                image = image.convert("RGB")
                if image.size == _VIDEO_THUMB_SIZE:
                    # A video thumbnail: the square art sits in the middle of a
                    # 16:9 frame inside the 4:3 image, between black bars and
                    # coloured side pillars. Keep the art alone.
                    image = image.crop(_VIDEO_THUMB_ART)
                # Fill the cover box, cropping the centre if the shapes differ.
                image = ImageOps.fit(image, (width, height), Image.Resampling.LANCZOS)
                image = round_corners(image)
                tmp = _temp_beside(target)
                image.save(tmp, format="PNG")
                tmp.replace(target)
        return target
    except Exception:
        logger.debug("Could not shrink cover %s", path, exc_info=True)
        return path


def round_corners(image: Any, ratio: float = CORNER_RATIO) -> Any:
    """*image* with softly rounded corners: transparent outside, drawn over the black page."""
    from PIL import Image, ImageDraw

    width, height = image.size
    radius = max(1, round(min(width, height) * ratio))
    scale = 4  # draw the mask large and shrink it, for smooth edges
    mask = Image.new("L", (width * scale, height * scale), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, width * scale - 1, height * scale - 1), radius=radius * scale, fill=255
    )
    rounded = image.convert("RGBA")
    rounded.putalpha(mask.resize((width, height), Image.Resampling.LANCZOS))
    return rounded


def _half_blocks(path: Path, cols: int, rows: int) -> Text | None:
    """The built-in half-block picture, for when textual-image is not installed."""
    try:
        from ytm_player.ui.widgets.album_art import AlbumArt

        return AlbumArt._image_to_half_blocks(path.read_bytes(), cols, rows)
    except Exception:
        logger.debug("Half-block cover failed: %s", path, exc_info=True)
        return None


async def _make_picture(path: Path, cols: int, rows: int) -> Any:
    """The picture of the cached cover *path* at *cols* x *rows* cells, built off the event loop."""
    renderable = renderable_class()
    if renderable is None:
        return await asyncio.to_thread(_half_blocks, path, cols, rows)
    if has_real_images():
        # The kitty encode runs while drawing, so hand it a small file.
        path = await asyncio.to_thread(shrink_to_cells, path, cols, rows)
    return await asyncio.to_thread(renderable, str(path), cols, rows)


async def load_cover_picture(url: str, cols: int, rows: int) -> Any:
    """Download *url* once and return its picture at *cols* x *rows* cells, or None.

    For widgets that draw covers inside their own rows, such as the track table.
    Show the picture while holding ``cover_image_turn()``, then await ``cover_image_gap()``.
    """
    path = await asyncio.to_thread(fetch_thumbnail, url) if url else None
    if path is None:
        return None
    return await _make_picture(path, cols, rows)


def cover_image_turn() -> asyncio.Lock:
    """The lock that keeps kitty images from being sent in the same frame."""
    return _image_turn()


async def cover_image_gap() -> None:
    """The pause between two kitty images, about two frames. None without kitty."""
    if has_real_images():
        await asyncio.sleep(_IMAGE_GAP)


class Cover(Widget):
    """A fixed-size cover. It shows ♫ until its picture is ready, then draws the picture."""

    DEFAULT_CSS = """
    Cover {
        width: 22;
        height: 11;
        background: $surface;
        color: $text-muted;
        content-align: center middle;
    }
    """

    url: reactive[str] = reactive("", init=False)

    def __init__(self, url: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.set_reactive(Cover.url, url)
        self._token = 0
        self._path: Path | None = None
        self._picture: Any = None
        self._picture_size: tuple[int, int] = (0, 0)

    def render(self) -> Any:
        if self._picture is not None:
            return self._picture
        return Text(icon("note"))

    def on_mount(self) -> None:
        if self.url:
            self._load(self.url)

    def watch_url(self, url: str) -> None:
        self._load(url)

    def on_resize(self) -> None:
        if self._path is not None and (self.size.width, self.size.height) != self._picture_size:
            self._load(self.url)

    def on_unmount(self) -> None:
        self._drop_picture()

    # Same interface as AlbumArt, so the playback bar can hold either.
    def set_track(self, thumbnail_url: str = "") -> None:
        self.url = thumbnail_url

    def clear_track(self) -> None:
        self.url = ""

    def _drop_picture(self) -> None:
        """Forget the picture, and delete it from kitty if it was a kitty image."""
        picture, self._picture = self._picture, None
        cleanup = getattr(picture, "cleanup", None)
        if cleanup is not None:
            try:
                cleanup()
            except Exception:
                logger.debug("Cover cleanup failed", exc_info=True)

    def _load(self, url: str) -> None:
        self._token += 1
        self.run_worker(self._fetch_and_show(url, self._token), exclusive=True, group="cover")

    async def _fetch_and_show(self, url: str, token: int) -> None:
        started = time.monotonic()
        path = await asyncio.to_thread(fetch_thumbnail, url) if url else None
        await asyncio.sleep(_FIRST_PAINT_DELAY)  # layout first: the size is 0 before it
        if token != self._token or not self.is_attached:
            return
        cols, rows = self.size.width, self.size.height
        self._path = path
        if path is None or not cols or not rows:
            self._drop_picture()
            self._picture_size = (0, 0)
            self.refresh()
            return
        picture = await _make_picture(path, cols, rows)
        async with _image_turn():
            if token != self._token or not self.is_attached:
                return
            self._drop_picture()
            self._picture, self._picture_size = picture, (cols, rows)
            self.refresh()  # repaint only: no layout, the cover's size never changes
            if has_real_images():
                await asyncio.sleep(_IMAGE_GAP)
        logger.debug("cover ready in %.2fs: %s", time.monotonic() - started, url[-24:])
