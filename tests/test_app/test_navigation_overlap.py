"""Overlapping navigations never mount two pages at once (#145).

Uses a real Textual app so ``remove_children`` and ``mount`` really yield,
which is where two page swaps used to interleave.
"""

from __future__ import annotations

import asyncio
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.message import Message
from textual.widgets import Static

from ytm_player.app._navigation import NavigationMixin
from ytm_player.app._sidebar import SidebarMixin


class _OpenBrowse(Message):
    """Ask a page to navigate from its own handler, like a search result click."""


class _Page(Static):
    def get_nav_state(self) -> dict[str, Any]:
        return {}

    async def on__open_browse(self, message: _OpenBrowse) -> None:
        # Still inside this handler when the other swap starts removing the page.
        await asyncio.sleep(0.05)
        await self.app.navigate_to("browse")  # type: ignore[attr-defined]


class _Host(NavigationMixin, App):
    """The navigation mixin over a bare #main-content container."""

    def __init__(self) -> None:
        super().__init__()
        self._current_page = ""
        self._current_page_kwargs = {}
        self._nav_stack = []
        self._forward_stack = []
        self._page_state_cache = {}
        self._active_library_playlist_id = None
        self._sidebar_per_page = {}
        self._sidebar_default = True
        self._lyrics_sidebar_open = False
        self._context_seq = 0
        self._active_pane = "content"
        self._nav_idle = asyncio.Event()
        self._nav_idle.set()
        self._nav_pending = None

    def compose(self) -> ComposeResult:
        yield Container(id="main-content")

    def _create_page(self, page_name: str, **kwargs: Any) -> _Page:
        return _Page(page_name, id=f"page-{page_name}")

    def _apply_playlist_sidebar(self, visible: bool) -> None:
        pass

    def _apply_lyrics_sidebar(self, visible: bool) -> None:
        pass

    def mounted_pages(self) -> list[str | None]:
        return [child.id for child in self.query_one("#main-content").children]


async def test_two_swaps_to_the_same_page_do_not_mount_it_twice():
    app = _Host()
    async with app.run_test() as pilot:
        await app.navigate_to("search")
        # Master raised DuplicateIds for 'page-library' here (#145).
        await asyncio.gather(app.navigate_to("library"), app.navigate_to("library"))
        await pilot.pause()

        # Run one after the other: Library, then the same page again goes back.
        assert app.mounted_pages() == ["page-search"]
        assert app._current_page == "search"


async def test_two_swaps_to_different_pages_leave_only_the_latest():
    app = _Host()
    async with app.run_test() as pilot:
        await app.navigate_to("search")
        await asyncio.gather(app.navigate_to("queue"), app.navigate_to("library"))
        await pilot.pause()

        assert app.mounted_pages() == ["page-library"]
        assert app._current_page == "library"


async def test_a_page_navigating_while_it_is_being_removed_does_not_hang():
    app = _Host()
    async with app.run_test() as pilot:
        await app.navigate_to("search")
        app.query_one("#page-search").post_message(_OpenBrowse())
        await asyncio.sleep(0.01)  # the search page is now inside its handler

        # Removing the search page waits for that handler to return; a lock
        # here would leave both sides waiting on each other.
        await asyncio.wait_for(app.navigate_to("queue"), timeout=3)
        await pilot.pause(0.2)

        assert app.mounted_pages() == ["page-browse"]
        assert app._current_page == "browse"


async def test_a_failed_swap_does_not_block_later_navigation(monkeypatch):
    app = _Host()
    async with app.run_test() as pilot:
        await app.navigate_to("search")

        def _broken(page_name: str, **kwargs: Any) -> _Page:
            raise RuntimeError("page failed to build")

        monkeypatch.setattr(app, "_create_page", _broken)
        try:
            await app.navigate_to("queue")
        except RuntimeError:
            pass
        monkeypatch.undo()

        await app.navigate_to("library")
        await pilot.pause()

        assert app.mounted_pages() == ["page-library"]
        assert app._nav_idle.is_set()


