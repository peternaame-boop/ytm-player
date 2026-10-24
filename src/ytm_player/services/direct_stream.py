"""A fast stream lookup: one innertube ``player`` request as the visionos client.

yt-dlp spends 1.7 to 2.2 s per track: it downloads the watch page, then calls
the player API, then reads the m3u8 manifest. The visionos client needs no
player JavaScript, so its stream URLs play as they come. One ``player``
request with a cached visitor ID returns them in about 0.6 s.

Every lookup here may fail. The caller then falls back to yt-dlp, which stays
the reference path. Skipping only the watch page inside yt-dlp was measured
slower (3.3 to 4.9 s), and without a visitor ID it failed YouTube's bot check.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_PLAYER_URL = "https://www.youtube.com/youtubei/v1/player?prettyPrint=false"
_HOME_URL = "https://music.youtube.com/"
_TIMEOUT = 6
_BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0 Safari/537.36"
# The client yt-dlp names "visionos" (yt_dlp/extractor/youtube/_base.py).
_CLIENT = {
    "clientName": "VISIONOS",
    "clientVersion": "1.02",
    "deviceMake": "Apple",
    "deviceModel": "RealityDevice17,1",
    "userAgent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_7_3) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/26.0 Safari/605.1.15",
    "osName": "visionOS",
    "osVersion": "26.5.23O471",
    "hl": "en",
}
_CLIENT_ID = "101"
# A visitor ID stays good for a long time. Refresh it daily, or after a failure.
_VISITOR_TTL = 24 * 60 * 60
_VISITOR_RE = re.compile(r'(?:VISITOR_DATA|visitorData)\\?"\s*:\s*\\?"([^"\\]+)')
# Highest audio bitrate each quality setting accepts, in bits per second.
_MAX_BITRATE = {"high": None, "medium": 135_000, "low": 70_000}

_lock = threading.Lock()
_visitor: tuple[float, str] | None = None


def _visitor_id() -> str | None:
    global _visitor
    with _lock:
        if _visitor and time.time() - _visitor[0] < _VISITOR_TTL:
            return _visitor[1]
    request = urllib.request.Request(_HOME_URL, headers={"User-Agent": _BROWSER_UA})
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as resp:  # noqa: S310 - YouTube Music
        found = _VISITOR_RE.findall(resp.read().decode("utf-8", "replace"))
    if not found:
        return None
    with _lock:
        _visitor = (time.time(), found[0])
    return found[0]


def forget_visitor() -> None:
    """Drop the cached visitor ID, so the next lookup fetches a fresh one."""
    global _visitor
    with _lock:
        _visitor = None


def pick_audio(formats: list[dict[str, Any]], quality: str) -> dict[str, Any] | None:
    """The best audio-only format with a direct URL that *quality* allows."""
    audio = [f for f in formats if f.get("url") and str(f.get("mimeType", "")).startswith("audio/")]
    if not audio:
        return None
    ceiling = _MAX_BITRATE.get(quality)
    allowed = [f for f in audio if ceiling is None or (f.get("bitrate") or 0) <= ceiling] or audio
    best_first = sorted(allowed, key=lambda f: f.get("bitrate") or 0, reverse=True)
    return best_first[0]


def lookup(video_id: str, quality: str) -> dict[str, Any] | None:
    """Stream details for *video_id*, or None when anything is unexpected.

    Returns ``url``, ``codec``, ``bitrate`` (kbps) and ``duration`` (seconds).
    Blocking. Run it in a thread.
    """
    try:
        visitor = _visitor_id()
        if not visitor:
            return None
        body = {
            "context": {"client": {**_CLIENT, "visitorData": visitor}},
            "videoId": video_id,
            "contentCheckOk": True,
            "racyCheckOk": True,
        }
        request = urllib.request.Request(
            _PLAYER_URL,
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "User-Agent": _CLIENT["userAgent"],
                "X-Goog-Visitor-Id": visitor,
                "X-YouTube-Client-Name": _CLIENT_ID,
                "X-YouTube-Client-Version": _CLIENT["clientVersion"],
                "Origin": "https://www.youtube.com",
            },
        )
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as resp:  # noqa: S310 - YouTube API
            data = json.loads(resp.read())
    except Exception:
        logger.debug("Direct lookup failed for %s", video_id, exc_info=True)
        forget_visitor()
        return None
    if data.get("playabilityStatus", {}).get("status") != "OK":
        logger.debug(
            "Direct lookup not playable for %s: %s", video_id, data.get("playabilityStatus")
        )
        return None
    streaming = data.get("streamingData") or {}
    chosen = pick_audio(streaming.get("adaptiveFormats") or [], quality)
    if chosen is None:
        return None
    codec = re.search(r'codecs="([^"]+)"', chosen.get("mimeType", ""))
    duration_ms = int(chosen.get("approxDurationMs") or 0)
    return {
        "url": chosen["url"],
        "codec": codec.group(1) if codec else "unknown",
        "bitrate": int((chosen.get("averageBitrate") or chosen.get("bitrate") or 0) / 1000),
        "duration": duration_ms // 1000,
    }
