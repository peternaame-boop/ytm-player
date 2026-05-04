# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

ytm-player is a YouTube Music TUI client built with Python 3.10+ and [Textual](https://textual.textualize.io/). It provides vim-style navigation, synced lyrics, playlist management, queue control, and integrations (MPRIS, Discord, Last.fm, Spotify import). Audio playback uses mpv via python-mpv; stream URLs are resolved via yt-dlp.

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
```

System dependency: `mpv` must be installed (`sudo pacman -S mpv` on Arch).

## Architecture

**Entry point:** `ytm` CLI command → `src/ytm_player/cli.py` (Click). Running `ytm` with no args launches the Textual TUI app (`app/` package — split into mixins, all extending `YTMHostBase` from `app/_base.py` which declares the shared attribute surface as a `TYPE_CHECKING`-only stub for clean Pyright type-checking). Subcommands (`ytm search`, `ytm play`, etc.) communicate with a running TUI instance via Unix socket IPC (`ipc.py`).

**Three-layer structure:**

- **`services/`** — Backend singletons: `Player` (mpv wrapper), `QueueManager` (shuffle/repeat), `StreamResolver` (yt-dlp), `YTMusicService` (ytmusicapi), `CacheManager` (LRU audio cache), `HistoryManager` (SQLite via aiosqlite), `AuthManager` (browser cookie extraction), `lrclib` (LRCLIB.net lyrics fallback — `get_synced_lyrics` runs a title sanitizer that strips `(feat. X)`, `(Remix)`, `(Remastered)`, `(Deluxe)`, `(Live)`, `(Acoustic)`, `(Official Music Video)`, etc., before lookup to improve match rate), `DownloadService` (offline downloads), `SpotifyImport`. Platform-specific: `MPRISService` (Linux D-Bus), `MacOSMediaService` + `MacOSEventTapService` (macOS), `MediaKeysService` (Windows pynput). Optional: `DiscordRPC`, `LastFMService`.
- **`ui/`** — Textual widgets: `pages/` (library, search, browse, context, queue, etc.), `sidebars/` (playlist list, synced lyrics), `popups/` (modals), `widgets/` (track table, progress bar, album art). Styling via `theme.py` with CSS variables.
- **`config/`** — `Settings` dataclass loaded from `~/.config/ytm-player/config.toml`. `KeyMap` system supports multi-key vim sequences and count prefixes. All paths centralized in `paths.py`.

**Key patterns:**

- **Event-driven playback:** `Player` emits `PlayerEvent` enums (`TRACK_END`, `TRACK_CHANGE`, etc.) dispatched to the Textual event loop via `call_soon_threadsafe`. The app registers callbacks to update UI.
- **Thread safety:** `Player` and `QueueManager` are singletons with `threading.Lock`. Player events bridge from mpv's callback thread to asyncio.
- **Track format:** All services use a standardized track dict with keys: `video_id`, `title`, `artist`, `artists` (list of dicts with `name`/`id`), `album`, `album_id`, `duration` (seconds, int or None), `thumbnail_url`, `is_video`. The `normalize_tracks()` function in `utils/formatting.py` converts inconsistent ytmusicapi response shapes into this format — always use it when ingesting API data. `extract_duration()` reads `duration_seconds` → `duration` → `length` in priority order; the `length` fallback exists because `get_watch_playlist` returns durations as `"M:SS"` strings under that key. Always go through `extract_duration()` rather than `track.get("duration_seconds", 0)` — that pattern silently returns 0 for normalized tracks.
- **Session persistence:** Volume, queue contents, shuffle/repeat state saved to `session.json` and restored on startup. When `[playback] resume_on_launch` is true (default), the last-played track + position are staged into `_pending_resume_video_id` / `_pending_resume_position` on the app and consumed the first time the user presses play, instead of auto-playing on launch.
- **Playback bar keybindings:** Standard transport keys plus `l` to toggle the like state of the currently playing track.
- **Prefetching:** Next track's stream URL is resolved in background for instant skip.
- **Page navigation:** `app/_navigation.py` manages a nav stack (max 20) via `navigate_to()`. Each page widget implements `handle_action(action, count)` for vim-style keybinding dispatch.
- **Lyric current colour:** `theme.py` exports `DEFAULT_LYRIC_CURRENT = "#ff4e45"` as the absolute fallback for the synced-lyrics current-line colour. The fallback chain is `theme.accent` → `theme.primary` → `DEFAULT_LYRIC_CURRENT`, identical across `theme.from_css_variables`, `_app.py:get_css_variables`, and `_app.py:watch_theme`.
- **Python 3.10 compatibility shims:** Three stdlib symbols added in 3.11+ are backported via `sys.version_info >= (3, 11)` checks (which Pyright narrows correctly): `tomllib` (in `config/keymap.py`, `config/settings.py`, `ui/theme.py`, `app/_app.py`, `tests/test_config/test_settings.py`) falls back to `tomli`; `typing.Self` (in the first three of those files) falls back to `typing_extensions.Self`; `enum.StrEnum` (in `services/queue.py`, `services/player.py`) falls back to a small `(str, Enum)` polyfill mirroring stdlib's `auto()` lowercase-name behaviour. `tomli` and `typing_extensions` are conditional dependencies (`python_version < "3.11"`) so 3.11+ users don't pull them.
- **LC_NUMERIC quirk:** `cli.py` forces `LC_NUMERIC=C` at import time — mpv segfaults without it. Don't remove this.

## Pre-commit Hooks

The repo uses [pre-commit](https://pre-commit.com/). After cloning, install hooks once:
```bash
pre-commit install
```
This sets up both pre-commit (ruff-format, ruff, pyright) and pre-push (pytest) hooks automatically via `default_install_hook_types` in `.pre-commit-config.yaml`.

**Manual fallback** — if hooks aren't installed, run BOTH before every commit:
```bash
ruff format src/ tests/
ruff check src/ tests/
```
`ruff check` alone is NOT enough. `ruff format` catches line length and style issues that `ruff check` does not. Always format first, then lint.

## Ruff Configuration

- Line length: 100, target Python 3.10
- Rules: E, F, I, N, W (E501 ignored — line length handled separately)
- Per-file exemptions: `mpris.py` (N802, N803, F821, F722 for D-Bus conventions), `spotify_import.py` (N803)
- CI pins `ruff==0.15.1` — match this locally to avoid lint drift

## Testing

- pytest with `asyncio_mode = "auto"` — async test functions are auto-detected, no `@pytest.mark.asyncio` needed
- UI code (`src/ytm_player/ui/*`) is excluded from coverage; services and config are covered
- Coverage floor: 10%
- Heavy mocking of mpv, ytmusicapi, yt-dlp, D-Bus — tests never hit real APIs or require mpv installed
- Test fixtures in `tests/conftest.py`: `sample_track`/`sample_tracks` use `_make_track()` helper to create standardized track dicts; `queue_manager` provides a fresh `QueueManager` instance
- CI runs on GitHub Actions (Ubuntu + macOS + Windows, Python 3.10 and 3.14): ruff lint + format check, then pytest with coverage

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

## Error handling architecture

The codebase uses ~263 `except Exception:` blocks as a deliberate graceful-degrade pattern. Service-layer methods return safe defaults (empty list, `False`, `None`) on any failure rather than raising; UI-layer handlers wrap service calls in their own broad catch knowing services don't raise. Every broad-except site has been audited and categorized in `docs/broad-except-audit.md` as KEEP (intentional graceful-degrade — 176 sites), NARROW (hides real bugs, should specify expected types — 87 sites), or PROMOTE (should let propagate — 0 sites).

**Before adding a broad `except Exception:` block:** check the audit doc to confirm the pattern is correct here, or document the new site with its category in the audit. Service-layer methods that can return safe defaults SHOULD do so; methods that change state SHOULD let unexpected exceptions propagate.

**The cascade contract:** if you narrow an exception in a service-layer method, every UI/app handler that wraps a call to that method may need updating in lockstep — the cascade map in the audit doc lists the dependents. The Phase 4 plan (also in the audit doc) sequences narrowings before cascade updates so regressions don't slip through.

## CI Workflows

Three GitHub Actions workflows live in `.github/workflows/`:

- `ci.yml` — runs ruff lint + format check, then pytest on the matrix `[3.10, 3.14]` × `[ubuntu, macos, windows]` (6 jobs total).
- `check-python-versions.yml` — runs monthly (1st of each month, 09:00 UTC) and opens a maintenance issue when CPython releases a new stable major.minor version newer than our matrix ceiling. Idempotent — won't reopen if an issue is already open. Uses pyyaml to parse the matrix robustly.
- `publish.yml` — tag-triggered (`v*`) and `workflow_dispatch`. Builds wheel + sdist, smoke-tests the wheel against `ytm --version` in a fresh venv, uploads to PyPI via OIDC trusted publishing (no API tokens), then creates a GitHub Release with the matching CHANGELOG section attached. Manual dispatch can target TestPyPI for dry-runs.

## Dependabot

`.github/dependabot.yml` runs weekly on Mondays. Both `pip` and `github-actions` ecosystems use two groups: `*-minor-patch` (auto-merge candidates) and `*-major` (review carefully — breaking changes possible). Major bumps are not skipped — they just open in their own PR so they're reviewed independently of low-risk updates.

## Releases

The flow is **tag-driven**. Pushing a `vX.Y.Z` tag triggers `publish.yml`, which handles PyPI + the GitHub Release end-to-end. AUR is still updated by hand afterward.

### One-time setup (already done)

Before the first tag, two PyPI trusted-publisher entries are configured at https://pypi.org/manage/account/publishing/ and https://test.pypi.org/manage/account/publishing/:

| Field | Value |
|-------|-------|
| Owner | `peternaame-boop` |
| Repository | `ytm-player` |
| Workflow | `publish.yml` |
| Environment | `pypi` (or `testpypi` on TestPyPI) |

GitHub Environments `pypi` and `testpypi` exist in repo settings. No API tokens stored anywhere — auth is OIDC.

### Cutting a release

1. Bump `__version__` in `src/ytm_player/__init__.py`.
2. Add a `### vX.Y.Z (YYYY-MM-DD)` section to the top of `CHANGELOG.md` (the publish workflow extracts it for the GitHub Release body).
3. Run `ruff format src/ tests/ && ruff check src/ tests/ && pytest`.
4. Commit (`chore(release): vX.Y.Z`), tag (`git tag vX.Y.Z`), push both (`git push && git push --tags`).
5. Watch the `Publish` workflow on GitHub Actions — it builds, smoke-tests, uploads to PyPI, and creates the release in roughly a minute.

### Dry-run via TestPyPI (paranoid release)

Trigger `Publish` manually from the Actions tab → `Run workflow` → target `testpypi`. Builds + uploads to https://test.pypi.org/project/ytm-player/ without touching production. Useful when changing build config or pyproject metadata.

### After PyPI publishes — update AUR

AUR (`ytm-player-git`) PKGBUILD lives in `aur/PKGBUILD`. After every release:

1. If dependencies changed: update `depends`/`optdepends`/`makedepends` in `aur/PKGBUILD`.
2. Push the AUR update:

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
