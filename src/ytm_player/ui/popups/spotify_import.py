"""Spotify playlist import popup — URL input, progress tracking, disambiguation, creation."""

from __future__ import annotations

import asyncio
import logging
import re

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListView, ListItem, ProgressBar, Static

# NOTE: ytm_player.services.spotify_import is imported lazily inside worker
# methods to avoid pulling in heavy optional deps (thefuzz, spotify_scraper)
# at module load time.

logger = logging.getLogger(__name__)

_URL_PATTERN = re.compile(r"https?://open\.spotify\.com/(playlist|album)/")

# Max tracks per add_playlist_items call.
_ADD_BATCH_SIZE = 100


class _ResultItem(ListItem):
    """A single track match result shown during progress."""

    def __init__(self, symbol: str, style: str, text: str) -> None:
        super().__init__()
        self._symbol = symbol
        self._style = style
        self._text = text

    def compose(self) -> ComposeResult:
        yield Label(f"[{self._style}]{self._symbol}[/{self._style}] {self._text}")


class _CandidateItem(ListItem):
    """A selectable candidate shown during disambiguation."""

    def __init__(self, index: int, candidate: dict) -> None:
        super().__init__()
        self.candidate = candidate
        self._index = index

    def compose(self) -> ComposeResult:
        title = self.candidate.get("title", "?")
        artists = self.candidate.get("artists", [])
        if isinstance(artists, list):
            artist_str = ", ".join(
                a.get("name", "") if isinstance(a, dict) else str(a) for a in artists
            )
        else:
            artist_str = str(artists)
        dur = self.candidate.get("duration", "")
        if not dur:
            dur_sec = self.candidate.get("duration_seconds", 0)
            dur = f"{dur_sec // 60}:{dur_sec % 60:02d}" if dur_sec else ""
        dur_part = f" ({dur})" if dur else ""
        yield Label(f"  {self._index}. {title} — {artist_str}{dur_part}")


class _SkipItem(ListItem):
    """The 'Skip' option at the end of the candidate list."""

    def __init__(self) -> None:
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Label("  [dim]Skip this track[/dim]")


