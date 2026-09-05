"""watch_theme re-registers the active theme with theme.toml colours (#123).

The rebuild must keep every non-colour field of the base theme: ansi mode,
panel, boost, luminosity spread and text alpha all feed Textual's
ColorSystem, and they used to fall back to defaults on every theme switch.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from textual.theme import BUILTIN_THEMES, Theme

from ytm_player.app._app import YTMPlayerApp
from ytm_player.ui.theme import ThemeColors

_BASE = Theme(
    name="probe",
    primary="#123456",
    secondary="#234567",
    warning="#345678",
    error="#456789",
    success="#56789a",
    accent="#6789ab",
    foreground="#789abc",
    background="#89abcd",
    surface="#9abcde",
    panel="#101010",
    boost="#202020",
    dark=False,
    luminosity_spread=0.3,
    text_alpha=0.8,
    variables={"playback-bar-bg": "#333333"},
    ansi=True,
)
_COLOURS = (
    "primary",
    "secondary",
    "warning",
    "error",
    "success",
    "accent",
    "foreground",
    "background",
    "surface",
)
_OTHER_FIELDS = ("name", "ansi", "panel", "boost", "dark", "luminosity_spread", "text_alpha")


@pytest.fixture
def rebuild(monkeypatch):
    """Run watch_theme on a host whose current theme is *theme*; return the re-registered one."""
    monkeypatch.setattr(ThemeColors, "_apply_toml_overrides", lambda self: None)
    monkeypatch.setattr("ytm_player.ui.theme.set_theme", lambda tc: None)

    def _rebuild(theme: Theme, toml_colors: dict | None = None) -> Theme:
        monkeypatch.setattr(
            "ytm_player.app._app._read_theme_toml_cached", lambda: toml_colors or {}
        )
        host = MagicMock(current_theme=replace(theme))
        YTMPlayerApp.watch_theme(host, theme.name)
        host.register_theme.assert_called_once()
        host.call_next.assert_called_once()
        return host.register_theme.call_args.args[0]

    return _rebuild


def _fields(theme: Theme, names) -> dict:
    return {name: getattr(theme, name) for name in names}


def test_rebuild_without_overrides_keeps_every_field(rebuild) -> None:
    updated = rebuild(_BASE)

    assert _fields(updated, _OTHER_FIELDS) == _fields(_BASE, _OTHER_FIELDS)
    assert _fields(updated, _COLOURS) == _fields(_BASE, _COLOURS)
    assert updated.variables == _BASE.variables


def test_rebuild_with_overrides_changes_only_the_overridden_colours(rebuild) -> None:
    updated = rebuild(_BASE, {"primary": "#abcdef", "surface": "#fedcba", "accent": "#0000ff"})

    assert _fields(updated, _OTHER_FIELDS) == _fields(_BASE, _OTHER_FIELDS)
    assert updated.variables == _BASE.variables
    expected = {
        **_fields(_BASE, _COLOURS),
        "primary": "#abcdef",
        "surface": "#fedcba",
        "accent": "#0000ff",
    }
    assert _fields(updated, _COLOURS) == expected


@pytest.mark.parametrize("name", ["ansi-dark", "nord"])
def test_builtin_theme_keeps_its_base_settings(rebuild, name: str) -> None:
    original = BUILTIN_THEMES[name]

    updated = rebuild(original)

    assert (updated.ansi, updated.panel, updated.boost) == (
        original.ansi,
        original.panel,
        original.boost,
    )
    assert _fields(updated, _COLOURS) == _fields(original, _COLOURS)
