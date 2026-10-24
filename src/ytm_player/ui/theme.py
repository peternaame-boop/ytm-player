"""Theme system integrating Textual's native theming with app-specific colors."""

from __future__ import annotations

import sys
from dataclasses import dataclass, fields
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    # Python 3.10 backport via PyPI
    import tomli as tomllib  # pyright: ignore[reportMissingImports]

if sys.version_info >= (3, 11):
    from typing import Self
else:
    # Python 3.10 backport via PyPI
    from typing_extensions import Self  # pyright: ignore[reportMissingImports]

from textual.theme import Theme

from ytm_player.config.paths import THEME_FILE

# Default lyric-current color used when a theme defines no accent/primary.
# Single source of truth: the dataclass default, the from_css_variables
# fallback, and the two _app.py fallbacks (get_css_variables, watch_theme)
# all derive from this constant.  Matches ytm-dark's accent.
DEFAULT_LYRIC_CURRENT = "#86b34a"


def rich_safe_color(color: str) -> str:
    """Translate Textual-only ANSI color tokens into Rich-parseable names.

    Textual's ansi-dark/ansi-light themes (8.2.5+) use tokens like
    ``ansi_cyan`` that Textual CSS understands but Rich markup rejects,
    crashing any widget that interpolates theme colors into Rich text
    (#89).  Rich accepts the bare ANSI names (``cyan``, ``bright_red``,
    ``default``), which preserve the terminal-palette behaviour.
    """
    return color[5:] if color.startswith("ansi_") else color


# ── App-specific CSS variable names (not provided by Textual themes) ───

_APP_VARS = (
    "playback_bar_bg",
    "active_tab",
    "inactive_tab",
    "selected_item",
    "progress_filled",
    "progress_empty",
    "lyrics_played",
    "lyrics_current",
    "lyrics_upcoming",
)

# ── YTM dark theme — YouTube Music-inspired defaults ──────────────────

YTM_DARK = Theme(
    name="ytm-dark",
    # Full black, with a dim chartreuse kept for accents only. Borders, scroll
    # bars and the row cursor stay neutral grey.
    primary="#6aa81e",
    secondary="#8a8a8a",
    accent="#86b34a",
    success="#2ecc71",
    warning="#f39c12",
    error="#e74c3c",
    foreground="#e8e8e8",
    background="#000000",
    surface="#000000",
    panel="#000000",
    dark=True,
    variables={
        "playback-bar-bg": "#000000",
        "active-tab": "#ffffff",
        "inactive-tab": "#8a8a8a",
        "selected-item": "#1c1c1c",
        "progress-filled": "#6aa81e",
        "progress-empty": "#2a2a2a",
        "border": "#2a2a2a",
        "border-blurred": "#1a1a1a",
        "block-cursor-background": "#1f1f1f",
        "block-cursor-foreground": "#b5e07a",
        "block-cursor-text-style": "bold",
        "block-cursor-blurred-background": "#141414",
        "block-cursor-blurred-foreground": "#e8e8e8",
        "block-hover-background": "#121212",
        "scrollbar": "#2e2e2e",
        "scrollbar-hover": "#4a4a4a",
        "scrollbar-active": "#6aa81e",
        "scrollbar-background": "#000000",
        "scrollbar-background-hover": "#000000",
        "scrollbar-background-active": "#000000",
        "scrollbar-corner-color": "#000000",
        # Note: lyrics-* vars intentionally NOT hardcoded here so they
        # derive from the active theme via from_css_variables fallbacks
        # (played → text-muted, current → accent, upcoming → text).
        # This keeps lyric coloring theme-aware on every theme.
    },
)


