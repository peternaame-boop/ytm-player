"""Terminal capability detection."""

from __future__ import annotations

import os
import shutil


def detect_image_protocol() -> str | None:
    term_program = os.environ.get("TERM_PROGRAM", "").lower()
    term = os.environ.get("TERM", "").lower()

    if term_program == "kitty" or "kitty" in term:
        return "kitty"

    if term_program in ("iterm.app", "iterm2"):
        return "iterm2"

    if term_program == "wezterm":
        return "iterm2"

    term_features = os.environ.get("TERM_FEATURES", "").lower()
    colorterm = os.environ.get("COLORTERM", "").lower()
    if "sixel" in term or "sixel" in term_features or "sixel" in colorterm:
        return "sixel"

    return "block"


def get_terminal_size() -> tuple[int, int]:
    size = shutil.get_terminal_size(fallback=(80, 24))
    return size.columns, size.lines


def get_orientation(cols: int, rows: int) -> str:
    return "horizontal" if rows == 0 or cols / rows > 2.3 else "vertical"
