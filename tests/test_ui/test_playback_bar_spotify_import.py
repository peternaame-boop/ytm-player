"""The footer Import button explains a missing thefuzz instead of crashing (#180)."""

from __future__ import annotations

from textual.app import App, ComposeResult

from ytm_player.services import spotify_import
from ytm_player.ui.playback_bar import _FooterButton
from ytm_player.ui.popups.spotify_import import SpotifyImportPopup


class _Host(App):
    """Just the Import button, recording notifications."""

    def __init__(self) -> None:
        super().__init__()
        self.notices: list[tuple[str, str, bool]] = []

    def compose(self) -> ComposeResult:
        yield _FooterButton("Import", "spotify_import", id="footer-import")

    def notify(  # type: ignore[override]
        self, message, *, severity="information", markup=True, **kwargs
    ) -> None:
        self.notices.append((str(message), severity, markup))


async def test_missing_thefuzz_shows_an_error_and_no_popup(monkeypatch):
    monkeypatch.setattr(spotify_import, "_HAS_SPOTIFY_DEPS", False)
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.click("#footer-import")
        await pilot.pause()

        assert not isinstance(app.screen, SpotifyImportPopup)
        assert len(app.notices) == 1
        message, severity, markup = app.notices[0]
        assert "thefuzz" in message
        assert severity == "error"
        # With markup on, Textual would drop "[spotify]" from the shown command.
        assert "'ytm-player[spotify]'" in message
        assert markup is False


async def test_with_thefuzz_the_popup_opens(monkeypatch):
    monkeypatch.setattr(spotify_import, "_HAS_SPOTIFY_DEPS", True)
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.click("#footer-import")
        await pilot.pause()

        assert isinstance(app.screen, SpotifyImportPopup)
        assert app.notices == []
