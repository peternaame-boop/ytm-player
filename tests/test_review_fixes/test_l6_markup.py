"""T6 (Spotify import popup): outside text is never parsed as markup.

Track, artist, playlist and candidate names, the typed playlist name and
error text from services used to be interpolated into markup strings: a
bracketed word vanished, ``[/x]`` raised ``MarkupError`` and
``[@click=...]`` became a clickable action. The popup's own styling (coloured
result symbols, the error colour, the bold disambiguation prefix) must still
render.
"""

from __future__ import annotations

import asyncio

import pytest
from textual.app import App, ComposeResult
from textual.content import Content, Span
from textual.widgets import Button, Input, Label, ListView, Static
from textual.widgets._toast import Toast

import ytm_player.services.spotify_import as spotify_service
from ytm_player.ui.popups.spotify_import import (
    SpotifyImportPopup,
    _CandidateItem,
    _ResultItem,
    _TabLabel,
)
from ytm_player.ui.theme import get_theme

_URL = "https://open.spotify.com/playlist/abc123"

_PAYLOADS = [
    pytest.param("Song [live]", id="lowercase-bracket-word"),
    pytest.param("Song [Live] [MV]", id="capitalised-bracket-words"),
    pytest.param("a [/x] b", id="malformed-closing-tag"),
    pytest.param("[@click=app.probe]x[/]", id="click-action"),
]

# Every hostile shape at once, for the sinks that only need one run.
_HOSTILE_NAME = "Mix [Live] a [/x] b [@click=app.probe]go[/]"


class _FakeYTMusic:
    def __init__(
        self,
        candidates: list[dict] | None = None,
        add_results: list[str] | None = None,
        create_error: Exception | None = None,
    ) -> None:
        self.candidates = candidates or []
        self.add_results = list(add_results or [])
        self.create_error = create_error
        self.search_calls = 0
        self.search_gate_after: int | None = None
        self.search_gate = asyncio.Event()
        self.search_blocked = asyncio.Event()
        self.create_started = asyncio.Event()
        self.create_gate = asyncio.Event()
        self.create_gate.set()

    async def search(self, query: str, filter: str | None = None, limit: int = 5) -> list:
        self.search_calls += 1
        if self.search_gate_after is not None and self.search_calls > self.search_gate_after:
            self.search_blocked.set()
            await self.search_gate.wait()
        return [dict(c) for c in self.candidates]

    async def create_playlist(self, name: str, desc: str = "") -> tuple[str, str]:
        self.create_started.set()
        await self.create_gate.wait()
        if self.create_error is not None:
            raise self.create_error
        return "success", "PLnew"

    async def add_playlist_items(self, playlist_id: str, video_ids: list[str], duplicates=False):
        return self.add_results.pop(0)


class _Host(App[None]):
    def __init__(self, ytmusic: _FakeYTMusic) -> None:
        super().__init__()
        self.ytmusic = ytmusic
        self.probes = 0

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        yield Static("host")

    def action_probe(self) -> None:
        self.probes += 1


def _content(widget) -> Content:
    visual = widget.render()
    assert isinstance(visual, Content)
    return visual


def _patch_fetch(monkeypatch, tracks: list[dict], name: str = "Source") -> None:
    monkeypatch.setattr(spotify_service, "extract_spotify_tracks", lambda url: (name, tracks))


def _patch_score(monkeypatch, score: int) -> None:
    monkeypatch.setattr(spotify_service, "_fuzzy_score", lambda sp, candidate: score)


async def _open_popup(app: _Host, pilot) -> SpotifyImportPopup:
    popup = SpotifyImportPopup()
    await app.push_screen(popup)
    await pilot.pause()
    return popup


async def _settle(app: _Host, pilot) -> None:
    await pilot.pause()
    await asyncio.wait_for(app.workers.wait_for_complete(), 5)
    await pilot.pause()


async def _submit_url(app: _Host, pilot, popup: SpotifyImportPopup) -> None:
    popup.query_one("#si-url-input", Input).value = _URL
    await pilot.press("enter")
    await pilot.pause()


# ── Progress rows (_ResultItem) ──────────────────────────────────────


