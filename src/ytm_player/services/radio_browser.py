"""radio-browser.info API client for the Stations page.

radio-browser.info exposes a JSON HTTP catalogue of ~30k internet radio
stations contributed by the community. We use it for browsing/searching
plus the optional click-log and vote endpoints (they help station
rankings stay accurate community-wide).

Why no httpx/requests dep: this service makes a couple of small JSON
GETs and is the only stations dependency. urllib + stdlib JSON keep the
``[stations]`` extra empty (see pyproject.toml).

API etiquette honoured:
- DNS round-robin via ``all.api.radio-browser.info`` for server discovery
- Identifying User-Agent (override via settings.stations.user_agent)
- 1h in-memory cache for list endpoints; manual refresh available
- Reasonable per-call timeout (settings.stations.request_timeout)

This service does NOT clash with the existing YouTube-Music "Start Radio"
feature in app/_track_actions.py — that uses ``radio_tracks`` and
``_fetch_and_play_radio`` to play algorithmic recommendations. The
terminology lives in two layers: YT-Music *radio* = algorithmic station;
*stations* = internet broadcasting catalogue.
"""

from __future__ import annotations

import json
import logging
import random
import socket
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ytm_player.config.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Station:
    """Normalized view of a radio-browser station record."""

    uuid: str
    name: str
    url: str  # stream URL (use url_resolved when present — radio-browser pre-resolves)
    homepage: str
    favicon: str
    country: str
    country_code: str
    language: str
    tags: list[str]
    codec: str
    bitrate: int
    votes: int
    click_count: int
    last_check_ok: bool

    @classmethod
    def from_api(cls, data: dict) -> Station:
        # radio-browser returns either `url_resolved` or `url`; prefer resolved.
        url = data.get("url_resolved") or data.get("url") or ""
        tags_raw = data.get("tags", "") or ""
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        return cls(
            uuid=str(data.get("stationuuid", "")),
            name=str(data.get("name", "")).strip(),
            url=url,
            homepage=str(data.get("homepage", "")),
            favicon=str(data.get("favicon", "")),
            country=str(data.get("country", "")),
            country_code=str(data.get("countrycode", "")),
            language=str(data.get("language", "")),
            tags=tags,
            codec=str(data.get("codec", "")),
            bitrate=int(data.get("bitrate") or 0),
            votes=int(data.get("votes") or 0),
            click_count=int(data.get("clickcount") or 0),
            last_check_ok=bool(data.get("lastcheckok")),
        )

    def to_track_dict(self) -> dict[str, Any]:
        """Adapt to the standardized track-dict shape used by Player.

        Stations don't have a video_id; we synthesise one prefixed by
        ``station:`` so downstream code that keys off video_id can tell
        a station from a YT track without ambiguity.
        """
        tag_blurb = ", ".join(self.tags[:3]) if self.tags else self.country
        return {
            "video_id": f"station:{self.uuid}",
            "title": self.name or "Unknown station",
            "artist": tag_blurb or "Radio",
            "artists": [{"name": tag_blurb or "Radio", "id": ""}],
            "album": "",
            "album_id": "",
            "duration": None,  # Live stream — duration is unknown / infinite
            "thumbnail_url": self.favicon or "",
            "is_video": False,
            "is_station": True,
            "station_url": self.url,
            "station_uuid": self.uuid,
        }


class RadioBrowserError(RuntimeError):
    """Raised when the radio-browser API can't be reached or parsed."""


