from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

import pytest
from textual.command import DiscoveryHit

from ytm_player.app._app import YTMPlayerApp
from ytm_player.app._commands import YTMCommandProvider
from ytm_player.config.settings import Settings


def test_app_initializes_theme_from_ui_settings(monkeypatch):
    settings = Settings()
    settings.ui.theme = "textual-dark"

    monkeypatch.setattr("ytm_player.app._app.get_settings", lambda: settings)
    monkeypatch.setattr("ytm_player.app._app.get_keymap", MagicMock())
    monkeypatch.setattr("ytm_player.app._app.get_theme", MagicMock())

    app = YTMPlayerApp()

    assert app.theme == "textual-dark"


def test_set_current_theme_as_default_saves_config(monkeypatch):
    settings = Settings()
    settings.ui.theme = "ytm-dark"
    settings.save = MagicMock()

    monkeypatch.setattr("ytm_player.app._app.get_settings", lambda: settings)
    monkeypatch.setattr("ytm_player.app._app.get_keymap", MagicMock())
    monkeypatch.setattr("ytm_player.app._app.get_theme", MagicMock())

    app = YTMPlayerApp()
    app.theme = "textual-dark"
    app.notify = MagicMock()

    app.action_set_current_theme_as_default()

    assert settings.ui.theme == "textual-dark"
    settings.save.assert_called_once_with()
    app.notify.assert_called_once()
    args, kwargs = app.notify.call_args
    message = args[0] if args else kwargs.get("message", "")
    assert "textual-dark" in message
    assert kwargs.get("severity") in (None, "information")


def test_set_current_theme_as_default_rolls_back_on_save_failure(monkeypatch):
    settings = Settings()
    settings.ui.theme = "ytm-dark"
    settings.save = MagicMock(side_effect=OSError("read-only filesystem"))

    monkeypatch.setattr("ytm_player.app._app.get_settings", lambda: settings)
    monkeypatch.setattr("ytm_player.app._app.get_keymap", MagicMock())
    monkeypatch.setattr("ytm_player.app._app.get_theme", MagicMock())

    app = YTMPlayerApp()
    app.theme = "textual-dark"
    app.notify = MagicMock()

    app.action_set_current_theme_as_default()

    assert settings.ui.theme == "ytm-dark"
    settings.save.assert_called_once_with()
    app.notify.assert_called_once()
    _, kwargs = app.notify.call_args
    assert kwargs.get("severity") == "error"


@pytest.mark.asyncio
async def test_ytm_command_provider_discover_yields_theme_command():
    app = MagicMock()
    screen = MagicMock()
    screen.app = app

    provider = YTMCommandProvider(screen)
    hits = [cast(DiscoveryHit, hit) async for hit in provider.discover()]

    assert len(hits) == 2
    theme_hit = next(hit for hit in hits if "Theme:" in str(hit.display))
    assert "Theme: Set Current as Default" in str(theme_hit.display)
    assert theme_hit.command is app.action_set_current_theme_as_default
    assert "Save the active theme to config.toml" in str(theme_hit.help)

    oauth_hit = next(hit for hit in hits if "OAuth" in str(hit.display))
    assert "Account: Sign in with Google (OAuth)" in str(oauth_hit.display)
    assert oauth_hit.command is app.action_oauth_login


@pytest.mark.asyncio
async def test_ytm_command_provider_search_matches_theme_command():
    app = MagicMock()
    screen = MagicMock()
    screen.app = app

    provider = YTMCommandProvider(screen)
    hits = [hit async for hit in provider.search("theme default")]

    assert len(hits) == 1
    hit = hits[0]
    assert hit.score > 0
    assert hit.command is app.action_set_current_theme_as_default


@pytest.mark.asyncio
async def test_ytm_command_provider_search_no_match_for_irrelevant_query():
    app = MagicMock()
    screen = MagicMock()
    screen.app = app

    provider = YTMCommandProvider(screen)
    hits = [hit async for hit in provider.search("zzzzzzzzz")]

    assert len(hits) == 0


def test_app_commands_includes_ytm_provider():
    from textual.app import App
    from textual.system_commands import SystemCommandsProvider

    from ytm_player.app._app import _get_ytm_commands_provider

    assert SystemCommandsProvider in {cls() for cls in App.COMMANDS}
    provider_cls = _get_ytm_commands_provider()
    assert provider_cls is YTMCommandProvider
    assert _get_ytm_commands_provider in YTMPlayerApp.COMMANDS
