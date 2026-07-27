"""Tests for AlbumArt's placeholder contrast helper."""

from __future__ import annotations

from ytm_player.ui.widgets.album_art import _readable_on


def test_readable_on_light_background_picks_dark() -> None:
    # A pale pastel primary (Noctalia-style theme) should get dark text,
    # not the near-white foreground the stock dark-red-primary theme uses.
    assert _readable_on("#fff59b", light="#f3edf7", dark="#070722") == "#070722"


def test_readable_on_dark_background_picks_light() -> None:
    # The stock theme's saturated red primary should still get light text.
    assert _readable_on("#ff0000", light="#ffffff", dark="#000000") == "#ffffff"


def test_readable_on_invalid_hex_falls_back_to_light() -> None:
    assert _readable_on("not-a-color", light="#ffffff", dark="#000000") == "#ffffff"


def test_readable_on_accepts_hex_without_hash() -> None:
    assert _readable_on("fff59b", light="#f3edf7", dark="#070722") == "#070722"
