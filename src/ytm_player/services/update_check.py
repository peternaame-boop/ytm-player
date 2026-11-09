"""Check PyPI for a newer ytm-player release.

The check runs in a background worker on app startup. Results are
cached for 24 hours in CONFIG_DIR/update_check.json so we don't hit
PyPI more than once a day. Network failures are silent — being offline
is not an error worth surfacing to the user.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import requests
from packaging.version import InvalidVersion, Version

logger = logging.getLogger(__name__)

_PYPI_URL = "https://pypi.org/pypi/ytm-player/json"
_CHECK_INTERVAL_SECONDS = 24 * 60 * 60  # 24h
_REQUEST_TIMEOUT = 5.0


def _is_newer(latest: str, current: str) -> bool:
    """True when *latest* is strictly newer than *current* under PEP 440."""
    try:
        return Version(latest) > Version(current)
    except InvalidVersion:
        return False


def _fetch_latest_from_pypi() -> str | None:
    """Hit PyPI for the latest ytm-player version. None on any failure."""
    try:
        resp = requests.get(
            _PYPI_URL,
            headers={"User-Agent": "ytm-player update-check"},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("info", {}).get("version")
    except (requests.RequestException, ValueError):
        return None
    except Exception:  # pragma: no cover — belt-and-braces
        logger.debug("Unexpected PyPI fetch failure", exc_info=True)
        return None


def _read_cache(cache_file: Path) -> dict:
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_cache(cache_file: Path, latest: str) -> None:
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"checked_at": time.time(), "latest": latest}
        cache_file.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        logger.debug("Failed to write update-check cache", exc_info=True)


def check_for_update(current_version: str, cache_file: Path) -> str | None:
    """Return the latest PyPI version string IF strictly newer than current.

    Returns None if:
    - The cache says we checked within the last 24h.
    - PyPI is unreachable.
    - The latest version is not newer than *current_version*.
    """
    cache = _read_cache(cache_file)
    try:
        last_checked = float(cache.get("checked_at", 0) or 0)
    except (TypeError, ValueError):
        last_checked = 0.0
    elapsed = time.time() - last_checked
    # Negative elapsed (clock went backwards) → treat as stale and re-fetch.
    if 0 <= elapsed < _CHECK_INTERVAL_SECONDS:
        latest = cache.get("latest")
        if isinstance(latest, str) and latest and _is_newer(latest, current_version):
            return latest
        return None

    latest = _fetch_latest_from_pypi()
    if latest is None:
        return None
    _write_cache(cache_file, latest)
    if _is_newer(latest, current_version):
        return latest
    return None
