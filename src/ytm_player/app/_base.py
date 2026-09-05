"""Type-checking base class for app mixins.

At runtime this module exposes ``YTMHostBase = object`` so that mixins
inherit from ``object`` and behavior is unchanged.  Under
``TYPE_CHECKING`` it exposes a typed stub class describing
``YTMPlayerApp``'s full attribute surface — Pyright walks this stub
when analyzing each mixin in isolation, eliminating the
"Cannot access attribute X for class FooMixin" noise.

Zero runtime cost: the rich type definition lives behind
``TYPE_CHECKING`` and is never evaluated by the interpreter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncio
    from typing import Any, Protocol

    from textual.app import App

    from ytm_player.app._playback import _LocalHistoryClaim
    from ytm_player.config.keymap import KeyMap
    from ytm_player.config.settings import Settings
    from ytm_player.ipc import IPCServer
    from ytm_player.services.cache import CacheManager
    from ytm_player.services.discord_rpc import DiscordRPC
    from ytm_player.services.download import DownloadService
    from ytm_player.services.history import HistoryManager
    from ytm_player.services.lastfm import LastFMService
    from ytm_player.services.mediakeys import MediaKeysService
    from ytm_player.services.mpris import MPRISService
    from ytm_player.services.player import Player
    from ytm_player.services.queue import QueueManager
    from ytm_player.services.shuffle_prefs import ShufflePreferences
    from ytm_player.services.stream import StreamResolver
    from ytm_player.services.ytmusic import YTMusicService
    from ytm_player.ui.theme import ThemeColors

    class PageWidget(Protocol):
        """Structural type for page widgets mounted in #main-content.

        All concrete page classes (LibraryPage, SearchPage, etc. plus
        the internal _PlaceholderPage) inherit from ``textual.widget.Widget``
        and implement ``handle_action``/``get_nav_state``.  This Protocol
        captures the subset of the surface that the app mixins actually
        touch on a current page widget.
        """

        async def handle_action(self, action: Any, count: int = 1) -> None: ...
        def get_nav_state(self) -> dict[str, Any]: ...
        # ``query`` comes from Widget; redeclared here so callers that
        # have a PageWidget reference can scan for child widgets without
        # casting back to Widget.
        def query(self, selector: Any = ..., /) -> Any: ...

    class YTMHostBase(App[None]):
        """Type stub mirroring YTMPlayerApp's runtime instance surface.

        Mirrors every attribute set in ``YTMPlayerApp.__init__`` so
        Pyright stops complaining when a mixin reads ``self.player``,
        ``self.queue``, ``self.notify``, etc. in isolation.
        """

        # ── Configuration ──────────────────────────────────────────────
        settings: Settings
        keymap: KeyMap
        theme_colors: ThemeColors

        # ── Core services (initialized in on_mount; may be None pre-mount) ──
        ytmusic: YTMusicService | None
        player: Player | None
        queue: QueueManager
        stream_resolver: StreamResolver | None
        history: HistoryManager | None
        cache: CacheManager | None

        # ── Platform-specific media integrations ───────────────────────
        mpris: MPRISService | None
        mac_media: Any  # MacOSMediaService — Any to avoid platform import surprises
        mac_eventtap: Any  # MacOSEventTapService
        mediakeys: MediaKeysService | None

        # ── Optional integrations ──────────────────────────────────────
        discord: DiscordRPC | None
        lastfm: LastFMService | None
        downloader: DownloadService
        shuffle_prefs: ShufflePreferences

        # ── Key input state ────────────────────────────────────────────
        _key_buffer: list[str]
        _count_buffer: str

        # ── Page / navigation state ────────────────────────────────────
        _current_page: str
        _current_page_kwargs: dict[str, Any]
        _nav_stack: list[tuple[str, dict]]
        _forward_stack: list[tuple[str, dict]]
        _page_state_cache: dict[str, dict]
        _active_library_playlist_id: str | None
        _context_seq: int

        # ── Playback state tracking ────────────────────────────────────
        _track_start_position: float
        _consecutive_failures: int
        _advancing: bool
        _last_play_video_id: str
        _last_play_time: float
        _play_generation: int
        _ytm_reported_generation: int
        _local_history_claim: _LocalHistoryClaim | None
        _ytm_history: list[dict] | None
        _play_lock: asyncio.Lock

        # ── Pending resume from prior session ──────────────────────────
        _pending_resume_video_id: str | None
        _pending_resume_position: float

        # ── Lifecycle / IPC ────────────────────────────────────────────
        _poll_timer: Any
        _ipc_server: IPCServer | None

        # ── Sidebar state ──────────────────────────────────────────────
        _sidebar_default: bool
        _sidebar_per_page: dict[str, bool]
        _lyrics_sidebar_open: bool

        # ── Pane focus state (Ctrl+w h/l/w traversal) ──────────────────
        _active_pane: str

        # ── Onboarding state ───────────────────────────────────────────
        _first_run_hint_shown: bool
        _mpris_hint_shown: bool

        # ── Cross-mixin method declarations ────────────────────────────
        # Each mixin is analyzed by Pyright in isolation, so calls to
        # methods defined in *other* mixins look unknown.  Re-declare
        # those signatures here (stub-only) so Pyright resolves them
        # via the shared base.  Add an entry only when Pyright actually
        # flags a missing attribute — YAGNI.

        # Defined in PlaybackMixin (_playback.py)
        async def play_track(self, track: dict | None) -> None: ...
        async def _download_track(self, track: dict) -> None: ...
        async def _toggle_play_pause(self) -> None: ...
        async def _play_next(self, *, ended_track: dict | None = None) -> None: ...
        async def _play_previous(self) -> None: ...
        async def _toggle_like_current(self) -> None: ...
        async def _start_discovery_mix(self) -> None: ...
        async def _fetch_and_play_radio(
            self, seed_track: dict | list[dict], *, label: str | None = None, append: bool = False
        ) -> None: ...

        # Defined in NavigationMixin (_navigation.py)
        async def navigate_to(self, page_name: str, **kwargs: Any) -> None: ...
        def _get_current_page(self) -> PageWidget | None: ...
        def _purge_playlist_nav_state(self, dead_ids: set[str]) -> None: ...

        # Defined in SidebarMixin (_sidebar.py)
        def _toggle_playlist_sidebar(self) -> None: ...
        def _toggle_lyrics_sidebar(self) -> None: ...
        def _toggle_album_art(self) -> None: ...
        def _apply_playlist_sidebar(self, visible: bool) -> None: ...
        def _apply_lyrics_sidebar(self, visible: bool) -> None: ...
        def _focus_pane(self, pane: str) -> None: ...
        def _focus_pane_left(self) -> None: ...
        def _focus_pane_right(self) -> None: ...
        def _cycle_pane(self) -> None: ...
        def _current_pane(self) -> str: ...
        def _focus_content_widget(self) -> None: ...
        def _is_playlist_sidebar_visible(self) -> bool: ...
        def _ensure_playlist_sidebar_visible(self) -> None: ...
        def _visible_panes(self) -> list[str]: ...

        # Defined in TrackActionsMixin (_track_actions.py)
        async def _open_add_to_playlist(self) -> None: ...
        async def _open_track_actions(self) -> None: ...
        def _add_focused_to_queue(self) -> None: ...
        def _play_focused_next(self) -> None: ...
        def _open_actions_for_track(self, track: dict) -> None: ...
        def _refresh_queue_page(self) -> None: ...
        def _sync_shuffle_bar(self) -> None: ...
        async def _replace_queue_and_play(
            self,
            tracks: list[dict],
            *,
            entity_id: str | None = None,
            start_index: int = 0,
            shuffle: bool | None = None,
            autoplay: bool = True,
        ) -> None: ...
        def _append_to_queue(self, tracks: list[dict], label: str) -> None: ...
        async def _play_playlist(
            self,
            playlist_id: str,
            name: str,
            *,
            shuffle: bool | None = None,
            order: str | None = None,
        ) -> None: ...
        async def _add_playlist_to_queue(self, playlist_id: str, name: str) -> None: ...
        async def _start_playlist_radio(self, item: dict) -> None: ...
        async def _fetch_remaining_for_queue(
            self, playlist_id: str, already_have: int, *, order: str | None = None
        ) -> None: ...
        async def _dispatch_entity_action(
            self, action_id: str, item: dict, item_type: str
        ) -> bool: ...
        async def _toggle_artist_subscribe_simple(self, browse_id: str) -> None: ...

else:
    YTMHostBase = object  # noqa: PYI042 — runtime resolves to plain object
