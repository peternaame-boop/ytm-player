"""Spotify playlist import popup — Single (≤100) and Multi (100+) modes.

Single mode: paste one Spotify URL → match → create YTM playlist.
Multi mode: enter combined name + number of parts → paste N URLs →
            match all sequentially → create one combined YTM playlist.
"""

from __future__ import annotations

import asyncio
import logging
import re

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Click
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Input,
    Label,
    ListView,
    ListItem,
    ProgressBar,
    Static,
)

# NOTE: ytm_player.services.spotify_import is imported lazily inside worker
# methods to avoid pulling in heavy optional deps (thefuzz, spotify_scraper)
# at module load time.

from ytm_player.utils.formatting import extract_artist

logger = logging.getLogger(__name__)

_URL_PATTERN = re.compile(r"https?://open\.spotify\.com/(playlist|album)/")

# Max tracks per add_playlist_items call.
_ADD_BATCH_SIZE = 100


# ── Shared list items for results ────────────────────────────────────


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
        artist_str = extract_artist(self.candidate)
        dur = self.candidate.get("duration", "")
        if not dur:
            dur_sec = self.candidate.get("duration_seconds", 0)
            dur = f"{dur_sec // 60}:{dur_sec % 60:02d}" if dur_sec else ""
        dur_part = f" ({dur})" if dur else ""
        yield Label(f"  {self._index}. {title} — {artist_str}{dur_part}")


class _SkipItem(ListItem):
    """The 'Skip' option at the end of the candidate list."""

    def compose(self) -> ComposeResult:
        yield Label("  [dim]Skip this track[/dim]")


# ── Tab button ───────────────────────────────────────────────────────


class _TabClicked(Message):
    """Posted by _TabLabel when clicked."""

    def __init__(self, tab_id: str) -> None:
        super().__init__()
        self.tab_id = tab_id


class _TabLabel(Static):
    """Clickable tab label that toggles active state."""

    DEFAULT_CSS = """
    _TabLabel {
        width: auto;
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }
    _TabLabel.active {
        color: $text;
        text-style: bold underline;
    }
    """

    def __init__(self, label: str, tab_id: str) -> None:
        super().__init__(label)
        self.tab_id = tab_id

    def on_click(self, event: Click) -> None:
        if self.tab_id in ("single", "multi"):
            event.stop()
            self.post_message(_TabClicked(self.tab_id))


# ── Main import popup ────────────────────────────────────────────────