@dataclass
class ThemeColors:
    """Resolved color values for Rich Text rendering in widget render() methods.

    Base colors come from the active Textual theme.  App-specific colors
    can be overridden via ``theme.toml``.
    """

    # Base colors (populated from Textual theme at runtime).
    background: str = "#000000"
    foreground: str = "#ffffff"
    primary: str = "#6aa81e"
    secondary: str = "#aaaaaa"
    accent: str = "#86b34a"
    success: str = "#2ecc71"
    warning: str = "#f39c12"
    error: str = "#e74c3c"
    surface: str = "#000000"
    border: str = "#2a2a2a"
    muted_text: str = "#999999"
    text: str = "#ffffff"

    # App-specific colors (customisable via theme.toml).
    playback_bar_bg: str = "#000000"
    active_tab: str = "#ffffff"
    inactive_tab: str = "#999999"
    selected_item: str = "#2a2a2a"
    progress_filled: str = "#6aa81e"
    progress_empty: str = "#555555"
    lyrics_played: str = "#999999"
    lyrics_current: str = DEFAULT_LYRIC_CURRENT
    lyrics_upcoming: str = "#aaaaaa"

    def __post_init__(self) -> None:
        # Normalize Textual-only ANSI tokens on every construction path
        # (watch_theme, from_css_variables, load) — see rich_safe_color.
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, str):
                setattr(self, f.name, rich_safe_color(value))

    @classmethod
    def from_css_variables(cls, variables: dict[str, str]) -> Self:
        """Build ThemeColors from resolved Textual CSS variables.

        Maps Textual's variable names to our field names and applies any
        app-specific overrides from ``theme.toml``.
        """
        tc = cls(
            background=variables.get("background", cls.background),
            foreground=variables.get("foreground", cls.foreground),
            primary=variables.get("primary", cls.primary),
            secondary=variables.get("secondary", cls.secondary),
            accent=variables.get("accent", cls.accent),
            success=variables.get("success", cls.success),
            warning=variables.get("warning", cls.warning),
            error=variables.get("error", cls.error),
            surface=variables.get("surface", cls.surface),
            border=variables.get("border", cls.border),
            muted_text=variables.get("text-muted", cls.muted_text),
            text=variables.get("text", cls.text),
            # App-specific: use theme value if present, else derive from base.
            playback_bar_bg=variables.get(
                "playback-bar-bg", variables.get("surface", cls.playback_bar_bg)
            ),
            active_tab=variables.get("active-tab", variables.get("text", cls.active_tab)),
            inactive_tab=variables.get(
                "inactive-tab", variables.get("text-muted", cls.inactive_tab)
            ),
            selected_item=variables.get(
                "selected-item", variables.get("surface", cls.selected_item)
            ),
            progress_filled=variables.get(
                "progress-filled", variables.get("primary", cls.progress_filled)
            ),
            progress_empty=variables.get(
                "progress-empty", variables.get("surface", cls.progress_empty)
            ),
            lyrics_played=variables.get(
                "lyrics-played", variables.get("text-muted", cls.lyrics_played)
            ),
            lyrics_current=variables.get(
                "lyrics-current",
                variables.get("accent", variables.get("primary", cls.lyrics_current)),
            ),
            lyrics_upcoming=variables.get(
                "lyrics-upcoming", variables.get("text", cls.lyrics_upcoming)
            ),
        )

        # Apply user overrides from theme.toml (app-specific vars only).
        tc._apply_toml_overrides()
        return tc

    def _apply_toml_overrides(self, path: Path = THEME_FILE) -> None:
        """Load color overrides from theme.toml (any field, not just app-specific)."""
        if not path.exists():
            return
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except (UnicodeDecodeError, tomllib.TOMLDecodeError):
            return

        colors = data.get("colors", data)
        for f_info in fields(self):
            if f_info.name in colors:
                value = colors[f_info.name]
                if isinstance(value, str):
                    value = rich_safe_color(value)
                setattr(self, f_info.name, value)

    @classmethod
    def load(cls, path: Path = THEME_FILE) -> Self:
        """Load from theme.toml (legacy fallback for non-app contexts)."""
        theme = cls()
        if not path.exists():
            return theme
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except (UnicodeDecodeError, tomllib.TOMLDecodeError):
            return theme
        colors = data.get("colors", data)
        for f_info in fields(theme):
            if f_info.name in colors:
                setattr(theme, f_info.name, colors[f_info.name])
        return theme

    def save(self, path: Path = THEME_FILE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["[colors]"]
        for f_info in fields(self):
            value = getattr(self, f_info.name)
            lines.append(f'{f_info.name} = "{value}"')
        lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")


_theme: ThemeColors | None = None


def get_theme() -> ThemeColors:
    global _theme
    if _theme is None:
        _theme = ThemeColors.load()
    return _theme


def set_theme(theme: ThemeColors) -> None:
    """Replace the cached ThemeColors (called when the Textual theme changes)."""
    global _theme
    _theme = theme


def reset_theme() -> None:
    """Invalidate the cached ThemeColors so it's rebuilt on next access."""
    global _theme
    _theme = None
