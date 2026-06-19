"""Regression tests for #89 — ANSI themes crash Rich rendering.

Textual's ansi-dark/ansi-light themes use color tokens like ``ansi_cyan``
that Rich cannot parse.  ThemeColors must normalize them on every
construction path so widget render() methods never hand Rich an
unparseable color.
"""

import pytest
from rich.color import Color as RichColor

from ytm_player.ui.theme import ThemeColors, rich_safe_color


class TestRichSafeColor:
    def test_strips_ansi_prefix(self):
        assert rich_safe_color("ansi_cyan") == "cyan"
        assert rich_safe_color("ansi_bright_red") == "bright_red"
        assert rich_safe_color("ansi_default") == "default"

    def test_passes_through_hex(self):
        assert rich_safe_color("#ff4e45") == "#ff4e45"

    def test_passes_through_named_colors(self):
        assert rich_safe_color("cyan") == "cyan"

    def test_passes_through_empty(self):
        assert rich_safe_color("") == ""

    def test_stripped_tokens_are_rich_parseable(self):
        for token in (
            "ansi_red",
            "ansi_green",
            "ansi_blue",
            "ansi_cyan",
            "ansi_magenta",
            "ansi_yellow",
            "ansi_white",
            "ansi_black",
            "ansi_bright_cyan",
            "ansi_default",
        ):
            RichColor.parse(rich_safe_color(token))  # must not raise


class TestThemeColorsNormalization:
    def test_constructor_normalizes_ansi_tokens(self):
        tc = ThemeColors(primary="ansi_blue", accent="ansi_green", surface="ansi_default")
        assert tc.primary == "blue"
        assert tc.accent == "green"
        assert tc.surface == "default"

    def test_constructor_keeps_hex_untouched(self):
        tc = ThemeColors(primary="#ff0000")
        assert tc.primary == "#ff0000"

    def test_builtin_ansi_themes_produce_rich_parseable_colors(self):
        """Every color field built from Textual's ANSI themes must Rich-parse."""
        textual_theme = pytest.importorskip("textual.theme")
        from dataclasses import fields

        for name in ("ansi-dark", "ansi-light"):
            theme = textual_theme.BUILTIN_THEMES.get(name)
            if theme is None:
                pytest.skip(f"installed Textual has no {name} theme")
            tc = ThemeColors(
                primary=theme.primary,
                accent=theme.accent or "#ff4e45",
                surface=theme.surface or "#1a1a1a",
                background=theme.background or "#0f0f0f",
                foreground=theme.foreground or "#ffffff",
            )
            for f in fields(tc):
                value = getattr(tc, f.name)
                RichColor.parse(value)  # must not raise

    def test_toml_overrides_normalized(self, tmp_path):
        theme_file = tmp_path / "theme.toml"
        theme_file.write_text('[colors]\nlyrics_current = "ansi_cyan"\n', encoding="utf-8")
        tc = ThemeColors()
        tc._apply_toml_overrides(path=theme_file)
        assert tc.lyrics_current == "cyan"
