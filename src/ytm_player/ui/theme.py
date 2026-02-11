"""Theme system with YouTube Music-inspired defaults."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Self

from ytm_player.config.paths import THEME_FILE


@dataclass
class ThemeColors:
    background: str = "#0f0f0f"
    foreground: str = "#ffffff"
    primary: str = "#ff0000"
    secondary: str = "#aaaaaa"
    accent: str = "#ff4e45"
    success: str = "#2ecc71"
    warning: str = "#f39c12"
    error: str = "#e74c3c"
    playback_bar_bg: str = "#1a1a1a"
    active_tab: str = "#ffffff"
    inactive_tab: str = "#999999"
    selected_item: str = "#2a2a2a"
    progress_filled: str = "#ff0000"
    progress_empty: str = "#555555"
    lyrics_played: str = "#999999"
    lyrics_current: str = "#ffffff"
    lyrics_upcoming: str = "#aaaaaa"
    border: str = "#333333"
    muted_text: str = "#999999"

    @classmethod
    def load(cls, path: Path = THEME_FILE) -> Self:
        theme = cls()

        if not path.exists():
            return theme

        with open(path, "rb") as f:
            data = tomllib.load(f)

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
        path.write_text("\n".join(lines))

    def to_css(self) -> str:
        return "\n".join([
            ":root {",
            f"    --background: {self.background};",
            f"    --foreground: {self.foreground};",
            f"    --primary: {self.primary};",
            f"    --secondary: {self.secondary};",
            f"    --accent: {self.accent};",
            f"    --success: {self.success};",
            f"    --warning: {self.warning};",
            f"    --error: {self.error};",
            f"    --playback-bar-bg: {self.playback_bar_bg};",
            f"    --active-tab: {self.active_tab};",
            f"    --inactive-tab: {self.inactive_tab};",
            f"    --selected-item: {self.selected_item};",
            f"    --progress-filled: {self.progress_filled};",
            f"    --progress-empty: {self.progress_empty};",
            f"    --lyrics-played: {self.lyrics_played};",
            f"    --lyrics-current: {self.lyrics_current};",
            f"    --lyrics-upcoming: {self.lyrics_upcoming};",
            f"    --border: {self.border};",
            f"    --muted-text: {self.muted_text};",
            "}",
            "",
            "Screen {",
            f"    background: {self.background};",
            f"    color: {self.foreground};",
            "}",
        ])


_theme: ThemeColors | None = None


def get_theme() -> ThemeColors:
    global _theme
    if _theme is None:
        _theme = ThemeColors.load()
    return _theme
