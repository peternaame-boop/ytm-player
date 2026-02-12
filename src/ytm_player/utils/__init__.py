"""Utility modules."""

from __future__ import annotations

from ytm_player.utils.formatting import (
    format_ago,
    format_count,
    format_duration,
    format_size,
    truncate,
)
from ytm_player.utils.terminal import (
    detect_image_protocol,
    get_orientation,
    get_terminal_size,
)

__all__ = [
    "format_duration",
    "truncate",
    "format_count",
    "format_size",
    "format_ago",
    "detect_image_protocol",
    "get_terminal_size",
    "get_orientation",
]
