"""Tests for keyboard pane-focus traversal (Ctrl+w h / l / w).

Covers issue #107: the keyboard could not focus the Playlists sidebar.
Three layers are exercised:

1. KeyMap — the new ``C-w`` prefixed sequences resolve to the focus actions.
2. SidebarMixin — the pane-focus state machine (which pane becomes active,
   auto-show, cycle order, reset-on-hide).
3. KeyHandlingMixin — movement/select/filter actions are routed to the
   active pane (sidebar / lyrics) instead of always the current page.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from ytm_player.app._keys import KeyHandlingMixin
from ytm_player.app._sidebar import PANE_CONTENT, PANE_LYRICS, PANE_PLAYLISTS, SidebarMixin
from ytm_player.config.keymap import Action, KeyMap, MatchResult

# ── Layer 1: KeyMap default bindings ────────────────────────────────


class TestPaneFocusBindings:
    def _km(self) -> KeyMap:
        km = KeyMap()
        km._load_defaults()
        return km

    def test_ctrl_w_h_focuses_pane_left(self):
        result, action = self._km().match(("C-w", "h"))
        assert result == MatchResult.EXACT
        assert action == Action.FOCUS_PANE_LEFT

    def test_ctrl_w_l_focuses_pane_right(self):
        result, action = self._km().match(("C-w", "l"))
        assert result == MatchResult.EXACT
        assert action == Action.FOCUS_PANE_RIGHT

    def test_ctrl_w_w_cycles_panes(self):
        result, action = self._km().match(("C-w", "w"))
        assert result == MatchResult.EXACT
        assert action == Action.FOCUS_PANE_CYCLE

    def test_ctrl_w_alone_is_pending(self):
        """Ctrl+w must wait for the direction key, not resolve immediately."""
        result, action = self._km().match(("C-w",))
        assert result == MatchResult.PENDING
        assert action is None

    def test_bare_l_still_likes(self):
        """The Ctrl+w prefix must not shadow the standalone like toggle."""
        result, action = self._km().match(("l",))
        assert result == MatchResult.EXACT
        assert action == Action.LIKE_TOGGLE


# ── Layer 2: SidebarMixin pane-focus state machine ──────────────────


def _make_sidebar_host(
    *,
    active_pane: str = PANE_CONTENT,
    sidebar_visible: bool = True,
    lyrics_open: bool = False,
) -> tuple[SidebarMixin, SimpleNamespace]:
    """Build a SidebarMixin with the state the pane helpers read, and the
    widget-touching collaborators stubbed out. Returns ``(host, mocks)``
    where ``mocks`` exposes the stubbed collaborators for assertions."""
    host = SidebarMixin()
    host._current_page = "library"
    host._sidebar_default = True
    host._sidebar_per_page = {"library": sidebar_visible}
    host._lyrics_sidebar_open = lyrics_open
    host._active_pane = active_pane

    # Stub the widget-touching collaborators so the state machine can be
    # tested without a mounted Textual app.
    list_view = MagicMock()
    list_view.index = 0
    list_view.children = [MagicMock()]
    query_one = MagicMock(return_value=list_view)
    apply_sidebar = MagicMock()
    focus_content = MagicMock()
    host.query_one = query_one
    host._apply_playlist_sidebar = apply_sidebar
    host._focus_content_widget = focus_content
    return host, SimpleNamespace(
        list_view=list_view,
        query_one=query_one,
        apply_sidebar=apply_sidebar,
        focus_content=focus_content,
    )


class TestVisiblePanes:
    def test_all_three_visible(self):
        host, _ = _make_sidebar_host(sidebar_visible=True, lyrics_open=True)
        assert host._visible_panes() == [PANE_PLAYLISTS, PANE_CONTENT, PANE_LYRICS]

    def test_content_only(self):
        host, _ = _make_sidebar_host(sidebar_visible=False, lyrics_open=False)
        assert host._visible_panes() == [PANE_CONTENT]

    def test_sidebar_and_content(self):
        host, _ = _make_sidebar_host(sidebar_visible=True, lyrics_open=False)
        assert host._visible_panes() == [PANE_PLAYLISTS, PANE_CONTENT]


class TestFocusPaneLeft:
    def test_from_content_focuses_playlists(self):
        host, mocks = _make_sidebar_host(active_pane=PANE_CONTENT)
        host._focus_pane_left()
        assert host._active_pane == PANE_PLAYLISTS
        mocks.list_view.focus.assert_called_once()

    def test_from_lyrics_steps_to_content(self):
        host, mocks = _make_sidebar_host(active_pane=PANE_LYRICS, lyrics_open=True)
        host._focus_pane_left()
        assert host._active_pane == PANE_CONTENT
        mocks.focus_content.assert_called_once()

    def test_auto_shows_hidden_sidebar(self):
        host, mocks = _make_sidebar_host(active_pane=PANE_CONTENT, sidebar_visible=False)
        host._focus_pane_left()
        # Sidebar was hidden — it must be revealed before focus.
        assert host._sidebar_per_page["library"] is True
        mocks.apply_sidebar.assert_called_once_with(True)
        assert host._active_pane == PANE_PLAYLISTS


class TestFocusPaneRight:
    def test_from_playlists_steps_to_content(self):
        host, _ = _make_sidebar_host(active_pane=PANE_PLAYLISTS)
        host._focus_pane_right()
        assert host._active_pane == PANE_CONTENT

    def test_from_content_enters_lyrics_when_open(self):
        host, _ = _make_sidebar_host(active_pane=PANE_CONTENT, lyrics_open=True)
        host._focus_pane_right()
        assert host._active_pane == PANE_LYRICS

    def test_from_content_stays_when_lyrics_closed(self):
        host, _ = _make_sidebar_host(active_pane=PANE_CONTENT, lyrics_open=False)
        host._focus_pane_right()
        assert host._active_pane == PANE_CONTENT

    def test_from_lyrics_stays(self):
        host, _ = _make_sidebar_host(active_pane=PANE_LYRICS, lyrics_open=True)
        host._focus_pane_right()
        assert host._active_pane == PANE_LYRICS


class TestCyclePane:
    def test_cycle_content_to_lyrics_to_playlists_and_back(self):
        host, _ = _make_sidebar_host(
            active_pane=PANE_CONTENT, sidebar_visible=True, lyrics_open=True
        )
        # Order is [playlists, content, lyrics]; cycle advances rightward.
        host._cycle_pane()
        assert host._active_pane == PANE_LYRICS
        host._cycle_pane()
        assert host._active_pane == PANE_PLAYLISTS
        host._cycle_pane()
        assert host._active_pane == PANE_CONTENT

    def test_cycle_with_only_content_is_noop(self):
        host, _ = _make_sidebar_host(
            active_pane=PANE_CONTENT, sidebar_visible=False, lyrics_open=False
        )
        host._cycle_pane()
        assert host._active_pane == PANE_CONTENT

    def test_cycle_skips_hidden_lyrics(self):
        host, _ = _make_sidebar_host(
            active_pane=PANE_CONTENT, sidebar_visible=True, lyrics_open=False
        )
        # Only [playlists, content] are visible.
        host._cycle_pane()
        assert host._active_pane == PANE_PLAYLISTS
        host._cycle_pane()
        assert host._active_pane == PANE_CONTENT


class TestLyricsFocusGuard:
    def test_focus_lyrics_when_closed_is_noop(self):
        host, _ = _make_sidebar_host(active_pane=PANE_CONTENT, lyrics_open=False)
        host._focus_pane(PANE_LYRICS)
        # Lyrics pane is hidden — focus must not move there.
        assert host._active_pane == PANE_CONTENT


class TestResetOnHide:
    def test_hiding_focused_sidebar_resets_to_content(self):
        host, mocks = _make_sidebar_host(active_pane=PANE_PLAYLISTS)
        # Exercise the real _apply_playlist_sidebar to hit the reset guard;
        # make the widget query degrade gracefully (no mounted app).
        del host._apply_playlist_sidebar  # restore the real bound method
        host.query_one = MagicMock(side_effect=Exception("no widget in tests"))
        host._apply_playlist_sidebar(False)
        assert host._active_pane == PANE_CONTENT
        mocks.focus_content.assert_called_once()


# ── Layer 3: KeyHandlingMixin action routing ────────────────────────


def _make_key_host(active_pane: str) -> tuple[KeyHandlingMixin, SimpleNamespace]:
    host = KeyHandlingMixin()
    host._active_pane = active_pane
    sidebar = MagicMock()  # PlaylistSidebar stand-in
    lyrics = MagicMock()
    lyrics.handle_action = AsyncMock()
    page = MagicMock()
    page.handle_action = AsyncMock()

    def _query_one(selector, *args, **kwargs):
        if "#playlist-sidebar" in selector:
            return sidebar
        if "#lyrics-sidebar" in selector:
            return lyrics
        raise Exception(f"unexpected query: {selector}")

    host.query_one = MagicMock(side_effect=_query_one)
    host._get_current_page = MagicMock(return_value=page)
    return host, SimpleNamespace(sidebar=sidebar, lyrics=lyrics, page=page)


class TestRouteNavigationAction:
    async def test_playlists_pane_receives_movement(self):
        host, mocks = _make_key_host("playlists")
        await host._route_navigation_action(Action.MOVE_DOWN, 1)
        mocks.sidebar.handle_sidebar_action.assert_called_once_with(Action.MOVE_DOWN, 1)
        mocks.page.handle_action.assert_not_called()

    async def test_playlists_pane_receives_filter(self):
        host, mocks = _make_key_host("playlists")
        await host._route_navigation_action(Action.FILTER, 1)
        mocks.sidebar.handle_sidebar_action.assert_called_once_with(Action.FILTER, 1)
        mocks.page.handle_action.assert_not_called()

    async def test_playlists_pane_non_sidebar_action_falls_through(self):
        """SORT_TITLE isn't a sidebar action — it must reach the page even
        while the Playlists pane is active."""
        host, mocks = _make_key_host("playlists")
        await host._route_navigation_action(Action.SORT_TITLE, 1)
        mocks.sidebar.handle_sidebar_action.assert_not_called()
        mocks.page.handle_action.assert_called_once_with(Action.SORT_TITLE, 1)

    async def test_lyrics_pane_receives_scroll(self):
        host, mocks = _make_key_host("lyrics")
        await host._route_navigation_action(Action.MOVE_DOWN, 2)
        mocks.lyrics.handle_action.assert_awaited_once_with(Action.MOVE_DOWN, 2)
        mocks.page.handle_action.assert_not_called()

    async def test_lyrics_pane_select_falls_through(self):
        """The lyrics pane has no select — SELECT reaches the page."""
        host, mocks = _make_key_host("lyrics")
        await host._route_navigation_action(Action.SELECT, 1)
        mocks.lyrics.handle_action.assert_not_called()
        mocks.page.handle_action.assert_called_once_with(Action.SELECT, 1)

    async def test_content_pane_routes_to_page(self):
        host, mocks = _make_key_host("content")
        await host._route_navigation_action(Action.MOVE_DOWN, 1)
        mocks.page.handle_action.assert_called_once_with(Action.MOVE_DOWN, 1)
