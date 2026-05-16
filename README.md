# ytm-player

[![PyPI](https://img.shields.io/pypi/v/ytm-player)](https://pypi.org/project/ytm-player/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://github.com/peternaame-boop/ytm-player/blob/master/LICENSE)
[![CI](https://github.com/peternaame-boop/ytm-player/actions/workflows/ci.yml/badge.svg)](https://github.com/peternaame-boop/ytm-player/actions/workflows/ci.yml)

A full-featured YouTube Music player for the terminal. Browse your library, search, queue tracks, and control playback — all from a TUI with vim-style keybindings. Runs on Linux, macOS, and Windows.

![ytm-player demo](https://raw.githubusercontent.com/peternaame-boop/ytm-player/master/docs/images/ytm-demo.gif)

> Available on **PyPI**, **AUR**, **NixOS**, and **Gentoo** — actively maintained with cross-platform support.

## Features

- **Vim-style keybindings** — j/k movement, multi-key sequences (`g s` for search, `g l` for library), count prefixes (`5j`)
- **Synced lyrics** — live-highlighted with LRCLIB fallback for tracks YouTube doesn't have, with title sanitization for better LRCLIB matches
- **mpv backend** — gapless audio, stream prefetching, broad codec support
- **Cross-platform native integrations** — MPRIS (Linux), Now Playing (macOS), media keys (Windows)
- **Theming** — 18+ Textual themes plus per-app color overrides in `theme.toml`
- **Spotify import** — pull playlists in via API or scraper fallback
- **CLI + IPC** — control a running TUI from another terminal (`ytm play`, `ytm pause`, etc.)
- **Free-tier support** — works without YouTube Music Premium
- **Session resume** — last-playing track + queue restored on launch
- **Local cache** — LRU audio cache for offline-like replay of previously heard tracks
- **Discord + Last.fm** — Rich Presence and scrobbling
- **Audio visualizer** — six built-in modes (spectrum bars, mirrored, pixel-gradient, waveform, oscilloscope, VU meter) with a Python plugin loader; toggle with `v`, cycle with `V`. Opt-in via `pip install ytm-player[viz]`
- **Internet radio stations** — `g R` opens the radio-browser.info catalogue (~30k stations) with Top Voted / Most Played / Favorites / Search tabs. ICY metadata updates the playback bar live

## Requirements

- **Python 3.10+**
- **[mpv](https://mpv.io/)** (audio playback backend, must be installed system-wide)
- A YouTube Music account (free or Premium)

## Install

```bash
# PyPI (Linux / macOS / Windows)
pip install ytm-player

# Arch / CachyOS / Manjaro (AUR)
yay -S ytm-player-git
```

For NixOS, Gentoo, Windows-specific mpv DLL setup, source builds, and optional extras (Discord, Last.fm, Spotify import, etc.), see [docs/installation.md](https://github.com/peternaame-boop/ytm-player/blob/master/docs/installation.md).

## Quickstart

```bash
ytm setup    # one-time auth (auto-detects browser cookies)
ytm          # launch the TUI
```

Windows: replace `ytm` with `py -m ytm_player`.

## Documentation

| Topic | Link |
|-------|------|
| Per-platform install + optional extras | [docs/installation.md](https://github.com/peternaame-boop/ytm-player/blob/master/docs/installation.md) |
| `config.toml` + `theme.toml` reference | [docs/configuration.md](https://github.com/peternaame-boop/ytm-player/blob/master/docs/configuration.md) |
| Full keyboard + mouse keybindings | [docs/keybindings.md](https://github.com/peternaame-boop/ytm-player/blob/master/docs/keybindings.md) |
| All `ytm` CLI subcommands | [docs/cli-reference.md](https://github.com/peternaame-boop/ytm-player/blob/master/docs/cli-reference.md) |
| Spotify playlist import | [docs/spotify-import.md](https://github.com/peternaame-boop/ytm-player/blob/master/docs/spotify-import.md) |
| Troubleshooting (mpv / auth / MPRIS / macOS / cache) | [docs/troubleshooting.md](https://github.com/peternaame-boop/ytm-player/blob/master/docs/troubleshooting.md) |
| File layout + stack | [docs/architecture.md](https://github.com/peternaame-boop/ytm-player/blob/master/docs/architecture.md) |
| Contributing | [CONTRIBUTING.md](https://github.com/peternaame-boop/ytm-player/blob/master/CONTRIBUTING.md) |
| Security policy | [SECURITY.md](https://github.com/peternaame-boop/ytm-player/blob/master/SECURITY.md) |

## Contributors

Thanks to [@dmnmsc](https://github.com/dmnmsc), [@Villoh](https://github.com/Villoh), [@valkyrieglasc](https://github.com/valkyrieglasc), [@dsafxP](https://github.com/dsafxP), [@Thayrov](https://github.com/Thayrov), [@glywil](https://github.com/glywil), [@Kineforce](https://github.com/Kineforce), [@CarterSnich](https://github.com/CarterSnich), [@Tohbuu](https://github.com/Tohbuu), [@nitsujri](https://github.com/nitsujri), [@uhs-robert](https://github.com/uhs-robert), [@moschi](https://github.com/moschi), and [@firedev](https://github.com/firedev) for bug reports, fixes, packaging, and platform support.

## Changelog

See [CHANGELOG.md](https://github.com/peternaame-boop/ytm-player/blob/master/CHANGELOG.md) for the full release history.
