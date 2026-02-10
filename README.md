# ytm-player

A full-featured YouTube Music TUI (Terminal User Interface) client for Linux, modeled after [spotify_player](https://github.com/aome510/spotify-player). Browse your library, search, queue tracks, and control playback — all from the terminal with vim-style keybindings.

Requires a **YouTube Music Premium** subscription for audio playback.

## Features

- **Full playback control** — play, pause, seek, volume, shuffle, repeat via mpv
- **7 pages** — Library, Search, Browse (For You, Charts, Moods), Context (album/artist/playlist), Lyrics, Queue, Help
- **Vim-style navigation** — `j`/`k` movement, multi-key sequences (`g l` for library, `g s` for search), count prefixes (`5j`)
- **Predictive search** — debounced with 300ms delay, music-first mode with toggle to all results
- **History tracking** — play history + search history stored in SQLite with listening stats
- **Audio caching** — LRU cache (1GB default) for offline-like replay of previously heard tracks
- **MPRIS integration** — hardware media keys and desktop player controls via D-Bus
- **CLI mode** — headless subcommands for scripting (`ytm search`, `ytm stats`, `ytm history`)
- **Fully configurable** — TOML config files for settings, keybindings, and theme

## Requirements

- Python 3.12+
- [mpv](https://mpv.io/) installed and available in `$PATH`
- YouTube Music Premium subscription

## Installation

```bash
# Clone and install
cd ytm-player
python -m venv .venv
source .venv/bin/activate
pip install -e .

# First-time setup — authenticate with YouTube Music
ytm setup
```

The `ytm setup` wizard will guide you through extracting browser cookies from YouTube Music (DevTools → Network → copy request headers).

## Usage

```bash
# Launch the TUI
ytm

# CLI subcommands (work without TUI running)
ytm search "daft punk"
ytm stats
ytm history
ytm cache status

# Playback control (requires TUI running)
ytm play
ytm pause
ytm next
ytm prev
```

## Keybindings

| Key | Action |
|---|---|
| `space` | Play/Pause |
| `n` | Next track |
| `p` | Previous track |
| `+` / `-` | Volume up/down |
| `j` / `k` | Move down/up |
| `enter` | Select/play |
| `g l` | Go to Library |
| `g s` | Go to Search |
| `g b` | Go to Browse |
| `z` | Go to Queue |
| `l` | Go to Lyrics |
| `?` | Help (full keybinding reference) |
| `tab` | Focus next panel |
| `a` | Track actions menu |
| `/` | Filter current list |
| `Ctrl+r` | Cycle repeat mode |
| `Ctrl+s` | Toggle shuffle |
| `q` | Quit |

Custom keybindings: `~/.config/ytm-player/keymap.toml`

## Configuration

Config files live in `~/.config/ytm-player/`:

- `config.toml` — general settings, playback, cache, UI
- `keymap.toml` — custom keybinding overrides
- `theme.toml` — color scheme customization

Open config in your editor: `ytm config`

## Architecture

```
src/ytm_player/
├── app.py              # Main Textual application
├── cli.py              # Click CLI entry point
├── ipc.py              # PID file for CLI↔TUI communication
├── config/             # Settings, keymap, theme (TOML)
├── services/           # Backend: auth, ytmusic, player, stream, queue, history, cache, mpris
├── ui/
│   ├── playback_bar.py # Persistent bottom bar (track info, progress, volume)
│   ├── pages/          # Library, Search, Browse, Context, Lyrics, Queue, Help
│   ├── popups/         # Actions menu, playlist picker, search filter
│   └── widgets/        # TrackTable, PlaybackProgress, AlbumArt
└── utils/              # Terminal detection, formatting helpers
```

**Stack:** Textual (TUI) · ytmusicapi (API) · yt-dlp (stream resolution) · python-mpv (playback) · aiosqlite (history/cache) · dbus-next (MPRIS)

## Changelog

### v1.0.0 (2026-02-07)
- Initial release
- Full TUI with 7 pages (Library, Search, Browse, Context, Lyrics, Queue, Help)
- Vim-style keybindings with multi-key sequences and count prefixes
- Audio playback via mpv with shuffle, repeat, queue management
- Predictive search with music-first mode
- Play and search history in SQLite
- Audio cache with LRU eviction (1GB default)
- MPRIS D-Bus integration for media key support
- CLI subcommands for headless usage
- TOML configuration for settings, keybindings, and theme
