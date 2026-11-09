"""Tests for AlbumArt's placeholder contrast helper and the placeholder itself."""

from __future__ import annotations

import pytest

from ytm_player.ui.theme import ThemeColors
from ytm_player.ui.widgets.album_art import AlbumArt, _readable_on


def _note_styles(theme: ThemeColors, monkeypatch) -> list[str]:
    monkeypatch.setattr("ytm_player.ui.widgets.album_art.get_theme", lambda: theme)
    rendered = AlbumArt._render_placeholder(None, 5, 5)  # type: ignore[arg-type]
    return [str(span.style) for span in rendered.spans if str(span.style).startswith("bold")]


class TestReadableOn:
    def test_light_background_picks_dark(self) -> None:
        # A pale pastel primary (Noctalia-style theme) gets the dark colour.
        assert _readable_on("#fff59b", "#f3edf7", "#070722") == "#070722"

    def test_dark_background_picks_light(self) -> None:
        assert _readable_on("#1e3a5f", "#ffffff", "#0f0f0f") == "#ffffff"

    def test_pure_red_picks_black_by_contrast_ratio(self) -> None:
        # Black on #ff0000 is 5.25:1, white is 4.0:1 -- the contrast ratio
        # decides, not which colour looks lighter.
        assert _readable_on("#ff0000", "#ffffff", "#000000") == "#000000"

    def test_mid_grey_picks_black(self) -> None:
        # #808080 has a relative luminance of about 0.22 once sRGB is
        # linearised: black is 5.3:1 against it, white 3.9:1. A plain
        # luminance difference would call it a toss-up.
        assert _readable_on("#808080", "#ffffff", "#000000") == "#000000"

    def test_order_of_candidates_does_not_matter_when_one_is_clearly_better(self) -> None:
        assert _readable_on("#808080", "#000000", "#ffffff") == "#000000"

    def test_tie_keeps_the_first_candidate(self) -> None:
        # Two spellings of the same colour: identical ratios, first wins.
        assert _readable_on("#ffffff", "000000", "#000000") == "000000"

    def test_accepts_hex_without_hash(self) -> None:
        assert _readable_on("fff59b", "#f3edf7", "#070722") == "#070722"

    @pytest.mark.parametrize("background", ["not-a-color", "ansi_default", "#fff", "", "red"])
    def test_unparseable_background_keeps_the_first_candidate(self, background: str) -> None:
        assert _readable_on(background, "#ffffff", "#000000") == "#ffffff"

    def test_unparseable_candidate_keeps_the_first_candidate(self) -> None:
        # No RGB value is invented for a terminal-dependent colour, so the
        # comparison is impossible and the first candidate stands -- even
        # when the other one would have won.
        assert _readable_on("#fff59b", "ansi_default", "#000000") == "ansi_default"
        assert _readable_on("#fff59b", "#ffffff", "ansi_default") == "#ffffff"


class TestPlaceholderNote:
    def test_light_theme_note_is_dark_on_pale_accent(self, monkeypatch) -> None:
        theme = ThemeColors(background="#ffffff", foreground="#111111", primary="#fff59b")

        assert _note_styles(theme, monkeypatch) == ["bold #111111 on #fff59b"]

    def test_dark_theme_note_is_light_on_dark_accent(self, monkeypatch) -> None:
        theme = ThemeColors(background="#0f0f0f", foreground="#ffffff", primary="#1e3a5f")

        assert _note_styles(theme, monkeypatch) == ["bold #ffffff on #1e3a5f"]

    def test_terminal_colours_keep_the_foreground(self, monkeypatch) -> None:
        theme = ThemeColors(background="black", foreground="white", primary="cyan")

        assert _note_styles(theme, monkeypatch) == ["bold white on cyan"]
