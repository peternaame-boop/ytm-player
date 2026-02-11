"""Formatting utilities for display values."""

from __future__ import annotations

import re
from datetime import datetime, timezone

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
    if max_len <= 3:
        return text[:max_len]
    return text[: max_len - 3] + "..."


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
    """Extract duration in seconds from various track dict formats."""
    dur = track.get("duration_seconds")
    if dur is not None:
        return int(dur)
    dur = track.get("duration")
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
    """
    normalized: list[dict] = []
    for t in raw_tracks:
        video_id = t.get("videoId") or t.get("video_id", "")
        title = t.get("title", "Unknown")
        artist = extract_artist(t)
        album_info = t.get("album")
        album = (
            album_info.get("name", "")
            if isinstance(album_info, dict)
            else (album_info or "")
        )
        album_id = (
            album_info.get("id")
            if isinstance(album_info, dict)
            else t.get("album_id")
        )
        duration = t.get("duration_seconds")
        if duration is None:
            duration = t.get("duration")
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
            }
        )
    return normalized


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