class SpotifyImportPopup(ModalScreen[str | None]):
    """Two-tab Spotify import: Single (≤100 tracks) and Multi (100+ tracks)."""

    BINDINGS = [
        Binding("escape", "cancel", "Close", show=False),
    ]

    DEFAULT_CSS = """
    SpotifyImportPopup {
        align: center middle;
    }

    SpotifyImportPopup > Vertical {
        width: 75;
        max-height: 85%;
        background: $surface;
        border: round $primary;
        padding: 1 2;
    }

    /* Title */
    SpotifyImportPopup #si-title {
        text-align: center;
        text-style: bold;
        width: 100%;
        color: $text;
        margin-bottom: 1;
    }

    /* Tab bar */
    SpotifyImportPopup #si-tab-bar {
        height: 1;
        width: 100%;
        margin-bottom: 1;
        align-horizontal: center;
    }

    /* Status */
    SpotifyImportPopup #si-status {
        text-align: center;
        width: 100%;
        color: $text-muted;
        margin-bottom: 1;
    }

    /* Single mode */
    SpotifyImportPopup #si-single-content { }

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

    /* Multi mode */
    SpotifyImportPopup #si-multi-content {
        display: none;
    }

    SpotifyImportPopup #si-multi-name-input {
        margin-bottom: 1;
    }

    SpotifyImportPopup #si-multi-count-input {
        margin-bottom: 1;
    }

    SpotifyImportPopup #si-multi-urls {
        max-height: 15;
        margin-bottom: 1;
    }

    SpotifyImportPopup #si-multi-urls Input {
        margin-bottom: 0;
    }

    SpotifyImportPopup #si-multi-start-btn {
        display: none;
        margin-top: 1;
        width: 100%;
    }

    SpotifyImportPopup #si-multi-start-btn.visible {
        display: block;
    }

    /* Shared */
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
        self._mode: str = "single"  # "single" or "multi"
        # Single-mode phases: url | creds | progress | disambiguate | summary | creating
        # Multi-mode phases: multi_setup | multi_urls | multi_progress | disambiguate | summary | creating
        self._phase: str = "url"
        self._playlist_name: str = ""
        self._results: list = []
        self._video_ids: list[str] = []
        self._pending_url: str = ""
        # Disambiguation state.
        self._disambig_queue: list = []
        self._disambig_index: int = 0
        # Multi-mode state.
        self._multi_name: str = ""
        self._multi_url_count: int = 0
        self._multi_urls: list[str] = []
        self._all_results: list = []

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Import Spotify Playlist", id="si-title")
            with Horizontal(id="si-tab-bar"):
                yield _TabLabel("Single (≤100)", "single")
                yield _TabLabel("  |  ", "separator")
                yield _TabLabel("Multi (100+)", "multi")
            yield Static("Paste a Spotify playlist URL and press Enter", id="si-status")

            # ── Single mode content ──
            with Vertical(id="si-single-content"):
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

            # ── Multi mode content ──
            with Vertical(id="si-multi-content"):
                yield Input(
                    placeholder="Combined playlist name (e.g. Pangaea)",
                    id="si-multi-name-input",
                )
                yield Input(
                    placeholder="Number of split playlists (2-20)",
                    id="si-multi-count-input",
                )
                yield VerticalScroll(id="si-multi-urls")
                yield Button("Start Import", id="si-multi-start-btn", variant="primary")

            # ── Shared widgets ──
            yield ProgressBar(total=100, show_eta=False, id="si-progress")
            yield ListView(id="si-results")
            yield Static("", id="si-summary")
            yield Input(placeholder="Playlist name...", id="si-name-input")

    def on_mount(self) -> None:
        self.query_one("#si-progress", ProgressBar).display = False
        self.query_one("#si-results", ListView).display = False
        self._activate_tab("single")
        self.query_one("#si-url-input", Input).focus()

    # ── Tab switching ────────────────────────────────────────────────

    def on__tab_clicked(self, event: _TabClicked) -> None:
        """Handle tab clicks — only before processing starts."""
        if self._phase in ("url", "multi_setup"):
            self._activate_tab(event.tab_id)

    def _activate_tab(self, tab_id: str) -> None:
        """Switch between single and multi mode."""
        self._mode = tab_id

        # Update tab labels.
        for tab in self.query(_TabLabel):
            if tab.tab_id == tab_id:
                tab.add_class("active")
            elif tab.tab_id != "separator":
                tab.remove_class("active")

        # Toggle content visibility.
        single_content = self.query_one("#si-single-content")
        multi_content = self.query_one("#si-multi-content")
        status = self.query_one("#si-status", Static)

        if tab_id == "single":
            self._phase = "url"
            single_content.display = True
            multi_content.display = False
            status.update("Paste a Spotify playlist URL and press Enter")
            self.query_one("#si-url-input", Input).focus()
        else:
            self._phase = "multi_setup"
            single_content.display = False
            multi_content.display = True
            status.update(
                "Split your 100+ track playlist into chunks of ≤100 on Spotify,\n"
                "then enter the combined name and number of parts below"
            )
            # Reset multi URL container.
            self.query_one("#si-multi-urls", VerticalScroll).remove_children()
            self.query_one("#si-multi-start-btn").remove_class("visible")
            self.query_one("#si-multi-name-input", Input).value = ""
            self.query_one("#si-multi-count-input", Input).value = ""
            self.query_one("#si-multi-name-input", Input).focus()

    # ── Input handling ───────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        input_id = event.input.id

        # ── Single mode ──
        if input_id == "si-url-input" and self._phase == "url":
            url = event.value.strip()
            if not _URL_PATTERN.match(url):
                self.notify("Invalid Spotify URL", severity="warning")
                return
            self._pending_url = url
            self._start_single_import(url)

        elif input_id == "si-cred-secret-input" and self._phase == "creds":
            self._save_creds_and_retry()

        elif input_id == "si-cred-id-input" and self._phase == "creds":
            self.query_one("#si-cred-secret-input", Input).focus()

        elif input_id == "si-name-input" and self._phase == "summary":
            name = event.value.strip()
            if not name:
                self.notify("Name cannot be empty", severity="warning")
                return
            self._create_playlist(name)

        # ── Multi mode ──
        elif input_id == "si-multi-count-input" and self._phase == "multi_setup":
            self._generate_url_inputs()

        elif input_id == "si-multi-name-input" and self._phase == "multi_setup":
            # Tab to count field.
            self.query_one("#si-multi-count-input", Input).focus()

        elif input_id and input_id.startswith("si-multi-url-") and self._phase == "multi_urls":
            # User pressed Enter on a URL input — move to next or start.
            idx_str = input_id.replace("si-multi-url-", "")
            try:
                idx = int(idx_str)
                next_idx = idx + 1
                next_id = f"si-multi-url-{next_idx}"
                try:
                    self.query_one(f"#{next_id}", Input).focus()
                except Exception:
                    # Last input — focus the start button.
                    self.query_one("#si-multi-start-btn", Button).focus()
            except ValueError:
                pass

    # ── Single mode: credential setup ────────────────────────────────

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
        self.query_one("#si-cred-id-input", Input).remove_class("visible")
        self.query_one("#si-cred-secret-input", Input).remove_class("visible")
        self.notify("Credentials saved", severity="information")
        self._start_single_import(self._pending_url)

    # ── Single mode: import flow ─────────────────────────────────────

    def _start_single_import(self, url: str) -> None:
        """Kick off the single-playlist import worker."""
        self._phase = "progress"
        self.query_one("#si-single-content").display = False
        self.query_one("#si-tab-bar").display = False
        self.query_one("#si-progress", ProgressBar).display = True
        self.query_one("#si-results", ListView).display = True
        self.query_one("#si-status", Static).update("Fetching Spotify playlist...")
        self.run_worker(self._do_single_import(url), name="spotify_import", exclusive=True)

    async def _do_single_import(self, url: str) -> None:
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

        # Detect truncation.
        if len(spotify_tracks) == 100 and not has_spotify_creds():
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

        uncertain = [r for r in results if r.match_type == MatchType.MULTIPLE]
        if uncertain:
            self._disambig_queue = uncertain
            self._disambig_index = 0
            self._start_disambiguation()
        else:
            self._show_summary(MatchType, _get_video_id)

    # ── Multi mode: setup ────────────────────────────────────────────

    def _generate_url_inputs(self) -> None:
        """Validate count and generate URL input fields."""
        name = self.query_one("#si-multi-name-input", Input).value.strip()
        count_str = self.query_one("#si-multi-count-input", Input).value.strip()

        if not name:
            self.notify("Enter a combined playlist name", severity="warning")
            self.query_one("#si-multi-name-input", Input).focus()
            return

        try:
            count = int(count_str)
        except ValueError:
            self.notify("Enter a valid number", severity="warning")
            return

        if count < 2 or count > 20:
            self.notify("Number of parts must be between 2 and 20", severity="warning")
            return

        self._multi_name = name
        self._multi_url_count = count
        self._phase = "multi_urls"

        # Disable setup inputs.
        self.query_one("#si-multi-name-input", Input).disabled = True
        self.query_one("#si-multi-count-input", Input).disabled = True

        # Generate URL inputs.
        url_container = self.query_one("#si-multi-urls", VerticalScroll)
        for i in range(count):
            url_container.mount(
                Input(
                    placeholder=f"Spotify URL for {name}{i + 1}",
                    id=f"si-multi-url-{i}",
                )
            )

        # Show the start button.
        self.query_one("#si-multi-start-btn").add_class("visible")

        status = self.query_one("#si-status", Static)
        status.update(
            f"Paste {count} Spotify playlist URLs below, then click Start Import"
        )

        # Focus the first URL input.
        self.call_later(lambda: self.query_one("#si-multi-url-0", Input).focus())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle the Start Import button for multi mode."""
        if event.button.id == "si-multi-start-btn" and self._phase == "multi_urls":
            self._start_multi_import()

    def _start_multi_import(self) -> None:
        """Validate all URLs and begin multi-playlist import."""
        urls: list[str] = []
        for i in range(self._multi_url_count):
            try:
                url_input = self.query_one(f"#si-multi-url-{i}", Input)
                url = url_input.value.strip()
            except Exception:
                self.notify(f"Missing URL input #{i + 1}", severity="error")
                return

            if not _URL_PATTERN.match(url):
                self.notify(
                    f"Invalid Spotify URL in slot {i + 1}", severity="warning"
                )
                url_input.focus()
                return
            urls.append(url)

        self._multi_urls = urls
        self._all_results = []

        # Hide multi inputs, show progress.
        self._phase = "multi_progress"
        self._playlist_name = self._multi_name
        self.query_one("#si-multi-content").display = False
        self.query_one("#si-tab-bar").display = False
        self.query_one("#si-progress", ProgressBar).display = True
        self.query_one("#si-results", ListView).display = True

        self.run_worker(
            self._do_multi_import(), name="spotify_multi_import", exclusive=True
        )

    async def _do_multi_import(self) -> None:
        """Process all split playlists sequentially."""
        from ytm_player.services.spotify_import import (
            MatchType,
            MatchResult,
            extract_spotify_tracks,
            _fuzzy_score,
            _get_video_id,
        )

        status = self.query_one("#si-status", Static)
        progress_bar = self.query_one("#si-progress", ProgressBar)
        results_list = self.query_one("#si-results", ListView)
        ytmusic_svc = self.app.ytmusic  # type: ignore[attr-defined]

        total_urls = len(self._multi_urls)

        for url_idx, url in enumerate(self._multi_urls):
            part_num = url_idx + 1
            status.update(
                f"[bold]Part {part_num}/{total_urls}:[/bold] Fetching from Spotify..."
            )

            # Extract tracks from this part.
            try:
                part_name, spotify_tracks = await asyncio.to_thread(
                    extract_spotify_tracks, url
                )
            except Exception as exc:
                status.update(
                    f"[red]Failed to fetch part {part_num}:[/red] {exc}"
                )
                return

            if not spotify_tracks:
                results_list.append(
                    _ResultItem("⚠", "yellow", f"Part {part_num}: no tracks found, skipping")
                )
                continue

            results_list.append(
                _ResultItem(
                    "▸", "bold",
                    f"Part {part_num}: {part_name} ({len(spotify_tracks)} tracks)",
                )
            )
            results_list.scroll_end(animate=False)

            total_tracks = len(spotify_tracks)
            progress_bar.update(total=total_tracks, progress=0)

            # Match each track.
            for i, sp_track in enumerate(spotify_tracks, 1):
                query = f"{sp_track['name']} {sp_track['artist']}"
                try:
                    search_results = await ytmusic_svc.search(
                        query, filter="songs", limit=5
                    )
                except Exception:
                    search_results = []

                result = self._classify_match(
                    sp_track, search_results, MatchType, MatchResult, _fuzzy_score
                )
                self._all_results.append(result)

                display_text = f"{sp_track['name']} — {sp_track['artist']}"
                if result.match_type == MatchType.EXACT:
                    results_list.append(_ResultItem("✓", "green", display_text))
                elif result.match_type == MatchType.MULTIPLE:
                    results_list.append(_ResultItem("?", "yellow", display_text))
                else:
                    results_list.append(_ResultItem("✗", "red", display_text))

                progress_bar.update(progress=i)
                status.update(
                    f"[bold]Part {part_num}/{total_urls}:[/bold] "
                    f"Matching... ({i}/{total_tracks})"
                )
                results_list.scroll_end(animate=False)

        # All parts processed. Store combined results.
        self._results = self._all_results

        # Check for uncertain matches.
        uncertain = [r for r in self._results if r.match_type == MatchType.MULTIPLE]
        if uncertain:
            self._disambig_queue = uncertain
            self._disambig_index = 0
            self._start_disambiguation()
        else:
            self._show_summary(MatchType, _get_video_id)

    # ── Shared: match classification ─────────────────────────────────

    @staticmethod
    def _classify_match(
        sp_track: dict, search_results: list[dict],
        MatchType, MatchResult, _fuzzy_score,
    ):
        """Score candidates and classify the match."""
        from ytm_player.services.spotify_import import AUTO_MATCH_THRESHOLD

        if not search_results:
            return MatchResult(spotify_track=sp_track, match_type=MatchType.NONE)

        scored = [(_fuzzy_score(sp_track, c), c) for c in search_results]
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_candidate = scored[0]

        if best_score >= AUTO_MATCH_THRESHOLD:
            return MatchResult(
                spotify_track=sp_track,
                match_type=MatchType.EXACT,
                candidates=[c for _, c in scored],
                selected=best_candidate,
            )
        return MatchResult(
            spotify_track=sp_track,
            match_type=MatchType.MULTIPLE,
            candidates=[c for _, c in scored],
            selected=None,
        )

    # ── Shared: disambiguation ───────────────────────────────────────

    def _start_disambiguation(self) -> None:
        """Enter the disambiguation phase."""
        self._phase = "disambiguate"
        self.query_one("#si-progress", ProgressBar).display = False
        self._show_current_disambig()

    def _show_current_disambig(self) -> None:
        """Display candidates for the current uncertain match."""
        if self._disambig_index >= len(self._disambig_queue):
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
            from ytm_player.services.spotify_import import MatchType
            result.selected = event.item.candidate
            result.match_type = MatchType.EXACT
        elif isinstance(event.item, _SkipItem):
            from ytm_player.services.spotify_import import MatchType
            result.selected = None
            result.match_type = MatchType.NONE

        self._disambig_index += 1
        self._show_current_disambig()

    # ── Shared: summary + create ─────────────────────────────────────

    def _show_summary(self, MatchType, _get_video_id) -> None:
        """Display summary stats and playlist name input."""
        self._phase = "summary"

        exact = sum(1 for r in self._results if r.match_type == MatchType.EXACT)
        fuzzy = sum(1 for r in self._results if r.match_type == MatchType.MULTIPLE)
        not_found = sum(1 for r in self._results if r.match_type == MatchType.NONE)
        total = len(self._results)

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

        if not self._video_ids:
            summary = self.query_one("#si-summary", Static)
            summary.update("No tracks matched — nothing to create. Press Escape to close.")
            summary.add_class("visible")
            return

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
            desc = f"Imported from Spotify: {self._playlist_name}"
            status.update(f"Creating '{name}' on YouTube Music...")
            playlist_id = await ytmusic_svc.create_playlist(name, desc)
            if not playlist_id:
                status.update("[red]Failed to create playlist.[/red]")
                return

            total_ids = len(self._video_ids)
            if total_ids > 0:
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
        """Cancel active workers and dismiss the popup."""
        for worker in self.workers:
            worker.cancel()
        self.dismiss(None)
