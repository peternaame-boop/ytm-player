"""Browse page — recommendations, charts, and new releases."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from textual.app import ComposeResult
from textual.containers import Horizontal, HorizontalScroll, Vertical, VerticalScroll
from textual.events import Click
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView, Static

from ytm_player.config.keymap import Action
from ytm_player.config.settings import get_settings
from ytm_player.ui.widgets.track_table import TrackTable
from ytm_player.utils.formatting import (
    extract_artist,
    get_video_id,
    normalize_tracks,
    truncate,
)

if TYPE_CHECKING:
    from ytm_player.app._base import YTMHostBase

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tab bar
# ---------------------------------------------------------------------------

_TABS = ("For You", "Charts", "New Releases")


# ---------------------------------------------------------------------------
# Chart shelf title normalisation
# ---------------------------------------------------------------------------


def _clean_shelf_title(raw: str) -> str:
    """Normalise a YouTube chart shelf title for compact pill display.

    YouTube returns titles like:
      "Coachella 2026: Daily Top 100 Songs"
      "Trending 20 United Kingdom"
      "Daily Top Music Videos - United Kingdom"
      "Daily Top Songs on Shorts - United Kingdom"
      "Daily Top 100 Songs (Live)"   (hypothetical YouTube variant)

    We strip brand prefixes, country suffixes, and apply preferred short
    labels: "Daily Top 100", "Trending 20", "Daily Top Videos",
    "Daily Top Songs (Shorts)". Patterns are regex-anchored so a future
    title like "Daily Top 100 Songs (Live)" rewrites to
    "Daily Top 100 (Live)" rather than failing the rewrite chain.
    """
    import re

    from ytm_player.services.regions import CHART_REGIONS

    s = raw.strip()
    # Strip " - <country>" suffix
    if " - " in s:
        head, tail = s.rsplit(" - ", 1)
        if any(tail.strip() == name for _, name in CHART_REGIONS):
            s = head.strip()
    # Strip trailing " <country>" without hyphen (e.g. "Trending 20 United Kingdom")
    for _, name in CHART_REGIONS:
        suffix = " " + name
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
            break
    # Apply canonical short-label rewrites. Each pattern matches against the
    # core token only — trailing decorations like " (Live)" / " (Acoustic)"
    # / extra suffixes are preserved verbatim.
    s = re.sub(r"\bDaily Top 100 Songs\b", "Daily Top 100", s)
    s = re.sub(r"\bDaily Top Music Videos\b", "Daily Top Videos", s)
    s = re.sub(r"\bDaily Top Songs on Shorts\b", "Daily Top Songs (Shorts)", s)
    return s


# ---------------------------------------------------------------------------
# Event vs country-chart classification
# ---------------------------------------------------------------------------


def _is_event_shelf(shelf: dict) -> bool:
    """A shelf is a global event playlist iff its title carries a brand
    prefix — YouTube formats events as ``"<Brand>: <Generic Title>"`` (e.g.
    ``"Coachella 2026: Daily Top 100 Songs"``). Country chart shelves never
    use the colon-space form.
    """
    title = str(shelf.get("title", ""))
    return ": " in title


# Country chart pills sort by this priority — the canonical "what's hot
# in country X" answer first, then the rest. Lower number = earlier.
_CHART_PRIORITY: tuple[tuple[str, int], ...] = (
    ("Top 100 Songs", 0),
    ("Weekly Top Songs on Shorts", 1),
    ("Trending 20", 2),
)


def _chart_sort_key(shelf: dict) -> tuple[int, str]:
    title = str(shelf.get("title", ""))
    for prefix, rank in _CHART_PRIORITY:
        if title.startswith(prefix):
            return (rank, title)
    return (99, title)


def _split_events_and_charts(
    shelves: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Partition shelves into (events, charts), with charts pre-sorted by
    canonical priority. Events keep their original order — there are
    rarely more than one, and YouTube's ordering reflects editorial
    intent.
    """
    events = [s for s in shelves if _is_event_shelf(s)]
    charts = sorted(
        (s for s in shelves if not _is_event_shelf(s)),
        key=_chart_sort_key,
    )
    return events, charts


