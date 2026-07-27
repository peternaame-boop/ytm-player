"""Tests for PlaylistSidebar's pinned nav item clicks.

Covers the two new pinned items added alongside the existing Liked Songs /
Recently Played / Discovery Mix ones: Browse and New Release Mix.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from ytm_player.ui.sidebars.playlist_sidebar import PlaylistSidebar


def _make_sidebar() -> tuple[PlaylistSidebar, MagicMock]:
    sidebar = PlaylistSidebar.__new__(PlaylistSidebar)
    posted = MagicMock(name="post_message")
    object.__setattr__(sidebar, "post_message", posted)
    return sidebar, posted


def _click_event(widget_id: str) -> MagicMock:
    event = MagicMock(name="click")
    event.widget = MagicMock(id=widget_id)
    return event


class TestPinnedNavClicks:
    def test_browse_click_posts_nav_item_clicked_browse(self):
        sidebar, posted = _make_sidebar()
        sidebar.on_click(_click_event("ps-nav-browse"))

        posted.assert_called_once()
        (message,), _ = posted.call_args
        assert isinstance(message, PlaylistSidebar.NavItemClicked)
        assert message.nav_id == "browse"

    def test_new_release_mix_click_posts_nav_item_clicked(self):
        sidebar, posted = _make_sidebar()
        sidebar.on_click(_click_event("ps-nav-new-release"))

        posted.assert_called_once()
        (message,), _ = posted.call_args
        assert isinstance(message, PlaylistSidebar.NavItemClicked)
        assert message.nav_id == "new_release_mix"

    def test_discovery_mix_click_still_works(self):
        """Regression: adding new pinned items must not disturb this one."""
        sidebar, posted = _make_sidebar()
        sidebar.on_click(_click_event("ps-nav-discovery"))

        posted.assert_called_once()
        (message,), _ = posted.call_args
        assert message.nav_id == "discovery_mix"

    def test_click_on_unrelated_widget_posts_nothing(self):
        sidebar, posted = _make_sidebar()
        sidebar.on_click(_click_event("something-else"))

        posted.assert_not_called()
