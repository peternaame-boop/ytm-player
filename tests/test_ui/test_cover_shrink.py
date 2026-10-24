"""Covers are shrunk to their cell size off the UI thread, and the copy is reused."""

from __future__ import annotations

from types import SimpleNamespace

from PIL import Image

from ytm_player.ui.widgets import cover


def test_shrinks_to_the_cells_pixel_box_and_caches(tmp_path, monkeypatch):
    import textual_image._terminal as terminal

    monkeypatch.setattr(terminal, "get_cell_size", lambda: SimpleNamespace(width=8, height=16))
    src = tmp_path / "big.img"
    Image.new("RGB", (576, 576), "red").save(src, format="JPEG")

    out = cover.shrink_to_cells(src, 22, 11)

    assert out.name == "big.176x176.r2.png"
    with Image.open(out) as image:
        assert image.size == (176, 176)
    # A rebuild renames a new file into place. A hit only touches the old one.
    inode = out.stat().st_ino
    assert cover.shrink_to_cells(src, 22, 11) == out
    assert out.stat().st_ino == inode, "the cached copy was rebuilt"


def test_a_broken_image_falls_back_to_the_original(tmp_path):
    src = tmp_path / "broken.img"
    src.write_bytes(b"not an image")
    assert cover.shrink_to_cells(src, 22, 11) == src


def test_the_same_cover_shrunk_twice_at_once_gives_both_the_copy(tmp_path, monkeypatch):
    """Regression: two covers with one URL shared a temp name, and the second rename failed."""
    from concurrent.futures import ThreadPoolExecutor

    import textual_image._terminal as terminal

    monkeypatch.setattr(terminal, "get_cell_size", lambda: SimpleNamespace(width=8, height=16))
    src = tmp_path / "big.img"
    Image.new("RGB", (576, 576), "red").save(src, format="JPEG")

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: cover.shrink_to_cells(src, 22, 11), range(8)))

    assert {r.name for r in results} == {"big.176x176.r2.png"}
    assert not list(tmp_path.glob("*.part"))


def test_covers_get_softly_rounded_transparent_corners(tmp_path, monkeypatch):
    import textual_image._terminal as terminal

    monkeypatch.setattr(terminal, "get_cell_size", lambda: SimpleNamespace(width=8, height=16))
    src = tmp_path / "big.img"
    Image.new("RGB", (576, 576), "red").save(src, format="JPEG")
    with Image.open(cover.shrink_to_cells(src, 22, 11)) as out:
        assert out.mode == "RGBA"
        assert out.getpixel((0, 0))[3] == 0, "corner is transparent"
        assert out.getpixel((88, 88))[3] == 255, "middle is solid"
        assert out.getpixel((88, 0))[3] == 255, "edge midpoints are not cut"


def test_a_track_without_a_thumbnail_falls_back_to_its_video_thumbnail():
    assert cover.track_thumbnail({"thumbnail_url": "https://x/a"}) == "https://x/a"
    assert cover.track_thumbnail({"video_id": "qAqXDZoa624", "thumbnail_url": None}) == (
        "https://i.ytimg.com/vi/qAqXDZoa624/hqdefault.jpg"
    )
    assert cover.track_thumbnail({}) == ""


def test_a_letterboxed_video_thumbnail_is_cropped_to_the_square_art(tmp_path, monkeypatch):
    import textual_image._terminal as terminal

    monkeypatch.setattr(terminal, "get_cell_size", lambda: SimpleNamespace(width=8, height=16))
    src = tmp_path / "video.img"
    image = Image.new("RGB", (480, 360), "black")
    image.paste(Image.new("RGB", (270, 270), "red"), (105, 45))
    image.save(src, format="JPEG")
    with Image.open(cover.shrink_to_cells(src, 22, 11)) as out:
        assert out.size == (176, 176)
        r, g, b, _a = out.getpixel((40, 88))
        assert r > 200 and g < 60, "the left edge shows the art, not a black bar"
