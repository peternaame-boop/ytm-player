"""Type-checking base class for app mixins.

At runtime this module exposes ``YTMHostBase = object`` so that mixins
inherit from ``object`` and behavior is unchanged.  Under
``TYPE_CHECKING`` it exposes a typed stub class describing
``YTMPlayerApp``'s full attribute surface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Protocol

    from textual.app import App
    from textual.widget import Widget

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
    from ytm_player.services.stream import StreamResolver
    from ytm_player.services.ytmusic import YTMusicService
    from ytm_player.ui.theme import ThemeColors

    class PageWidget(Protocol):
        async def handle_action(self, action: Any, count: int = 1) -> None: ...
        def get_nav_state(self) -> dict[str, Any]: ...
        def query(self, selector: Any = ..., /) -> Any: ...

    class YTMHostBase(App[None]):
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
        mac_media: Any
        mac_eventtap: Any
        mediakeys: MediaKeysService | None

        # ── Optional integrations ──────────────────────────────────────
        discord: DiscordRPC | None
        lastfm: LastFMService | None
        downloader: DownloadService

        # ── Key input state ────────────────────────────────────────────
        _key_buffer: list[str]
        _count_buffer: str

        # ── Page / navigation state ────────────────────────────────────
        _current_page: str
        _current_page_kwargs: dict[str, Any]
        _nav_stack: list[tuple[str, dict]]
        _page_state_cache: dict[str, dict]
        _active_library_playlist_id: str | None

        # ── Playback state tracking ────────────────────────────────────
        _track_start_position: float
        _consecutive_failures: int
        _advancing: bool
        _last_play_video_id: str
        _last_play_time: float

        # ── Lifecycle / IPC ────────────────────────────────────────────
        _poll_timer: Any
        _ipc_server: IPCServer | None
        _clean_exit: bool

        # ── Sidebar state ──────────────────────────────────────────────
        _sidebar_default: bool
        _sidebar_per_page: dict[str, bool]
        _lyrics_sidebar_open: bool

        # ── Cross-mixin method declarations ────────────────────────────
        # Defined in PlaybackMixin (_playback.py)
        async def play_track(self, track: dict) -> None: ...
        async def _download_track(self, track: dict) -> None: ...
        async def _toggle_play_pause(self) -> None: ...
        async def _play_next(self, *, ended_track: dict | None = None) -> None: ...
        async def _play_previous(self) -> None: ...
        async def _toggle_like_current(self) -> None: ...
        async def _start_discovery_mix(self) -> None: ...
        async def _start_playlist_radio(self, item: dict) -> None: ...
        async def _fetch_and_play_radio(self, seed_track: dict | None = None) -> None: ...

        # Defined in NavigationMixin (_navigation.py)
        async def navigate_to(self, page_name: str, **kwargs: Any) -> None: ...
        def _get_current_page(self) -> Widget | None: ...

        # Defined in SidebarMixin (_sidebar.py)
        def _toggle_playlist_sidebar(self) -> None: ...
        def _toggle_lyrics_sidebar(self) -> None: ...
        def _toggle_album_art(self) -> None: ...
        def _apply_playlist_sidebar(self, visible: bool) -> None: ...
        def _apply_lyrics_sidebar(self, visible: bool) -> None: ...

        # Defined in TrackActionsMixin (_track_actions.py)
        async def _open_add_to_playlist(self) -> None: ...
        async def _open_track_actions(self) -> None: ...
        def _open_actions_for_track(self, track: dict) -> None: ...
        def _refresh_queue_page(self) -> None: ...
        async def _start_radio_for(self, track: dict) -> None: ...

else:
    YTMHostBase = object  # noqa: PYI042
