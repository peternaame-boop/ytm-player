"""Bottom-bar search/filter widget for filtering page content in real-time."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input


class SearchFilter(Widget):
    """A dock-bottom input bar that filters the parent page's visible items.

    Messages emitted:
        ``FilterChanged`` -- on every keystroke with the current query string.
        ``FilterClosed``  -- when the filter bar is dismissed, indicating
            whether the filter text should be kept or cleared.

    Usage:
        Mount this widget onto a page to activate filtering. Listen for
        ``SearchFilter.FilterChanged`` to update visible items, and
        ``SearchFilter.FilterClosed`` to handle cleanup.
    """

    DEFAULT_CSS = """
    SearchFilter {
        dock: bottom;
        height: auto;
        max-height: 1;
        background: $surface;
    }

    SearchFilter > Input {
        height: 1;
        border: none;
        background: $surface;
        padding: 0 1;
    }

    SearchFilter > Input:focus {
        border: none;
    }
    """

    # ── Messages ────────────────────────────────────────────────────

    class FilterChanged(Message):
        """Posted whenever the filter query text changes."""

        def __init__(self, query: str) -> None:
            self.query = query
            super().__init__()

    class FilterClosed(Message):
        """Posted when the filter bar is closed.

        Attributes:
            keep_filter: If ``True`` the parent should keep the current
                filter active (user pressed Enter). If ``False`` the
                parent should restore the unfiltered view (user pressed
                Escape).
        """

        def __init__(self, keep_filter: bool) -> None:
            self.keep_filter = keep_filter
            super().__init__()

    # ── Compose / lifecycle ─────────────────────────────────────────

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._query: str = ""

    def compose(self) -> ComposeResult:
        yield Input(placeholder="/", id="filter-input")

    def on_mount(self) -> None:
        self.query_one("#filter-input", Input).focus()

    # ── Input handling ──────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        """Emit FilterChanged on every keystroke."""
        if event.input.id != "filter-input":
            return
        self._query = event.value
        self.post_message(self.FilterChanged(event.value))

    def on_key(self, event) -> None:
        """Handle Escape, Enter, and Backspace-on-empty."""
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            self.post_message(self.FilterClosed(keep_filter=False))
            self.remove()
            return

        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.FilterClosed(keep_filter=True))
            self.remove()
            return

        if event.key == "backspace":
            inp = self.query_one("#filter-input", Input)
            if not inp.value:
                event.stop()
                event.prevent_default()
                self.post_message(self.FilterClosed(keep_filter=False))
                self.remove()
                return

    # ── Public API ──────────────────────────────────────────────────

    @property
    def query_text(self) -> str:
        """The current filter query string."""
        return self._query
