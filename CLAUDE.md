# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

ytm-player is a YouTube Music TUI client built with Python 3.12+ and [Textual](https://textual.textualize.io/). It provides vim-style navigation, synced lyrics, playlist management, queue control, and integrations (MPRIS, Discord, Last.fm, Spotify import). Audio playback uses mpv via python-mpv; stream URLs are resolved via yt-dlp.

## Commands

```bash
# Install (editable, all features + dev tools)
pip install -e ".[spotify,mpris,discord,lastfm,transliteration,dev]"

# Run the TUI
ytm

# Lint
ruff check src/ tests/
ruff format --check src/ tests/

# Auto-format
ruff format src/ tests/

# Tests
pytest
pytest --cov=ytm_player --cov-report=term-missing

# Single test file
pytest tests/test_services/test_queue.py

# Single test
pytest tests/test_services/test_queue.py::test_add_track -v

# Type checking
uv run pyright src/ytm_player/

# Pre-commit (run all hooks)
uv run pre-commit run --all-files

# Install pre-commit hooks
uv run pre-commit install
```

System dependency: `mpv` must be installed (`sudo pacman -S mpv` on Arch).

## Pre-commit Hooks

Four hooks run automatically on `git commit`:

1. **ruff-format** — Auto-formats staged files. If formatting changes are needed, the commit is aborted so you can review and re-stage.
2. **ruff** — Lints with `--fix --exit-non-zero-on-fix`. Auto-fixes what it can; aborts if fixes were applied so you can re-stage.
3. **pyright** — Type checks `src/ytm_player/` at standard strictness. Blocks commit on type errors.
4. **pytest** — Runs the full test suite. Blocks commit on test failures.

To skip hooks for a quick commit: `git commit --no-verify`

To install hooks in a fresh clone: `uv run pre-commit install`

## Architecture

**Entry point:** `ytm` CLI command → `src/ytm_player/cli.py` (Click). Running `ytm` with no args launches the Textual TUI app (`app/` package — split into mixins). Subcommands (`ytm search`, `ytm play`, etc.) communicate with a running TUI instance via Unix socket IPC (`ipc.py`).

**Three-layer structure:**

- **`services/`** — Backend singletons: `Player` (mpv wrapper), `QueueManager` (shuffle/repeat), `StreamResolver` (yt-dlp), `YTMusicService` (ytmusicapi), `CacheManager` (LRU audio cache), `HistoryManager` (SQLite via aiosqlite), `AuthManager` (browser cookie extraction), `lrclib` (LRCLIB.net lyrics fallback), `DownloadService` (offline downloads), `SpotifyImport`. Platform-specific: `MPRISService` (Linux D-Bus via `dbus-fast`, not `dbus-next`), `MacOSMediaService` + `MacOSEventTapService` (macOS), `MediaKeysService` (Windows pynput). Optional: `DiscordRPC`, `LastFMService`.
- **`ui/`** — Textual widgets: `pages/` (library, search, browse, context, queue, etc.), `sidebars/` (playlist list, synced lyrics), `popups/` (modals), `widgets/` (track table, progress bar, album art). Styling via `theme.py` with CSS variables.
- **`config/`** — `Settings` dataclass loaded from `~/.config/ytm-player/config.toml`. `KeyMap` system supports multi-key vim sequences and count prefixes. All paths centralized in `paths.py`.

**Key patterns:**

- **Event-driven playback:** `Player` emits `PlayerEvent` enums (`TRACK_END`, `TRACK_CHANGE`, etc.) dispatched to the Textual event loop via `call_soon_threadsafe`. The app registers callbacks to update UI.
- **Thread safety:** `Player` and `QueueManager` are singletons with `threading.Lock`. Player events bridge from mpv's callback thread to asyncio.
- **Track format:** All services use a standardized track dict with keys: `video_id`, `title`, `artist`, `artists` (list of dicts with `name`/`id`), `album`, `album_id`, `duration` (seconds, int or None), `thumbnail_url`, `is_video`. The `normalize_tracks()` function in `utils/formatting.py` converts inconsistent ytmusicapi response shapes into this format — always use it when ingesting API data.
- **Session persistence:** Volume, queue contents, shuffle/repeat state saved to `session.json` and restored on startup.
- **Prefetching:** Next track's stream URL is resolved in background for instant skip.
- **Page navigation:** `app/_navigation.py` manages a nav stack (max 20) via `navigate_to()`. Each page widget implements `handle_action(action, count)` for vim-style keybinding dispatch.
- **LC_NUMERIC quirk:** `cli.py` forces `LC_NUMERIC=C` at import time — mpv segfaults without it. Don't remove this.

## Pre-commit Checklist

**MANDATORY before every commit — run BOTH:**
```bash
ruff format src/ tests/
ruff check src/ tests/
```
`ruff check` alone is NOT enough. `ruff format` catches line length and style issues that `ruff check` does not. Always format first, then lint.

**Note:** Pre-commit hooks now automate the format + lint steps above. Manual runs are only needed when hooks are bypassed with `--no-verify`.

## Ruff Configuration

- Line length: 100, target Python 3.12
- Rules: E, F, I, N, W (E501 ignored — line length handled separately)
- Per-file exemptions: `mpris.py` (N802, N803, F821, F722 for D-Bus conventions), `spotify_import.py` (N803)
- CI pins `ruff==0.15.1` — match this locally to avoid lint drift

## Testing

- pytest with `asyncio_mode = "auto"` — async test functions are auto-detected, no `@pytest.mark.asyncio` needed
- UI code (`src/ytm_player/ui/*`) is excluded from coverage; services and config are covered
- Coverage floor: 10%
- Heavy mocking of mpv, ytmusicapi, yt-dlp, D-Bus — tests never hit real APIs or require mpv installed
- Test fixtures in `tests/conftest.py`: `sample_track`/`sample_tracks` use `_make_track()` helper to create standardized track dicts; `queue_manager` provides a fresh `QueueManager` instance
- CI runs on GitHub Actions (Ubuntu + macOS + Windows, Python 3.12 and 3.13): ruff lint + format check, pyright type check, then pytest with coverage

## Logging

Logs go to `~/.config/ytm-player/logs/ytm.log` via `setup_logging()` in
`src/ytm_player/utils/logging.py`. **Never use `print()`** in
non-CLI code — Textual's alt-screen swallows stderr.

Conventions:
- Use `logger = logging.getLogger(__name__)` at module top.
- For caught exceptions you want to surface in bug reports, use
  `logger.exception("descriptive message")` — *not* `logger.debug(...,
  exc_info=True)`, which silently routes to debug level.
- Reserve `logger.debug` for verbose tracing only enabled with `--debug`.
- Unhandled crashes are captured by `install_excepthooks()` and written
  to `~/.config/ytm-player/crashes/`.
- For diagnostics, run `ytm doctor` — outputs version, paths, recent
  log, and most recent crash trace, suitable for pasting into issues.

## AUR Package

This project is published on AUR as `ytm-player-git`. The PKGBUILD lives in `aur/PKGBUILD`.

**When pushing changes to GitHub, always update the AUR package too.** The process:

1. If it's a new version: bump `__version__` in `src/ytm_player/__init__.py`, tag it (`git tag vX.Y.Z`), push the tag
2. If dependencies changed: update `depends`/`optdepends`/`makedepends` in `aur/PKGBUILD`
3. Push the AUR update:

```bash
git clone ssh://aur@aur.archlinux.org/ytm-player-git.git /tmp/ytm-player-aur
cp aur/PKGBUILD /tmp/ytm-player-aur/
cd /tmp/ytm-player-aur && makepkg --printsrcinfo > .SRCINFO
git add PKGBUILD .SRCINFO && git commit -m "Update to vX.Y.Z" && git push
rm -rf /tmp/ytm-player-aur
```

AUR package URL: https://aur.archlinux.org/packages/ytm-player-git

## Distribution

Published on four channels:
- **PyPI:** `pip install ytm-player` — https://pypi.org/project/ytm-player/
- **AUR:** `yay -S ytm-player-git` — https://aur.archlinux.org/packages/ytm-player-git
- **NixOS:** `flake.nix` with `ytm-player` and `ytm-player-full` packages
- **Gentoo:** `emerge media-sound/ytm-player` via GURU overlay (community-maintained by @dsafxP)
