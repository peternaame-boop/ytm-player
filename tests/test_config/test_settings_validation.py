"""Tests for Settings load hardening: type validation and path expansion."""

from __future__ import annotations

import logging
from pathlib import Path

from ytm_player.config.settings import Settings


def test_cache_location_is_expanduser(tmp_config_dir):
    """A `~`-relative cache location resolves to an absolute path, not a literal ~."""
    path = tmp_config_dir / "config.toml"
    path.write_text('[cache]\nlocation = "~/my-cache"\n', encoding="utf-8")

    s = Settings.load(path)

    assert s.cache_dir == Path("~/my-cache").expanduser()
    assert "~" not in str(s.cache_dir)
    assert s.cache_dir.is_absolute()


def test_invalid_int_field_falls_back_to_default(tmp_config_dir, caplog):
    """A string in an int field falls back to the default and logs a warning."""
    path = tmp_config_dir / "config.toml"
    path.write_text('[playback]\ndefault_volume = "loud"\n', encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        s = Settings.load(path)

    assert s.playback.default_volume == 80  # dataclass default
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "playback" in msgs and "default_volume" in msgs


def test_int_into_bool_field_falls_back(tmp_config_dir, caplog):
    """An int loaded into a bool field is rejected (bool != int) → default kept."""
    path = tmp_config_dir / "config.toml"
    path.write_text("[cache]\nenabled = 0\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        s = Settings.load(path)

    assert s.cache.enabled is True  # default; the int 0 was rejected
    assert any("enabled" in r.getMessage() for r in caplog.records)


def test_valid_values_still_load(tmp_config_dir):
    """Well-typed config values load unchanged (no over-rejection)."""
    path = tmp_config_dir / "config.toml"
    path.write_text(
        '[playback]\ndefault_volume = 55\ngapless = false\n[ui]\ntheme = "textual-dark"\n',
        encoding="utf-8",
    )

    s = Settings.load(path)

    assert s.playback.default_volume == 55
    assert s.playback.gapless is False
    assert s.ui.theme == "textual-dark"


def test_union_field_accepts_str_and_list(tmp_config_dir):
    """`str | list[str]` fields accept either a string or a list of strings."""
    path = tmp_config_dir / "config.toml"
    path.write_text(
        '[yt_dlp]\nremote_components = ["a", "b"]\njs_runtimes = "deno"\n',
        encoding="utf-8",
    )

    s = Settings.load(path)

    assert s.yt_dlp.remote_components == ["a", "b"]
    assert s.yt_dlp.js_runtimes == "deno"


def test_union_field_rejects_bad_type(tmp_config_dir, caplog):
    """A value matching neither union member falls back to the default."""
    path = tmp_config_dir / "config.toml"
    path.write_text("[yt_dlp]\nremote_components = 5\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        s = Settings.load(path)

    assert s.yt_dlp.remote_components == ""  # default
    assert any("remote_components" in r.getMessage() for r in caplog.records)
