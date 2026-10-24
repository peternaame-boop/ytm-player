"""A cover draws its own picture, so a new picture repaints one cover and never re-lays-out the page.

Regression: each cover mounted its picture as a child widget. Every mount re-laid-out the whole
screen, and a page of covers blocked the UI thread for seconds (a click registered 9 s late).
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from ytm_player.ui.widgets import cover
from ytm_player.ui.widgets.cover import Cover


class _Picture:
    """Stands in for a textual-image renderable."""

    made: list[_Picture] = []

    def __init__(self, path: str, width: int, height: int) -> None:
        self.path, self.width, self.height = path, width, height
        self.cleaned = False
        _Picture.made.append(self)

    def __rich_console__(self, console, options):
        yield from ()

    def cleanup(self) -> None:
        self.cleaned = True


class _Host(App):
    def compose(self) -> ComposeResult:
        yield Cover("https://img/a", id="c")


@pytest.fixture(autouse=True)
def _fake_images(monkeypatch: pytest.MonkeyPatch, tmp_path):
    _Picture.made = []
    image = tmp_path / "a.img"
    image.write_bytes(b"x")
    monkeypatch.setattr(cover, "fetch_thumbnail", lambda url: image if url else None)
    monkeypatch.setattr(cover, "renderable_class", lambda: _Picture)
    monkeypatch.setattr(cover, "has_real_images", lambda: False)
    monkeypatch.setattr(cover, "_FIRST_PAINT_DELAY", 0)


async def _settle(pilot, times: int = 10) -> None:
    for _ in range(times):
        await pilot.pause(0.02)


async def test_the_picture_is_drawn_by_the_cover_itself_with_no_child_widget():
    app = _Host()
    async with app.run_test(size=(60, 20)) as pilot:
        await _settle(pilot)
        c = app.query_one(Cover)
        assert not c.children
        assert c.render() is _Picture.made[-1]
        assert (_Picture.made[-1].width, _Picture.made[-1].height) == (22, 11)


async def test_a_new_picture_repaints_without_a_layout_pass(monkeypatch):
    app = _Host()
    async with app.run_test(size=(60, 20)) as pilot:
        await _settle(pilot)
        c = app.query_one(Cover)
        layouts = []
        original = c.refresh

        def spy(*args, **kwargs):
            layouts.append(kwargs.get("layout", False))
            return original(*args, **kwargs)

        monkeypatch.setattr(c, "refresh", spy)
        c.url = "https://img/b"
        await _settle(pilot)
        assert layouts and not any(layouts)


async def test_changing_the_url_deletes_the_old_picture():
    app = _Host()
    async with app.run_test(size=(60, 20)) as pilot:
        await _settle(pilot)
        first = _Picture.made[-1]
        app.query_one(Cover).url = "https://img/b"
        await _settle(pilot)
        assert first.cleaned
        assert app.query_one(Cover).render() is _Picture.made[-1]


async def test_no_url_shows_the_placeholder():
    app = _Host()
    async with app.run_test(size=(60, 20)) as pilot:
        await _settle(pilot)
        c = app.query_one(Cover)
        c.clear_track()
        await _settle(pilot)
        assert str(c.render()) == "♫"


async def test_unmount_deletes_the_picture():
    app = _Host()
    async with app.run_test(size=(60, 20)) as pilot:
        await _settle(pilot)
        picture = _Picture.made[-1]
        await app.query_one(Cover).remove()
        assert picture.cleaned


async def test_a_resize_draws_the_picture_again_at_the_new_size():
    app = _Host()
    async with app.run_test(size=(60, 20)) as pilot:
        await _settle(pilot)
        c = app.query_one(Cover)
        c.styles.width, c.styles.height = 30, 15
        await _settle(pilot)
        assert (_Picture.made[-1].width, _Picture.made[-1].height) == (30, 15)
