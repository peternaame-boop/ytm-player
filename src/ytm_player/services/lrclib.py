"""LRCLIB.net fallback for synced lyrics when YouTube Music doesn't provide them."""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

_BASE_URL = "https://lrclib.net/api/get"
_TIMEOUT = 5


async def get_synced_lyrics(
    title: str, artist: str, duration_seconds: float | None = None
) -> str | None:
    """Fetch synced LRC lyrics from LRCLIB.net.

    Returns the LRC-format string if available, or None.
    """
    params: dict[str, str] = {
        "track_name": title,
        "artist_name": artist,
    }
    if duration_seconds is not None:
        params["duration"] = str(int(duration_seconds))

    url = f"{_BASE_URL}?{urllib.parse.urlencode(params)}"

    def _fetch() -> str | None:
        req = urllib.request.Request(url, headers={"User-Agent": "ytm-player/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
                return data.get("syncedLyrics") or None
        except Exception:
            logger.debug("LRCLIB request failed for %r by %r", title, artist, exc_info=True)
            return None

    return await asyncio.to_thread(_fetch)
