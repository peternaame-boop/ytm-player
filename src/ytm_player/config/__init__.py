"""Configuration management for ytm-player."""

from __future__ import annotations

from ytm_player.config.keymap import Action, KeyMap, MatchResult, get_keymap
from ytm_player.config.settings import Settings, get_settings

__all__ = ["Settings", "get_settings", "KeyMap", "Action", "MatchResult", "get_keymap"]