@pytest.mark.parametrize("payload", _PAYLOADS)
async def test_progress_row_shows_track_text_as_typed(monkeypatch, payload) -> None:
    _patch_fetch(monkeypatch, [{"name": payload, "artist": "Band"}, {"name": "B", "artist": "C"}])
    ytmusic = _FakeYTMusic()
    ytmusic.search_gate_after = 1  # hold the second search so the first row stays up

    app = _Host(ytmusic)
    async with app.run_test(size=(100, 40)) as pilot:
        popup = await _open_popup(app, pilot)
        await _submit_url(app, pilot, popup)
        await asyncio.wait_for(ytmusic.search_blocked.wait(), 5)
        await pilot.pause()

        rows = list(popup.query_one("#si-results", ListView).query(_ResultItem))
        assert len(rows) == 1
        label = rows[0].query_one(Label)
        content = _content(label)
        assert content.plain == f"✗ {payload} — Band"
        # The app's coloured symbol is the only styled span.
        assert content.spans == [Span(0, 1, get_theme().error)]

        await pilot.click(label, offset=(2, 0))
        await pilot.pause()
        assert app.probes == 0

        ytmusic.search_gate.set()
        await _settle(app, pilot)


# ── Disambiguation (status line + _CandidateItem) ────────────────────


@pytest.mark.parametrize("payload", _PAYLOADS)
async def test_disambiguation_shows_names_as_typed(monkeypatch, payload) -> None:
    _patch_fetch(monkeypatch, [{"name": payload, "artist": f"Band {payload}"}])
    _patch_score(monkeypatch, 50)  # below the auto-match threshold
    ytmusic = _FakeYTMusic(
        candidates=[
            {
                "videoId": "vid1",
                "title": payload,
                "artists": [{"name": payload}],
                "duration": "3:00",
            }
        ]
    )

    app = _Host(ytmusic)
    async with app.run_test(size=(100, 40)) as pilot:
        popup = await _open_popup(app, pilot)
        await _submit_url(app, pilot, popup)
        await _settle(app, pilot)

        status = _content(popup.query_one("#si-status", Static))
        prefix = "Uncertain match (1/1):"
        assert status.plain == (
            f'{prefix} "{payload}" by Band {payload}\nSelect the correct match or skip:'
        )
        assert status.spans == [Span(0, len(prefix), "bold")]

        candidate = popup.query_one(_CandidateItem).query_one(Label)
        content = _content(candidate)
        assert content.plain == f"  1. {payload} — {payload} (3:00)"
        assert content.spans == []


# ── Fetch / create errors on the status line ─────────────────────────


@pytest.mark.parametrize("payload", _PAYLOADS)
async def test_single_fetch_error_shows_error_text_as_typed(monkeypatch, payload) -> None:
    def _fail(url: str):
        raise RuntimeError(payload)

    monkeypatch.setattr(spotify_service, "extract_spotify_tracks", _fail)

    app = _Host(_FakeYTMusic())
    async with app.run_test(size=(100, 40)) as pilot:
        popup = await _open_popup(app, pilot)
        await _submit_url(app, pilot, popup)
        await _settle(app, pilot)

        status = _content(popup.query_one("#si-status", Static))
        prefix = "Failed to fetch playlist:"
        assert status.plain == f"{prefix} {payload}"
        assert status.spans == [Span(0, len(prefix), get_theme().error)]


@pytest.mark.parametrize("payload", _PAYLOADS)
async def test_multi_fetch_error_shows_error_text_as_typed(monkeypatch, payload) -> None:
    def _fail(url: str):
        raise RuntimeError(payload)

    monkeypatch.setattr(spotify_service, "extract_spotify_tracks", _fail)

    app = _Host(_FakeYTMusic())
    async with app.run_test(size=(100, 40)) as pilot:
        popup = await _open_popup(app, pilot)
        multi_tab = next(tab for tab in popup.query(_TabLabel) if tab.tab_id == "multi")
        await pilot.click(multi_tab)
        await pilot.pause()

        popup.query_one("#si-multi-name-input", Input).value = "Mix"
        count = popup.query_one("#si-multi-count-input", Input)
        count.value = "2"
        count.focus()
        await pilot.press("enter")
        await pilot.pause()
        for i in range(2):
            popup.query_one(f"#si-multi-url-{i}", Input).value = _URL
        popup.query_one("#si-multi-start-btn", Button).press()
        await _settle(app, pilot)

        status = _content(popup.query_one("#si-status", Static))
        prefix = "Failed to fetch part 1:"
        assert status.plain == f"{prefix} {payload}"
        assert status.spans == [Span(0, len(prefix), get_theme().error)]


