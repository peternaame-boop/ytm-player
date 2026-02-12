"""Spotify playlist import — extract tracks, match on YouTube Music, create playlist."""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum

import click
from ytmusicapi import YTMusic

try:
    from rich.console import Console
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
    from thefuzz import fuzz

    _HAS_SPOTIFY_DEPS = True
except ImportError:
    _HAS_SPOTIFY_DEPS = False

from pathlib import Path

from ytm_player.config.paths import CONFIG_DIR, SECURE_FILE_MODE, SPOTIFY_CREDS_FILE
from ytm_player.utils.formatting import (
    VALID_VIDEO_ID,
    extract_artist,
    extract_duration,
    format_duration,
    get_video_id,
)

logger = logging.getLogger(__name__)

TITLE_MATCH_WEIGHT = 0.6
ARTIST_MATCH_WEIGHT = 0.4
AUTO_MATCH_THRESHOLD = 85


class MatchType(Enum):
    EXACT = "exact"
    MULTIPLE = "multiple"
    NONE = "none"


@dataclass
class MatchResult:
    """Result of matching a Spotify track against YouTube Music."""

    spotify_track: dict
    match_type: MatchType
    candidates: list[dict] = field(default_factory=list)
    selected: dict | None = None


# ── Spotify credential helpers ────────────────────────────────────────


def load_spotify_creds() -> dict[str, str] | None:
    """Load stored Spotify client_id/client_secret, or ``None``."""
    if not SPOTIFY_CREDS_FILE.exists():
        return None
    try:
        data = json.loads(SPOTIFY_CREDS_FILE.read_text())
        if data.get("client_id") and data.get("client_secret"):
            return data
    except Exception:
        logger.debug("Failed to parse Spotify credentials file", exc_info=True)
    return None


def save_spotify_creds(client_id: str, client_secret: str) -> None:
    """Persist Spotify API credentials."""
    import os

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(SPOTIFY_CREDS_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, SECURE_FILE_MODE)
    with os.fdopen(fd, "w") as f:
        json.dump({"client_id": client_id, "client_secret": client_secret}, f, indent=2)


def has_spotify_creds() -> bool:
    """Return True if Spotify API credentials are configured."""
    return load_spotify_creds() is not None


# ── Track extraction ──────────────────────────────────────────────────


def _extract_playlist_id(url: str) -> str:
    """Pull the playlist/album ID from a Spotify URL."""
    m = re.search(r"(playlist|album)/([a-zA-Z0-9]+)", url)
    return m.group(2) if m else ""


def _parse_spotipy_item(item: dict) -> dict:
    """Normalise a spotipy track item to our internal format."""
    track = item.get("track", item)
    if not track or not track.get("name"):
        return {}
    artist_name = extract_artist(track)
    album = track.get("album", {})
    return {
        "name": track.get("name", ""),
        "artist": artist_name,
        "album": album.get("name", "") if isinstance(album, dict) else "",
        "duration_ms": track.get("duration_ms", 0),
    }


def extract_spotify_tracks_spotipy(url: str) -> tuple[str, list[dict]]:
    """Extract ALL tracks using the Spotify Web API (with pagination).

    Uses stored client credentials from ``~/.config/ytm-player/spotify.json``.
    Raises ``RuntimeError`` if credentials are missing or the request fails.
    """
    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials

    creds = load_spotify_creds()
    if not creds:
        raise RuntimeError("Spotify API credentials not configured")

    sp = spotipy.Spotify(
        auth_manager=SpotifyClientCredentials(
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
        )
    )

    playlist_id = _extract_playlist_id(url)
    if not playlist_id:
        raise RuntimeError("Could not parse playlist ID from URL")

    # Determine if this is a playlist or album.
    is_album = "/album/" in url

    if is_album:
        album = sp.album(playlist_id)
        playlist_name = album.get("name", "Imported Album")
        results = album.get("tracks", {})
    else:
        playlist = sp.playlist(playlist_id)
        playlist_name = playlist.get("name", "Imported Playlist")
        results = playlist.get("tracks", {})

    tracks: list[dict] = []
    while results:
        for item in results.get("items", []):
            parsed = _parse_spotipy_item(item)
            if parsed:
                tracks.append(parsed)
        # Follow pagination.
        if results.get("next"):
            results = sp.next(results)
        else:
            break

    return playlist_name, tracks