class RadioBrowser:
    """Singleton wrapper over the radio-browser.info JSON API."""

    _instance: RadioBrowser | None = None

    def __new__(cls) -> RadioBrowser:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True

        settings = get_settings().stations
        self._timeout = max(2, min(60, settings.request_timeout))
        self._cache_ttl = max(0, settings.cache_ttl_seconds)
        self._user_agent = settings.user_agent or self._default_ua()
        self._server_override = settings.server

        self._base_url: str | None = None
        self._base_url_at: float = 0.0
        self._cache: dict[str, tuple[float, Any]] = {}

    @staticmethod
    def _default_ua() -> str:
        try:
            from ytm_player import __version__
        except Exception:
            __version__ = "dev"
        return f"ytm-player/{__version__}"

    # ── server discovery ──────────────────────────────────────────────

    def _resolve_server(self) -> str:
        """Pick a radio-browser server.

        Caches the choice for an hour. radio-browser publishes a DNS
        round-robin at ``all.api.radio-browser.info``; we look up the
        A records and pick a random host, mirroring the API's own
        recommendation. If DNS fails, fall back to ``de1.api...``.
        """
        if self._server_override and self._server_override.lower() != "auto":
            return self._server_override.rstrip("/")

        now = time.time()
        if self._base_url and (now - self._base_url_at) < 3600:
            return self._base_url

        try:
            _, _, ips = socket.gethostbyname_ex("all.api.radio-browser.info")
            hosts = []
            for ip in ips:
                try:
                    name, _, _ = socket.gethostbyaddr(ip)
                    hosts.append(name)
                except OSError:
                    continue
            if not hosts:
                raise RadioBrowserError("no servers")
            chosen = random.choice(hosts)
            self._base_url = f"https://{chosen}"
        except (OSError, RadioBrowserError):
            self._base_url = "https://de1.api.radio-browser.info"
        self._base_url_at = now
        logger.debug("RadioBrowser using server %s", self._base_url)
        return self._base_url

    # ── HTTP plumbing ─────────────────────────────────────────────────

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        base = self._resolve_server()
        query = f"?{urlencode(params)}" if params else ""
        url = f"{base}{path}{query}"
        req = Request(
            url,
            headers={
                "User-Agent": self._user_agent,
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=self._timeout) as resp:
                payload = resp.read()
        except (HTTPError, URLError, TimeoutError, socket.timeout) as exc:
            raise RadioBrowserError(f"GET {path} failed: {exc}") from exc
        try:
            return json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RadioBrowserError(f"GET {path} returned non-JSON: {exc}") from exc

    def _post(self, path: str) -> None:
        """Fire-and-forget POST (click-log + vote). Errors are logged, not raised."""
        base = self._resolve_server()
        req = Request(
            f"{base}{path}",
            method="POST",
            headers={"User-Agent": self._user_agent, "Content-Length": "0"},
        )
        try:
            with urlopen(req, timeout=self._timeout) as resp:
                resp.read()
        except (HTTPError, URLError, TimeoutError, socket.timeout) as exc:
            logger.debug("RadioBrowser POST %s failed: %s", path, exc)

    def _cached(self, key: str) -> Any | None:
        if self._cache_ttl <= 0:
            return None
        entry = self._cache.get(key)
        if entry is None:
            return None
        ts, val = entry
        if (time.time() - ts) > self._cache_ttl:
            self._cache.pop(key, None)
            return None
        return val

    def _store(self, key: str, val: Any) -> None:
        if self._cache_ttl > 0:
            self._cache[key] = (time.time(), val)

    def clear_cache(self) -> None:
        self._cache.clear()

    # ── public endpoints ──────────────────────────────────────────────

    def top_voted(self, limit: int = 100) -> list[Station]:
        return self._listing(f"/json/stations/topvote/{int(limit)}", cache_key=f"topvote:{limit}")

    def top_clicked(self, limit: int = 100) -> list[Station]:
        return self._listing(f"/json/stations/topclick/{int(limit)}", cache_key=f"topclick:{limit}")

    def search(
        self,
        *,
        name: str = "",
        tag: str = "",
        country_code: str = "",
        language: str = "",
        limit: int = 100,
    ) -> list[Station]:
        params: dict[str, Any] = {
            "limit": int(limit),
            "hidebroken": "true",
            "order": "votes",
            "reverse": "true",
        }
        if name:
            params["name"] = name
        if tag:
            params["tag"] = tag
        if country_code:
            params["countrycode"] = country_code
        if language:
            params["language"] = language
        key = f"search:{tuple(sorted(params.items()))}"
        return self._listing("/json/stations/search", params=params, cache_key=key)

    def by_country(self, country_code: str, limit: int = 100) -> list[Station]:
        return self.search(country_code=country_code, limit=limit)

    def by_tag(self, tag: str, limit: int = 100) -> list[Station]:
        return self.search(tag=tag, limit=limit)

    def log_click(self, uuid: str) -> None:
        """Increment radio-browser's click counter for a station."""
        if uuid:
            self._post(f"/json/url/{uuid}")

    def vote(self, uuid: str) -> None:
        if uuid:
            self._post(f"/json/vote/{uuid}")

    # ── plumbing ──────────────────────────────────────────────────────

    def _listing(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        cache_key: str,
    ) -> list[Station]:
        cached = self._cached(cache_key)
        if cached is not None:
            return cached
        try:
            raw = self._get(path, params)
        except RadioBrowserError:
            logger.exception("radio-browser listing %s failed", path)
            return []
        if not isinstance(raw, list):
            return []
        stations = [Station.from_api(item) for item in raw if isinstance(item, dict)]
        # Drop entries with empty URLs — they can't be played.
        stations = [s for s in stations if s.url]
        self._store(cache_key, stations)
        return stations
