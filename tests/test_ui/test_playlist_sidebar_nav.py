"""Pinned sidebar navigation; Browse stays in the footer instead."""

from __future__ import annotations

from unittest.mock import MagicMock

from textual.app import App, ComposeResult

from ytm_player.ui.playback_bar import FooterBar
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
    def test_removed_browse_item_does_not_dispatch(self):
        sidebar, posted = _make_sidebar()
        sidebar.on_click(_click_event("ps-nav-browse"))

        posted.assert_not_called()

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


async def test_sidebar_omits_browse_but_footer_keeps_it():
    class Host(App):
        def compose(self) -> ComposeResult:
            yield PlaylistSidebar()
            yield FooterBar()

    app = Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert [item.id for item in app.query(".ps-pinned-item")] == [
            "ps-nav-liked",
            "ps-nav-recent",
            "ps-nav-discovery",
        ]
        assert not app.query("#ps-nav-browse")
        assert app.query_one("#footer-browse").display
