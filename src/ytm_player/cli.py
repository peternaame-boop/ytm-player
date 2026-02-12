"""CLI entry point for ytm-player.

Provides both the Textual TUI launcher (default) and headless subcommands
for scripting and shell integration.
"""

from __future__ import annotations

import os

# mpv segfaults if LC_NUMERIC is not C. The actual locale is set via
# ctypes in player.py; this env var provides a hint for subprocesses.
os.environ["LC_NUMERIC"] = "C"

import json
import logging
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, NoReturn

import click

from ytm_player import __version__
from ytm_player.config.paths import (
    CACHE_DB,
    CACHE_DIR,
    CONFIG_DIR,
    CONFIG_FILE,
    HISTORY_DB,
    ensure_dirs,
)
from ytm_player.ipc import ipc_request, is_tui_running
from ytm_player.services.auth import AuthManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json_output(data: Any, *, compact: bool = False) -> None:
    """Print *data* as JSON to stdout."""
    indent = None if compact else 2
    click.echo(json.dumps(data, indent=indent, default=str))


def _error(msg: str) -> NoReturn:
    """Print an error message to stderr and exit with code 1."""
    click.echo(f"Error: {msg}", err=True)
    sys.exit(1)


def _require_tui() -> None:
    """Exit with an error if the TUI is not currently running."""
    if not is_tui_running():
        _error("ytm-player is not running. Launch with `ytm` first.")


