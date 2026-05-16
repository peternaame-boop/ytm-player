"""Local favorites list for internet radio stations.

Persists a tiny JSON list at ``~/.config/ytm-player/stations.json``. Each
entry is a frozen ``Station`` record (denormalized so a removed favorite
keeps working even if radio-browser drops the UUID).

Concurrency: writes go through a re-entrant lock since the Stations page,
the IPC handler, and the playback path could all touch favorites at the
same time. Reads return fresh copies of the list so callers can iterate
without holding the lock.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict
from pathlib import Path

from ytm_player.config.paths import SECURE_FILE_MODE, STATIONS_FILE, secure_chmod
from ytm_player.services.radio_browser import Station

logger = logging.getLogger(__name__)


class StationFavorites:
    """Singleton holding the user's favorite stations on disk."""

    _instance: StationFavorites | None = None

    def __new__(cls) -> StationFavorites:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, path: Path = STATIONS_FILE) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._path = path
        self._lock = threading.RLock()
        self._entries: list[Station] = []
        self._load()

    # ── public API ────────────────────────────────────────────────────

    def list(self) -> list[Station]:
        with self._lock:
            return list(self._entries)

    def is_favorite(self, uuid: str) -> bool:
        if not uuid:
            return False
        with self._lock:
            return any(s.uuid == uuid for s in self._entries)

    def add(self, station: Station) -> bool:
        """Add a station; returns True if newly added, False if already present."""
        if not station.uuid:
            return False
        with self._lock:
            if any(s.uuid == station.uuid for s in self._entries):
                return False
            self._entries.append(station)
            self._save()
        return True

    def remove(self, uuid: str) -> bool:
        if not uuid:
            return False
        with self._lock:
            before = len(self._entries)
            self._entries = [s for s in self._entries if s.uuid != uuid]
            if len(self._entries) == before:
                return False
            self._save()
        return True

    def toggle(self, station: Station) -> bool:
        """Add if missing, remove if present. Returns new is_favorite state."""
        if self.is_favorite(station.uuid):
            self.remove(station.uuid)
            return False
        self.add(station)
        return True

    # ── persistence ───────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("StationFavorites: failed to load %s", self._path)
            return
        if not isinstance(raw, list):
            return
        entries: list[Station] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                entries.append(self._station_from_dict(item))
            except Exception:
                logger.debug("StationFavorites: ignoring malformed entry: %s", item)
        self._entries = entries

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [asdict(s) for s in self._entries]
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            secure_chmod(tmp, SECURE_FILE_MODE)
            os.replace(tmp, self._path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    @staticmethod
    def _station_from_dict(data: dict) -> Station:
        return Station(
            uuid=str(data.get("uuid", "")),
            name=str(data.get("name", "")),
            url=str(data.get("url", "")),
            homepage=str(data.get("homepage", "")),
            favicon=str(data.get("favicon", "")),
            country=str(data.get("country", "")),
            country_code=str(data.get("country_code", "")),
            language=str(data.get("language", "")),
            tags=list(data.get("tags", []) or []),
            codec=str(data.get("codec", "")),
            bitrate=int(data.get("bitrate") or 0),
            votes=int(data.get("votes") or 0),
            click_count=int(data.get("click_count") or 0),
            last_check_ok=bool(data.get("last_check_ok", True)),
        )