def extract_spotify_tracks(url: str) -> tuple[str, list[dict]]:
    """Extract tracks — tries spotipy (full), falls back to spotify_scraper (≤100).

    Returns:
        Tuple of (playlist_name, list of track dicts with name/artist/album/duration_ms).
    """
    # Try spotipy first (supports full pagination).
    if has_spotify_creds():
        try:
            return extract_spotify_tracks_spotipy(url)
        except Exception as exc:
            logger.warning("spotipy extraction failed, falling back to scraper: %s", exc)

    # Fallback: spotify_scraper (limited to ~100 tracks).
    from spotify_scraper import SpotifyClient

    client = SpotifyClient()
    try:
        playlist = client.get_playlist_info(url)

        playlist_name = playlist.get("name", "Imported Playlist")
        track_count = playlist.get("track_count", 0)
        tracks = []

        for item in playlist.get("tracks", []):
            track = item.get("track", item)
            artist_name = extract_artist(track)
            album = track.get("album", {})

            tracks.append(
                {
                    "name": track.get("name", ""),
                    "artist": artist_name,
                    "album": album.get("name", ""),
                    "duration_ms": track.get("duration_ms", 0),
                }
            )

        # Flag truncation so the TUI can warn the user.
        if track_count and track_count > len(tracks):
            logger.warning(
                "Playlist has %d tracks but scraper returned %d (limit ~100). "
                "Configure Spotify API credentials for full import.",
                track_count,
                len(tracks),
            )

        return playlist_name, tracks
    finally:
        client.close()


def _fuzzy_score(spotify_track: dict, ytm_track: dict) -> int:
    """Compute a fuzzy match score between a Spotify track and a YTM result."""
    sp_title = spotify_track.get("name", "").lower()
    sp_artist = spotify_track.get("artist", "").lower()

    ytm_title = (ytm_track.get("title", "") or "").lower()
    ytm_artist = extract_artist(ytm_track).lower()

    title_score = fuzz.ratio(sp_title, ytm_title)
    artist_score = fuzz.ratio(sp_artist, ytm_artist)

    # Weighted: title matters more but artist is still important.
    return int(title_score * TITLE_MATCH_WEIGHT + artist_score * ARTIST_MATCH_WEIGHT)


_MATCH_MAX_WORKERS = 5


def _search_and_score(
    ytmusic: YTMusic, sp_track: dict, index: int
) -> tuple[int, MatchResult]:
    """Search YTM for a single Spotify track and return (original_index, result).

    Designed to run inside a thread pool.  All data it touches is either
    thread-local (search_results, scored) or read-only (sp_track), so no
    locking is needed.
    """
    query = f"{sp_track['name']} {sp_track['artist']}"
    try:
        search_results = ytmusic.search(query, filter="songs", limit=5)
    except Exception:
        search_results = []

    if not search_results:
        return index, MatchResult(
            spotify_track=sp_track,
            match_type=MatchType.NONE,
        )

    scored = [(_fuzzy_score(sp_track, c), c) for c in search_results]
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_candidate = scored[0]

    if best_score >= AUTO_MATCH_THRESHOLD:
        return index, MatchResult(
            spotify_track=sp_track,
            match_type=MatchType.EXACT,
            candidates=[c for _, c in scored],
            selected=best_candidate,
        )

    return index, MatchResult(
        spotify_track=sp_track,
        match_type=MatchType.MULTIPLE,
        candidates=[c for _, c in scored],
    )


def match_tracks(
    ytmusic: YTMusic, spotify_tracks: list[dict], console: Console
) -> list[MatchResult]:
    """Search YTM for each Spotify track and categorize matches."""
    if not _HAS_SPOTIFY_DEPS:
        click.echo("Spotify import requires extra dependencies: pip install ytm-player[spotify]")
        return []

    # Pre-allocate results list so we can slot them back in order.
    results: list[MatchResult | None] = [None] * len(spotify_tracks)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task = progress.add_task("Searching YouTube Music...", total=len(spotify_tracks))

        with ThreadPoolExecutor(max_workers=_MATCH_MAX_WORKERS) as executor:
            futures = {
                executor.submit(_search_and_score, ytmusic, sp_track, idx): idx
                for idx, sp_track in enumerate(spotify_tracks)
            }

            for future in as_completed(futures):
                idx, match_result = future.result()
                results[idx] = match_result
                progress.advance(task)

    # All slots should be filled; narrow the type for the caller.
    return [r for r in results if r is not None]


def _display_candidate(idx: int, candidate: dict) -> str:
    """Format a single YTM candidate for display."""
    title = candidate.get("title", "?")
    artist_str = extract_artist(candidate)
    dur_sec = extract_duration(candidate)
    duration = format_duration(dur_sec) if dur_sec else "?"
    result_type = candidate.get("resultType", "")
    suffix = f" [{result_type}]" if result_type and result_type != "song" else ""
    return f"  {idx}. {title} — {artist_str} ({duration}){suffix}"


