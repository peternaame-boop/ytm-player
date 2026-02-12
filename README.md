# ytm-player

A full-featured YouTube Music TUI (Terminal User Interface) client for Linux, modeled after [spotify_player](https://github.com/aome510/spotify-player). Browse your library, search, queue tracks, and control playback — all from the terminal with vim-style keybindings.

Requires a **YouTube Music Premium** subscription for audio playback.

## Features

- **Full playback control** — play, pause, seek, volume, shuffle, repeat via mpv
- **7 pages** — Library, Search, Browse (For You, Charts, Moods), Context (album/artist/playlist), Lyrics, Queue, Help
- **Vim-style navigation** — `j`/`k` movement, multi-key sequences (`g l` for library, `g s` for search), count prefixes (`5j`)
- **Predictive search** — debounced with 300ms delay, music-first mode with toggle to all results
- **Spotify import** — import playlists from Spotify via API or URL scraping
- **History tracking** — play history + search history stored in SQLite with listening stats
- **Audio caching** — LRU cache (1GB default) for offline-like replay of previously heard tracks
- **Album art** — colored half-block rendering in the playback bar (requires Pillow)
- **MPRIS integration** — hardware media keys and desktop player controls via D-Bus
- **CLI mode** — headless subcommands for scripting (`ytm search`, `ytm stats`, `ytm history`)
- **IPC control** — control the running TUI from another terminal (`ytm play`, `ytm pause`, `ytm next`)
- **Fully configurable** — TOML config files for settings, keybindings, and theme

## Requirements

- **Python 3.12+**
- **[mpv](https://mpv.io/)** — audio playback backend, must be installed system-wide
- **YouTube Music Premium** subscription

## Installation

### 1. Install mpv

mpv is required for audio playback. Install it with your system package manager:

```bash
# Arch / CachyOS / Manjaro
sudo pacman -S mpv

# Ubuntu / Debian
sudo apt install mpv

# Fedora
sudo dnf install mpv

# macOS (Homebrew)
brew install mpv
```

### 2. Install ytm-player

```bash
git clone https://github.com/peternaame-boop/ytm-player.git
cd ytm-player
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

#### Optional extras

```bash
# Spotify playlist import
pip install -e ".[spotify]"

# MPRIS media key support (Linux only, requires D-Bus)
pip install -e ".[mpris]"

# Album art rendering (colored half-block images)
pip install -e ".[images]"

# All optional features
pip install -e ".[spotify,mpris,images]"

# Development tools (pytest, ruff)
pip install -e ".[dev]"
```

### 3. Authenticate

```bash
ytm setup
```

The setup wizard will attempt to auto-extract cookies from your browser (Chrome, Firefox, Brave, Edge). If auto-detection fails, it will prompt you to manually paste request headers from YouTube Music:

1. Open [music.youtube.com](https://music.youtube.com) in your browser
2. Open DevTools (F12) → Network tab
3. Click any request to `music.youtube.com`
4. Copy the request headers (specifically the `Cookie` and `Authorization` headers)
5. Paste them into the setup wizard

Credentials are stored in `~/.config/ytm-player/` with `0o600` permissions.

## Usage

### TUI (interactive)

```bash
# Launch the player
ytm
```

### CLI (headless)

These work without the TUI running:

```bash
# Search YouTube Music
ytm search "daft punk"
ytm search "bohemian rhapsody" --filter songs --json

# Listening stats
ytm stats
ytm stats --json

# Play history
ytm history
ytm history search

# Cache management
ytm cache status
ytm cache clear

# Spotify import
ytm import "https://open.spotify.com/playlist/..."
```

### Playback control (requires TUI running)

Control the running TUI from another terminal via IPC:

```bash
ytm play          # Resume playback
ytm pause         # Pause playback
ytm next          # Skip to next track
ytm prev          # Previous track
ytm seek +10      # Seek forward 10 seconds
ytm seek -5       # Seek backward 5 seconds
ytm seek 1:30     # Seek to 1:30

ytm now            # Current track info (JSON)
ytm status         # Player status (JSON)
ytm queue          # Queue contents (JSON)
ytm queue add ID   # Add track by video ID
ytm queue clear    # Clear queue
```

## Keybindings

| Key | Action |
|-----|--------|
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
| `right-click` | Context menu on tracks |
| `/` | Filter current list |
| `Ctrl+r` | Cycle repeat mode |
| `Ctrl+s` | Toggle shuffle |
| `backspace` | Go back |
| `q` | Quit |

Custom keybindings: edit `~/.config/ytm-player/keymap.toml`

## Configuration

Config files live in `~/.config/ytm-player/` (respects `$XDG_CONFIG_HOME`):

| File | Purpose |
|------|---------|
| `config.toml` | General settings, playback, cache, UI |
| `keymap.toml` | Custom keybinding overrides |
| `theme.toml` | Color scheme customization |
| `headers_auth.json` | YouTube Music credentials (auto-generated) |

Open config directory in your editor:

```bash
ytm config
```

### Example `config.toml`

```toml
[general]
startup_page = "library"     # library, search, browse

[playback]
audio_quality = "high"       # high, medium, low
default_volume = 80          # 0-100
autoplay = true
seek_step = 5                # seconds per seek

[cache]
enabled = true
max_size_mb = 1024           # 1GB default
prefetch_next = true

[ui]
album_art = true
progress_style = "block"     # block or line
sidebar_width = 30

[notifications]
enabled = true
timeout_seconds = 5

[mpris]
enabled = true
```

### Example `theme.toml`

```toml
[colors]
background = "#0f0f0f"
foreground = "#ffffff"
primary = "#ff0000"
secondary = "#aaaaaa"
accent = "#ff4e45"
success = "#2ecc71"
warning = "#f39c12"
error = "#e74c3c"
muted_text = "#999999"
border = "#333333"
selected_item = "#2a2a2a"
progress_filled = "#ff0000"
progress_empty = "#555555"
playback_bar_bg = "#1a1a1a"
```

## Architecture

```
src/ytm_player/
├── app.py              # Main Textual application
├── cli.py              # Click CLI entry point
├── ipc.py              # Unix socket IPC for CLI↔TUI communication
├── config/             # Settings, keymap, theme (TOML)
├── services/           # Backend services
│   ├── auth.py         #   Browser cookie auth
│   ├── ytmusic.py      #   YouTube Music API wrapper
│   ├── player.py       #   mpv audio playback
│   ├── stream.py       #   yt-dlp stream URL resolution
│   ├── queue.py        #   Playback queue with shuffle/repeat
│   ├── history.py      #   SQLite play/search history
│   ├── cache.py        #   LRU audio file cache
│   ├── mpris.py        #   D-Bus MPRIS media controls
│   └── spotify_import.py  # Spotify playlist import
├── ui/
│   ├── playback_bar.py # Persistent bottom bar (track info, progress, controls)
│   ├── theme.py        # Theme system with CSS variable generation
│   ├── pages/          # Library, Search, Browse, Context, Lyrics, Queue, Help
│   ├── popups/         # Actions menu, playlist picker, Spotify import
│   └── widgets/        # TrackTable, PlaybackProgress, AlbumArt
└── utils/              # Terminal detection, formatting helpers
```

**Stack:** [Textual](https://textual.textualize.io/) (TUI) · [ytmusicapi](https://github.com/sigma67/ytmusicapi) (API) · [yt-dlp](https://github.com/yt-dlp/yt-dlp) (streams) · [python-mpv](https://github.com/jaseg/python-mpv) (playback) · [aiosqlite](https://github.com/omnilib/aiosqlite) (history/cache) · [dbus-next](https://github.com/altdesktop/python-dbus-next) (MPRIS)

## Troubleshooting

### "mpv not found" or playback doesn't start

Ensure mpv is installed and in your `$PATH`:

```bash
mpv --version
```

If installed but not found, check that the `libmpv` shared library is available:

```bash
# Arch
pacman -Qs mpv

# Ubuntu/Debian — you may need the dev package
sudo apt install libmpv-dev
```

### Authentication fails

- Make sure you're signed in to YouTube Music Premium in your browser
- Try a different browser: `ytm setup` auto-detects Chrome, Firefox, Brave, and Edge
- If auto-detection fails, use the manual paste method
- Re-run `ytm setup` to re-authenticate

### No sound / wrong audio device

mpv uses your system's default audio output. To change it, create `~/.config/mpv/mpv.conf`:

```
audio-device=pulse/your-device-name
```

List available devices with `mpv --audio-device=help`.

### MPRIS / media keys not working

Install the optional MPRIS dependency:

```bash
pip install -e ".[mpris]"
```

Requires D-Bus (standard on most Linux desktops). Verify with:

```bash
dbus-send --session --print-reply --dest=org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus.ListNames
```

### Cache taking too much space

```bash
# Check cache size
ytm cache status

# Clear all cached audio
ytm cache clear
```

Or reduce the limit in `config.toml`:

```toml
[cache]
max_size_mb = 512
```

## License

MIT — see [LICENSE](LICENSE).

## Changelog

### v1.0.0 (2026-02-07)

- Initial release
- Full TUI with 7 pages (Library, Search, Browse, Context, Lyrics, Queue, Help)
- Vim-style keybindings with multi-key sequences and count prefixes
- Audio playback via mpv with shuffle, repeat, queue management
- Predictive search with music-first mode
- Spotify playlist import (API + scraper)
- Play and search history in SQLite
- Audio cache with LRU eviction (1GB default)
- Album art with colored half-block rendering
- MPRIS D-Bus integration for media key support
- Unix socket IPC for CLI↔TUI control
- CLI subcommands for headless usage
- TOML configuration for settings, keybindings, and theme
