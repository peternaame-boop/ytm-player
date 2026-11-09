"""T6 (popups): popup titles, menu labels, messages and toasts show outside text as typed.

The actions menu title is a track/album/artist/playlist name; the artist
picker's menu labels are artist names; the confirm message and the
playlist picker's status line and toasts carry playlist names. Parsed as
Textual markup a lowercase ``[tag]`` vanished, ``[/x]`` raised
``MarkupError`` and ``[@click=...]`` became a clickable action. Each test
pushes the real popup in a headless app and reads back what it renders.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.content import Content
from textual.widgets import Label, ListView, Static
from textual.widgets._toast import Toast

from ytm_player.app._track_actions import TrackActionsMixin
from ytm_player.config.settings import Settings
from ytm_player.services.ytmusic import mutation_failure_suffix
from ytm_player.ui.popups.actions import ActionsPopup, _ActionItem
from ytm_player.ui.popups.confirm_popup import ConfirmPopup
from ytm_player.ui.popups.country_picker import CountryPickerModal
from ytm_player.ui.popups.input_popup import InputPopup
from ytm_player.ui.popups.playlist_picker import PlaylistPicker

# Lowercase and uppercase tags: rich.markup.escape only escapes the lowercase
# form, while Textual 8 markup swallows both.
LIVE = "Song [live] [Live] [MV]"
BAD_CLOSE = "old [/x] mix"
CLICK = "[@click=app.quit]x[/]"


def _shown(widget: Static) -> Content:
    visual = widget.visual
    assert isinstance(visual, Content)
    return visual


def _literal(content: Content) -> str:
    assert not [s for s in content.spans if "@click" in str(s.style)]
    return content.plain


def _toasts(app: App) -> list[str]:
    return [_literal(toast.render()) for toast in app.screen.query(Toast)]


class _Host(TrackActionsMixin, App[None]):
    """Counts ``app.quit`` instead of exiting, so a leaked click action is observable."""

    def __init__(self) -> None:
        super().__init__()
        self.quit_calls = 0
        self.settings = Settings()
        self.ytmusic = MagicMock()
        self.ytmusic.get_library_playlists = AsyncMock(return_value=[])
        self.player = MagicMock(current_track=None)
        self._current_page = "search"
        self._current_page_kwargs: dict[str, Any] = {}

    def get_css_variables(self) -> dict[str, str]:
        return {**super().get_css_variables(), "selected-item": "#3a3a3a"}

    def compose(self) -> ComposeResult:
        yield Static("host")

    async def action_quit(self) -> None:
        self.quit_calls += 1


@pytest.fixture(autouse=True)
def _isolated():
    with (
        patch("ytm_player.config.settings._settings", Settings()),
        patch("ytm_player.ui.popups.playlist_picker._load_recent_ids", return_value=[]),
        patch("ytm_player.ui.popups.playlist_picker._record_recent"),
    ):
        yield


# ── Actions menu ─────────────────────────────────────────────────────


def _labels(popup: ActionsPopup) -> list[str]:
    return [_literal(_shown(item.query_one(Label))) for item in popup.query(_ActionItem)]


@pytest.mark.parametrize("title", [LIVE, BAD_CLOSE, CLICK])
async def test_actions_title_shows_the_item_name_as_typed(title: str) -> None:
    app = _Host()
    async with app.run_test() as pilot:
        popup = ActionsPopup({"title": title, "video_id": "v1"})
        await app.push_screen(popup)
        await pilot.pause()

        assert _literal(_shown(popup.query_one("#actions-title", Static))) == title
        # The app's own labels are untouched.
        assert _labels(popup)[:3] == ["Play", "Play Next", "Add to Queue"]


async def test_artist_picker_labels_show_artist_names_as_typed() -> None:
    track = {
        "video_id": "v1",
        "title": "Duet",
        "artists": [
            {"name": LIVE, "id": "UC1"},
            {"name": BAD_CLOSE, "id": "UC2"},
            {"name": CLICK, "id": "UC3"},
        ],
    }
    app = _Host()
    async with app.run_test() as pilot:
        app._open_actions_for_artist(track)
        await pilot.pause()

        popup = app.screen
        assert isinstance(popup, ActionsPopup)
        assert _labels(popup) == [LIVE, BAD_CLOSE, CLICK]


async def test_clicking_an_artist_label_runs_no_markup_action() -> None:
    track = {
        "video_id": "v1",
        "title": "Duet",
        "artists": [{"name": "A", "id": "UC1"}, {"name": CLICK, "id": "UC2"}],
    }
    app = _Host()
    async with app.run_test() as pilot:
        app._open_actions_for_artist(track)
        await pilot.pause()
        popup = app.screen
        assert isinstance(popup, ActionsPopup)

        label = list(popup.query(_ActionItem))[1].query_one(Label)
        await pilot.click(label)
        await pilot.pause()

        assert app.quit_calls == 0


# ── Confirm and input popups ─────────────────────────────────────────


async def test_confirm_message_shows_playlist_name_as_typed() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        message = f"Are you sure you want to delete '{BAD_CLOSE} {CLICK} {LIVE}'?"
        popup = ConfirmPopup(message)
        await app.push_screen(popup)
        await pilot.pause()

        assert _literal(_shown(popup.query_one("#confirm-message", Static))) == message


async def test_input_popup_title_is_literal() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        title = f"Rename '{BAD_CLOSE}' {CLICK}"
        popup = InputPopup(title)
        await app.push_screen(popup)
        await pilot.pause()

        assert _literal(_shown(popup.query_one("#input-title", Static))) == title


@pytest.mark.parametrize("region", ["[/x]", "[Live]"])
async def test_country_picker_status_shows_the_configured_region_as_typed(region: str) -> None:
    app = _Host()
    async with app.run_test() as pilot:
        popup = CountryPickerModal(current_code=region)
        await app.push_screen(popup)
        await pilot.pause()

        assert _literal(_shown(popup.query_one("#picker-status", Static))) == f"Current: {region}"


# ── Playlist picker ──────────────────────────────────────────────────


async def _picker(app: _Host, pilot, playlists: list[dict[str, Any]]) -> PlaylistPicker:
    app.ytmusic.get_library_playlists = AsyncMock(return_value=playlists)
    picker = PlaylistPicker(["song"])
    await app.push_screen(picker)
    await pilot.pause()
    await asyncio.wait_for(app.workers.wait_for_complete(), 5)
    await pilot.pause()
    return picker


def _pick(picker: PlaylistPicker, playlist_id: str) -> None:
    view = picker.query_one("#playlist-list", ListView)
    children = list(view.children)
    item = next(c for c in children if getattr(c, "playlist_id", None) == playlist_id)
    picker.post_message(ListView.Selected(view, item, children.index(item)))


def _status(picker: PlaylistPicker) -> str:
    return _literal(_shown(picker.query_one("#picker-status", Static)))


async def test_picker_status_and_failure_toast_show_playlist_name_as_typed() -> None:
    app = _Host()
    async with app.run_test(notifications=True) as pilot:
        picker = await _picker(app, pilot, [{"playlistId": "MIX", "title": BAD_CLOSE}])
        release = asyncio.Event()

        async def add(*_args, **_kwargs):
            await release.wait()
            return "network"

        app.ytmusic.add_playlist_items = add
        _pick(picker, "MIX")
        await pilot.pause()
        assert _status(picker) == f"Adding to '{BAD_CLOSE}'..."

        release.set()
        await asyncio.wait_for(app.workers.wait_for_complete(), 5)
        await pilot.pause()

        assert _status(picker) == "Error"
        assert _toasts(app) == [
            f"Couldn't add to '{BAD_CLOSE}' — {mutation_failure_suffix('network')}"
        ]


async def test_picker_success_and_timeout_toasts_show_playlist_name_as_typed() -> None:
    app = _Host()
    async with app.run_test(notifications=True) as pilot:
        picker = await _picker(
            app, pilot, [{"playlistId": "A", "title": CLICK}, {"playlistId": "B", "title": LIVE}]
        )
        app.ytmusic.add_playlist_items = AsyncMock(return_value="timeout")
        _pick(picker, "B")
        await asyncio.wait_for(app.workers.wait_for_complete(), 5)
        await pilot.pause()
        assert _toasts(app) == [
            f"Couldn't confirm adding to '{LIVE}' — {mutation_failure_suffix('timeout')}"
        ]

        picker = await _picker(app, pilot, [{"playlistId": "A", "title": CLICK}])
        app.ytmusic.add_playlist_items = AsyncMock(return_value="success")
        _pick(picker, "A")
        await asyncio.wait_for(app.workers.wait_for_complete(), 5)
        await pilot.pause()

        assert f"Added 1 track to '{CLICK}'" in _toasts(app)
        assert app.quit_calls == 0


async def test_create_flow_status_and_toast_show_new_name_as_typed() -> None:
    app = _Host()
    async with app.run_test(notifications=True) as pilot:
        picker = await _picker(app, pilot, [])
        release = asyncio.Event()

        async def create(*_args, **_kwargs):
            await release.wait()
            return "success", "PLNEW"

        app.ytmusic.create_playlist = create
        app.ytmusic.add_playlist_items = AsyncMock(return_value="success")
        name = f"{BAD_CLOSE} {LIVE}"
        picker._on_create_result((name, "", "PRIVATE"))
        await pilot.pause()
        assert _status(picker) == f"Creating '{name}'..."

        release.set()
        await asyncio.wait_for(app.workers.wait_for_complete(), 5)
        await pilot.pause()

        assert f"Added 1 track to '{name}'" in _toasts(app)


async def test_picker_add_error_toast_shows_playlist_name_as_typed() -> None:
    app = _Host()
    async with app.run_test(notifications=True) as pilot:
        picker = await _picker(app, pilot, [{"playlistId": "MIX", "title": BAD_CLOSE}])
        app.ytmusic.add_playlist_items = AsyncMock(side_effect=RuntimeError("boom"))
        _pick(picker, "MIX")
        await asyncio.wait_for(app.workers.wait_for_complete(), 5)
        await pilot.pause()

        assert _toasts(app) == [f"Failed to add to '{BAD_CLOSE}'"]


NEW = f"{BAD_CLOSE} {LIVE}"


@pytest.mark.parametrize(
    ("add", "record_fails", "expected"),
    [
        pytest.param(
            AsyncMock(return_value="network"),
            False,
            f"Created '{NEW}' but couldn't add tracks — {mutation_failure_suffix('network')}",
            id="add-refused",
        ),
        pytest.param(
            AsyncMock(side_effect=RuntimeError("boom")),
            False,
            f"Created '{NEW}' but couldn't add tracks",
            id="add-raised",
        ),
        pytest.param(
            AsyncMock(return_value="success"),
            True,
            f"Created '{NEW}' but couldn't finish adding tracks",
            id="finish-raised",
        ),
        pytest.param(
            AsyncMock(return_value="duplicate"),
            False,
            f"Created '{NEW}' with nothing added",
            id="duplicate-declined",
        ),
    ],
)
async def test_create_flow_outcome_toasts_show_new_name_as_typed(
    add: AsyncMock, record_fails: bool, expected: str
) -> None:
    app = _Host()
    with patch(
        "ytm_player.ui.popups.playlist_picker._record_recent",
        side_effect=RuntimeError("disk") if record_fails else None,
    ):
        async with app.run_test(notifications=True) as pilot:
            picker = await _picker(app, pilot, [])
            app.ytmusic.create_playlist = AsyncMock(return_value=("success", "PLNEW"))
            app.ytmusic.add_playlist_items = add
            picker._on_create_result((NEW, "", "PRIVATE"))
            await asyncio.wait_for(app.workers.wait_for_complete(), 5)
            await pilot.pause()
            if isinstance(app.screen, ConfirmPopup):
                assert _literal(_shown(app.screen.query_one("#confirm-message", Static))) == (
                    f"This track is already in '{NEW}'.\nAdd anyway?"
                )
                await pilot.press("n")
                await asyncio.wait_for(app.workers.wait_for_complete(), 5)
                await pilot.pause()

            assert expected in _toasts(app)
