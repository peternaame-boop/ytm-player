"""Tests for NavigationMixin invariants.

The two bugs we're guarding against:
1. Back-navigation must NOT push current page onto stack — the stack
   would ping-pong between two pages forever.
2. Forward navigation should restore cached page state (cursor row,
   active tab, search query) when no explicit kwargs are passed.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from ytm_player.app._navigation import PAGE_NAMES, NavigationMixin


def _nav_with_page_states(states: dict[str, dict]) -> NavigationMixin:
    """Nav host whose current page reports get_nav_state from *states* by page name.

    Mutate *states* between navigations to simulate the user moving the
    cursor while a page is mounted.
    """
    nav = _fresh_nav_host()

    def _current_page():
        page = MagicMock()
        page.get_nav_state = MagicMock(return_value=dict(states.get(nav._current_page, {})))
        return page

    nav._get_current_page = _current_page
    return nav


def _fresh_nav_host() -> NavigationMixin:
    """Build a NavigationMixin instance with the state attrs it reads."""
    nav = NavigationMixin()
    nav._current_page = ""
    nav._current_page_kwargs = {}
    nav._nav_stack = []
    nav._forward_stack = []
    nav._page_state_cache = {}
    nav._active_library_playlist_id = None
    nav._sidebar_per_page = {}
    nav._sidebar_default = True
    nav._lyrics_sidebar_open = False
    nav._nav_idle = asyncio.Event()
    nav._nav_idle.set()
    nav._nav_pending = None

    # Stub the methods navigate_to calls on self.
    container = MagicMock()
    container.remove_children = AsyncMock()
    container.mount = AsyncMock()
    container.children = []

    # query_one is called with (#main-content, Container) and (#app-footer, FooterBar).
    # The footer call is inside a try/except so raising is fine; we return the
    # container for the first call and raise for the footer.
    def _query_one(selector, *args, **kwargs):
        if "#main-content" in selector:
            return container
        raise Exception("no footer in tests")

    nav.query_one = MagicMock(side_effect=_query_one)
    nav._create_page = MagicMock(side_effect=lambda name, **kw: MagicMock(_name=name, _kw=kw))
    nav._apply_playlist_sidebar = MagicMock()
    nav._apply_lyrics_sidebar = MagicMock()
    return nav


class TestNavigateTo:
    async def test_unknown_page_rejected_silently(self):
        nav = _fresh_nav_host()
        await nav.navigate_to("nonsense")
        assert nav._current_page == ""
        nav._create_page.assert_not_called()

    async def test_navigates_to_valid_page(self):
        nav = _fresh_nav_host()
        await nav.navigate_to("library")
        assert nav._current_page == "library"
        assert nav._nav_stack == []  # Nothing to push (no previous page)

    async def test_forward_nav_pushes_current_onto_stack(self):
        nav = _fresh_nav_host()
        await nav.navigate_to("library")
        await nav.navigate_to("search")
        assert nav._current_page == "search"
        assert nav._nav_stack == [("library", {})]

    async def test_back_nav_does_not_push_current_onto_stack(self):
        """Regression: pushing on back creates infinite ping-pong."""
        nav = _fresh_nav_host()
        await nav.navigate_to("library")
        await nav.navigate_to("search")
        # Stack: [library], current: search
        await nav.navigate_to("back")
        # After back: current is library, stack is empty (we popped + did not push search).
        assert nav._current_page == "library"
        assert nav._nav_stack == []

    async def test_back_with_empty_stack_goes_to_library(self):
        nav = _fresh_nav_host()
        await nav.navigate_to("back")
        assert nav._current_page == "library"

    async def test_state_cache_round_trip(self):
        """Forward nav with no kwargs restores cached state from prior visit."""
        nav = _fresh_nav_host()
        # First visit to library — pretend page reports state.
        await nav.navigate_to("library")
        page = nav._create_page.return_value
        page.get_nav_state = MagicMock(return_value={"playlist_id": "PL1", "cursor_row": 5})
        # Simulate _get_current_page returning that page.
        nav._get_current_page = MagicMock(return_value=page)
        # Navigate away — state should be cached.
        await nav.navigate_to("search")
        assert nav._page_state_cache.get("library") == {
            "playlist_id": "PL1",
            "cursor_row": 5,
        }
        # Navigate back to library with no explicit kwargs — state restored.
        await nav.navigate_to("library")
        # _create_page receives the cached kwargs.
        last_call = nav._create_page.call_args
        assert last_call.kwargs.get("playlist_id") == "PL1"
        assert last_call.kwargs.get("cursor_row") == 5

    async def test_nav_stack_capped_at_20(self):
        nav = _fresh_nav_host()
        # Navigate through 25 page changes — alternating to force pushes.
        names = ["library", "search"]
        for i in range(25):
            await nav.navigate_to(names[i % 2])
        assert len(nav._nav_stack) <= 20


class TestStackSnapshotFreshness:
    """Back/forward stack snapshots must capture the page's CURRENT state.

    Regression (T14): the snapshots were built from `_page_state_cache`
    BEFORE the fresh get_nav_state refresh ran, so on a page visited ≥2
    times they captured the previous visit's cursor, and the stale
    non-empty kwargs suppressed the fresh-cache fallback on restore.
    """

    async def test_forward_restores_latest_state_after_revisit(self):
        states = {"search": {"cursor_row": 1}}
        nav = _nav_with_page_states(states)
        await nav.navigate_to("search")
        await nav.navigate_to("library")  # caches search: cursor 1
        await nav.navigate_to("search")  # revisit — cursor 1 restored
        states["search"] = {"cursor_row": 5}  # user moves cursor
        await nav.navigate_to("back")  # forward-stack snapshot of search taken here
        await nav.navigate_to("forward")  # return to search
        assert nav._create_page.call_args.kwargs.get("cursor_row") == 5

    async def test_back_restores_latest_state_after_revisit(self):
        states = {"search": {"cursor_row": 1}}
        nav = _nav_with_page_states(states)
        await nav.navigate_to("search")
        await nav.navigate_to("library")  # caches search: cursor 1
        await nav.navigate_to("back")  # → search, cursor 1
        states["search"] = {"cursor_row": 5}  # user moves cursor
        await nav.navigate_to("forward")  # nav-stack snapshot of search taken here
        await nav.navigate_to("back")  # → search: must restore cursor 5
        assert nav._create_page.call_args.kwargs.get("cursor_row") == 5


class TestPurgePlaylistNavState:
    """Deleting a playlist must drop every cached nav reference to it —
    otherwise a bare footer-nav to Library reopens the deleted playlist."""

    async def test_drops_dead_cache_and_stack_entries(self):
        nav = _fresh_nav_host()
        nav._page_state_cache["library"] = {"playlist_id": "VLPL9", "cursor_row": 3}
        nav._nav_stack = [("library", {"playlist_id": "VLPL9"}), ("search", {"cursor_row": 2})]
        nav._forward_stack = [("library", {"playlist_id": "PL9"})]
        nav._purge_playlist_nav_state({"VLPL9", "PL9"})
        assert "library" not in nav._page_state_cache
        assert nav._nav_stack == [("search", {"cursor_row": 2})]
        assert nav._forward_stack == []

    async def test_keeps_unrelated_state(self):
        nav = _fresh_nav_host()
        nav._page_state_cache["library"] = {"playlist_id": "VLPL_OTHER", "cursor_row": 3}
        nav._nav_stack = [("library", {"playlist_id": "VLPL_OTHER"})]
        nav._forward_stack = [("search", {"cursor_row": 4})]
        nav._purge_playlist_nav_state({"VLPL9", "PL9"})
        assert nav._page_state_cache["library"] == {
            "playlist_id": "VLPL_OTHER",
            "cursor_row": 3,
        }
        assert nav._nav_stack == [("library", {"playlist_id": "VLPL_OTHER"})]
        assert nav._forward_stack == [("search", {"cursor_row": 4})]

    async def test_bare_nav_to_library_after_purge_shows_default_view(self):
        """The T14 repro: delete playlist → footer-click Library."""
        states = {"library": {"playlist_id": "VLPL9", "cursor_row": 3}}
        nav = _nav_with_page_states(states)
        await nav.navigate_to("library", playlist_id="VLPL9")
        await nav.navigate_to("search")  # caches library's (now-dead) state
        nav._purge_playlist_nav_state({"VLPL9", "PL9"})
        await nav.navigate_to("library")  # bare footer nav — no kwargs
        assert "playlist_id" not in nav._create_page.call_args.kwargs

    async def test_clears_last_played_fallback(self):
        """LibraryPage.on_mount falls back to app._active_library_playlist_id
        when mounted without a playlist_id — a dead id here would auto-reload
        the deleted playlist."""
        nav = _fresh_nav_host()
        nav._active_library_playlist_id = "VLPL9"
        nav._purge_playlist_nav_state({"VLPL9", "PL9"})
        assert nav._active_library_playlist_id is None

    async def test_keeps_unrelated_last_played_fallback(self):
        nav = _fresh_nav_host()
        nav._active_library_playlist_id = "VLPL_OTHER"
        nav._purge_playlist_nav_state({"VLPL9", "PL9"})
        assert nav._active_library_playlist_id == "VLPL_OTHER"

    async def test_drops_dead_playlist_context_entries(self):
        """Playlist context pages store the id as context_id, not playlist_id
        (search/browse → navigate_to("context", context_type="playlist", ...))."""
        nav = _fresh_nav_host()
        nav._nav_stack = [
            ("context", {"context_type": "playlist", "context_id": "PL9"}),
            ("context", {"context_type": "album", "context_id": "PL9"}),
        ]
        nav._forward_stack = [("context", {"context_type": "playlist", "context_id": "VLPL9"})]
        nav._purge_playlist_nav_state({"VLPL9", "PL9"})
        assert nav._nav_stack == [("context", {"context_type": "album", "context_id": "PL9"})]
        assert nav._forward_stack == []

    async def test_empty_fresh_state_drops_stale_cache(self):
        """A page reporting {} as its fresh state must clear its cache entry —
        otherwise a bare nav later restores state the user deliberately left."""
        states = {"library": {"playlist_id": "VLPL1", "cursor_row": 2}}
        nav = _nav_with_page_states(states)
        await nav.navigate_to("library", playlist_id="VLPL1")
        await nav.navigate_to("search")  # caches library state
        await nav.navigate_to("library", playlist_id=None)  # user leaves the playlist
        states["library"] = {}  # plain library reports no state
        await nav.navigate_to("search")  # refresh must DROP the stale cache
        await nav.navigate_to("library")  # bare footer nav
        assert "playlist_id" not in nav._create_page.call_args.kwargs

    async def test_purge_refreshes_header_buttons(self):
        """Purging can empty the stacks — the header back/forward buttons
        must be refreshed or a stale back button stays clickable."""
        nav = _fresh_nav_host()
        nav._nav_stack = [("library", {"playlist_id": "VLPL9"})]
        nav._forward_stack = [("library", {"playlist_id": "PL9"})]
        header = MagicMock()

        def _query_one(selector, *args, **kwargs):
            if "#app-header" in selector:
                return header
            raise Exception("not in test")

        nav.query_one = MagicMock(side_effect=_query_one)
        nav._purge_playlist_nav_state({"VLPL9", "PL9"})
        header.set_back_visible.assert_called_once_with(False)
        header.set_forward_visible.assert_called_once_with(False)

    async def test_purge_after_navigating_off_dead_playlist(self):
        """Delete-while-open ordering: navigate_to refreshes the dying page's
        state back into the cache, so the delete path purges AFTER navigating.
        Mirror that order here and check the cache stays clean."""
        states = {"library": {"playlist_id": "VLPL9", "cursor_row": 3}}
        nav = _nav_with_page_states(states)
        await nav.navigate_to("library", playlist_id="VLPL9")
        await nav.navigate_to("library", playlist_id=None)  # delete path: navigate first
        nav._purge_playlist_nav_state({"VLPL9", "PL9"})  # then purge
        assert "library" not in nav._page_state_cache


class TestPageNames:
    def test_no_duplicates(self):
        assert len(PAGE_NAMES) == len(set(PAGE_NAMES))

    def test_library_is_a_valid_page(self):
        """library is the back-navigation fallback — must be valid."""
        assert "library" in PAGE_NAMES
