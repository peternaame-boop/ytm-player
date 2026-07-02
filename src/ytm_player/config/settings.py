"""Settings management using TOML configuration."""

from __future__ import annotations

import logging
import sys
import types
import typing
from dataclasses import dataclass, field, fields
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

from ytm_player.config.paths import CACHE_DIR, CONFIG_FILE

logger = logging.getLogger(__name__)


@dataclass
class GeneralSettings:
    startup_page: str = "library"
    playback_bar_position: str = "bottom"
    brand_account_id: str = ""
    check_for_updates: bool = True


@dataclass
class PlaybackSettings:
    audio_quality: str = "high"
    autoplay: bool = True
    prefer_audio: bool = True
    default_volume: int = 80
    seek_step: int = 5
    gapless: bool = True
    api_timeout: int = 15
    resume_on_launch: bool = True
    # Report plays back to your YouTube Music account history (so tracks
    # played in the TUI show up in YT Music like any other client). Opt-out:
    # set to false to keep TUI listening off your account.
    sync_history_to_ytmusic: bool = True


@dataclass
class YtDlpSettings:
    cookies_file: str = ""
    ca_bundle: str = ""
    remote_components: str | list[str] = ""
    js_runtimes: str | list[str] = ""


@dataclass
class SearchSettings:
    default_mode: str = "music"
    max_history: int = 500
    predictive: bool = True


@dataclass
class CacheSettings:
    enabled: bool = True
    max_size_mb: int = 1024
    prefetch_next: bool = True
    location: str = ""


@dataclass
class UISettings:
    theme: str = "ytm-dark"
    album_art: bool = True
    progress_style: str = "block"
    sidebar_width: int = 30
    col_index: int = 4
    col_title: int = 0  # 0 = auto-fill (flexible)
    col_artist: int = 0
    col_album: int = 0
    col_duration: int = 8
    bidi_mode: str = "auto"  # "auto", "reorder", "passthrough"
    show_selection_info: bool = True
    home_shelves: int = 3
    region: str = "ZZ"  # ISO 3166-1 alpha-2 (or "ZZ" = Global) — used by Browse > Charts
    sidebar_overflow: str = "truncate"  # "truncate" or "wrap"
    show_queue_source: bool = True


@dataclass
class NotificationSettings:
    enabled: bool = True
    timeout_seconds: int = 5
    format: str = "{title} — {artist}"


@dataclass
class MPRISSettings:
    enabled: bool = True


@dataclass
class DiscordSettings:
    enabled: bool = False
    # Empty = use the bundled "YouTube Music" app; set to your own Discord
    # application ID to publish Rich Presence under your own app.
    client_id: str = ""


@dataclass
class LyricsSettings:
    transliteration: bool = False


@dataclass
class LastFMSettings:
    enabled: bool = False
    api_key: str = ""
    api_secret: str = ""
    session_key: str = ""
    username: str = ""
    password_hash: str = ""


@dataclass
class LoggingSettings:
    level: str = "WARNING"  # DEBUG, INFO, WARNING, ERROR, CRITICAL
    max_bytes: int = 5 * 1024 * 1024  # 5 MB per file
    backup_count: int = 3  # rotate up to 3 old log files
    keep_crashes: int = 10  # max number of crash files to keep


SECTION_MAP: dict[str, type] = {
    "general": GeneralSettings,
    "playback": PlaybackSettings,
    "yt_dlp": YtDlpSettings,
    "search": SearchSettings,
    "cache": CacheSettings,
    "ui": UISettings,
    "notifications": NotificationSettings,
    "mpris": MPRISSettings,
    "lyrics": LyricsSettings,
    "discord": DiscordSettings,
    "lastfm": LastFMSettings,
    "logging": LoggingSettings,
}