def run_import(spotify_url: str, auth_file: Path) -> None:
    """Orchestrate the full interactive Spotify → YTM import flow."""
    if not _HAS_SPOTIFY_DEPS:
        click.echo("Spotify import requires extra dependencies: pip install ytm-player[spotify]")
        return
    console = Console()

    # Validate URL.
    if not re.match(r"https?://open\.spotify\.com/(playlist|album)/", spotify_url):
        console.print("[red]Invalid Spotify URL.[/red] Expected a playlist or album link.")
        return

    # Step 1: Extract tracks from Spotify.
    console.print()
    with console.status("Fetching Spotify playlist..."):
        try:
            playlist_name, spotify_tracks = extract_spotify_tracks(spotify_url)
        except Exception as exc:
            console.print(f"[red]Failed to fetch Spotify playlist:[/red] {exc}")
            return

    if not spotify_tracks:
        console.print("[yellow]No tracks found in the playlist.[/yellow]")
        return

    console.print(f'Fetched [bold]"{playlist_name}"[/bold] ({len(spotify_tracks)} tracks)')
    console.print()

    # Step 2: Initialize YTM client.
    try:
        ytmusic = YTMusic(str(auth_file))
    except Exception as exc:
        console.print(f"[red]Failed to initialize YouTube Music client:[/red] {exc}")
        return

    # Step 3: Search & match.
    results = match_tracks(ytmusic, spotify_tracks, console)

    exact = [r for r in results if r.match_type == MatchType.EXACT]
    multiple = [r for r in results if r.match_type == MatchType.MULTIPLE]
    none_found = [r for r in results if r.match_type == MatchType.NONE]

    console.print()
    console.print("Results:")
    console.print(f"  [green]✓[/green] {len(exact)} exact matches")
    if multiple:
        console.print(f"  [yellow]?[/yellow] {len(multiple)} need your input")
    if none_found:
        console.print(f"  [red]✗[/red] {len(none_found)} not found")
    console.print()

    # Step 4: Resolve ambiguous matches interactively.
    for result in multiple:
        sp = result.spotify_track
        console.print(f'── [bold]"{sp["name"]}"[/bold] by {sp["artist"]} ──')
        console.print("Multiple matches:")
        for i, candidate in enumerate(result.candidates[:5], 1):
            console.print(_display_candidate(i, candidate))
        skip_idx = len(result.candidates[:5]) + 1
        console.print(f"  {skip_idx}. Skip")

        choice = click.prompt(
            "Choice",
            type=int,
            default=1,
            show_default=True,
        )

        if 1 <= choice <= len(result.candidates[:5]):
            result.selected = result.candidates[choice - 1]
            result.match_type = MatchType.EXACT
        else:
            result.match_type = MatchType.NONE
        console.print()

    # Step 5: Resolve "not found" tracks.
    still_none = [r for r in results if r.match_type == MatchType.NONE]
    for result in still_none:
        sp = result.spotify_track
        console.print(f'── [bold]"{sp["name"]}"[/bold] by {sp["artist"]} ──')
        console.print("[red]No match found.[/red]")
        console.print("  1. Search with different query")
        console.print("  2. Enter video ID manually")
        console.print("  3. Skip")

        choice = click.prompt("Choice", type=int, default=3, show_default=True)

        if choice == 1:
            custom_query = click.prompt("Search query")
            try:
                custom_results = ytmusic.search(custom_query, filter="songs", limit=5)
            except Exception:
                custom_results = []

            if custom_results:
                for i, candidate in enumerate(custom_results, 1):
                    console.print(_display_candidate(i, candidate))
                skip_idx = len(custom_results) + 1
                console.print(f"  {skip_idx}. Skip")
                pick = click.prompt("Choice", type=int, default=1, show_default=True)
                if 1 <= pick <= len(custom_results):
                    result.selected = custom_results[pick - 1]
                    result.match_type = MatchType.EXACT
            else:
                console.print("[yellow]No results found.[/yellow]")

        elif choice == 2:
            video_id = click.prompt("Video ID").strip()
            if video_id and VALID_VIDEO_ID.match(video_id):
                result.selected = {"videoId": video_id}
                result.match_type = MatchType.EXACT
            elif video_id:
                console.print("[yellow]Invalid video ID format.[/yellow]")

        console.print()

    # Step 6: Collect confirmed track IDs.
    confirmed = [r for r in results if r.selected is not None]
    skipped = len(results) - len(confirmed)

    if not confirmed:
        console.print("[yellow]No tracks to add. Import cancelled.[/yellow]")
        return

    # Step 7: Let user rename the playlist.
    final_name = click.prompt(
        "Playlist name",
        default=playlist_name,
        show_default=True,
    )

    # Step 8: Create the YTM playlist and add tracks.
    console.print()
    with console.status(f'Creating playlist "{final_name}" on YouTube Music...'):
        try:
            video_ids = [get_video_id(r.selected) for r in confirmed if r.selected]
            video_ids = [vid for vid in video_ids if vid]  # Filter empty.

            playlist_id = ytmusic.create_playlist(
                final_name,
                f"Imported from Spotify: {playlist_name}",
                privacy_status="PRIVATE",
                video_ids=video_ids,
            )

            if not playlist_id or not isinstance(playlist_id, str):
                console.print("[red]Failed to create playlist.[/red]")
                return

        except Exception as exc:
            console.print(f"[red]Failed to create playlist:[/red] {exc}")
            return

    console.print(
        f"[green]✓[/green] Playlist created with {len(video_ids)} tracks"
        + (f" ({skipped} skipped)" if skipped else "")
    )
