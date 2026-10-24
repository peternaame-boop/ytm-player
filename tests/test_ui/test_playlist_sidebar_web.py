"""The sidebar's playlists as the web app lists them: name over owner, and a New playlist button."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import ListView

from ytm_player.ui.sidebars.playlist_sidebar import LibraryPanel

ITEMS = [
    {"playlistId": "LM", "title": "Liked Music", "description": "Auto playlist"},
    {"playlistId": "PL1", "title": "Road trip", "description": "Synx • 52 tracks",
     "author": [{"name": "Synx", "id": "UC1"}], "count": "52"},
    {"playlistId": "PL2", "title": "No subtitle"},
]  # fmt: skip


class _Host(App):
    def __init__(self) -> None:
        super().__init__()
        self.new_requests = 0

    def compose(self) -> ComposeResult:
        yield LibraryPanel("Playlists", id="ps-playlists", instant_select=True)

    def on_library_panel_new_requested(self, _message: LibraryPanel.NewRequested) -> None:
        self.new_requests += 1


def test_owner_is_the_author_or_else_the_subtitle():
    assert LibraryPanel._owner(ITEMS[0]) == "Auto playlist"
    assert LibraryPanel._owner(ITEMS[1]) == "Synx"
    assert LibraryPanel._owner(ITEMS[2]) == ""


async def test_rows_show_the_name_over_the_owner():
    app = _Host()
    async with app.run_test(size=(40, 24)) as pilot:
        panel = app.query_one(LibraryPanel)
        panel.load_items(ITEMS)
        await pilot.pause()
        rows = [str(item.query_one("Static").render()) for item in app.query_one(ListView).children]
        assert rows == ["Liked Music\nAuto playlist", "Road trip\nSynx", "No subtitle"]


async def test_the_new_playlist_button_shows_when_asked_and_posts_a_request():
    app = _Host()
    async with app.run_test(size=(40, 24)) as pilot:
        panel = app.query_one(LibraryPanel)
        panel.load_items(ITEMS)
        button = app.query_one("#ps-playlists-new")
        assert not button.display or not button.has_class("visible")
        panel.show_new_button()
        await pilot.pause()
        assert button.has_class("visible")
        await pilot.click("#ps-playlists-new")
        await pilot.pause()
        assert app.new_requests == 1
