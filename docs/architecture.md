# Architecture

For users curious about how the app is organized, and contributors getting started. For dev workflow (lint, test, ruff order, etc.) see [CONTRIBUTING.md](../CONTRIBUTING.md).

## File tree

```
src/ytm_player/
├── app/                # Main Textual application (mixin package)
│   ├── _app.py         #   Class def, __init__, compose, lifecycle
│   ├── _base.py        #   YTMHostBase TYPE_CHECKING stub for Pyright (mixin attrs)
│   ├── _playback.py    #   play_track, player events, history, download
│   ├── _keys.py        #   Key handling and action dispatch
│   ├── _sidebar.py     #   Sidebar toggling and playlist sidebar events
│   ├── _navigation.py  #   Page navigation and nav stack
│   ├── _ipc.py         #   IPC command handling for CLI
│   ├── _track_actions.py  # Track selection, actions popup, radio
│   ├── _session.py     #   Session save/restore (queue, volume, last-playing track)
│   └── _mpris.py       #   MPRIS/media key callbacks
├── cli.py              # Click CLI entry point
├── ipc.py              # Unix socket IPC for CLI ↔ TUI
├── config/             # Settings, keymap, theme (TOML)
├── services/           # Backend services
│   ├── auth.py         #   Browser cookie auth (multi-account aware)
│   ├── ytmusic.py      #   YouTube Music API wrapper
│   ├── player.py       #   mpv audio playback
│   ├── stream.py       #   yt-dlp stream URL resolution
│   ├── queue.py        #   Playback queue with shuffle/repeat
│   ├── history.py      #   SQLite play/search history
│   ├── cache.py        #   LRU audio file cache
│   ├── lrclib.py       #   LRCLIB.net synced lyrics fallback (with title sanitization)
│   ├── mpris.py        #   D-Bus MPRIS media controls (Linux)
│   ├── macos_media.py  #   macOS Now Playing integration
│   ├── macos_eventtap.py  # macOS hardware media key interception
│   ├── mediakeys.py    #   Cross-platform media key service
│   ├── download.py     #   Offline audio downloads
│   ├── discord_rpc.py  #   Discord Rich Presence
│   ├── lastfm.py       #   Last.fm scrobbling
│   ├── yt_dlp_options.py  # yt-dlp config/cookie handling
│   └── spotify_import.py  # Spotify playlist import
├── ui/
│   ├── header_bar.py   # Top bar with sidebar toggle buttons
│   ├── playback_bar.py # Persistent bottom bar (track info, progress, controls, heart)
│   ├── theme.py        # Textual theme integration + app-specific color overrides
│   ├── sidebars/       # Persistent playlist sidebar (left) and lyrics sidebar (right)
│   ├── pages/          # Library, Search, Browse, Context, Queue, Liked Songs, Recently Played, Help
│                       #   Queue/Liked/Recent share the project's TrackTable widget
│                       #   (right-click context menu, filter, sort, play indicator)
│   ├── popups/         # Actions menu, playlist picker, Spotify import, country picker (charts region — 68 entries with Global default)
│   └── widgets/        # TrackTable, PlaybackProgress, AlbumArt
└── utils/              # Terminal detection, formatting, BiDi text, transliteration
```

## Stack

| Library | Purpose |
|---------|---------|
| [Textual](https://textual.textualize.io/) | TUI framework |
| [ytmusicapi](https://github.com/sigma67/ytmusicapi) | YouTube Music HTTP API |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Stream URL resolution + offline downloads |
| [python-mpv](https://github.com/jaseg/python-mpv) | mpv playback (libmpv wrapper) |
| [aiosqlite](https://github.com/omnilib/aiosqlite) | Async SQLite for history + cache index |
| [click](https://click.palletsprojects.com/) | CLI framework |
| [Pillow](https://python-pillow.org/) | Album art rendering (downscaled to terminal half-blocks) |
| [dbus-fast](https://github.com/Bluetooth-Devices/dbus-fast) | MPRIS D-Bus (Linux) — optional |
| [pypresence](https://github.com/qwertyquerty/pypresence) | Discord Rich Presence — optional |
| [pylast](https://github.com/pylast/pylast) | Last.fm scrobbling — optional |

## Key patterns

- **Mixin architecture** — `YTMPlayerApp` is composed from 8 mixins (Playback, Session, Keys, Navigation, Sidebar, TrackActions, MPRIS, IPC). Each mixin extends `YTMHostBase` (in `app/_base.py`), a `TYPE_CHECKING`-only stub class that mirrors the runtime instance attribute surface so Pyright doesn't emit `Cannot access attribute X for class FooMixin` noise. At runtime `YTMHostBase = object` — zero behaviour change.
- **Event-driven playback** — `Player` emits `PlayerEvent` enums (`TRACK_END`, `TRACK_CHANGE`, etc.) dispatched to the Textual event loop via `call_soon_threadsafe`. The app registers callbacks to update the UI.
- **Thread safety** — `Player` and `QueueManager` are singletons with `threading.Lock`. Player events bridge from mpv's callback thread to asyncio.
- **Track format** — All services use a standardized track dict (`video_id`, `title`, `artist`, `artists`, `album`, `album_id`, `duration`, `thumbnail_url`, `is_video`). The `normalize_tracks()` helper in `utils/formatting.py` converts inconsistent ytmusicapi response shapes into this format.
- **Session persistence** — Volume, queue, shuffle/repeat, theme, and the last-playing track + position are saved on every exit to `session.json`. When `[playback] resume_on_launch` is true (default), the last-playing track + position are staged into the playback bar at launch and consumed the first time the user presses play. Per-playlist Shuffle lock state is stored separately in `shuffle_prefs.json`.
- **Playback bar keybindings** — Standard transport keys plus `l` to toggle the like state of the currently playing track.
- **Prefetching** — Next track's stream URL is resolved in the background for instant skip.
- **Page navigation** — `app/_navigation.py` manages a nav stack (max 20). Each page implements `handle_action(action, count)` for vim-style keybinding dispatch and `get_nav_state()` for state preservation across navigation.
- **LC_NUMERIC quirk** — `cli.py` forces `LC_NUMERIC=C` at import time — mpv segfaults without it.
