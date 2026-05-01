# ytm-player

A full-featured YouTube Music player for the terminal. Browse your library, search, queue tracks, and control playback — all from a TUI with vim-style keybindings. Runs on Linux, macOS, and Windows.

![ytm-player screenshot](https://raw.githubusercontent.com/peternaame-boop/ytm-player/master/screenshot-v4.png)

## Features

- **Full playback control** — play, pause, seek, volume, shuffle, repeat via mpv with gapless audio and stream prefetching
- **Cross-platform** — Linux, macOS, and Windows with platform-native integrations on each
- **Persistent sidebars** — playlist sidebar (left) visible across all views, synced lyrics sidebar (right) with auto-scroll and click-to-seek, both toggleable from header bar
- **Synced lyrics** — real-time highlighted lyrics with LRCLIB.net fallback, ASCII transliteration toggle (`T`) for non-Latin scripts
- **8 pages** — Library, Search, Browse, Context (album/artist/playlist), Queue, Liked Songs, Recently Played, Help — all with state preservation across navigation
- **Vim-style navigation** — `j`/`k` movement, multi-key sequences (`g l` for library, `g s` for search), count prefixes (`5j`)
- **Table sorting** — click column headers or use keyboard (`s t`/`s a`/`s A`/`s d`/`s r`), drag-to-resize columns
- **Predictive search** — debounced with 300ms delay, music-first mode with clickable toggle to all results
- **Theming** — 18 built-in Textual themes (nord, dracula, gruvbox, catppuccin, etc.) via `Ctrl+P`, plus custom app-specific color overrides in `theme.toml`. Theme selection persists across sessions
- **Session resume** — restores queue position, volume, shuffle/repeat state, theme on startup
- **Free-tier support** — works with free YouTube Music accounts (Premium-only tracks are filtered with notice)
- **Multi-account & Brand Account support** — `ytm setup` auto-detects multiple Google accounts; Brand Accounts configurable via `brand_account_id` in config
- **Spotify import** — import playlists from Spotify via API or URL scraping
- **History tracking** — play history + search history stored in SQLite with listening stats
- **Audio caching** — LRU cache (1GB default) for offline-like replay of previously heard tracks
- **Progressive loading** — large playlists (1500+ tracks) load instantly with background fetching
- **Offline downloads** — right-click any track → "Download for Offline" to save locally
- **Discord Rich Presence** — show what you're listening to in your Discord status
- **Last.fm scrobbling** — automatic scrobbling with Now Playing updates
- **Album art** — colored half-block rendering in the playback bar, toggleable with `Ctrl+A`
- **Media keys** — MPRIS/D-Bus on Linux, native Now Playing + Quartz event taps on macOS, pynput on Windows
- **CLI mode** — headless subcommands for scripting (`ytm search`, `ytm stats`, `ytm history`, `ytm like`)
- **IPC control** — control the running TUI from another terminal (`ytm play`, `ytm pause`, `ytm next`, `ytm like`)
- **yt-dlp integration** — cookie file auth, configurable `remote_components` and `js_runtimes`
- **Fully configurable** — TOML config files for settings, keybindings, and theme

## Requirements

- **Python 3.12+**
- **[mpv](https://mpv.io/)** — audio playback backend, must be installed system-wide
- A **YouTube Music** account (free or Premium)

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

# NixOS — handled by the flake (see NixOS section below)

# macOS (Homebrew)
brew install mpv

# Windows — see "Windows Setup" section below for full instructions
scoop install mpv
```

### 2. Install ytm-player

#### Arch Linux / CachyOS / EndeavourOS / Manjaro (AUR)

```bash
yay -S ytm-player-git
```

Or with any other AUR helper. Package: [ytm-player-git](https://aur.archlinux.org/packages/ytm-player-git)

#### Gentoo ([GURU](https://wiki.gentoo.org/wiki/Project:GURU))

Enable the repository as read in [Project:GURU/Information for End Users](https://wiki.gentoo.org/wiki/Project:GURU/Information_for_End_Users) then emerge the package:

```bash
emerge --ask media-sound/ytm-player
```

#### PyPI (Linux / macOS)

```bash
pip install ytm-player
```

#### Windows

```powershell
pip install ytm-player
```

Then run with:

```powershell
py -m ytm_player
```

> `pip install` on Windows does not add the `ytm` command to PATH. Use `py -m ytm_player` to launch — this always works. Alternatively, install with [pipx](https://pipx.pypa.io/) which handles PATH automatically: `pipx install ytm-player`

> **Important:** Windows requires extra mpv setup — see [Windows Setup](#windows-setup) below.

#### From source

```bash
git clone https://github.com/peternaame-boop/ytm-player.git
cd ytm-player
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

#### NixOS (Flake)

ytm-player provides a `flake.nix` with two packages, a dev shell, and an overlay.

**Try it without installing:**

```bash
nix run github:peternaame-boop/ytm-player
```

**Add to your system flake (`flake.nix`):**

```nix
{
  inputs.ytm-player.url = "github:peternaame-boop/ytm-player";

  outputs = { nixpkgs, ytm-player, ... }: {
    nixosConfigurations.myhost = nixpkgs.lib.nixosSystem {
      modules = [
        {
          nixpkgs.overlays = [ ytm-player.overlays.default ];
          environment.systemPackages = with pkgs; [
            ytm-player          # core (MPRIS + album art included)
            # ytm-player-full   # all features (Discord, Last.fm, Spotify import)
          ];
        }
      ];
    };
  };
}
```

**Or install imperatively with `nix profile`:**

```bash
# Core
nix profile install github:peternaame-boop/ytm-player

# All features (Discord, Last.fm, Spotify import, etc.)
nix profile install github:peternaame-boop/ytm-player#ytm-player-full
```

**Dev shell** (for contributors):

```bash
git clone https://github.com/peternaame-boop/ytm-player.git
cd ytm-player
nix develop  # drops you into a shell with all deps + dev tools
```

> **Note:** If you install via `pip` instead of the flake, NixOS doesn't expose `libmpv.so` in standard library paths. Add to your shell config:
> ```fish
> # Fish
> set -gx LD_LIBRARY_PATH /run/current-system/sw/lib $LD_LIBRARY_PATH
> ```
> ```bash
> # Bash/Zsh
> export LD_LIBRARY_PATH="/run/current-system/sw/lib:$LD_LIBRARY_PATH"
> ```
> The flake handles this automatically — no manual `LD_LIBRARY_PATH` needed.

#### Optional extras (pip)

```bash
# Spotify playlist import
pip install "ytm-player[spotify]"

# MPRIS media key support (Linux only, requires D-Bus)
pip install "ytm-player[mpris]"

# Discord Rich Presence
pip install "ytm-player[discord]"

# Last.fm scrobbling
pip install "ytm-player[lastfm]"

# Lyrics transliteration (non-Latin scripts → ASCII)
pip install "ytm-player[transliteration]"

# All optional features
pip install "ytm-player[spotify,mpris,discord,lastfm,transliteration]"

# Development tools (pytest, ruff)
pip install -e ".[dev]"
```

#### Optional extras (AUR)

If you installed via AUR, install optional dependencies with pacman/yay — **not** pip (pip won't work on Arch due to [PEP 668](https://peps.python.org/pep-0668/)):

```bash
# MPRIS media key support (Linux)
sudo pacman -S python-dbus-fast

# Last.fm scrobbling
yay -S python-pylast

# Discord Rich Presence
yay -S python-pypresence

# Spotify playlist import
yay -S python-spotipy python-thefuzz
```

### Windows Setup

On Linux and macOS, `mpv` packages include the shared library that ytm-player needs. On Windows, `scoop install mpv` (and most other methods) only install the **player executable** — the `libmpv-2.dll` library must be downloaded separately.

**Steps:**

1. Install mpv: `scoop install mpv` (or [download from mpv.io](https://mpv.io/installation/))
2. Install 7zip if you don't have it: `scoop install 7zip`
3. Download the latest **`mpv-dev-x86_64-*.7z`** from [shinchiro's mpv builds](https://github.com/shinchiro/mpv-winbuild-cmake/releases) (the file starting with `mpv-dev`, not just `mpv`)
4. Extract `libmpv-2.dll` into your mpv directory:

```powershell
# Adjust the filename to match what you downloaded
7z e "$env:TEMP\mpv-dev-x86_64-*.7z" -o"$env:USERPROFILE\scoop\apps\mpv\current" libmpv-2.dll -y
```

If you installed mpv a different way, place `libmpv-2.dll` next to `mpv.exe` or anywhere on your `%PATH%`.

ytm-player automatically searches common install locations (scoop, chocolatey, Program Files) for the DLL.

### 3. Authenticate

```bash
ytm setup                    # Auto-detect browser cookies
ytm setup --browser firefox  # Target a specific browser
ytm setup --manual           # Skip detection, paste headers directly
```

Windows: replace `ytm` with `py -m ytm_player`.

The setup wizard has three modes:

**Automatic (default):** If `[yt_dlp].cookies_file` is set, setup first tries that Netscape cookies file (same format as `yt-dlp --cookies FILE`). If not configured or invalid, it scans installed browsers (Helium, Chrome, Chromium, Brave, Firefox, Edge, Vivaldi, Opera) for YouTube Music cookies.

**Browser-specific (`--browser <name>`):** Extract cookies from a specific browser — useful when auto-detect picks the wrong one. Supports: `chrome`, `firefox`, `brave`, `edge`, `chromium`, `vivaldi`, `opera`.

**Manual (`--manual`):** Skip all browser detection and paste raw request headers directly:

1. Open [music.youtube.com](https://music.youtube.com) in your browser
2. Open DevTools (F12) → Network tab
3. Refresh the page, filter requests by `/browse`
4. Click a `music.youtube.com` request
5. Right-click "Request Headers" → Copy
6. Paste into the wizard and press Enter on an empty line

The wizard accepts multiple paste formats (Chrome alternating lines, Firefox `Name: Value`, terminal escape-separated).

Credentials are stored in `~/.config/ytm-player/auth.json` with `0o600` permissions.

> ⚠️ `remote_components` allows fetching external JS components (npm/GitHub). Enable it only if you trust the source and network path.

## Usage

### TUI (interactive)

```bash
ytm                # Linux / macOS
py -m ytm_player   # Windows
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

ytm like           # Like current track
ytm dislike        # Dislike current track
ytm unlike         # Remove like/dislike

ytm now            # Current track info (JSON)
ytm status         # Player status (JSON)
ytm queue          # Queue contents (JSON)
ytm queue add ID   # Add track by video ID
ytm queue clear    # Clear queue
```

## Keybindings

### Keyboard

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
| `g L` | Toggle lyrics sidebar |
| `Ctrl+e` | Toggle playlist sidebar |
| `g y` | Go to Liked Songs |
| `g r` | Go to Recently Played |
| `?` | Help (full keybinding reference) |
| `tab` | Focus next panel |
| `a` | Track actions menu |
| `/` | Filter current list |
| `Ctrl+r` | Cycle repeat mode |
| `Ctrl+s` | Toggle shuffle |
| `backspace` | Go back |
| `s t` / `s a` / `s A` / `s d` | Sort by Title / Artist / Album / Duration |
| `s r` | Reverse current sort |
| `T` | Toggle lyrics transliteration (ASCII) |
| `Ctrl+a` | Toggle album art |
| `Ctrl+p` | Change theme |
| `q` | Quit |

### Mouse

| Action | Where | Effect |
|--------|-------|--------|
| Click | Progress bar | Seek to position |
| Scroll up/down | Progress bar | Scrub forward/backward (commits after 0.6s pause) |
| Scroll up/down | Volume display | Adjust volume by 5% |
| Click | Repeat button | Cycle repeat mode (off → all → one) |
| Click | Shuffle button | Toggle shuffle on/off |
| Click | Footer buttons | Navigate pages, play/pause, prev/next |
| Right-click | Track row | Open context menu (play, queue, add to playlist, etc.) |

Custom keybindings: edit `~/.config/ytm-player/keymap.toml`

## Configuration

Config files live in `~/.config/ytm-player/` (respects `$XDG_CONFIG_HOME`):

| File | Purpose |
|------|---------|
| `config.toml` | General settings, playback, cache, UI |
| `keymap.toml` | Custom keybinding overrides |
| `theme.toml` | Color scheme customization |
| `auth.json` | YouTube Music credentials (auto-generated) |

Open config directory in your editor:

```bash
ytm config
```

### Example `config.toml`

```toml
[general]
startup_page = "library"     # library, search, browse
brand_account_id = ""        # YouTube Brand Account ID (21-digit number, find at myaccount.google.com/brandaccounts)

[playback]
audio_quality = "high"       # high, medium, low
default_volume = 80          # 0-100
autoplay = true
seek_step = 5                # seconds per seek

[cache]
enabled = true
max_size_mb = 1024           # 1GB default
prefetch_next = true

[yt_dlp]
cookies_file = ""            # Optional: path to yt-dlp Netscape cookies.txt
remote_components = ""       # Optional: ejs:npm/ejs:github (enables remote component downloads)
js_runtimes = ""             # Optional: bun or bun:/path/to/bun (also node/quickjs forms)

[ui]
album_art = true
progress_style = "block"     # block or line
sidebar_width = 30
col_index = 4                # 0 = auto-fill
col_title = 0                # 0 = auto-fill
col_artist = 30
col_album = 25
col_duration = 8
bidi_mode = "auto"           # auto, reorder, passthrough (RTL text handling)

[notifications]
enabled = true
timeout_seconds = 5

[mpris]
enabled = true

[discord]
enabled = false              # Requires pypresence

[lastfm]
enabled = false              # Requires pylast
api_key = ""
api_secret = ""
session_key = ""
username = ""
```

### Example `theme.toml`

Base colors (primary, background, etc.) come from the active Textual theme — switch themes with `Ctrl+P`. The `theme.toml` file overrides app-specific colors only:

```toml
[colors]
playback_bar_bg = "#1a1a1a"
selected_item = "#2a2a2a"
progress_filled = "#ff0000"
progress_empty = "#555555"
lyrics_played = "#999999"
lyrics_current = "#2ecc71"
lyrics_upcoming = "#aaaaaa"
active_tab = "#ffffff"
inactive_tab = "#999999"
```

## Spotify Import

Import your Spotify playlists into YouTube Music — from the TUI or CLI.

![Spotify import popup](https://raw.githubusercontent.com/peternaame-boop/ytm-player/master/screenshot-spotify-import.png)

### How it works

1. **Extract** — Reads track names and artists from the Spotify playlist
2. **Match** — Searches YouTube Music for each track using fuzzy matching (title 60% + artist 40% weighted score)
3. **Resolve** — Tracks scoring 85%+ are auto-matched. Lower scores prompt you to pick from candidates or skip
4. **Create** — Creates a new private playlist on your YouTube Music account with all matched tracks

### Two modes

| Mode | Use case | How |
|------|----------|-----|
| **Single** (≤100 tracks) | Most playlists | Paste one Spotify URL |
| **Multi** (100+ tracks) | Large playlists split across parts | Enter a name + number of parts, paste a URL for each |

### From the TUI

Click **Import** in the footer bar (or press the import button). A popup lets you paste URLs, choose single/multi mode, and watch progress in real-time.

### From the CLI

```bash
ytm import "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
```

Interactive flow: fetches tracks, shows match results, lets you resolve ambiguous/missing tracks, name the playlist, then creates it.

### Extraction methods

The importer tries two approaches in order:

1. **Spotify Web API** (full pagination, handles any playlist size) — requires a free [Spotify Developer](https://developer.spotify.com/) app. On first use, you'll be prompted for your `client_id` and `client_secret`, which are stored in `~/.config/ytm-player/spotify.json`
2. **Scraper fallback** (no credentials needed, limited to ~100 tracks) — used automatically if API credentials aren't configured

For playlists over 100 tracks, set up the API credentials.

## Architecture

```
src/ytm_player/
├── app/                # Main Textual application (mixin package)
│   ├── _app.py         #   Class def, __init__, compose, lifecycle
│   ├── _playback.py    #   play_track, player events, history, download
│   ├── _keys.py        #   Key handling and action dispatch
│   ├── _sidebar.py     #   Sidebar toggling and playlist sidebar events
│   ├── _navigation.py  #   Page navigation and nav stack
│   ├── _ipc.py         #   IPC command handling for CLI
│   ├── _track_actions.py  # Track selection, actions popup, radio
│   ├── _session.py     #   Session save/restore
│   └── _mpris.py       #   MPRIS/media key callbacks
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
│   ├── lrclib.py       #   LRCLIB.net synced lyrics fallback
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
│   ├── playback_bar.py # Persistent bottom bar (track info, progress, controls)
│   ├── theme.py        # Textual theme integration + app-specific color overrides
│   ├── sidebars/       # Persistent playlist sidebar (left) and lyrics sidebar (right)
│   ├── pages/          # Library, Search, Browse, Context, Queue, Liked Songs, Recently Played, Help
│   ├── popups/         # Actions menu, playlist picker, Spotify import
│   └── widgets/        # TrackTable, PlaybackProgress, AlbumArt
└── utils/              # Terminal detection, formatting, BiDi text, transliteration
```

**Stack:** [Textual](https://textual.textualize.io/) (TUI) · [ytmusicapi](https://github.com/sigma67/ytmusicapi) (API) · [yt-dlp](https://github.com/yt-dlp/yt-dlp) (streams/downloads) · [python-mpv](https://github.com/jaseg/python-mpv) (playback) · [aiosqlite](https://github.com/omnilib/aiosqlite) (history/cache) · [dbus-fast](https://github.com/Bluetooth-Devices/dbus-fast) (MPRIS) · [pypresence](https://github.com/qwertyquerty/pypresence) (Discord) · [pylast](https://github.com/pylast/pylast) (Last.fm)

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

- Make sure you're signed in to YouTube Music (free or Premium) in your browser
- Try a different browser: `ytm setup` auto-detects Chrome, Firefox, Brave, and Edge
- If auto-detection fails, use the manual paste method
- Re-run `ytm setup` to re-authenticate

### No sound / wrong audio device

mpv uses your system's default audio output. To change it, create `~/.config/mpv/mpv.conf`:

```
audio-device=pulse/your-device-name
```

List available devices with `mpv --audio-device=help`.

### macOS media keys open Apple Music instead of ytm-player

- ytm-player now registers with macOS Now Playing when running, so media keys should target it.
- Start playback in `ytm` first; macOS routes media keys to the active Now Playing app.
- Grant Accessibility and Input Monitoring permission to your terminal app (Terminal, Ghostty, iTerm) in System Settings -> Privacy & Security.
- If Apple Music still steals keys, fully quit Music.app and press play/pause once in ytm.

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

### Logs and diagnostics

ytm-player writes a rotating log file to:

- Linux/macOS: `~/.config/ytm-player/logs/ytm.log`
- Windows: `%APPDATA%\ytm-player\logs\ytm.log`

Crash tracebacks for any unhandled exception (main thread or background
thread) are saved to the `crashes/` directory next to the log file.

For verbose logs, launch with `--debug`:

```bash
ytm --debug
```

When reporting a bug, please run:

```bash
ytm doctor
```

and paste the output into your GitHub issue. It includes the version,
your Python and mpv versions, paths, the last 50 log lines, and the
most recent crash trace if any.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full release history.
