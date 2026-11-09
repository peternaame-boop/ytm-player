"""Rendered-width regression tests for the Recently Played status lines.

The All tab's partial-results notice and the description under the tab row
are longer than an 80-column terminal. Asserting on the string handed to
``update()`` proves nothing about what the user sees, so these mount the
real page in a headless Textual app and read back the rendered lines.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from textual.app import App, ComposeResult
from textual.geometry import Region
from textual.widget import Widget
from textual.widgets import Static

from ytm_player.ui.pages.recently_played import _TAB_ALL, _TAB_DESCRIPTIONS, RecentlyPlayedPage

_LOCAL = [
    {
        "video_id": f"loc{i}",
        "title": f"Local {i}",
        "artist": "Artist",
        "album": "Album",
        "duration_seconds": 100,
        "played_at": f"2026-09-06T12:{59 - i:02d}:00",
    }
    for i in range(3)
]
_FEED = [
    {
        "videoId": f"acc{i}",
        "title": f"Phone play {i}",
        "artists": [{"name": "Artist", "id": "A1"}],
        "album": {"name": "Album", "id": "AL1"},
        "duration": "2:00",
    }
    for i in range(2)
]


class _Host(App):
    """Minimal host exposing the attributes RecentlyPlayedPage reads."""

    def __init__(self, *, local, local_error=None, feed=None, ytmusic=True) -> None:
        super().__init__()
        self.history = MagicMock()
        if local_error is not None:
            self.history.get_recently_played = AsyncMock(side_effect=local_error)
        else:
            self.history.get_recently_played = AsyncMock(return_value=list(local))
        if ytmusic:
            self.ytmusic = MagicMock()
            self.ytmusic.get_history = AsyncMock(return_value=feed)
        else:
            self.ytmusic = None
        self._ytm_history = None
        self._ytm_history_pending = []
        self._ytm_history_pending_seq = 0

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        yield RecentlyPlayedPage(id="page")


def _rendered_text(widget: Widget) -> str:
    """The text actually painted for *widget*, wrapped lines joined by a space."""
    size = widget.outer_size
    strips = widget.render_lines(Region(0, 0, size.width, size.height))
    return " ".join(strip.text.strip() for strip in strips if strip.text.strip())


async def _render_all_tab(host: _Host, width: int) -> tuple[str, str]:
    async with host.run_test(size=(width, 24)) as pilot:
        await host.workers.wait_for_complete()
        await pilot.pause()
        page = host.query_one("#page", RecentlyPlayedPage)
        assert page._active_tab == _TAB_ALL
        footer = _rendered_text(page.query_one("#recent-footer", Static))
        description = _rendered_text(page.query_one("#recent-tab-desc", Static))
        return footer, description


@pytest.mark.parametrize("width", [80, 100, 120])
async def test_both_sources_footer_and_description_are_fully_visible(width: int) -> None:
    footer, description = await _render_all_tab(_Host(local=_LOCAL, feed=_FEED), width)

    assert footer == "5 tracks — local history first, then additional account history"
    assert description == _TAB_DESCRIPTIONS[_TAB_ALL]


@pytest.mark.parametrize("width", [80, 100, 120])
async def test_ytm_failure_notice_is_fully_visible(width: int) -> None:
    footer, description = await _render_all_tab(_Host(local=_LOCAL, feed=None), width)

    assert footer == "YT Music history couldn't be loaded; showing local history only — 3 tracks"
    assert description == _TAB_DESCRIPTIONS[_TAB_ALL]


@pytest.mark.parametrize("width", [80, 100, 120])
async def test_sign_in_notice_is_fully_visible(width: int) -> None:
    footer, _ = await _render_all_tab(_Host(local=_LOCAL, ytmusic=False), width)

    assert footer == (
        "Sign in to YT Music to include your account history; showing local history only — 3 tracks"
    )


@pytest.mark.parametrize("width", [80, 100, 120])
async def test_local_failure_notice_is_fully_visible(width: int) -> None:
    host = _Host(local=[], local_error=OSError("locked"), feed=_FEED)
    footer, _ = await _render_all_tab(host, width)

    assert footer == "Local history couldn't be loaded; showing YT Music history only — 2 tracks"