class BrowseTabBar(Widget):
    """A horizontal tab selector for the Browse page sections."""

    DEFAULT_CSS = """
    BrowseTabBar {
        height: 4;
        width: 1fr;
        padding: 1 1 0 1;
        background: $surface;
        border-bottom: solid $border;
    }

    BrowseTabBar Horizontal {
        height: 3;
        align: left middle;
    }

    BrowseTabBar .tab-item {
        width: auto;
        min-width: 12;
        padding: 0 2;
        height: 3;
        content-align: center middle;
        color: $text-muted;
        border-bottom: tall transparent;
    }

    BrowseTabBar .tab-item:hover {
        color: $text;
    }

    BrowseTabBar .tab-item.active {
        text-style: bold;
        color: $text;
        border-bottom: tall $primary;
    }
    """

    active_tab: reactive[int] = reactive(0)

    class TabChanged(Message):
        """Emitted when the user switches to a different tab."""

        def __init__(self, index: int, label: str) -> None:
            super().__init__()
            self.index = index
            self.label = label

    def compose(self) -> ComposeResult:
        with Horizontal():
            for i, label in enumerate(_TABS):
                classes = "tab-item active" if i == 0 else "tab-item"
                yield Static(f" {label} ", id=f"tab-{i}", classes=classes)

    def on_click(self, event: Click) -> None:
        """Handle click on a tab label."""
        # Walk up from the click target to find the tab Static.
        node = event.widget
        while node is not None and not (isinstance(node, Static) and "tab-item" in node.classes):
            node = node.parent
        if node is None:
            return
        for i in range(len(_TABS)):
            if node.id == f"tab-{i}":
                self.switch_to(i)
                return

    def switch_to(self, index: int) -> None:
        """Activate the tab at *index*."""
        if index == self.active_tab:
            return
        # Update CSS classes.
        for i in range(len(_TABS)):
            tab = self.query_one(f"#tab-{i}", Static)
            if i == index:
                tab.add_class("active")
            else:
                tab.remove_class("active")
        self.active_tab = index
        self.post_message(self.TabChanged(index, _TABS[index]))


# ---------------------------------------------------------------------------
# Content sections
# ---------------------------------------------------------------------------


