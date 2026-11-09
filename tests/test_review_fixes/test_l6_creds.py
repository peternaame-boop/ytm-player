"""T10: Spotify import credentials prompt and the obsolete 100-track inference.

The import hid ``#si-single-content`` and the credentials prompt only showed
the inputs inside it, so the prompt asked for credentials with no visible
fields. The prompt was triggered by treating exactly 100 extracted tracks as a
truncated playlist, which stopped being true once extraction fetched every
track, so a complete 100-track playlist was blocked on that prompt.
"""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.widgets import Input, Static

import ytm_player.services.spotify_import as spotify_service
from ytm_player.ui.popups.spotify_import import SpotifyImportPopup

_URL = "https://open.spotify.com/playlist/abc123"


class _FakeYTMusic:
    def __init__(self) -> None:
        self.search_calls = 0

    async def search(self, query: str, filter: str | None = None, limit: int = 5) -> list:
        self.search_calls += 1
        return []


class _Host(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.ytmusic = _FakeYTMusic()

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        yield Static("host")


async def _open_popup(app: _Host, pilot) -> SpotifyImportPopup:
    popup = SpotifyImportPopup()
    await app.push_screen(popup)
    await pilot.pause()
    return popup


async def _submit_url(app: _Host, pilot, popup: SpotifyImportPopup) -> None:
    popup.query_one("#si-url-input", Input).value = _URL
    await pilot.press("enter")
    await pilot.pause()
    await asyncio.wait_for(app.workers.wait_for_complete(), 5)
    await pilot.pause()


async def test_a_complete_100_track_playlist_is_matched_without_asking_for_credentials(
    monkeypatch,
) -> None:
    tracks = [{"name": f"Song {i}", "artist": "Artist"} for i in range(100)]
    monkeypatch.setattr(spotify_service, "extract_spotify_tracks", lambda url: ("Hundred", tracks))
    monkeypatch.setattr(spotify_service, "has_spotify_creds", lambda: False)

    app = _Host()
    async with app.run_test(size=(100, 40)) as pilot:
        popup = await _open_popup(app, pilot)
        await _submit_url(app, pilot, popup)

        assert app.ytmusic.search_calls == 100
        status = str(popup.query_one("#si-status", Static).render())
        assert status == "0 matched, 100 skipped/not found (100 total)"
        assert not popup.query_one("#si-cred-id-input", Input).has_class("visible")


async def test_credentials_prompt_shows_inputs_that_can_be_filled_in(monkeypatch) -> None:
    monkeypatch.setattr(spotify_service, "extract_spotify_tracks", lambda url: ("Empty", []))
    saved: list[tuple[str, str]] = []
    monkeypatch.setattr(
        spotify_service, "save_spotify_creds", lambda cid, secret: saved.append((cid, secret))
    )

    app = _Host()
    async with app.run_test(size=(100, 40)) as pilot:
        popup = await _open_popup(app, pilot)
        # A real import hides the single-mode form, which is the state the
        # prompt used to open from. No import path opens the prompt any more
        # (the exactly-100 inference is gone), so open it directly.
        await _submit_url(app, pilot, popup)
        assert popup.query_one("#si-single-content").display is False

        popup._show_creds_prompt()
        await pilot.pause()

        cred_id = popup.query_one("#si-cred-id-input", Input)
        cred_secret = popup.query_one("#si-cred-secret-input", Input)
        for field in (cred_id, cred_secret):
            assert field.region.width > 0 and field.region.height > 0
        assert popup.query_one("#si-url-input", Input).region.area == 0
        assert app.focused is cred_id

        await pilot.press(*"myid")
        await pilot.press("enter")
        assert app.focused is cred_secret
        await pilot.press(*"mysecret")
        await pilot.press("enter")
        await pilot.pause()
        await asyncio.wait_for(app.workers.wait_for_complete(), 5)

        assert saved == [("myid", "mysecret")]
