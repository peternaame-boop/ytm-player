"""Icon sets: both sets name the same icons, and [ui] icons picks the set."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ytm_player.ui import icons


@pytest.fixture(autouse=True)
def _fresh_cache():
    icons.active_set.cache_clear()
    yield
    icons.active_set.cache_clear()


def _use_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    settings = SimpleNamespace(ui=SimpleNamespace(icons=mode))
    monkeypatch.setattr("ytm_player.config.settings.get_settings", lambda: settings)


def test_both_sets_name_the_same_icons():
    assert icons._NERD.keys() == icons._UNICODE.keys()


def test_nerd_icons_sit_in_the_material_design_range():
    for name, glyph in icons._NERD.items():
        assert 0xF0000 <= ord(glyph) <= 0xF1AF0, name


@pytest.mark.parametrize("mode", ["nerd", "unicode"])
def test_explicit_mode_wins(monkeypatch: pytest.MonkeyPatch, mode: str):
    _use_mode(monkeypatch, mode)
    monkeypatch.setattr(icons, "_nerd_font_installed", lambda: mode != "nerd")
    assert icons.active_set() == mode
    assert icons.icon("play") == icons._SETS[mode]["play"]


@pytest.mark.parametrize(("installed", "expected"), [(True, "nerd"), (False, "unicode")])
def test_auto_follows_font_detection(monkeypatch: pytest.MonkeyPatch, installed, expected):
    _use_mode(monkeypatch, "auto")
    monkeypatch.setattr(icons, "_nerd_font_installed", lambda: installed)
    assert icons.active_set() == expected


def test_unknown_mode_falls_back_to_auto(monkeypatch: pytest.MonkeyPatch):
    _use_mode(monkeypatch, "fancy")
    monkeypatch.setattr(icons, "_nerd_font_installed", lambda: False)
    assert icons.active_set() == "unicode"


def test_no_fc_list_means_no_nerd_font(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(icons.shutil, "which", lambda _name: None)
    assert icons._nerd_font_installed() is False


@pytest.mark.parametrize(
    ("volume", "name"),
    [(0, "volume_off"), (20, "volume_low"), (50, "volume_medium"), (100, "volume_high")],
)
def test_volume_icon_tracks_the_level(monkeypatch: pytest.MonkeyPatch, volume, name):
    _use_mode(monkeypatch, "nerd")
    assert icons.volume_icon(volume) == icons._NERD[name]