class SpotifyImportPopup(ModalScreen[str | None]):
    """Multi-phase Spotify import: URL → creds? → progress → disambiguate → create."""

    BINDINGS = [
        Binding("escape", "cancel", "Close", show=False),
    ]

    DEFAULT_CSS = """
    SpotifyImportPopup {
        align: center middle;
    }

    SpotifyImportPopup > Vertical {
        width: 70;
        max-height: 85%;
        background: $surface;
        border: round $primary;
        padding: 1 2;
    }

    SpotifyImportPopup #si-title {
        text-align: center;
        text-style: bold;
        width: 100%;
        color: $text;
        margin-bottom: 1;
    }

    SpotifyImportPopup #si-status {
        text-align: center;
        width: 100%;
        color: $text-muted;
        margin-bottom: 1;
    }

    SpotifyImportPopup #si-url-input {
        margin-bottom: 1;
    }

    SpotifyImportPopup #si-cred-id-input {
        display: none;
        margin-bottom: 1;
    }

    SpotifyImportPopup #si-cred-id-input.visible {
        display: block;
    }

    SpotifyImportPopup #si-cred-secret-input {
        display: none;
        margin-bottom: 1;
    }

    SpotifyImportPopup #si-cred-secret-input.visible {
        display: block;
    }

    SpotifyImportPopup #si-progress {
        margin: 0 1 1 1;
    }

    SpotifyImportPopup #si-results {
        height: auto;
        max-height: 20;
        background: $surface;
    }

    SpotifyImportPopup ListItem {
        padding: 0 1;
        height: 1;
    }

    SpotifyImportPopup #si-name-input {
        display: none;
    }

    SpotifyImportPopup #si-name-input.visible {
        display: block;
        margin-top: 1;
    }

    SpotifyImportPopup #si-summary {
        display: none;
    }

    SpotifyImportPopup #si-summary.visible {
        display: block;
        text-align: center;
        width: 100%;
        color: $text;
        margin-top: 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        # Phases: url | creds | progress | disambiguate | summary | creating
        self._phase: str = "url"
        self._playlist_name: str = ""
        self._results: list = []
        self._video_ids: list[str] = []
        self._pending_url: str = ""
        # Disambiguation state.
        self._disambig_queue: list = []  # MatchResults needing user input
        self._disambig_index: int = 0
        # Track whether extraction was truncated.
        self._was_truncated: bool = False

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Import Spotify Playlist", id="si-title")
            yield Static("Paste a Spotify playlist URL and press Enter", id="si-status")
            yield Input(
                placeholder="https://open.spotify.com/playlist/...",
                id="si-url-input",
            )
            yield Input(
                placeholder="Spotify Client ID",
                id="si-cred-id-input",
            )
            yield Input(
                placeholder="Spotify Client Secret",
                id="si-cred-secret-input",
                password=True,
            )
            yield ProgressBar(total=100, show_eta=False, id="si-progress")
            yield ListView(id="si-results")
            yield Static("", id="si-summary")
            yield Input(placeholder="Playlist name...", id="si-name-input")

    def on_mount(self) -> None:
        self.query_one("#si-progress", ProgressBar).display = False
        self.query_one("#si-results", ListView).display = False
        self.query_one("#si-url-input", Input).focus()

    # ── Phase 1: URL submission ──────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "si-url-input" and self._phase == "url":
            url = event.value.strip()
            if not _URL_PATTERN.match(url):
                self.notify("Invalid Spotify URL", severity="warning")
                return
            self._pending_url = url
            self._start_import(url)

        elif event.input.id == "si-cred-secret-input" and self._phase == "creds":
            self._save_creds_and_retry()

        elif event.input.id == "si-cred-id-input" and self._phase == "creds":
            # Tab to secret field.
            self.query_one("#si-cred-secret-input", Input).focus()

        elif event.input.id == "si-name-input" and self._phase == "summary":
            name = event.value.strip()
            if not name:
                self.notify("Name cannot be empty", severity="warning")
                return
            self._create_playlist(name)

    # ── Phase 1b: Credential setup (if needed) ───────────────────────

    def _show_creds_prompt(self) -> None:
        """Ask the user for Spotify API credentials."""
        self._phase = "creds"
        status = self.query_one("#si-status", Static)
        status.update(
            "Playlist has >100 tracks. Spotify API credentials needed.\n"
            "Get free credentials at [bold]developer.spotify.com/dashboard[/bold]\n"
            "Enter Client ID and Client Secret below:"
        )
        self.query_one("#si-url-input", Input).display = False
        self.query_one("#si-progress", ProgressBar).display = False

        cred_id = self.query_one("#si-cred-id-input", Input)
        cred_secret = self.query_one("#si-cred-secret-input", Input)
        cred_id.add_class("visible")
        cred_secret.add_class("visible")
        cred_id.focus()

    def _save_creds_and_retry(self) -> None:
        """Save credentials and restart the import."""
        from ytm_player.services.spotify_import import save_spotify_creds

        client_id = self.query_one("#si-cred-id-input", Input).value.strip()
        client_secret = self.query_one("#si-cred-secret-input", Input).value.strip()
        if not client_id or not client_secret:
            self.notify("Both Client ID and Secret are required", severity="warning")
            return

        save_spotify_creds(client_id, client_secret)

        # Hide cred inputs.
        self.query_one("#si-cred-id-input", Input).remove_class("visible")
        self.query_one("#si-cred-secret-input", Input).remove_class("visible")

        self.notify("Credentials saved", severity="information")
        self._start_import(self._pending_url)

    # ── Phase 2: Progress ────────────────────────────────────────────

    def _start_import(self, url: str) -> None:
        """Kick off the import worker."""
        self._phase = "progress"
        self.query_one("#si-url-input", Input).display = False
        self.query_one("#si-progress", ProgressBar).display = True
        self.query_one("#si-results", ListView).display = True
        self.query_one("#si-status", Static).update("Fetching Spotify playlist...")
        self.run_worker(self._do_import(url), name="spotify_import", exclusive=True)

    async def _do_import(self, url: str) -> None:
        """Fetch Spotify tracks, then match each against YouTube Music."""
        from ytm_player.services.spotify_import import (
            MatchType,
            MatchResult,
            extract_spotify_tracks,
            has_spotify_creds,
            _fuzzy_score,
            _get_video_id,
        )

        status = self.query_one("#si-status", Static)
        progress_bar = self.query_one("#si-progress", ProgressBar)
        results_list = self.query_one("#si-results", ListView)

        # Step 1: Extract tracks from Spotify.
        try:
            playlist_name, spotify_tracks = await asyncio.to_thread(
                extract_spotify_tracks, url
            )
        except Exception as exc:
            status.update(f"[red]Failed to fetch playlist:[/red] {exc}")
            return

        if not spotify_tracks:
            status.update("[yellow]No tracks found in the playlist.[/yellow]")
            return

        self._playlist_name = playlist_name

        # Detect truncation: if we got exactly 100 tracks but the playlist
        # likely has more (spotify_scraper fallback), offer creds setup.
        if len(spotify_tracks) == 100 and not has_spotify_creds():
            # Might be truncated — prompt for creds.
            self._was_truncated = True
            self.call_later(self._show_creds_prompt)
            return

        total = len(spotify_tracks)
        progress_bar.update(total=total, progress=0)
        status.update(f"Matching on YouTube Music... (0/{total})")

        # Step 2: Match each track.
        results: list[MatchResult] = []
        ytmusic_svc = self.app.ytmusic  # type: ignore[attr-defined]

        for i, sp_track in enumerate(spotify_tracks, 1):
            query = f"{sp_track['name']} {sp_track['artist']}"
            try:
                search_results = await ytmusic_svc.search(query, filter="songs", limit=5)
            except Exception:
                search_results = []

            result = self._classify_match(
                sp_track, search_results, MatchType, MatchResult, _fuzzy_score
            )
            results.append(result)

            # Stream result into the list.
            display_text = f"{sp_track['name']} — {sp_track['artist']}"
            if result.match_type == MatchType.EXACT:
                results_list.append(_ResultItem("✓", "green", display_text))
            elif result.match_type == MatchType.MULTIPLE:
                results_list.append(_ResultItem("?", "yellow", display_text))
            else:
                results_list.append(_ResultItem("✗", "red", display_text))

            progress_bar.update(progress=i)
            status.update(f"Matching on YouTube Music... ({i}/{total})")
            results_list.scroll_end(animate=False)

        self._results = results

        # Check if there are uncertain matches that need user input.
        uncertain = [r for r in results if r.match_type == MatchType.MULTIPLE]
        if uncertain:
            self._disambig_queue = uncertain
            self._disambig_index = 0
            self._start_disambiguation()
        else:
            self._show_summary(MatchType, _get_video_id)

    @staticmethod
    def _classify_match(
        sp_track: dict, search_results: list[dict],
        MatchType, MatchResult, _fuzzy_score,
    ):
        """Score candidates and classify the match."""
        if not search_results:
            return MatchResult(spotify_track=sp_track, match_type=MatchType.NONE)

        scored = [(_fuzzy_score(sp_track, c), c) for c in search_results]
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_candidate = scored[0]

        if best_score >= 85:
            return MatchResult(
                spotify_track=sp_track,
                match_type=MatchType.EXACT,
                candidates=[c for _, c in scored],
                selected=best_candidate,
            )
        elif scored:
            # Don't auto-pick — let the user decide.
            return MatchResult(
                spotify_track=sp_track,
                match_type=MatchType.MULTIPLE,
                candidates=[c for _, c in scored],
                selected=None,
            )
        return MatchResult(spotify_track=sp_track, match_type=MatchType.NONE)

    # ── Phase 3: Disambiguation ──────────────────────────────────────

    def _start_disambiguation(self) -> None:
        """Enter the disambiguation phase — show first uncertain match."""
        self._phase = "disambiguate"
        self.query_one("#si-progress", ProgressBar).display = False
        self._show_current_disambig()

    def _show_current_disambig(self) -> None:
        """Display candidates for the current uncertain match."""
        if self._disambig_index >= len(self._disambig_queue):
            # All resolved — move to summary.
            from ytm_player.services.spotify_import import MatchType, _get_video_id
            self._show_summary(MatchType, _get_video_id)
            return

        result = self._disambig_queue[self._disambig_index]
        sp = result.spotify_track
        total_uncertain = len(self._disambig_queue)
        current = self._disambig_index + 1

        status = self.query_one("#si-status", Static)
        status.update(
            f"[bold]Uncertain match ({current}/{total_uncertain}):[/bold] "
            f'"{sp["name"]}" by {sp["artist"]}\n'
            f"Select the correct match or skip:"
        )

        results_list = self.query_one("#si-results", ListView)
        results_list.clear()
        results_list.display = True

        for i, candidate in enumerate(result.candidates[:5], 1):
            results_list.append(_CandidateItem(i, candidate))
        results_list.append(_SkipItem())

        results_list.index = 0
        results_list.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle candidate selection during disambiguation."""
        if self._phase != "disambiguate":
            return

        event.stop()
        result = self._disambig_queue[self._disambig_index]

        if isinstance(event.item, _CandidateItem):
            # User picked a candidate.
            from ytm_player.services.spotify_import import MatchType
            result.selected = event.item.candidate
            result.match_type = MatchType.EXACT
        elif isinstance(event.item, _SkipItem):
            # User skipped — mark as NONE.
            from ytm_player.services.spotify_import import MatchType
            result.selected = None
            result.match_type = MatchType.NONE

        self._disambig_index += 1
        self._show_current_disambig()

    # ── Phase 4: Summary + create ────────────────────────────────────

    def _show_summary(self, MatchType, _get_video_id) -> None:
        """Display summary stats and playlist name input."""
        self._phase = "summary"

        exact = sum(1 for r in self._results if r.match_type == MatchType.EXACT)
        fuzzy = sum(1 for r in self._results if r.match_type == MatchType.MULTIPLE)
        not_found = sum(1 for r in self._results if r.match_type == MatchType.NONE)
        total = len(self._results)

        # Collect video IDs from all matched tracks.
        self._video_ids = [
            _get_video_id(r.selected)
            for r in self._results
            if r.selected is not None
        ]
        self._video_ids = [vid for vid in self._video_ids if vid]

        results_list = self.query_one("#si-results", ListView)
        results_list.clear()
        results_list.display = False

        self.query_one("#si-progress", ProgressBar).display = False

        status = self.query_one("#si-status", Static)
        matched = exact + fuzzy
        status.update(
            f"{matched} matched, {not_found} skipped/not found ({total} total)"
        )

        summary = self.query_one("#si-summary", Static)
        summary.update("Enter a playlist name and press Enter to create")
        summary.add_class("visible")

        name_input = self.query_one("#si-name-input", Input)
        name_input.value = self._playlist_name
        name_input.add_class("visible")
        name_input.focus()

    def _create_playlist(self, name: str) -> None:
        """Create the playlist on YouTube Music."""
        self._phase = "creating"
        self.run_worker(self._do_create(name), name="create_playlist", exclusive=True)

    async def _do_create(self, name: str) -> None:
        """Create playlist and add tracks in batches of 100."""
        status = self.query_one("#si-status", Static)
        progress_bar = self.query_one("#si-progress", ProgressBar)

        ytmusic_svc = self.app.ytmusic  # type: ignore[attr-defined]

        try:
            status.update(f"Creating '{name}' on YouTube Music...")
            playlist_id = await ytmusic_svc.create_playlist(
                name,
                f"Imported from Spotify: {self._playlist_name}",
            )
            if not playlist_id:
                status.update("[red]Failed to create playlist.[/red]")
                return

            total_ids = len(self._video_ids)
            if total_ids > 0:
                # Batch add in groups of _ADD_BATCH_SIZE.
                batches = [
                    self._video_ids[i:i + _ADD_BATCH_SIZE]
                    for i in range(0, total_ids, _ADD_BATCH_SIZE)
                ]
                if len(batches) > 1:
                    progress_bar.display = True
                    progress_bar.update(total=len(batches), progress=0)

                for batch_idx, batch in enumerate(batches, 1):
                    status.update(
                        f"Adding tracks... ({min(batch_idx * _ADD_BATCH_SIZE, total_ids)}/{total_ids})"
                    )
                    await ytmusic_svc.add_playlist_items(playlist_id, batch)
                    if len(batches) > 1:
                        progress_bar.update(progress=batch_idx)

            self.notify(
                f"Created '{name}' with {total_ids} tracks",
                severity="information",
            )
            self.dismiss(playlist_id)

        except Exception as exc:
            logger.exception("Failed to create playlist")
            status.update(f"[red]Error:[/red] {exc}")

    # ── Cancel ───────────────────────────────────────────────────────

    def action_cancel(self) -> None:
        """Escape dismisses the popup."""
        self.dismiss(None)
