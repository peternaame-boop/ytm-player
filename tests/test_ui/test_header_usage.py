"""The top bar shows ytm's CPU and memory use beside Lyrics, in the primary colour."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from ytm_player.config.settings import get_settings
from ytm_player.ui import header_bar
from ytm_player.ui.header_bar import HeaderBar
from ytm_player.utils.usage import Usage


class _Host(App):
    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables.setdefault("playback-bar-bg", "#222222")
        return variables

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header-bar")


class _FakeSampler:
    def __init__(self) -> None:
        self.calls = 0

    def sample(self) -> Usage:
        self.calls += 1
        return Usage(cpu_percent=float(self.calls), rss_bytes=150 * 1024 * 1024)


@pytest.fixture
def fake_sampler(monkeypatch: pytest.MonkeyPatch) -> _FakeSampler:
    sampler = _FakeSampler()
    monkeypatch.setattr(header_bar, "UsageSampler", lambda: sampler)
    return sampler


async def test_usage_sits_left_of_lyrics_in_the_primary_colour(fake_sampler):
    app = _Host()
    async with app.run_test(size=(120, 3)) as pilot:
        await pilot.pause(0.7)
        usage = app.query_one("#header-usage", Static)
        lyrics = app.query_one("#toggle-lyrics", Static)
        assert str(usage.render()) == "CPU 1%  MEM 150M"
        assert usage.region.right <= lyrics.region.x
        assert usage.styles.color == app.current_theme.to_color_system().primary


async def test_usage_refreshes(fake_sampler, monkeypatch):
    monkeypatch.setattr(header_bar, "USAGE_REFRESH_SECONDS", 0.1)
    app = _Host()
    async with app.run_test(size=(120, 3)) as pilot:
        await pilot.pause(1.0)
        assert fake_sampler.calls >= 3
        assert str(app.query_one("#header-usage", Static).render()).startswith(
            f"CPU {fake_sampler.calls}%"
        )


class _RecordingHost(_Host):
    def __init__(self) -> None:
        super().__init__()
        self.toggles = 0

    def on_header_bar_toggle_lyrics_sidebar(self, _message: HeaderBar.ToggleLyricsSidebar) -> None:
        self.toggles += 1


async def test_clicking_lyrics_still_toggles_with_usage_shown(fake_sampler):
    app = _RecordingHost()
    async with app.run_test(size=(120, 3)) as pilot:
        await pilot.click("#toggle-lyrics")
        await pilot.pause()
    assert app.toggles == 1


async def test_usage_can_be_turned_off(fake_sampler, monkeypatch):
    monkeypatch.setattr(get_settings().ui, "show_usage", False)
    app = _Host()
    async with app.run_test(size=(120, 3)) as pilot:
        await pilot.pause(0.7)
        assert not app.query("#header-usage")
        assert fake_sampler.calls == 0