def _ipc(command: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Send an IPC command; exit with a friendly message on failure."""
    try:
        return ipc_request(command, args)
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        _error("TUI is not responding. Is ytm-player running?")


def _require_auth() -> Path:
    """Return the auth file path, or exit if not authenticated."""
    auth = AuthManager()
    if not auth.is_authenticated():
        _error("Not authenticated. Run `ytm setup` to configure YouTube Music credentials.")
    return auth.auth_file


# ---------------------------------------------------------------------------
# Main group
# ---------------------------------------------------------------------------


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="ytm-player")
@click.option(
    "--json",
    "compact_json",
    is_flag=True,
    hidden=True,
    help="Compact JSON output (no indentation).",
)
@click.pass_context
def main(ctx: click.Context, compact_json: bool) -> None:
    """ytm-player -- a full-featured YouTube Music TUI client.

    Launch without arguments to start the interactive TUI.
    Use subcommands for headless / scripting control.
    """
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s: %(message)s",
    )
    ensure_dirs()
    ctx.ensure_object(dict)
    ctx.obj["compact"] = compact_json

    if ctx.invoked_subcommand is None:
        from ytm_player.app import YTMPlayerApp

        app = YTMPlayerApp()
        app.run()


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


@main.command()
def setup() -> None:
    """Interactive authentication wizard for YouTube Music."""
    auth = AuthManager()

    if auth.is_authenticated():
        click.echo("Existing authentication found.")
        if not click.confirm("Do you want to re-authenticate?", default=False):
            click.echo("Setup cancelled.")
            return

    success = auth.setup_interactive()
    if not success:
        _error("Authentication setup failed.")

    click.echo("\nValidating credentials...")
    if auth.validate():
        click.echo("Authentication is valid. You're all set!")
    else:
        click.echo(
            "Warning: Could not validate authentication. "
            "The headers may be incomplete or expired. "
            "Try running `ytm setup` again.",
            err=True,
        )


# ---------------------------------------------------------------------------
# Playback controls (require TUI running)
# ---------------------------------------------------------------------------


@main.command()
def play() -> None:
    """Resume playback."""
    _require_tui()
    resp = _ipc("play")
    if resp.get("ok"):
        click.echo("Resumed.")
    else:
        _error(resp.get("error", "unknown error"))


@main.command()
def pause() -> None:
    """Pause playback."""
    _require_tui()
    resp = _ipc("pause")
    if resp.get("ok"):
        click.echo("Paused.")
    else:
        _error(resp.get("error", "unknown error"))


@main.command("next")
def next_track() -> None:
    """Skip to the next track."""
    _require_tui()
    resp = _ipc("next")
    if resp.get("ok"):
        click.echo("Skipped to next.")
    else:
        _error(resp.get("error", "unknown error"))


@main.command("prev")
def prev_track() -> None:
    """Go back to the previous track."""
    _require_tui()
    resp = _ipc("prev")
    if resp.get("ok"):
        click.echo("Went to previous.")
    else:
        _error(resp.get("error", "unknown error"))


@main.command()
@click.argument("offset")
def seek(offset: str) -> None:
    """Seek within the current track.

    OFFSET can be relative ("+10", "-10" for seconds) or absolute ("1:30").
    """
    _require_tui()
    resp = _ipc("seek", {"offset": offset})
    if resp.get("ok"):
        click.echo(f"Seeked to {offset}.")
    else:
        _error(resp.get("error", "unknown error"))


# ---------------------------------------------------------------------------
# Now playing / status (require TUI running)
# ---------------------------------------------------------------------------


@main.command()
@click.pass_context
def now(ctx: click.Context) -> None:
    """Show current track info (JSON)."""
    _require_tui()
    resp = _ipc("now")
    if resp.get("ok"):
        compact = ctx.obj.get("compact", False)
        _json_output(resp.get("data"), compact=compact)
    else:
        _error(resp.get("error", "unknown error"))


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show player status (JSON)."""
    _require_tui()
    resp = _ipc("status")
    if resp.get("ok"):
        compact = ctx.obj.get("compact", False)
        _json_output(resp.get("data"), compact=compact)
    else:
        _error(resp.get("error", "unknown error"))


# ---------------------------------------------------------------------------
# Search (standalone -- does not require TUI)
# ---------------------------------------------------------------------------


@main.command()
@click.argument("query", nargs=-1, required=True)
@click.option(
    "--filter",
    "-f",
    "filter_type",
    type=click.Choice(["songs", "albums", "artists", "playlists", "videos"], case_sensitive=False),
    default=None,
    help="Filter results by type.",
)
@click.option(
    "--limit", "-l", type=int, default=20, show_default=True, help="Maximum number of results."
)
@click.option("--json", "compact_json", is_flag=True, help="Compact JSON output.")
def search(query: tuple[str, ...], filter_type: str | None, limit: int, compact_json: bool) -> None:
    """Search YouTube Music and print results as JSON."""
    _require_auth()
    search_query = " ".join(query)

    auth = AuthManager()
    try:
        ytm = auth.create_ytmusic_client()
        results = ytm.search(search_query, filter=filter_type, limit=limit)
    except Exception as exc:
        _error(f"Search failed: {exc}")

    _json_output(results, compact=compact_json)


# ---------------------------------------------------------------------------
# Queue group (require TUI running)
# ---------------------------------------------------------------------------


@main.group(invoke_without_command=True)
@click.pass_context
def queue(ctx: click.Context) -> None:
    """Show or manage the play queue."""
    if ctx.invoked_subcommand is None:
        _require_tui()
        resp = _ipc("queue")
        if resp.get("ok"):
            compact = ctx.obj.get("compact", False)
            _json_output(resp.get("data"), compact=compact)
        else:
            _error(resp.get("error", "unknown error"))


@queue.command("add")
@click.argument("video_id")
def queue_add(video_id: str) -> None:
    """Add a track to the queue by VIDEO_ID."""
    _require_tui()
    resp = _ipc("queue_add", {"video_id": video_id})
    if resp.get("ok"):
        click.echo(f"Added {video_id} to queue.")
    else:
        _error(resp.get("error", "unknown error"))


@queue.command("clear")
def queue_clear() -> None:
    """Clear the play queue."""
    _require_tui()
    resp = _ipc("queue_clear")
    if resp.get("ok"):
        click.echo("Queue cleared.")
    else:
        _error(resp.get("error", "unknown error"))


# ---------------------------------------------------------------------------
# History group (standalone)
# ---------------------------------------------------------------------------


@main.group(invoke_without_command=True)
@click.option(
    "--limit", "-l", type=int, default=50, show_default=True, help="Number of history entries."
)
@click.option("--json", "compact_json", is_flag=True, help="Compact JSON output.")
@click.pass_context
def history(ctx: click.Context, limit: int, compact_json: bool) -> None:
    """Show recent play history (JSON)."""
    if ctx.invoked_subcommand is not None:
        return

    if not HISTORY_DB.exists():
        _json_output([], compact=compact_json)
        return

    data: list[dict] = []
    try:
        with sqlite3.connect(str(HISTORY_DB)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM play_history ORDER BY played_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            data = [dict(row) for row in rows]
    except sqlite3.Error as exc:
        _error(f"Failed to read history database: {exc}")

    _json_output(data, compact=compact_json)


@history.command("search")
@click.option(
    "--limit",
    "-l",
    type=int,
    default=50,
    show_default=True,
    help="Number of search history entries.",
)
@click.option("--json", "compact_json", is_flag=True, help="Compact JSON output.")
def history_search(limit: int, compact_json: bool) -> None:
    """Show recent search history (JSON)."""
    if not HISTORY_DB.exists():
        _json_output([], compact=compact_json)
        return

    data: list[dict] = []
    try:
        with sqlite3.connect(str(HISTORY_DB)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM search_history ORDER BY last_searched DESC LIMIT ?",
                (limit,),
            ).fetchall()
            data = [dict(row) for row in rows]
    except sqlite3.Error as exc:
        _error(f"Failed to read search history database: {exc}")

    _json_output(data, compact=compact_json)


# ---------------------------------------------------------------------------
# Stats (standalone)
# ---------------------------------------------------------------------------


@main.command()
@click.option("--json", "compact_json", is_flag=True, help="Compact JSON output.")
def stats(compact_json: bool) -> None:
    """Show listening statistics (JSON)."""
    if not HISTORY_DB.exists():
        _json_output(
            {"total_plays": 0, "total_seconds": 0, "unique_tracks": 0}, compact=compact_json
        )
        return

    data: dict[str, Any] = {
        "total_plays": 0,
        "total_seconds": 0,
        "unique_tracks": 0,
        "top_tracks": [],
    }
    try:
        with sqlite3.connect(str(HISTORY_DB)) as conn:
            total_plays = conn.execute("SELECT COUNT(*) FROM play_history").fetchone()[0]
            total_seconds = conn.execute(
                "SELECT COALESCE(SUM(listened_seconds), 0) FROM play_history"
            ).fetchone()[0]
            unique_tracks = conn.execute(
                "SELECT COUNT(DISTINCT video_id) FROM play_history"
            ).fetchone()[0]
            top_tracks = conn.execute(
                "SELECT video_id, title, artist, COUNT(*) as play_count "
                "FROM play_history GROUP BY video_id ORDER BY play_count DESC LIMIT 10"
            ).fetchall()

            data = {
                "total_plays": total_plays,
                "total_seconds": total_seconds,
                "unique_tracks": unique_tracks,
                "top_tracks": [
                    {"video_id": r[0], "title": r[1], "artist": r[2], "play_count": r[3]}
                    for r in top_tracks
                ],
            }
    except sqlite3.Error as exc:
        _error(f"Failed to read history database: {exc}")

    _json_output(data, compact=compact_json)


# ---------------------------------------------------------------------------
# Cache group (standalone)
# ---------------------------------------------------------------------------


@main.group(invoke_without_command=True)
@click.pass_context
def cache(ctx: click.Context) -> None:
    """Manage the audio cache."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(cache_status)


@cache.command("status")
@click.option("--json", "compact_json", is_flag=True, help="Compact JSON output.")
def cache_status(compact_json: bool) -> None:
    """Show cache size and statistics."""
    from ytm_player.utils.formatting import format_size

    total_bytes = 0
    file_count = 0

    if CACHE_DIR.exists():
        for f in CACHE_DIR.iterdir():
            if f.is_file():
                total_bytes += f.stat().st_size
                file_count += 1

    db_size = 0
    if CACHE_DB.exists():
        db_size = CACHE_DB.stat().st_size

    data: dict[str, Any] = {
        "cache_dir": str(CACHE_DIR),
        "file_count": file_count,
        "total_bytes": total_bytes,
        "total_human": format_size(total_bytes),
        "db_size_bytes": db_size,
        "db_size_human": format_size(db_size),
    }

    if compact_json:
        _json_output(data, compact=True)
    else:
        click.echo(f"Cache directory: {CACHE_DIR}")
        click.echo(f"Cached files:    {file_count}")
        click.echo(f"Total size:      {format_size(total_bytes)}")
        click.echo(f"Database size:   {format_size(db_size)}")


@cache.command("clear")
@click.confirmation_option(prompt="Are you sure you want to clear the audio cache?")
def cache_clear() -> None:
    """Clear the audio cache."""
    removed = 0

    if CACHE_DIR.exists():
        for f in CACHE_DIR.iterdir():
            if f.is_file():
                f.unlink()
                removed += 1

    if CACHE_DB.exists():
        CACHE_DB.unlink()

    click.echo(f"Cleared {removed} cached file(s).")


# ---------------------------------------------------------------------------
# Config (standalone)
# ---------------------------------------------------------------------------


@main.command("import")
@click.argument("spotify_url")
def import_playlist(spotify_url: str) -> None:
    """Import a public Spotify playlist into YouTube Music.

    SPOTIFY_URL is the full URL of a public Spotify playlist
    (e.g. https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M).
    """
    auth_path = _require_auth()

    from ytm_player.services.spotify_import import run_import

    run_import(spotify_url, auth_path)


@main.command()
def config() -> None:
    """Open the config directory in your editor.

    Uses $EDITOR if set, otherwise falls back to xdg-open.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Ensure a default config exists.
    if not CONFIG_FILE.exists():
        from ytm_player.config.settings import Settings

        Settings().save(CONFIG_FILE)

    editor = os.environ.get("EDITOR")
    if editor:
        target = str(CONFIG_FILE)
    else:
        editor = shutil.which("xdg-open")
        target = str(CONFIG_DIR)
        if editor is None:
            _error(f"No $EDITOR set and xdg-open not found. Open manually: {CONFIG_DIR}")

    try:
        subprocess.run([editor, target], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        _error(f"Failed to open editor: {exc}")
