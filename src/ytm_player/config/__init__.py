"""Configuration management for ytm-player.

The names below are imported on first use. Importing this package, which
`ytm_player.config.paths` does implicitly, must not load keymap.py or
settings.py: on Python 3.10 they import tomli and typing_extensions, and
`ytm report` has to start without running third-party code.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ytm_player.config.keymap import Action, KeyMap, MatchResult, get_keymap
    from ytm_player.config.settings import Settings, get_settings

__all__ = ["Settings", "get_settings", "KeyMap", "Action", "MatchResult", "get_keymap"]

_EXPORTS = {
    "Settings": "settings",
    "get_settings": "settings",
    "KeyMap": "keymap",
    "Action": "keymap",
    "MatchResult": "keymap",
    "get_keymap": "keymap",
}


def __getattr__(name: str) -> object:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(f"{__name__}.{module}"), name)
