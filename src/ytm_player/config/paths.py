"""Centralized path definitions for ytm-player.

Single source of truth for all filesystem paths used across the application.
Respects $XDG_CONFIG_HOME and $XDG_CACHE_HOME when set.
"""

from __future__ import annotations

import os
from pathlib import Path

_xdg_config = os.environ.get("XDG_CONFIG_HOME")
_xdg_cache = os.environ.get("XDG_CACHE_HOME")

CONFIG_DIR = Path(_xdg_config) / "ytm-player" if _xdg_config else Path.home() / ".config" / "ytm-player"
CONFIG_FILE = CONFIG_DIR / "config.toml"
AUTH_FILE = CONFIG_DIR / "auth.json"
OAUTH_FILE = CONFIG_DIR / "oauth.json"
OAUTH_CREDS_FILE = CONFIG_DIR / "oauth_creds.json"
SPOTIFY_CREDS_FILE = CONFIG_DIR / "spotify.json"

CACHE_DIR = Path(_xdg_cache) / "ytm-player" / "audio" if _xdg_cache else Path.home() / ".cache" / "ytm-player" / "audio"
CACHE_DB = Path(_xdg_cache) / "ytm-player" / "cache.db" if _xdg_cache else Path.home() / ".cache" / "ytm-player" / "cache.db"
HISTORY_DB = CONFIG_DIR / "history.db"