class ForYouSection(Widget):
    """Personalised recommendation shelves from get_home()."""

    DEFAULT_CSS = """
    ForYouSection {
        height: 1fr;
        width: 1fr;
        padding: 0 1;
    }

    ForYouSection .shelf-title {
        text-style: bold;
        color: $primary;
        padding: 1 0 0 0;
    }

    ForYouSection .shelf-items {
        height: auto;
        padding: 0 0 1 0;
        margin: 0 0 1 0;
        border-bottom: solid $border;
    }

    ForYouSection .loading {
        height: 1fr;
        width: 1fr;
        content-align: center middle;
        color: $text-muted;
    }
    """

    is_loading: reactive[bool] = reactive(True)

    class ItemSelected(Message):
        def __init__(self, item: dict[str, Any]) -> None:
            super().__init__()
            self.item = item

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._shelves: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield Static("Loading recommendations...", id="foryou-loading", classes="loading")
        yield VerticalScroll(id="foryou-shelves")

    def on_unmount(self) -> None:
        """Release shelf data to prevent memory retention."""
        self._shelves.clear()

    async def load_data(self) -> None:
        """Fetch and display personalised home shelves."""
        self.is_loading = True
        try:
            ytmusic = cast("YTMHostBase", self.app).ytmusic
            assert ytmusic is not None
            limit = get_settings().ui.home_shelves
            self._shelves = await ytmusic.get_home(limit=limit)
        except Exception:
            logger.debug("Failed to load home recommendations", exc_info=True)
            self._show_error("Failed to load recommendations.")
            self.is_loading = False
            return

        try:
            await self._populate_shelves()
        except Exception:
            logger.debug("Failed to render home shelves", exc_info=True)
            # Clean up any partially-mounted widgets.
            try:
                container = self.query_one("#foryou-shelves", VerticalScroll)
                await container.remove_children()
            except Exception:
                pass
            self._show_error("Failed to load recommendations.")
        finally:
            self.is_loading = False

    async def _populate_shelves(self) -> None:
        loading = self.query_one("#foryou-loading", Static)
        loading.display = False

        container = self.query_one("#foryou-shelves", VerticalScroll)
        # Clear _shelf_items references from old ListViews before removing.
        for lv in container.query(ListView):
            if hasattr(lv, "_shelf_items"):
                lv._shelf_items = []  # type: ignore[attr-defined]
        await container.remove_children()

        if not self._shelves:
            await container.mount(Static("No recommendations available.", classes="loading"))
            return

        for shelf in self._shelves:
            title = shelf.get("title", "Recommendations")
            contents = shelf.get("contents", [])
            if not contents:
                continue

            try:
                await container.mount(Label(title, classes="shelf-title"))

                list_view = ListView(classes="shelf-items")
                await container.mount(list_view)

                for item in contents[:8]:
                    item_title = item.get("title", "Unknown")
                    subtitle_parts: list[str] = []
                    artist_str = extract_artist(item)
                    if artist_str and artist_str != "Unknown":
                        subtitle_parts.append(artist_str)
                    description = item.get("description", "")
                    if description:
                        subtitle_parts.append(str(description))
                    subtitle = " - ".join(subtitle_parts)
                    display = truncate(f"{item_title}  {subtitle}", 80) if subtitle else item_title

                    list_view.append(ListItem(Label(display)))

                # Store items on the list_view for later retrieval.
                list_view._shelf_items = contents[:8]  # type: ignore[attr-defined]
            except Exception:
                logger.debug("Failed to render shelf %r", title, exc_info=True)

    def _show_error(self, message: str) -> None:
        loading = self.query_one("#foryou-loading", Static)
        loading.update(message)
        loading.display = True

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle item selection within a shelf."""
        try:
            list_view = event.list_view
            items = getattr(list_view, "_shelf_items", [])
            idx = list_view.index
            if idx is not None and 0 <= idx < len(items):
                self.post_message(self.ItemSelected(items[idx]))
        except Exception:
            logger.exception("ForYouSection.on_list_view_selected failed")


class ChartsSection(Widget):
    """Top charts from get_charts() displayed as a track table.

    YouTube Music returns multiple daily chart shelves per country (typically
    "Top songs / Top hits / Top videos / New & hot" — varies by region). We
    show all of them as a clickable pill row above the track table; clicking
    a pill switches the table to that shelf's playlist.
    """

    DEFAULT_CSS = """
    ChartsSection {
        height: 1fr;
        width: 1fr;
        padding: 0 1;
    }

    ChartsSection .loading {
        height: 1fr;
        width: 1fr;
        content-align: center middle;
        color: $text-muted;
    }

    ChartsSection .section-title {
        text-style: bold;
        color: $text;
        height: 1;
        padding: 0 0 1 0;
    }

    ChartsSection #charts-country {
        dock: top;
        height: 1;
        width: auto;
        color: $text-muted;
        padding: 0 0 0 1;
    }

    ChartsSection #charts-event-pills {
        height: 1;
        width: 1fr;
        align: left middle;
        scrollbar-size-horizontal: 0;
    }

    ChartsSection #charts-chart-pills {
        height: 1;
        width: 1fr;
        margin: 0 0 1 0;
        align: left middle;
        scrollbar-size-horizontal: 0;
    }

    ChartsSection .shelf-pill {
        width: auto;
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
        margin-right: 1;
    }

    ChartsSection .shelf-pill:hover {
        background: $border;
        color: $text;
    }

    ChartsSection .shelf-pill.active {
        background: $primary;
        color: $text;
        text-style: bold;
    }

    /* Event pills — accent colour marks them as YouTube-promoted global
       events distinct from country charts. */
    ChartsSection .shelf-pill.event {
        background: $surface;
        color: $accent;
    }

    ChartsSection .shelf-pill.event:hover {
        background: $border;
        color: $accent;
    }

    ChartsSection .shelf-pill.event.active {
        background: $accent;
        color: $surface;
        text-style: bold;
    }

    /* Static leading label inside the event row that explains why these
       pills are here. Muted so the eye still goes to the pills. */
    ChartsSection .event-row-label {
        width: auto;
        height: 1;
        color: $text-muted;
        padding: 0 1 0 0;
        margin-right: 1;
    }
    """

    is_loading: reactive[bool] = reactive(True)

    # Below this terminal width (cols), the event pill row is hidden to
    # reclaim vertical space. Chart pills always render — they're the
    # primary content.
    _NARROW_THRESHOLD: int = 80

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._chart_data: dict[str, Any] = {}
        self._country: str = get_settings().ui.region
        # Combined shelves list: events first, then country charts (sorted
        # by priority). Each entry is the dict ytmusicapi returns —
        # keys include "title", "playlistId", "thumbnails".
        self._dailies: list[dict[str, Any]] = []
        # Number of leading entries in _dailies that are global event
        # playlists. Charts begin at _dailies[_event_count].
        self._event_count: int = 0
        # Index into self._dailies that's currently loaded into the table.
        self._active_daily: int = 0

    def on_unmount(self) -> None:
        """Release chart data to prevent memory retention."""
        self._chart_data.clear()
        self._dailies.clear()

    def compose(self) -> ComposeResult:
        yield Static("Loading charts...", id="charts-loading", classes="loading")
        with Vertical(id="charts-content"):
            yield Static("Global Charts", classes="section-title")
            yield Static(
                f"Country: {self._country}    (press 'c' to change)",
                id="charts-country",
            )
            # Two stacked pill rows:
            #   #charts-event-pills — global event playlists (Coachella et al);
            #     hidden when no events OR terminal too narrow.
            #   #charts-chart-pills — country-specific shelves; always shown
            #     when data is present.
            # Each row uses HorizontalScroll so pills overflow gracefully on
            # narrow terminals. Composed with placeholders so the containers
            # are never empty (Textual collapses empty children).
            with HorizontalScroll(id="charts-event-pills"):
                yield Static(" ", id="charts-event-pill-placeholder", classes="shelf-pill event")
            with HorizontalScroll(id="charts-chart-pills"):
                yield Static(" ", id="charts-chart-pill-placeholder", classes="shelf-pill")
            yield TrackTable(
                show_album=True,
                show_index=True,
                id="charts-table",
            )

    def on_mount(self) -> None:
        # Hide content until data loads.
        try:
            self.query_one("#charts-content").display = False
        except Exception:
            logger.debug("Failed to hide charts content on mount", exc_info=True)

    async def load_data(self, country: str | None = None) -> None:
        """Fetch chart data for *country* and display the first daily shelf.

        ytmusicapi's ``get_charts`` returns chart *playlists* under several
        shelves (daily / weekly / genres / artists). We show all daily shelves
        as clickable pills; selecting one loads its playlist into the table.

        YouTube Music has uneven regional coverage: some country codes return
        no daily chart at all. Distinguish that case from a real network/API
        error so the user knows whether to retry or pick a different region.
        """
        self.is_loading = True
        if country is None:
            country = get_settings().ui.region
        self._country = country
        try:
            ytmusic = cast("YTMHostBase", self.app).ytmusic
            assert ytmusic is not None
            self._chart_data = await ytmusic.get_charts(country=country)
        except Exception:
            logger.exception("Failed to load charts for country=%r", country)
            self._show_error(f"Failed to load charts for {country} — try again later.")
            self.is_loading = False
            return

        try:
            raw: list[dict[str, Any]] = []
            if isinstance(self._chart_data, dict):
                for key in ("daily", "weekly", "videos"):
                    arr = self._chart_data.get(key)
                    if isinstance(arr, list):
                        raw.extend(s for s in arr if isinstance(s, dict))

            events, charts = _split_events_and_charts(raw)
            self._dailies = events + charts
            self._event_count = len(events)

            if not self._dailies:
                logger.info("No chart data available for country=%r", country)
                self._show_error(
                    f"No chart data available for {country}. "
                    "YouTube Music coverage varies by region — press 'c' to pick a different one."
                )
                return

            # Default-load the first country chart, not an event. Falls
            # back to position 0 only if there are no charts at all.
            self._active_daily = self._event_count if charts else 0
            await self._render_pills()
            await self._load_active_daily()
        except Exception:
            logger.exception("Failed to populate charts for country=%r", country)
            self._show_error(f"Failed to load charts for {country} — try again later.")
        finally:
            self.is_loading = False

    async def _render_pills(self) -> None:
        """Populate the two pill rows from ``_dailies``.

        Indices 0.._event_count-1 land in the event row;
        _event_count..end land in the chart row. Each pill keeps its
        absolute index in ``_dailies`` as its id suffix so click
        handling stays uniform.
        """
        try:
            event_row = self.query_one("#charts-event-pills", HorizontalScroll)
            chart_row = self.query_one("#charts-chart-pills", HorizontalScroll)
        except Exception:
            logger.debug("charts pill containers not found", exc_info=True)
            return
        await event_row.remove_children()
        await chart_row.remove_children()
        # Leading explainer label inside the event row so users understand
        # what this strip is. Mounted only if there's at least one event.
        if self._event_count > 0:
            await event_row.mount(Static("Featured globally:", classes="event-row-label"))
        for i, shelf in enumerate(self._dailies):
            raw_title = (
                shelf.get("title") if isinstance(shelf, dict) else None
            ) or f"Shelf {i + 1}"
            title = _clean_shelf_title(str(raw_title))
            is_event = i < self._event_count
            classes = "shelf-pill event" if is_event else "shelf-pill"
            if i == self._active_daily:
                classes += " active"
            pill = Static(title, classes=classes, id=f"charts-pill-{i}")
            await (event_row if is_event else chart_row).mount(pill)
        # Final visibility pass: hide the event row if no events at all,
        # or if the terminal is too narrow to spare a row.
        self._update_event_row_visibility()

    def _update_event_row_visibility(self) -> None:
        """Show/hide the event pill row based on event count + terminal width."""
        try:
            event_row = self.query_one("#charts-event-pills", HorizontalScroll)
        except Exception:
            return
        try:
            term_width = self.app.size.width
        except Exception:
            term_width = 0
        show = self._event_count > 0 and term_width >= self._NARROW_THRESHOLD
        event_row.display = show

    def on_resize(self) -> None:
        """Re-evaluate event row visibility when the terminal resizes."""
        self._update_event_row_visibility()

    def _refresh_pill_active_class(self) -> None:
        """Re-apply the .active class to the current pill (no remount needed)."""
        for i in range(len(self._dailies)):
            try:
                pill = self.query_one(f"#charts-pill-{i}", Static)
            except Exception:
                continue
            if i == self._active_daily:
                pill.add_class("active")
            else:
                pill.remove_class("active")

    async def _load_active_daily(self) -> None:
        """Fetch and render the playlist for ``self._active_daily``.

        OLAK5-prefixed playlistIds (e.g. YouTube's "Trending 20" auto-
        generated playlist) hit a parser bug in ytmusicapi's
        ``parse_audio_playlist`` — ``tracks[0]['album']`` is None and the
        function raises TypeError. ``get_watch_playlist`` calls a different
        endpoint and works for these IDs, so we use it as the fallback for
        any OLAK5-prefixed shelf.
        """
        if not (0 <= self._active_daily < len(self._dailies)):
            return
        shelf = self._dailies[self._active_daily]
        playlist_id = shelf.get("playlistId") if isinstance(shelf, dict) else None
        if not playlist_id:
            self._show_error("Selected chart has no playlist id.")
            return
        try:
            ytmusic = cast("YTMHostBase", self.app).ytmusic
            assert ytmusic is not None
            tracks = await ytmusic.get_chart_shelf_tracks(playlist_id, limit=100)
        except Exception:
            logger.exception("Failed to load playlist for chart shelf %r", playlist_id)
            self._show_error("Failed to load chart playlist — try again later.")
            return
        self._populate_charts(tracks)

    def on_click(self, event: Click) -> None:
        """Pill click → switch active daily shelf."""
        widget = getattr(event, "widget", None)
        wid = getattr(widget, "id", None) if widget is not None else None
        if not isinstance(wid, str) or not wid.startswith("charts-pill-"):
            return
        try:
            new_index = int(wid.removeprefix("charts-pill-"))
        except ValueError:
            return
        if new_index == self._active_daily or not (0 <= new_index < len(self._dailies)):
            return
        self._active_daily = new_index
        self._refresh_pill_active_class()
        self.run_worker(
            self._load_active_daily(),
            name="charts-switch-shelf",
            exclusive=True,
            exit_on_error=False,
        )

    def _populate_charts(self, tracks: list[dict[str, Any]]) -> None:
        loading = self.query_one("#charts-loading", Static)
        loading.display = False

        content = self.query_one("#charts-content")
        content.display = True

        # Update country label.
        country_label = self.query_one("#charts-country", Static)
        country_label.update(f"Country: {self._country}    (press 'c' to change)")

        table = self.query_one("#charts-table", TrackTable)
        table.load_tracks(normalize_tracks(tracks))

    def _show_error(self, message: str) -> None:
        loading = self.query_one("#charts-loading", Static)
        loading.update(message)
        loading.display = True
        try:
            self.query_one("#charts-content").display = False
        except Exception:
            logger.debug("Failed to hide charts content on error", exc_info=True)


class NewReleasesSection(Widget):
    """New album releases from get_new_releases()."""

    DEFAULT_CSS = """
    NewReleasesSection {
        height: 1fr;
        width: 1fr;
        padding: 0 1;
    }

    NewReleasesSection .loading {
        height: 1fr;
        width: 1fr;
        content-align: center middle;
        color: $text-muted;
    }

    NewReleasesSection .section-title {
        text-style: bold;
        color: $text;
        height: 1;
        padding: 0 0 1 0;
    }

    NewReleasesSection ListView {
        height: 1fr;
    }
    """

    is_loading: reactive[bool] = reactive(True)

    class AlbumSelected(Message):
        def __init__(self, album: dict[str, Any]) -> None:
            super().__init__()
            self.album = album

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._albums: list[dict[str, Any]] = []

    def on_unmount(self) -> None:
        """Release album data to prevent memory retention."""
        self._albums.clear()

    def compose(self) -> ComposeResult:
        yield Static("Loading new releases...", id="releases-loading", classes="loading")
        with Vertical(id="releases-content"):
            yield Label("New Releases", classes="section-title")
            yield ListView(id="releases-list")

    def on_mount(self) -> None:
        try:
            self.query_one("#releases-content").display = False
        except Exception:
            logger.debug("Failed to hide releases content on mount", exc_info=True)

    async def load_data(self) -> None:
        """Fetch and display new releases."""
        self.is_loading = True
        try:
            ytmusic = cast("YTMHostBase", self.app).ytmusic
            assert ytmusic is not None
            self._albums = await ytmusic.get_new_releases()
            self._populate_releases()
        except Exception:
            logger.exception("Failed to load new releases")
            self._show_error("Failed to load new releases.")
        finally:
            self.is_loading = False

    def _populate_releases(self) -> None:
        loading = self.query_one("#releases-loading", Static)
        loading.display = False

        content = self.query_one("#releases-content")
        content.display = True

        list_view = self.query_one("#releases-list", ListView)
        list_view.clear()

        for album in self._albums:
            title = album.get("title", "Unknown Album")
            artist_str = extract_artist(album)
            if artist_str == "Unknown":
                artist_str = ""
            album_type = album.get("type", "")
            year = album.get("year", "")

            parts = [title]
            if artist_str:
                parts.append(f"by {artist_str}")
            meta_parts: list[str] = []
            if album_type:
                meta_parts.append(album_type)
            if year:
                meta_parts.append(str(year))
            if meta_parts:
                parts.append(f"({', '.join(meta_parts)})")

            display = truncate(" ".join(parts), 80)
            list_view.append(ListItem(Label(display)))

    def _show_error(self, message: str) -> None:
        loading = self.query_one("#releases-loading", Static)
        loading.update(message)
        loading.display = True
        try:
            self.query_one("#releases-content").display = False
        except Exception:
            logger.debug("Failed to hide releases content on error", exc_info=True)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle album selection."""
        try:
            idx = event.list_view.index
            if idx is not None and 0 <= idx < len(self._albums):
                self.post_message(self.AlbumSelected(self._albums[idx]))
        except Exception:
            logger.exception("NewReleasesSection.on_list_view_selected failed")


