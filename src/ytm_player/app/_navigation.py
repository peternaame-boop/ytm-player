"""Page navigation mixin for YTMPlayerApp."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, cast

from textual.app import ComposeResult
from textual.containers import Container
from textual.widget import Widget
from textual.widgets import Static

from ytm_player.app._base import YTMHostBase
from ytm_player.config import Action

if TYPE_CHECKING:
    from ytm_player.app._base import PageWidget

logger = logging.getLogger(__name__)

# Valid page names.
PAGE_NAMES = (
    "home",
    "library",
    "search",
    "context",
    "browse",
    "explore",
    "mood",
    "now_playing",
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


class NavigationMixin(YTMHostBase):
    """Page navigation: navigate_to, _create_page, _get_current_page."""

    @property
    def current_page_name(self) -> str:
        return self._current_page

    async def navigate_to(self, page_name: str, **kwargs: Any) -> None:
        """Swap the content of #main-content to a new page.

        Extra *kwargs* are forwarded to the page constructor (e.g.
        ``context_type`` and ``context_id`` for ContextPage).

        Pass ``page_name="back"`` to pop from the back stack (and push the
        current page onto the forward stack), or ``page_name="forward"``
        to do the reverse.
        """
        # Refresh the current page's nav state into the cache FIRST — the
        # back/forward stack snapshots below read from this cache, so a
        # stale entry here would restore a previous visit's cursor.
        if self._current_page:
            current_page = self._get_current_page()
            if current_page is not None and hasattr(current_page, "get_nav_state"):
                page_state = current_page.get_nav_state()
                if page_state:
                    self._page_state_cache[self._current_page] = page_state
                else:
                    # An empty fresh state means the user left whatever the
                    # cache remembers — drop it rather than restore it later.
                    self._page_state_cache.pop(self._current_page, None)

        # Handle "back" / "forward" navigation via stacks.
        is_back = page_name == "back"
        is_forward = page_name == "forward"
        if is_back:
            if self._nav_stack:
                prev_page, prev_kwargs = self._nav_stack.pop()
                # Push the page we're leaving onto the forward stack so
                # the user can come back to it via forward.
                if self._current_page:
                    cur_kwargs = dict(self._current_page_kwargs)
                    cur_kwargs.update(self._page_state_cache.get(self._current_page, {}))
                    self._forward_stack.append((self._current_page, cur_kwargs))
                    if len(self._forward_stack) > _MAX_NAV_STACK:
                        self._forward_stack = self._forward_stack[-_MAX_NAV_STACK:]
                page_name = prev_page
                kwargs = prev_kwargs
            else:
                page_name = "library"
        elif is_forward:
            if self._forward_stack:
                next_page, next_kwargs = self._forward_stack.pop()
                # Push the page we're leaving back onto the nav stack so
                # the user can return via back.
                if self._current_page:
                    cur_kwargs = dict(self._current_page_kwargs)
                    cur_kwargs.update(self._page_state_cache.get(self._current_page, {}))
                    self._nav_stack.append((self._current_page, cur_kwargs))
                    if len(self._nav_stack) > _MAX_NAV_STACK:
                        self._nav_stack = self._nav_stack[-_MAX_NAV_STACK:]
                page_name = next_page
                kwargs = next_kwargs
            else:
                # No forward history — silently no-op.
                return

        if page_name not in PAGE_NAMES:
            logger.warning("Unknown page: %s", page_name)
            return

        same_page_back = False
        if page_name == self._current_page and not kwargs:
            # Clicking the same page again goes back to the previous page.
            if self._nav_stack:
                prev_page, prev_kwargs = self._nav_stack.pop()
                page_name = prev_page
                kwargs = prev_kwargs
                same_page_back = True
            else:
                return

        # Push current page onto the nav stack before switching.
        # Skip for back / forward / same-page-back — those already managed
        # the stacks above (or popped, in the same-page case).
        if (
            not is_back
            and not is_forward
            and not same_page_back
            and self._current_page
            and self._current_page != page_name
        ):
            nav_kwargs = dict(self._current_page_kwargs)
            nav_kwargs.update(self._page_state_cache.get(self._current_page, {}))
            self._nav_stack.append((self._current_page, nav_kwargs))
            # Cap stack size.
            if len(self._nav_stack) > _MAX_NAV_STACK:
                self._nav_stack = self._nav_stack[-_MAX_NAV_STACK:]
            # Browser semantics: any non-back/forward navigation invalidates
            # the forward history (you can't redo a future you didn't take).
            self._forward_stack.clear()

        # Restore cached state for forward navigation when no explicit kwargs given.
        if not kwargs and page_name in self._page_state_cache:
            kwargs = dict(self._page_state_cache[page_name])

        container = self.query_one("#main-content", Container)

        # remove_children and mount are async; must await them.
        started = time.monotonic()
        await container.remove_children()
        removed = time.monotonic()
        page_widget = self._create_page(page_name, **kwargs)
        await container.mount(page_widget)
        logger.debug(
            "page %s: old page removed in %.2fs, new page mounted in %.2fs",
            page_name, removed - started, time.monotonic() - removed,
        )  # fmt: skip
        self._current_page = page_name
        self._current_page_kwargs = dict(kwargs)
        # A page swap moves the user back into the content pane; reset the
        # keyboard-focus pane so movement keys drive the new page, not a
        # sidebar that was focused on the previous page.
        self._active_pane = "content"

        # Highlight the sidebar's pinned item for this page, as the web menu does.
        try:
            from ytm_player.ui.sidebars.playlist_sidebar import PlaylistSidebar

            self.query_one("#playlist-sidebar", PlaylistSidebar).set_active_page(page_name)
        except Exception:
            logger.debug("Failed to update sidebar active item", exc_info=True)

        # Show/hide the header back/forward buttons based on stack state.
        try:
            from ytm_player.ui.header_bar import HeaderBar

            header = self.query_one("#app-header", HeaderBar)
            header.set_back_visible(bool(self._nav_stack))
            header.set_forward_visible(bool(self._forward_stack))
        except Exception:
            logger.exception("Failed to update header back/forward button visibility")

        try:
            from ytm_player.ui.playback_bar import PlaybackBar

            self.query_one("#playback-bar", PlaybackBar).set_now_playing_open(
                page_name == "now_playing"
            )
        except Exception:
            logger.debug("Failed to update the player bar's expand button", exc_info=True)

        # Apply per-page playlist sidebar visibility.
        sidebar_visible = self._sidebar_per_page.get(page_name, self._sidebar_default)
        self._apply_playlist_sidebar(sidebar_visible)

        # Apply global lyrics sidebar visibility.
        self._apply_lyrics_sidebar(self._lyrics_sidebar_open)

        logger.debug("Navigated to page: %s", page_name)

    def _purge_playlist_nav_state(self, dead_ids: set[str]) -> None:
        """Drop cached/stacked navigation state referencing a deleted playlist.

        Without this, a bare footer-nav to Library (empty kwargs → cached
        state applied) would reopen the deleted playlist.
        """
        # LibraryPage.on_mount falls back to this id when mounted bare.
        if self._active_library_playlist_id in dead_ids:
            self._active_library_playlist_id = None
        lib_state = self._page_state_cache.get("library")
        if lib_state and lib_state.get("playlist_id") in dead_ids:
            del self._page_state_cache["library"]

        def _is_dead(kw: dict) -> bool:
            # Library entries carry playlist_id; playlist context pages
            # (opened from search/browse) carry context_type + context_id.
            if kw.get("playlist_id") in dead_ids:
                return True
            return kw.get("context_type") == "playlist" and kw.get("context_id") in dead_ids

        self._nav_stack = [(name, kw) for name, kw in self._nav_stack if not _is_dead(kw)]
        self._forward_stack = [(name, kw) for name, kw in self._forward_stack if not _is_dead(kw)]

        # The purge may have emptied a stack — refresh the header buttons so
        # a stale back/forward control doesn't stay clickable.
        try:
            from ytm_player.ui.header_bar import HeaderBar

            header = self.query_one("#app-header", HeaderBar)
            header.set_back_visible(bool(self._nav_stack))
            header.set_forward_visible(bool(self._forward_stack))
        except Exception:
            logger.exception("Failed to update header back/forward button visibility")

    def _create_page(self, page_name: str, **kwargs: Any) -> Widget:
        """Instantiate the widget for a given page name."""
        from ytm_player.ui.pages.browse import BrowsePage
        from ytm_player.ui.pages.context import ContextPage
        from ytm_player.ui.pages.explore import ExplorePage, MoodPage
        from ytm_player.ui.pages.help import HelpPage
        from ytm_player.ui.pages.home import HomePage
        from ytm_player.ui.pages.library import LibraryPage
        from ytm_player.ui.pages.liked_songs import LikedSongsPage
        from ytm_player.ui.pages.now_playing import NowPlayingPage
        from ytm_player.ui.pages.queue import QueuePage
        from ytm_player.ui.pages.recently_played import RecentlyPlayedPage
        from ytm_player.ui.pages.search import SearchPage

        page_map: dict[str, type[Widget]] = {
            "home": HomePage,
            "library": LibraryPage,
            "search": SearchPage,
            "context": ContextPage,
            "browse": BrowsePage,
            "explore": ExplorePage,
            "mood": MoodPage,
            "now_playing": NowPlayingPage,
            "queue": QueuePage,
            "help": HelpPage,
            "liked_songs": LikedSongsPage,
            "recently_played": RecentlyPlayedPage,
        }
        page_cls = page_map.get(page_name)
        if page_cls is None:
            return _PlaceholderPage(page_name, id=f"page-{page_name}")
        # ContextPage uses unique IDs because back-to-back navigation between
        # contexts (e.g. album → album) can race the previous instance's
        # removal, causing DuplicateIds when the new one mounts.
        if page_name == "context":
            self._context_seq += 1
            return page_cls(id=f"page-context-{self._context_seq}", **kwargs)
        return page_cls(id=f"page-{page_name}", **kwargs)

    def _get_current_page(self) -> PageWidget | None:
        """Return the currently mounted page widget, or None.

        Returned as a ``PageWidget`` (Protocol) so callers can invoke
        ``handle_action`` and ``get_nav_state`` without ``hasattr``
        gymnastics for the type-checker.  All concrete pages mounted
        in ``#main-content`` implement this protocol.
        """
        try:
            container = self.query_one("#main-content", Container)
            children = list(container.children)
            if not children:
                return None
            return cast("PageWidget", children[0])
        except Exception:
            logger.debug("Failed to get current page", exc_info=True)
            return None
