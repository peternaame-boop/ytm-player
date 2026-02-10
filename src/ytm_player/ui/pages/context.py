"""Dynamic context page for album, artist, and playlist detail views."""

from __future__ import annotations

import logging
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import DataTable, Label, Static
from textual.worker import Worker, WorkerState

from ytm_player.config.keymap import Action
from ytm_player.ui.widgets.track_table import TrackTable
from ytm_player.utils.formatting import format_count, format_duration

logger = logging.getLogger(__name__)


class _ArtistAlbumList(DataTable):
    """Lightweight DataTable for artist album/single listings."""

    DEFAULT_CSS = """
    _ArtistAlbumList {
        height: 1fr;
        width: 1fr;
    }
    _ArtistAlbumList > .datatable--cursor {
        background: #2a2a2a;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(cursor_type="row", zebra_stripes=True, **kwargs)
        self._albums: list[dict] = []

    def on_mount(self) -> None:
        self.add_column("Album", width=None, key="title")
        self.add_column("Year", width=6, key="year")

    def load_albums(self, albums: list[dict]) -> None:
        self.clear()
        self._albums = list(albums)
        for album in self._albums:
            title = album.get("title", "Unknown")
            year = str(album.get("year", ""))
            self.add_row(title, year, key=album.get("browseId", title))

    @property
    def selected_album(self) -> dict | None:
        if self.cursor_row is not None and 0 <= self.cursor_row < len(self._albums):
            return self._albums[self.cursor_row]
        return None


class ContextPage(Widget):
    """Shows details for a selected album, artist, or playlist.

    The app navigates here with context_type and context_id parameters
    that determine which data to fetch and how to render it.
    """

    DEFAULT_CSS = """
    ContextPage {
        layout: vertical;
        width: 1fr;
        height: 1fr;
    }
    .context-header {
        height: auto;
        max-height: 5;
        padding: 1 2;
    }
    .context-title {
        text-style: bold;
    }
    .context-subtitle {
        color: $text-muted;
    }
    .context-body {
        height: 1fr;
    }
    .context-loading {
        width: 1fr;
        height: 1fr;
        content-align: center middle;
    }
    .context-error {
        width: 1fr;
        height: 1fr;
        content-align: center middle;
        color: $error;
    }
    .artist-columns {
        height: 1fr;
    }
    .artist-left {
        width: 1fr;
    }
    .artist-right {
        width: 1fr;
    }
    .similar-artists-bar {
        height: auto;
        max-height: 3;
        padding: 0 2;
        color: $text-muted;
    }
    """

    loading: reactive[bool] = reactive(True)
    error_message: reactive[str] = reactive("")

    def __init__(
        self,
        context_type: str,
        context_id: str,
        **kwargs: Any,
    ) -> None:
        """Create a context page.

        Args:
            context_type: One of "album", "artist", or "playlist".
            context_id: The YouTube Music browse/playlist ID.
        """
        super().__init__(**kwargs)
        self.context_type = context_type
        self.context_id = context_id
        self._data: dict[str, Any] = {}
        self._active_focus: str = "tracks"  # "tracks" or "albums" for artist view

    def compose(self) -> ComposeResult:
        yield Label("Loading...", id="context-loading", classes="context-loading")
        yield Label("", id="context-error", classes="context-error")
        yield Vertical(id="context-content")

    def on_mount(self) -> None:
        self.query_one("#context-error").display = False
        self.query_one("#context-content").display = False
        self._load_data()

    def _load_data(self) -> None:
        """Start an async worker to fetch context data."""
        self.loading = True
        self.error_message = ""
        self.run_worker(self._fetch_data(), name="fetch_context", exclusive=True)

    async def _fetch_data(self) -> dict[str, Any]:
        """Fetch data from ytmusic based on context_type."""
        ytmusic = self.app.ytmusic  # type: ignore[attr-defined]
        match self.context_type:
            case "album":
                return await ytmusic.get_album(self.context_id)
            case "artist":
                return await ytmusic.get_artist(self.context_id)
            case "playlist":
                return await ytmusic.get_playlist(self.context_id)
            case _:
                raise ValueError(f"Unknown context type: {self.context_type}")

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name != "fetch_context":
            return

        if event.state == WorkerState.SUCCESS:
            self._data = event.worker.result or {}
            self.loading = False
            if not self._data:
                self.error_message = "No data found."
            else:
                self._build_content()
        elif event.state == WorkerState.ERROR:
            self.loading = False
            self.error_message = f"Failed to load {self.context_type}."
            logger.exception(
                "Failed to load %s %s", self.context_type, self.context_id
            )

    def watch_loading(self, loading: bool) -> None:
        try:
            self.query_one("#context-loading").display = loading
        except Exception:
            pass

    def watch_error_message(self, msg: str) -> None:
        try:
            error_label = self.query_one("#context-error", Label)
            error_label.display = bool(msg)
            error_label.update(msg)
        except Exception:
            pass

    # ── Content builders ──────────────────────────────────────────────

    def _build_content(self) -> None:
        """Build the inner layout based on context_type and fetched data."""
        content = self.query_one("#context-content", Vertical)
        content.remove_children()
        content.display = True

        match self.context_type:
            case "album":
                self._build_album(content)
            case "artist":
                self._build_artist(content)
            case "playlist":
                self._build_playlist(content)

    def _build_album(self, container: Vertical) -> None:
        data = self._data
        title = data.get("title", "Unknown Album")
        artists_raw = data.get("artists", [])
        artist_name = (
            ", ".join(a.get("name", "") for a in artists_raw if isinstance(a, dict))
            if isinstance(artists_raw, list) and artists_raw
            else data.get("artist", "Unknown Artist")
        )
        year = data.get("year", "")
        tracks = data.get("tracks", [])
        track_count = len(tracks)

        subtitle_parts = [artist_name]
        if year:
            subtitle_parts.append(str(year))
        subtitle_parts.append(f"{track_count} track{'s' if track_count != 1 else ''}")

        header = Vertical(classes="context-header")
        container.mount(header)
        header.mount(Label(f"[b]Album[/b]", markup=True))
        header.mount(Label(title, classes="context-title"))
        header.mount(
            Label(" \u00b7 ".join(subtitle_parts), classes="context-subtitle")
        )

        table = TrackTable(show_album=False, id="context-tracks")
        container.mount(table)
        table.load_tracks(self._normalize_tracks(tracks))

    def _build_playlist(self, container: Vertical) -> None:
        data = self._data
        title = data.get("title", "Unknown Playlist")
        author = data.get("author", {})
        owner = author.get("name", "Unknown") if isinstance(author, dict) else str(author)
        tracks = data.get("tracks", [])
        track_count = data.get("trackCount", len(tracks))

        subtitle = f"{owner} \u00b7 {track_count} track{'s' if track_count != 1 else ''}"

        header = Vertical(classes="context-header")
        container.mount(header)
        header.mount(Label(f"[b]Playlist[/b]", markup=True))
        header.mount(Label(title, classes="context-title"))
        header.mount(Label(subtitle, classes="context-subtitle"))

        table = TrackTable(show_album=True, id="context-tracks")
        container.mount(table)
        table.load_tracks(self._normalize_tracks(tracks))

    def _build_artist(self, container: Vertical) -> None:
        data = self._data
        name = data.get("name", "Unknown Artist")
        subs = data.get("subscribers", "")
        subscriber_text = f"{subs} subscribers" if subs else ""

        header = Vertical(classes="context-header")
        container.mount(header)
        header.mount(Label(f"[b]Artist[/b]", markup=True))
        header.mount(Label(name, classes="context-title"))
        if subscriber_text:
            header.mount(Label(subscriber_text, classes="context-subtitle"))

        # Two-column layout: top songs on left, albums on right.
        columns = Horizontal(classes="artist-columns")
        container.mount(columns)

        left = Vertical(classes="artist-left")
        columns.mount(left)
        left.mount(Label("[b]Top Songs[/b]", markup=True))

        songs_section = data.get("songs", {})
        top_songs = (
            songs_section.get("results", [])
            if isinstance(songs_section, dict)
            else []
        )
        top_tracks_table = TrackTable(
            show_album=False, id="context-tracks"
        )
        left.mount(top_tracks_table)
        top_tracks_table.load_tracks(self._normalize_tracks(top_songs))

        right = Vertical(classes="artist-right")
        columns.mount(right)
        right.mount(Label("[b]Albums / Singles[/b]", markup=True))

        albums_section = data.get("albums", {})
        albums_list = (
            albums_section.get("results", [])
            if isinstance(albums_section, dict)
            else []
        )
        singles_section = data.get("singles", {})
        singles_list = (
            singles_section.get("results", [])
            if isinstance(singles_section, dict)
            else []
        )
        all_albums = albums_list + singles_list

        album_table = _ArtistAlbumList(id="context-albums")
        right.mount(album_table)
        album_table.load_albums(all_albums)

        # Similar artists bar at the bottom.
        related_section = data.get("related", {})
        related_artists = (
            related_section.get("results", [])
            if isinstance(related_section, dict)
            else []
        )
        if related_artists:
            names = [
                a.get("name", "") if isinstance(a, dict) else str(a)
                for a in related_artists[:8]
            ]
            similar_text = "Similar Artists: " + " \u00b7 ".join(n for n in names if n)
            container.mount(Label(similar_text, classes="similar-artists-bar"))

    # ── Track normalization ───────────────────────────────────────────

    @staticmethod
    def _normalize_tracks(raw_tracks: list[dict]) -> list[dict]:
        """Ensure tracks have the fields TrackTable expects.

        ytmusicapi returns slightly different shapes for album tracks,
        playlist tracks, and search results. This normalizes them.
        """
        normalized: list[dict] = []
        for t in raw_tracks:
            video_id = t.get("videoId") or t.get("video_id", "")
            title = t.get("title", "Unknown")
            artists = t.get("artists", [])
            artist = (
                ", ".join(
                    a.get("name", "") if isinstance(a, dict) else str(a)
                    for a in artists
                )
                if isinstance(artists, list) and artists
                else t.get("artist", "Unknown")
            )
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
            duration = t.get("duration_seconds") or t.get("duration")
            thumbnail = None
            thumbs = t.get("thumbnails")
            if isinstance(thumbs, list) and thumbs:
                thumbnail = thumbs[-1].get("url") if isinstance(thumbs[-1], dict) else None

            normalized.append(
                {
                    "video_id": video_id,
                    "title": title,
                    "artist": artist,
                    "artists": artists,
                    "album": album,
                    "album_id": album_id,
                    "duration": duration,
                    "thumbnail_url": thumbnail,
                    "is_video": t.get("isVideo", t.get("is_video", False)),
                }
            )
        return normalized

    # ── Events ────────────────────────────────────────────────────────

    async def on_track_table_track_selected(self, event: TrackTable.TrackSelected) -> None:
        """Play the selected track and enqueue remaining tracks."""
        event.stop()
        table = self.query_one("#context-tracks", TrackTable)
        tracks = table.tracks
        idx = event.index

        # Load all tracks into the queue starting from the selected one.
        self.app.queue.clear()  # type: ignore[attr-defined]
        self.app.queue.add_multiple(tracks)  # type: ignore[attr-defined]
        self.app.queue.jump_to(idx)  # type: ignore[attr-defined]

        # Play selected track.
        await self.app.play_track(event.track)  # type: ignore[attr-defined]

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle album selection in artist view."""
        # Only intercept events from the album list, not the track table.
        source = event.control
        if not isinstance(source, _ArtistAlbumList):
            return
        event.stop()

        album = source.selected_album
        if album and album.get("browseId"):
            await self.app.navigate_to(  # type: ignore[attr-defined]
                "context",
                context_type="album",
                context_id=album["browseId"],
            )

    # ── Action handling ───────────────────────────────────────────────

    async def handle_action(self, action: Action, count: int = 1) -> None:
        """Process vim-style navigation actions."""
        match action:
            case Action.GO_BACK:
                await self.app.navigate_to("back")  # type: ignore[attr-defined]
                return
            case Action.FOCUS_NEXT:
                self._cycle_focus(forward=True)
                return
            case Action.FOCUS_PREV:
                self._cycle_focus(forward=False)
                return

        # Delegate movement actions to the currently focused widget.
        if self.context_type == "artist" and self._active_focus == "albums":
            try:
                album_table = self.query_one("#context-albums", _ArtistAlbumList)
                match action:
                    case Action.MOVE_DOWN:
                        for _ in range(count):
                            album_table.action_cursor_down()
                    case Action.MOVE_UP:
                        for _ in range(count):
                            album_table.action_cursor_up()
                    case Action.GO_TOP:
                        if album_table.row_count > 0:
                            album_table.move_cursor(row=0)
                    case Action.GO_BOTTOM:
                        if album_table.row_count > 0:
                            album_table.move_cursor(row=album_table.row_count - 1)
                    case Action.SELECT:
                        album = album_table.selected_album
                        if album and album.get("browseId"):
                            await self.app.navigate_to(  # type: ignore[attr-defined]
                                "context",
                                context_type="album",
                                context_id=album["browseId"],
                            )
                return
            except Exception:
                pass

        # Default: delegate to the track table.
        try:
            table = self.query_one("#context-tracks", TrackTable)
            await table.handle_action(action, count)
        except Exception:
            pass

    def _cycle_focus(self, forward: bool) -> None:
        """Cycle focus between track table and album list (artist view only)."""
        if self.context_type != "artist":
            return
        if forward:
            self._active_focus = "albums" if self._active_focus == "tracks" else "tracks"
        else:
            self._active_focus = "tracks" if self._active_focus == "albums" else "albums"
