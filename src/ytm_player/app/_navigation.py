"""Page navigation mixin for YTMPlayerApp."""

from __future__ import annotations

import logging
from typing import Any

from textual.app import ComposeResult
from textual.containers import Container
from textual.widget import Widget
from textual.widgets import Static

from ytm_player.config import Action
from ytm_player.ui.playback_bar import FooterBar

logger = logging.getLogger(__name__)

# Valid page names.
PAGE_NAMES = (
    "library",
    "search",
    "context",
    "browse",
    "queue",
    "help",
    "liked_songs",
    "recently_played",
)

_MAX_NAV_STACK = 20


# ── Placeholder page widget ─────────────────────────────────────────


class _PlaceholderPage(Widget):
    """Temporary placeholder shown for pages not yet implemented."""

    DEFAULT_CSS = """
    _PlaceholderPage {
        width: 1fr;
        height: 1fr;
        content-align: center middle;
    }
    """

    def __init__(self, page_name: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._page_name = page_name

    def compose(self) -> ComposeResult:
        yield Static(
            f"\n\n  [{self._page_name.upper()}]\n\n"
            f"  This page is not yet implemented.\n"
            f"  Navigate with: g l (library), g s (search), z (queue), ? (help)\n",
            id="placeholder-text",
        )

    async def handle_action(self, action: Action, count: int = 1) -> None:
        """No-op action handler for placeholder pages."""
        pass


class NavigationMixin:
    """Page navigation: navigate_to, _create_page, _get_current_page."""

    @property
    def current_page_name(self) -> str:
        return self._current_page

    async def navigate_to(self, page_name: str, **kwargs: Any) -> None:
        """Swap the content of #main-content to a new page.

        Extra *kwargs* are forwarded to the page constructor (e.g.
        ``context_type`` and ``context_id`` for ContextPage).
        Pass ``page_name="back"`` to pop from the navigation stack.
        """
        # Handle "back" navigation via stack.
        is_back = page_name == "back"
        if is_back:
            if self._nav_stack:
                prev_page, prev_kwargs = self._nav_stack.pop()
                page_name = prev_page
                kwargs = prev_kwargs
            else:
                page_name = "library"

        if page_name not in PAGE_NAMES:
            logger.warning("Unknown page: %s", page_name)
            return

        if page_name == self._current_page and not kwargs:
            # Clicking the same page again goes back to the previous page.
            if self._nav_stack:
                prev_page, prev_kwargs = self._nav_stack.pop()
                page_name = prev_page
                kwargs = prev_kwargs
            else:
                return

        # Cache current page state before destroying it.
        # This allows forward navigation (footer/sidebar clicks) to restore state.
        if self._current_page:
            current_page = self._get_current_page()
            if current_page and hasattr(current_page, "get_nav_state"):
                page_state = current_page.get_nav_state()
                if page_state:
                    self._page_state_cache[self._current_page] = page_state

        # Push current page onto the nav stack before switching.
        # Skip for back navigation -- we already popped the target, don't push
        # the current page or the stack ping-pongs between two pages.
        if not is_back and self._current_page and self._current_page != page_name:
            nav_kwargs = dict(self._current_page_kwargs)
            nav_kwargs.update(self._page_state_cache.get(self._current_page, {}))
            self._nav_stack.append((self._current_page, nav_kwargs))
            # Cap stack size.
            if len(self._nav_stack) > _MAX_NAV_STACK:
                self._nav_stack = self._nav_stack[-_MAX_NAV_STACK:]

        # Restore cached state for forward navigation when no explicit kwargs given.
        if not kwargs and page_name in self._page_state_cache:
            kwargs = dict(self._page_state_cache[page_name])

        container = self.query_one("#main-content", Container)

        # remove_children and mount are async; must await them.
        await container.remove_children()
        page_widget = self._create_page(page_name, **kwargs)
        await container.mount(page_widget)
        self._current_page = page_name
        self._current_page_kwargs = dict(kwargs)

        # Update footer active page indicator.
        try:
            footer = self.query_one("#app-footer", FooterBar)
            footer.set_active_page(page_name)
        except Exception:
            logger.debug("Failed to update footer active page indicator", exc_info=True)

        # Apply per-page playlist sidebar visibility.
        sidebar_visible = self._sidebar_per_page.get(page_name, self._sidebar_default)
        self._apply_playlist_sidebar(sidebar_visible)

        # Apply global lyrics sidebar visibility.
        self._apply_lyrics_sidebar(self._lyrics_sidebar_open)

        logger.debug("Navigated to page: %s", page_name)

    def _create_page(self, page_name: str, **kwargs: Any) -> Widget:
        """Instantiate the widget for a given page name."""
        from ytm_player.ui.pages.browse import BrowsePage
        from ytm_player.ui.pages.context import ContextPage
        from ytm_player.ui.pages.help import HelpPage
        from ytm_player.ui.pages.library import LibraryPage
        from ytm_player.ui.pages.liked_songs import LikedSongsPage
        from ytm_player.ui.pages.queue import QueuePage
        from ytm_player.ui.pages.recently_played import RecentlyPlayedPage
        from ytm_player.ui.pages.search import SearchPage

        page_map: dict[str, type[Widget]] = {
            "library": LibraryPage,
            "search": SearchPage,
            "context": ContextPage,
            "browse": BrowsePage,
            "queue": QueuePage,
            "help": HelpPage,
            "liked_songs": LikedSongsPage,
            "recently_played": RecentlyPlayedPage,
        }
        page_cls = page_map.get(page_name)
        if page_cls is None:
            return _PlaceholderPage(page_name, id=f"page-{page_name}")
        return page_cls(id=f"page-{page_name}", **kwargs)

    def _get_current_page(self) -> Widget | None:
        """Return the currently mounted page widget, or None."""
        try:
            container = self.query_one("#main-content", Container)
            children = list(container.children)
            return children[0] if children else None
        except Exception:
            logger.debug("Failed to get current page", exc_info=True)
            return None