@dataclass
class Settings:
    general: GeneralSettings = field(default_factory=GeneralSettings)
    playback: PlaybackSettings = field(default_factory=PlaybackSettings)
    yt_dlp: YtDlpSettings = field(default_factory=YtDlpSettings)
    search: SearchSettings = field(default_factory=SearchSettings)
    cache: CacheSettings = field(default_factory=CacheSettings)
    ui: UISettings = field(default_factory=UISettings)
    notifications: NotificationSettings = field(default_factory=NotificationSettings)
    mpris: MPRISSettings = field(default_factory=MPRISSettings)
    lyrics: LyricsSettings = field(default_factory=LyricsSettings)
    discord: DiscordSettings = field(default_factory=DiscordSettings)
    lastfm: LastFMSettings = field(default_factory=LastFMSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)

    @classmethod
    def load(cls, path: Path = CONFIG_FILE) -> Self:
        settings = cls()

        if not path.exists():
            settings._create_default(path)
            return settings

        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            logger.warning(
                "Config file %s is corrupted (%s) — backing up and recreating with defaults.",
                path,
                exc,
            )
            backup = path.with_suffix(".toml.bak")
            try:
                path.rename(backup)
                logger.warning("Backed up corrupted config to %s", backup)
            except OSError:
                pass
            settings._create_default(path)
            return settings

        for section_name, section_cls in SECTION_MAP.items():
            if section_name not in data:
                continue
            section_data = data[section_name]
            if not isinstance(section_data, dict):
                logger.warning("Config section [%s] is not a table — ignoring.", section_name)
                continue
            section_instance = getattr(settings, section_name)
            type_hints = typing.get_type_hints(section_cls)
            for f_info in fields(section_instance):
                if f_info.name not in section_data:
                    continue
                value = section_data[f_info.name]
                expected = type_hints.get(f_info.name)
                if expected is not None and not _value_matches_type(value, expected):
                    logger.warning(
                        "Config value %s.%s has invalid type %s — using default.",
                        section_name,
                        f_info.name,
                        type(value).__name__,
                    )
                    continue
                setattr(section_instance, f_info.name, value)

        settings.ui.home_shelves = max(1, min(25, settings.ui.home_shelves))

        return settings

    def save(self, path: Path = CONFIG_FILE) -> None:
        import os

        from ytm_player.config.paths import SECURE_FILE_MODE, secure_chmod

        path.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []

        for section_name in SECTION_MAP:
            section = getattr(self, section_name)
            lines.append(f"[{section_name}]")
            for f_info in fields(section):
                value = getattr(section, f_info.name)
                lines.append(f"{f_info.name} = {_format_toml_value(value)}")
            lines.append("")

        content = "\n".join(lines)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp_path.write_text(content, encoding="utf-8")
            secure_chmod(tmp_path, SECURE_FILE_MODE)
            try:
                os.replace(tmp_path, path)
            except PermissionError:
                path.write_text(content, encoding="utf-8")
                secure_chmod(path, SECURE_FILE_MODE)
        finally:
            # Clean up temp file if replace failed.
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def _create_default(self, path: Path) -> None:
        self.save(path)

    @property
    def cache_dir(self) -> Path:
        if self.cache.location:
            return Path(self.cache.location).expanduser()
        return CACHE_DIR


def _value_matches_type(value: object, expected: object) -> bool:
    """Best-effort check that a TOML-loaded `value` fits a declared field type.

    Handles str, bool, int, float, list, and unions (including ``Optional`` and
    PEP 604 ``X | Y``). Deliberately pragmatic:

    - bool is checked before int (bool is an int subclass), so a bool field
      rejects a plain int and an int field rejects a bool.
    - float fields also accept int (numeric widening), but not bool.
    - unions match if *any* member matches; a ``None`` member allows ``None``.
    - unknown/complex types are accepted rather than rejected (fail-open).
    """
    origin = typing.get_origin(expected)
    if origin is typing.Union or origin is types.UnionType:
        return any(_value_matches_type(value, arg) for arg in typing.get_args(expected))

    # Strip generic parameters (e.g. list[str] -> list) for the isinstance check.
    concrete = origin if origin is not None else expected

    if concrete is type(None):
        return value is None
    if concrete is bool:
        return isinstance(value, bool)
    if concrete is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if concrete is float:
        return isinstance(value, float) or (isinstance(value, int) and not isinstance(value, bool))
    if concrete is str:
        return isinstance(value, str)
    if concrete is list:
        return isinstance(value, list)
    if isinstance(concrete, type):
        return isinstance(value, concrete)
    return True


def _format_toml_value(value: object) -> str:
    match value:
        case bool():
            return "true" if value else "false"
        case int():
            return str(value)
        case str():
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'
        case list():
            items = ", ".join(_format_toml_value(v) for v in value)
            return f"[{items}]"
        case _:
            return repr(value)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.load()
    return _settings
