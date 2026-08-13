# CLI Reference

ytm-player has three modes:
- **TUI** (default) — `ytm` launches the interactive terminal UI.
- **CLI** — headless subcommands that work without the TUI running (search, stats, history, cache).
- **IPC** — control a running TUI from another terminal (play, pause, next, queue control).

Windows: replace `ytm` with `py -m ytm_player` in any of the commands below.

## Setup

```bash
ytm setup                    # Auto-detect browser cookies
ytm setup --browser firefox  # Target a specific browser (chrome, firefox, brave, edge, chromium, vivaldi, opera, helium)
ytm setup --manual           # Skip detection, paste raw request headers
ytm setup --oauth            # Sign in via Google OAuth device flow instead (see docs/oauth-login.md)
```

Browser-cookie auth (the default) needs no setup beyond a signed-in browser, but cookies are
short-lived and typically need re-running `ytm setup` every day or so. OAuth is more durable
(access tokens refresh themselves automatically) but needs a one-time, free Google Cloud OAuth
client — see [docs/oauth-login.md](oauth-login.md) for the five-minute setup.

## Search

```bash
ytm search "daft punk"
ytm search "bohemian rhapsody" --filter songs --json
```

Available filters: `songs`, `videos`, `albums`, `artists`, `playlists`, `community_playlists`, `featured_playlists`.

## Stats and history

```bash
ytm stats                    # Listening stats summary
ytm stats --json             # Machine-readable
ytm history                  # Recent play history
ytm history search           # Recent search history
```

## Cache management

```bash
ytm cache status             # Cache size + entry count
ytm cache clear              # Wipe all cached audio
```

## Playback control (IPC, requires TUI running)

```bash
ytm play                     # Resume playback
ytm pause                    # Pause playback
ytm next                     # Skip to next track
ytm prev                     # Previous track
ytm seek +10                 # Seek forward 10 seconds
ytm seek -5                  # Seek backward 5 seconds
ytm seek 1:30                # Seek to 1:30 (m:ss or h:mm:ss)
```

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
ytm doctor                   # 8-section diagnostic report (version, paths, config, deps, logs, crashes, env, settings — secrets redacted)
ytm config                   # Open config dir in your editor
ytm --debug                  # Launch with verbose logging
```