# ---------------------------------------------------------------------------
# Main browse page
# ---------------------------------------------------------------------------


class BrowsePage(Widget):
    """Tabbed browse page: For You, Charts, New Releases.

    Each tab lazily loads its data on first activation.
    """

    DEFAULT_CSS = """
    BrowsePage {
        height: 1fr;
        width: 1fr;
    }

    BrowsePage > Vertical {
        height: 1fr;
        width: 1fr;
    }

    #browse-content {
        height: 1fr;
        width: 1fr;
    }

    #browse-content > Widget {
        display: none;
    }

    #browse-content > Widget.active-section {
        display: block;
    }
    """

    active_tab: reactive[int] = reactive(0)

    def __init__(
        self,
        *,
        active_tab: int | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._tabs_loaded: set[int] = set()
        self._restore_tab = active_tab

    def compose(self) -> ComposeResult:
        with Vertical():
            yield BrowseTabBar(id="browse-tabs")
            with Vertical(id="browse-content"):
                yield ForYouSection(id="section-foryou", classes="active-section")
                yield ChartsSection(id="section-charts")
                yield NewReleasesSection(id="section-releases")

    def on_mount(self) -> None:
        if self._restore_tab and self._restore_tab > 0:
            # Restore previously active tab — update tab bar CSS + lazy load.
            tab_bar = self.query_one("#browse-tabs", BrowseTabBar)
            tab_bar.switch_to(self._restore_tab)
            self._restore_tab = None
        else:
            # Load the default tab (For You).
            self._load_tab(0)

    def get_nav_state(self) -> dict[str, Any]:
        """Return state to preserve when navigating away."""
        if self.active_tab > 0:
            return {"active_tab": self.active_tab}
        return {}

    # ------------------------------------------------------------------
    # Tab switching
    # ------------------------------------------------------------------

    def on_browse_tab_bar_tab_changed(self, event: BrowseTabBar.TabChanged) -> None:
        """Switch the visible content section and lazy-load data."""
        self._switch_section(event.index)

    def _switch_section(self, index: int) -> None:
        """Show the section at *index* and hide all others."""
        section_ids = [
            "section-foryou",
            "section-charts",
            "section-releases",
        ]

        for i, sid in enumerate(section_ids):
            try:
                section = self.query_one(f"#{sid}")
                if i == index:
                    section.add_class("active-section")
                else:
                    section.remove_class("active-section")
            except Exception:
                logger.debug("Failed to toggle browse section '%s'", sid, exc_info=True)

        self.active_tab = index
        self._load_tab(index)

    def _load_tab(self, index: int) -> None:
        """Lazy-load data for a tab if not already loaded."""
        if index in self._tabs_loaded:
            return
        self._tabs_loaded.add(index)

        match index:
            case 0:
                section = self.query_one("#section-foryou", ForYouSection)
                self.run_worker(
                    section.load_data(),
                    name="load-foryou",
                    exclusive=True,
                    exit_on_error=False,
                )
            case 1:
                section = self.query_one("#section-charts", ChartsSection)
                self.run_worker(
                    section.load_data(),
                    name="load-charts",
                    exclusive=True,
                    exit_on_error=False,
                )
            case 2:
                section = self.query_one("#section-releases", NewReleasesSection)
                self.run_worker(
                    section.load_data(),
                    name="load-releases",
                    exclusive=True,
                    exit_on_error=False,
                )

    # ------------------------------------------------------------------
    # Item selection handlers
    # ------------------------------------------------------------------

    async def on_for_you_section_item_selected(self, event: ForYouSection.ItemSelected) -> None:
        """Handle item selection from the For You shelves."""
        item = event.item
        await self._navigate_item(item)

    async def on_new_releases_section_album_selected(
        self, event: NewReleasesSection.AlbumSelected
    ) -> None:
        """Navigate to the selected album."""
        album = event.album
        album_id = album.get("browseId") or album.get("album_id")
        if album_id:
            host = cast("YTMHostBase", self.app)
            await host.navigate_to("context", context_type="album", context_id=album_id)

    async def on_track_table_track_selected(self, event: TrackTable.TrackSelected) -> None:
        """Play the selected chart track and populate the queue."""
        event.stop()
        table = self.query_one("#charts-table", TrackTable)
        host = cast("YTMHostBase", self.app)
        host.queue.clear()
        host.queue.add_multiple(table.tracks)
        host.queue.jump_to_real(event.index)
        # Charts queue is ephemeral — clear any prior context (TP-7).
        host.queue.set_context(None)
        await host.play_track(event.track)

    async def _navigate_item(self, item: dict[str, Any]) -> None:
        """Route an item to the appropriate context page or play it directly."""
        result_type = (item.get("resultType") or item.get("type") or "").lower()
        video_id = get_video_id(item)
        browse_id = item.get("browseId")
        playlist_id = item.get("playlistId") or item.get("audioPlaylistId")
        host = cast("YTMHostBase", self.app)

        if result_type in ("song", "video", "flat_song") or video_id:
            normalized_tracks = normalize_tracks([item])
            track_to_play = normalized_tracks[0] if normalized_tracks else item
            await host.play_track(track_to_play)
        elif result_type in ("album", "single"):
            if browse_id:
                await host.navigate_to("context", context_type="album", context_id=browse_id)
        elif result_type == "artist":
            if browse_id:
                await host.navigate_to("context", context_type="artist", context_id=browse_id)
        elif result_type == "playlist":
            if playlist_id or browse_id:
                await host.navigate_to(
                    "context", context_type="playlist", context_id=playlist_id or browse_id
                )
        elif playlist_id:
            # Shelves like "Mixed for you", "Listen again" radio entries, mixes etc.
            # have a playlistId but no resultType.
            await host.navigate_to("context", context_type="playlist", context_id=playlist_id)
        elif browse_id:
            # Fallback: treat any remaining browseId as an album/playlist context.
            await host.navigate_to("context", context_type="album", context_id=browse_id)

    # ------------------------------------------------------------------
    # Vim-style action handler
    # ------------------------------------------------------------------

    async def handle_action(self, action: Action, count: int = 1) -> None:
        """Process vim-style navigation actions dispatched from the app."""
        match action:
            case Action.MOVE_DOWN:
                focused = self.app.focused
                if isinstance(focused, ListView):
                    for _ in range(count):
                        focused.action_cursor_down()
                elif isinstance(focused, TrackTable):
                    await focused.handle_action(action, count)

            case Action.MOVE_UP:
                focused = self.app.focused
                if isinstance(focused, ListView):
                    for _ in range(count):
                        focused.action_cursor_up()
                elif isinstance(focused, TrackTable):
                    await focused.handle_action(action, count)

            case Action.PAGE_DOWN:
                focused = self.app.focused
                if isinstance(focused, ListView):
                    focused.action_scroll_down()
                elif isinstance(focused, TrackTable):
                    await focused.handle_action(action, count)

            case Action.PAGE_UP:
                focused = self.app.focused
                if isinstance(focused, ListView):
                    focused.action_scroll_up()
                elif isinstance(focused, TrackTable):
                    await focused.handle_action(action, count)

            case Action.GO_TOP:
                focused = self.app.focused
                if isinstance(focused, ListView):
                    if len(focused.children) > 0:
                        focused.index = 0
                elif isinstance(focused, TrackTable):
                    await focused.handle_action(action, count)

            case Action.GO_BOTTOM:
                focused = self.app.focused
                if isinstance(focused, ListView):
                    if len(focused.children) > 0:
                        focused.index = len(focused.children) - 1
                elif isinstance(focused, TrackTable):
                    await focused.handle_action(action, count)

            case Action.SELECT:
                focused = self.app.focused
                if isinstance(focused, ListView):
                    focused.action_select_cursor()
                elif isinstance(focused, TrackTable):
                    await focused.handle_action(action, count)

            case Action.FOCUS_NEXT:
                # Move to next tab.
                tab_bar = self.query_one("#browse-tabs", BrowseTabBar)
                next_idx = (tab_bar.active_tab + 1) % len(_TABS)
                tab_bar.switch_to(next_idx)

            case Action.FOCUS_PREV:
                # Move to previous tab.
                tab_bar = self.query_one("#browse-tabs", BrowseTabBar)
                prev_idx = (tab_bar.active_tab - 1) % len(_TABS)
                tab_bar.switch_to(prev_idx)

            case Action.PICK_COUNTRY:
                # Charts sub-tab only — index 1 in the (For You, Charts, New
                # Releases) tab order. No-op on other sub-tabs.
                if self.active_tab != 1:
                    return
                from ytm_player.ui.popups.country_picker import CountryPickerModal

                current = getattr(get_settings().ui, "region", "ZZ") or "ZZ"
                self.app.push_screen(
                    CountryPickerModal(current_code=current),
                    self._on_country_picked,
                )

    async def _on_country_picked(self, code: str | None) -> None:
        """Callback for the CountryPickerModal — refresh charts on success."""
        if not code:
            return
        settings = get_settings()
        try:
            settings.ui.region = code
            settings.save()
        except Exception:
            logger.exception("Failed to persist new region setting")
        try:
            section = self.query_one("#section-charts", ChartsSection)
            self.run_worker(
                section.load_data(country=code),
                name="reload-charts",
                exclusive=True,
                exit_on_error=False,
            )
        except Exception:
            logger.exception("Failed to reload charts after region change")
