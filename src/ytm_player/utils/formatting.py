"""Formatting utilities for display values."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Shared regex for validating YouTube video IDs.
VALID_VIDEO_ID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def format_duration(seconds: int) -> str:
    if seconds < 0:
        seconds = 0

    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def truncate(text: str, max_len: int) -> str:
    if max_len < 1:
        return ""
    if len(text) <= max_len:
        return text
    if max_len <= 1:
        return text[:max_len]
    return text[: max_len - 1] + "…"


def format_count(n: int) -> str:
    if abs(n) >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def format_size(bytes_val: int) -> str:
    size = float(bytes_val)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def get_video_id(track: dict) -> str:
    """Extract video ID from a track dict, checking both key conventions."""
    return track.get("videoId", "") or track.get("video_id", "")


def extract_artist(track: dict) -> str:
    """Extract display-friendly artist string from a track dict."""
    artist = track.get("artist")
    if artist:
        return artist
    artists = track.get("artists")
    if isinstance(artists, list) and artists:
        names = [a.get("name", "") if isinstance(a, dict) else str(a) for a in artists]
        return ", ".join(n for n in names if n)
    return "Unknown"


def extract_duration(track: dict) -> int:
    """Extract duration in seconds from various track dict formats.

    Checks ``duration_seconds``, ``duration``, and ``length``
    (ytmusicapi's get_watch_playlist uses ``length`` for "M:SS" strings).
    """
    dur = track.get("duration_seconds")
    if dur is not None:
        return int(dur)
    for key in ("duration", "length"):
        dur = track.get(key)
        if isinstance(dur, int):
            return dur
        if isinstance(dur, str) and ":" in dur:
            parts = dur.split(":")
            try:
                if len(parts) == 2:
                    return int(parts[0]) * 60 + int(parts[1])
                if len(parts) == 3:
                    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            except ValueError:
                pass
    return 0


def normalize_tracks(raw_tracks: list[dict]) -> list[dict]:
    """Ensure tracks have the fields TrackTable expects.

    ytmusicapi returns slightly different shapes for album tracks,
    playlist tracks, and search results.  This normalizes them.
    Tracks without a video_id are dropped (unplayable).
    """
    normalized: list[dict] = []
    skipped = 0
    for t in raw_tracks:
        video_id = t.get("videoId") or t.get("video_id", "")
        if not video_id:
            skipped += 1
            continue
        title = t.get("title", "Unknown")
        artist = extract_artist(t)
        album_info = t.get("album")
        album = album_info.get("name", "") if isinstance(album_info, dict) else (album_info or "")
        album_id = album_info.get("id") if isinstance(album_info, dict) else t.get("album_id")
        raw_dur = next(
            (t[k] for k in ("duration_seconds", "duration", "length") if t.get(k) is not None),
            None,
        )
        duration = extract_duration(t) if raw_dur is not None else None
        thumbnail = None
        thumbs = t.get("thumbnails")
        if isinstance(thumbs, list) and thumbs:
            thumbnail = thumbs[-1].get("url") if isinstance(thumbs[-1], dict) else None

        normalized.append(
            {
                "video_id": video_id,
                "title": title,
                "artist": artist,
                "artists": t.get("artists", []),
                "album": album,
                "album_id": album_id,
                "duration": duration,
                "thumbnail_url": thumbnail,
                "is_video": t.get("isVideo", t.get("is_video", False)),
                "likeStatus": t.get("likeStatus"),
            }
        )
    if skipped:
        logger.debug("normalize_tracks: dropped %d tracks without video_id", skipped)
    return normalized


def clean_shelf_title(raw: str) -> str:
    """Normalise a YouTube chart shelf title for compact display.

    Strips country suffixes and applies preferred short labels.
    Does NOT strip brand prefixes (e.g. "Coachella 2026:") —
    event pills need the prefix to stay visually distinguishable
    from country chart pills.
    """
    from ytm_player.services.regions import CHART_REGIONS

    s = raw.strip()
    if " - " in s:
        head, tail = s.rsplit(" - ", 1)
        if any(tail.strip() == name for _, name in CHART_REGIONS):
            s = head.strip()
    for _, name in CHART_REGIONS:
        suffix = " " + name
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
            break
    s = re.sub(r"\bDaily Top 100 Songs\b", "Daily Top 100", s)
    s = re.sub(r"\bDaily Top Music Videos\b", "Daily Top Videos", s)
    s = re.sub(r"\bDaily Top Songs on Shorts\b", "Daily Top Songs (Shorts)", s)
    return s


def format_ago(timestamp: datetime) -> str:
    now = datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    delta = now - timestamp
    total_seconds = int(delta.total_seconds())

    if total_seconds < 0:
        return "just now"
    if total_seconds < 60:
        return f"{total_seconds} second{'s' if total_seconds != 1 else ''} ago"

    minutes = total_seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"

    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"

    days = hours // 24
    if days < 30:
        return f"{days} day{'s' if days != 1 else ''} ago"

    months = days // 30
    if months < 12:
        return f"{months} month{'s' if months != 1 else ''} ago"

    years = days // 365
    return f"{years} year{'s' if years != 1 else ''} ago"


def copy_to_clipboard(text: str) -> bool:
    """Copy text to system clipboard. Returns True on success."""
    import shutil
    import subprocess
    import sys

    if sys.platform == "win32":
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $input"],
                input=text.encode("utf-8"),
                check=True,
                creationflags=0x08000000,
            )
            return True
        except Exception:
            return False

    if sys.platform == "darwin":
        try:
            subprocess.run(["pbcopy"], input=text.encode(), check=True)
            return True
        except Exception:
            return False

    # Linux: try X11/Wayland clipboard tools.
    for cmd in ("xclip", "xsel", "wl-copy"):
        if shutil.which(cmd):
            try:
                args = [cmd]
                if cmd == "xclip":
                    args += ["-selection", "clipboard"]
                elif cmd == "xsel":
                    args += ["--clipboard", "--input"]
                subprocess.run(args, input=text.encode(), check=True)
                return True
            except Exception:
                continue
    return False


# Patterns commonly found in YouTube/YouTube Music titles that aren't
# part of the actual song name. Consolidated into a single compiled regex
# so sanitisation is one pass rather than N.
#
# The body alternation matches everything *inside* one set of brackets
# (round or square). `[^)\]]*` keeps each alternative from gobbling
# across nested closing brackets.
_LYRIC_NOISE_RE = re.compile(
    r"""
    \s*                                  # leading whitespace
    [\(\[]\s*                            # opening bracket
    (?:
        # ── "Official"-style descriptors ──
        official\s*(?:music\s*)?(?:video|audio|lyric|lyrics)
      | lyrics?\s*video
      | official
      | audio
      | video
      | hd
      | 4k

        # ── Featured-artist annotations ──
        # (feat. X), (ft. X), (featuring X) — bounded by the closing
        # bracket. The body alternation explicitly excludes `(` from the
        # plain-char branch so a nested `(...)` MUST be consumed by the
        # second alternative, leaving the outer `)` available for the
        # bracket close. This handles things like "feat. Bob (Junior)"
        # and "feat. Bob (of Band X)" without leaving an orphan `)`.
      | (?:feat\.?|ft\.?|featuring)\b(?:[^()\]]|\([^)]*\))*

        # ── Versions / re-releases / editions ──
      | remaster(?:ed)?(?:\s+\d{4})?     # Remastered, Remastered 2009
      | (?:[^)\]]+\s+)?remix(?:\s+[^)\]]*)?   # Remix, Extended/Radio/Club Remix
      | deluxe(?:\s+edition)?            # Deluxe, Deluxe Edition

        # ── Performance / arrangement annotations ──
      | live(?:\s+[^)\]]*)?              # Live, Live at X
      | acoustic(?:\s+[^)\]]*)?          # Acoustic, Acoustic Version, Acoustic Mix
    )
    \s*[\)\]]                            # closing bracket
    \s*                                  # trailing whitespace
    """,
    re.IGNORECASE | re.VERBOSE,
)


def sanitize_title_for_lyric_lookup(title: str, artist: str = "") -> str:
    """Strip common noise from a track title for better LRCLIB matching.

    Removes parenthesized/bracketed annotations like "(Official Music Video)",
    "[Lyrics]", "(Audio)", "(HD)", "(feat. Bob)", "(Remastered 2020)",
    "(Live)", "(Deluxe Edition)", and the "Artist - " prefix if *artist*
    is provided. Preserves everything else untouched.

    Returns the cleaned title. If sanitization would empty the title,
    returns the original string unchanged.
    """
    if not title:
        return title
    # Replace each match with a single space, then collapse runs so that
    # back-to-back annotations don't leave double spaces behind.
    cleaned = _LYRIC_NOISE_RE.sub(" ", title)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    if artist:
        cleaned_lower = cleaned.lower()
        prefix_lower = f"{artist.lower()} - "
        if cleaned_lower.startswith(prefix_lower):
            cleaned = cleaned[len(prefix_lower) :]
    cleaned = cleaned.strip()
    return cleaned if cleaned else title
