"""SelectionInfoBar — fixed 1-row bar showing the currently-focused item's full name."""

from __future__ import annotations

import logging

from textual.app import ComposeResult
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

logger = logging.getLogger(__name__)


class SelectionChanged(Message):
    """Posted by widgets when their currently-selected/focused item changes.

    Bubbles up the DOM; SelectionInfoBar handles it and updates its display.
    Empty *text* signals no current selection (bar shows nothing).
    """

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class SelectionInfoBar(Widget):
    """A 1-row bar that shows the full name of the currently-focused item.

    Displays text from SelectionChanged messages. Centered, single line.
    Hidden entirely when `[ui] show_selection_info = false` (display = False
    so the row is reclaimed by the layout).
    """

    DEFAULT_CSS = """
    SelectionInfoBar {
        height: 1;
        width: 100%;
        color: $text-muted;
        content-align: center middle;
        padding: 0 1;
    }
    """

    text: reactive[str] = reactive("")

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._inner: Static | None = None

    def compose(self) -> ComposeResult:
        # markup=False: the bar shows outside text (track and playlist names).
        self._inner = Static("", id="selection-info-text", markup=False)
        yield self._inner

    def watch_text(self, old: str, new: str) -> None:
        """Reactive watcher — re-render the inner Static when text changes."""
        if self._inner is None:
            return
        try:
            from ytm_player.utils.formatting import truncate

            visible_width = max(self.size.width - 2, 10) if self.size.width else 80
            self._inner.update(truncate(new, visible_width))
        except Exception:
            logger.exception("SelectionInfoBar.watch_text failed")
            try:
                self._inner.update(new)
            except Exception:
                pass

    def on_resize(self, _event) -> None:
        """Re-truncate on resize so the visible text fits the new width."""
        try:
            self.watch_text(self.text, self.text)
        except Exception:
            pass

    async def on_selection_changed(self, message: SelectionChanged) -> None:
        """Receive a SelectionChanged message bubbled from a descendant."""
        message.stop()
        self.text = message.text
