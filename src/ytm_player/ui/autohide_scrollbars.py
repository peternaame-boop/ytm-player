"""Scroll bars that show only while the pointer is active over their list.

The app's stylesheet draws every scroll thumb in the background colour, so it
is invisible. This mixin gives the ``-scroll-hover`` class to each scrolling
container under the pointer, and the stylesheet shows the thumb there. The bars
keep their width, so nothing moves when a thumb appears.

The thumb hides again HIDE_AFTER_SECONDS after the last mouse move or scroll,
and at once when the window loses focus. A terminal does not report the pointer
leaving its window, so without these the last list hovered would keep its thumb.

Textual's ``:hover`` applies only to the widget directly under the pointer, not
to the list that holds it, so the class is set from bubbling mouse events.
"""

from __future__ import annotations

from typing import Any

from textual import events
from textual.containers import ScrollableContainer
from textual.dom import DOMNode
from textual.scroll_view import ScrollView

HOVER_CLASS = "-scroll-hover"
# Seconds without mouse activity before a shown thumb hides again.
HIDE_AFTER_SECONDS = 1.5


def scrolling_ancestors(node: DOMNode | None) -> set[DOMNode]:
    """*node* and its ancestors that scroll."""
    if node is None:
        return set()
    return {n for n in node.ancestors_with_self if isinstance(n, (ScrollableContainer, ScrollView))}


class AutoHideScrollbarsMixin:
    """App mixin that marks the scrolling containers under an active pointer."""

    _scroll_hovered: set[DOMNode] = set()
    _scroll_hide_timer: Any = None

    def _mark_scroll_hover(self, node: DOMNode | None) -> None:
        now = scrolling_ancestors(node)
        for gone in self._scroll_hovered - now:
            gone.remove_class(HOVER_CLASS)
        for new in now - self._scroll_hovered:
            new.add_class(HOVER_CLASS)
        self._scroll_hovered = now

    def _pointer_active(self, node: DOMNode | None) -> None:
        """Show the thumbs around *node* and restart the countdown that hides them."""
        self._mark_scroll_hover(node)
        if self._scroll_hide_timer is not None:
            self._scroll_hide_timer.stop()
        self._scroll_hide_timer = self.set_timer(  # type: ignore[attr-defined]
            HIDE_AFTER_SECONDS, lambda: self._mark_scroll_hover(None)
        )

    def on_enter(self, event: events.Enter) -> None:
        self._pointer_active(event.node)

    def on_mouse_move(self, event: events.MouseMove) -> None:
        self._pointer_active(getattr(self, "mouse_over", None))

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self._pointer_active(getattr(self, "mouse_over", None))

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self._pointer_active(getattr(self, "mouse_over", None))

    def on_leave(self, event: events.Leave) -> None:
        # The pointer left every widget: Textual clears mouse_over before this arrives.
        if getattr(self, "mouse_over", None) is None:
            self._mark_scroll_hover(None)

    def on_app_blur(self, event: events.AppBlur) -> None:
        self._mark_scroll_hover(None)