class _StatePage(Static):
    """A page that reports its kwargs as nav state and can hold its mount open."""

    def __init__(self, page_name: str, state: dict[str, Any]) -> None:
        super().__init__(page_name, id=f"page-{page_name}")
        self.state = dict(state)

    def get_nav_state(self) -> dict[str, Any]:
        return dict(self.state)

    async def on_mount(self) -> None:
        app = self.app
        if isinstance(app, _DeleteHost) and app.hold_mount:
            app.hold_mount = False
            app.mount_entered.set()
            await app.release_mount.wait()


class _FakeSidebar:
    def query_one(self, selector: str, widget_type: type) -> Any:
        return self

    def remove_item(self, playlist_id: str) -> None:
        pass


class _FakeYTMusic:
    async def delete_playlist(self, playlist_id: str) -> str:
        return "success"


class _DeleteHost(_Host):
    """The real playlist-delete flow over the real navigation."""

    def __init__(self) -> None:
        super().__init__()
        self.ytmusic = _FakeYTMusic()
        self.hold_mount = False
        self.mount_entered = asyncio.Event()
        self.release_mount = asyncio.Event()

    def _create_page(self, page_name: str, **kwargs: Any) -> _StatePage:  # type: ignore[override]
        return _StatePage(page_name, kwargs)

    def query_one(self, selector: Any, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        if selector == "#playlist-sidebar":
            return _FakeSidebar()
        return super().query_one(selector, *args, **kwargs)

    def notify(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        pass


async def test_deleting_a_playlist_during_a_page_switch_keeps_it_out_of_history():
    app = _DeleteHost()
    async with app.run_test():
        await app.navigate_to("search")
        await app.navigate_to("context", context_type="playlist", context_id="PL_DEAD")
        await app.navigate_to("library", playlist_id="PL_DEAD")

        # Back to the playlist's context page, held mid-mount.
        app.hold_mount = True
        back = asyncio.create_task(app.navigate_to("back"))
        await app.mount_entered.wait()
        delete = asyncio.create_task(
            SidebarMixin._delete_sidebar_playlist(
                app,  # type: ignore[arg-type]
                {"playlistId": "PL_DEAD", "title": "Dead"},
            )
        )
        await asyncio.sleep(0.05)
        app.release_mount.set()
        await back
        await asyncio.wait_for(delete, timeout=3)

        # The delete acted on the page Back opened, and purged after it.
        assert app._current_page == "library"
        assert app._current_page_kwargs == {"playlist_id": None}
        history = app._nav_stack + app._forward_stack
        assert all("PL_DEAD" not in str(kwargs) for _, kwargs in history)


async def test_deleting_a_playlist_during_two_quick_backs_keeps_it_out_of_history():
    """A second Back can start before the delete resumes; the delete waits for it too."""
    app = _DeleteHost()
    async with app.run_test():
        await app.navigate_to("search")
        await app.navigate_to("context", context_type="playlist", context_id="PL_DEAD")
        await app.navigate_to("library", playlist_id="PL_DEAD")
        await app.navigate_to("queue")

        async def two_backs() -> None:
            await app.navigate_to("back")
            await app.navigate_to("back")

        app.hold_mount = True
        backs = asyncio.create_task(two_backs())
        await app.mount_entered.wait()
        delete = asyncio.create_task(
            SidebarMixin._delete_sidebar_playlist(
                app,  # type: ignore[arg-type]
                {"playlistId": "PL_DEAD", "title": "Dead"},
            )
        )
        await asyncio.sleep(0.05)
        app.release_mount.set()
        await asyncio.wait_for(asyncio.gather(backs, delete), timeout=3)

        assert app._current_page == "library"
        assert app._current_page_kwargs == {"playlist_id": None}
        history = app._nav_stack + app._forward_stack
        assert all("PL_DEAD" not in str(kwargs) for _, kwargs in history)
