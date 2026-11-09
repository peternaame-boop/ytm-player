# CLI Reference

ytm-player has three modes:
- **TUI** (default) — `ytm` launches the interactive terminal UI.
- **CLI** — headless subcommands that work without the TUI running (search, stats, history, cache).
- **IPC** — control a running TUI from another terminal (play, pause, next, queue control).

Windows: replace `ytm` with `py -m ytm_player` in any of the commands below.

## Setup

```bash
ytm setup                    # Browser cookies: [yt_dlp] cookies_file first if set, then cookie_browser if set, else auto-detect
ytm setup --browser firefox  # Target a specific browser (chrome, firefox, zen, brave, edge, chromium, vivaldi, opera, helium)
ytm setup --slots 15         # Probe more account slots (default 10) in multi-account sessions
ytm setup --manual           # Skip detection, paste raw request headers
```

## Search

```bash
ytm search "daft punk"
ytm search "bohemian rhapsody" --filter songs --json
ytm search "daft punk" --limit 5  # -l; default 20
```

Results print as JSON; `--json` makes it compact (one line).

Available filters (`--filter` / `-f`): `songs`, `videos`, `albums`, `artists`, `playlists`.

## Stats and history

```bash
ytm stats                    # Listening stats (JSON)
ytm stats --json             # Same, as compact one-line JSON
ytm history                  # Recent play history (JSON)
ytm history --limit 20       # -l; default 50 (--json for compact output)
ytm history search           # Recent search history (JSON)
ytm history search --limit 20  # -l; default 50 (--json for compact output)
```

## Cache management

```bash
ytm cache status             # Cache size + entry count
ytm cache status --json      # Same, as compact JSON
ytm cache clear              # Wipe all cached audio (asks first; --yes skips the prompt)
```

`ytm cache status` reports the `[cache] location` directory when it is set. `ytm cache clear` deletes every file the cache index lists, wherever it is, and empties the index; other files in a custom `[cache] location` are left alone. It refuses to run while the TUI is running; quit `ytm` first.

## Playback control (IPC, requires TUI running)

```bash
ytm play                     # Resume playback
ytm pause                    # Pause playback
ytm next                     # Skip to next track
ytm prev                     # Previous track
ytm seek +10                 # Seek forward 10 seconds
ytm seek -- -5               # Seek backward 5 seconds (`--` stops -5 being read as an option)
ytm seek 1:30                # Seek to 1:30 (m:ss or h:mm:ss)
ytm play-index 4             # Play the fifth track in the current queue
```

`play-index` uses a zero-based position in the playback order returned by `ytm queue`, including
shuffle. The position is evaluated when the command is handled. Success confirms acceptance; audio
may start later while the stream resolves.

## Like / dislike (IPC)

```bash
ytm like                     # Like current track
ytm dislike                  # Dislike current track
ytm unlike                   # Remove like/dislike (sets to INDIFFERENT)
```

## Status (IPC)

```bash
ytm now                      # Current track info (JSON)
ytm status                   # Player status (JSON)
ytm queue                    # Queue contents (JSON)
ytm queue add VIDEO_ID       # Add track by video ID
ytm queue clear              # Clear queue
```

## Spotify import

```bash
ytm import "https://open.spotify.com/playlist/..."
```

Interactive flow — see [docs/spotify-import.md](spotify-import.md).

## Diagnostics

```bash
ytm doctor                   # 9-section diagnostic report (version/platform/mpv, paths, process status, MPRIS/media keys, recent errors, mpv warnings, faulthandler trace, last crash, active hooks; auth headers/tokens and URLs redacted)
ytm config                   # Open config.toml in $EDITOR (no $EDITOR: the config dir via xdg-open on Linux, config.toml in the default app on Windows; on macOS set $EDITOR)
ytm --version                # Print the version
ytm --debug                  # Launch with verbose logging
```