async def _reach_summary(app: _Host, pilot, monkeypatch, track_count: int = 1):
    _patch_fetch(
        monkeypatch,
        [{"name": f"Song {i}", "artist": "Band"} for i in range(track_count)],
    )
    _patch_score(monkeypatch, 100)
    popup = await _open_popup(app, pilot)
    await _submit_url(app, pilot, popup)
    await _settle(app, pilot)
    name_input = popup.query_one("#si-name-input", Input)
    assert app.focused is name_input
    return popup, name_input


_MATCH = [{"videoId": "vid1", "title": "Song", "artists": [{"name": "Band"}]}]


@pytest.mark.parametrize("payload", _PAYLOADS)
async def test_create_error_shows_error_text_as_typed(monkeypatch, payload) -> None:
    app = _Host(_FakeYTMusic(candidates=_MATCH, create_error=RuntimeError(payload)))
    async with app.run_test(size=(100, 40)) as pilot:
        popup, _ = await _reach_summary(app, pilot, monkeypatch)
        await pilot.press("enter")
        await _settle(app, pilot)

        status = _content(popup.query_one("#si-status", Static))
        assert status.plain == f"Error: {payload}"
        assert status.spans == [Span(0, len("Error:"), get_theme().error)]


@pytest.mark.parametrize("payload", _PAYLOADS)
async def test_creating_status_shows_typed_name_as_typed(monkeypatch, payload) -> None:
    ytmusic = _FakeYTMusic(candidates=_MATCH, add_results=["success"])
    ytmusic.create_gate.clear()

    app = _Host(ytmusic)
    async with app.run_test(size=(100, 40)) as pilot:
        popup, name_input = await _reach_summary(app, pilot, monkeypatch)
        name_input.value = payload
        await pilot.press("enter")
        await asyncio.wait_for(ytmusic.create_started.wait(), 5)
        await pilot.pause()

        status = _content(popup.query_one("#si-status", Static))
        assert status.plain == f"Creating '{payload}' on YouTube Music..."
        assert status.spans == []

        ytmusic.create_gate.set()
        await _settle(app, pilot)


# ── Result notifications ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("track_count", "add_results", "expected"),
    [
        pytest.param(1, ["success"], "Created '{n}' with 1 tracks", id="all-added"),
        pytest.param(
            1,
            ["network"],
            "Created '{n}' but couldn't add any tracks — check your connection",
            id="none-added-one-cause",
        ),
        pytest.param(
            101,
            ["network", "timeout"],
            "Created '{n}' but failed to add any tracks (2 batch(es) rejected)",
            id="none-added-mixed-causes",
        ),
        pytest.param(
            101,
            ["success", "network"],
            "Created '{n}' with partial track set: 100/101 added (1 batch(es) failed)",
            id="partial",
        ),
    ],
)
async def test_result_notification_shows_typed_name_as_typed(
    monkeypatch, track_count, add_results, expected
) -> None:
    app = _Host(_FakeYTMusic(candidates=_MATCH, add_results=add_results))
    async with app.run_test(size=(100, 40), notifications=True) as pilot:
        popup, name_input = await _reach_summary(app, pilot, monkeypatch, track_count)
        name_input.value = _HOSTILE_NAME
        await pilot.press("enter")
        await _settle(app, pilot)

        assert app.screen is not popup
        toasts = [_content(toast) for toast in app.screen.query(Toast)]
        assert [toast.plain for toast in toasts] == [expected.format(n=_HOSTILE_NAME)]
        assert not any("@click" in str(span.style) for span in toasts[0].spans)
