"""Scroll thumbs show only while the pointer is over their list."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from ytm_player.ui import autohide_scrollbars
from ytm_player.ui.autohide_scrollbars import HOVER_CLASS, AutoHideScrollbarsMixin

CSS = """
* { scrollbar-color: black; }
.-scroll-hover { scrollbar-color: #2e2e2e; }
VerticalScroll { width: 20; height: 5; }
"""


class _Host(AutoHideScrollbarsMixin, App):
    CSS = CSS

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="left"):
            for i in range(20):
                yield Static(f"left {i}", id=f"l{i}")
        with VerticalScroll(id="right"):
            for i in range(20):
                yield Static(f"right {i}", id=f"r{i}")


async def test_only_the_list_under_the_pointer_shows_its_thumb():
    app = _Host()
    async with app.run_test(size=(40, 12)) as pilot:
        left, right = app.query_one("#left"), app.query_one("#right")
        assert not left.has_class(HOVER_CLASS) and not right.has_class(HOVER_CLASS)
        await pilot.hover("#l1")
        await pilot.pause()
        assert left.has_class(HOVER_CLASS) and not right.has_class(HOVER_CLASS)
        assert left.styles.scrollbar_color != right.styles.scrollbar_color
        await pilot.hover("#r1")
        await pilot.pause()
        assert right.has_class(HOVER_CLASS) and not left.has_class(HOVER_CLASS)


async def test_the_thumb_hides_when_the_pointer_leaves_the_window():
    app = _Host()
    async with app.run_test(size=(40, 12)) as pilot:
        await pilot.hover("#l1")
        await pilot.pause()
        app._set_mouse_over(None, None)
        await pilot.pause()
        assert not app.query_one("#left").has_class(HOVER_CLASS)


async def test_the_thumb_hides_after_the_pointer_rests(monkeypatch):
    monkeypatch.setattr(autohide_scrollbars, "HIDE_AFTER_SECONDS", 0.2)
    app = _Host()
    async with app.run_test(size=(40, 12)) as pilot:
        await pilot.hover("#l1")
        await pilot.pause()
        assert app.query_one("#left").has_class(HOVER_CLASS)
        await pilot.pause(0.4)
        assert not app.query_one("#left").has_class(HOVER_CLASS)
        await pilot.hover("#l2")
        await pilot.pause()
        assert app.query_one("#left").has_class(HOVER_CLASS), "a move shows it again"


async def test_the_thumb_hides_when_the_window_loses_focus():
    from textual import events

    app = _Host()
    async with app.run_test(size=(40, 12)) as pilot:
        await pilot.hover("#l1")
        await pilot.pause()
        app.post_message(events.AppBlur())
        await pilot.pause()
        assert not app.query_one("#left").has_class(HOVER_CLASS)
