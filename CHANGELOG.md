# Changelog

All notable changes to ytm-player are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/).

---

### v1.8.0 (2026-04-28)

A reliability and quality release driven by a multi-agent expert audit. Hardens error handling across the service/UI cascade so silent-failure UX is replaced with actionable feedback, fixes several latent runtime bugs, and brings the codebase to zero non-exempted Pyright errors (down from 218).

**New**

- **First-run discoverability toast** — on first launch, a 1.5s-delayed toast reads "Press ? for help · vim-style keys" (8s timeout). State persists in `session.json` so the hint shows once. Legacy session files without the field upgrade cleanly.
- **Per-cause mutation-failure toasts** — like/playlist-add operations now distinguish auth-required, auth-expired, network-down, and server-error failures with specific messages (e.g. "Sign in again — run `ytm setup`" vs "Check your connection") instead of a single generic "Couldn't update". The classifier inspects ytmusicapi's exception types and parses HTTP status from `YTMusicServerError` messages.
- **Page error-fallback states** — Recently Played and Context (album/artist/playlist) pages used to show "Loading…" forever on API/disk failure. Now they replace the loading indicator with a clear error message pointing at `~/.config/ytm-player/logs/ytm.log`.
- **9 new integration tests** in `tests/test_integration/` covering the search→queue→play flow, track-change fan-out, cache-bypass behaviour, session round-trip, search cancellation, and the mutation cascade. Coverage floor raised 10 % → 47 %.

**Fixed**

- **Album art crashed on Pillow ≥ 10.** `Image.LANCZOS` was removed in Pillow 10. Switched to `Image.Resampling.LANCZOS`. Pyproject pins `Pillow>=10`, so this had been shipping broken for every modern install.
- **Spotify single/multi import would have ImportError.** The popup imported `_get_video_id` from `services.spotify_import`, but that symbol does not exist (the function is `get_video_id` in `utils/formatting.py`). Renamed all call sites.
- **`gg` / `G` in browse and playlist sidebar would crash.** Code called `ListView.action_first()` / `action_last()` — neither method exists on Textual's ListView. Replaced with the standard cursor-index assignment.
- **New Releases tab in Browse silently empty.** `YTMusicService.get_new_releases` called `client.get_new_releases` which doesn't exist on `YTMusic`. The wrapping broad-except swallowed the AttributeError. Switched to `get_explore()['new_releases']` per the actual ytmusicapi surface.
- **Session-save errors disappeared into the void.** `_save_session_state` now narrows its catch to `(OSError, TypeError)` and surfaces a toast on failure instead of silently dropping the user's volume / queue / playback position.
- **Mutation methods now return `MutationResult`** — `rate_song`, `add_playlist_items`, `remove_playlist_items`, plus `add_to_library`, `remove_album_from_library`, `unsubscribe_artist`, and `delete_playlist`. A Literal: success / auth_required / auth_expired / network / server_error. Previously returned `None` (or `bool`) whether the server accepted or not — UI showed "Liked!" or "Added!" toasts even when the API failed silently. Worst case was the Spotify import "Created with N tracks" toast firing when every batch failed. All UI cascade sites now show a per-cause toast suffix via `mutation_failure_suffix`.
- **Spotify multi-import partial-failure track count was off by up to 99.** When a 350-track import had only the last batch fail, the toast reported "~300/350 added" because the formula assumed every successful batch was a full 100. Now tracks `added_total` cumulatively by summing `len(batch)` on success, so the count is exact.
- **Read-side `logger.debug` → `logger.exception` (~18 sites in `ytmusic.py`).** Search, library-list, get_album/artist/playlist/song/lyrics/history etc. previously caught broad exceptions and logged at debug level, so post-mortem of "library page came up empty" required `--debug`. Now they land in the log file at default level.
- **`YTMusicService._call` outer catch narrowed.** Programming-error exceptions (TypeError, AttributeError) now propagate instead of being swallowed and mistakenly counted toward the consecutive-failure threshold.
- **Thread-safety on lazy `YTMusicService.client` init.** Concurrent first-access from `asyncio.to_thread` workers is now guarded by a `threading.Lock` with double-checked locking.
- **Credential file writes use `O_NOFOLLOW`.** `auth.json` (`services/auth.py`) and `spotify.json` (`services/spotify_import.py`) now refuse to follow a symlink at the target path — defense-in-depth matching the existing pattern in `utils/logging.py`.

**Internal**

- **Comprehensive broad-except audit** at `docs/broad-except-audit.md` — categorizes all 263 `except Exception:` sites in the codebase as KEEP / NARROW / PROMOTE with a cross-cutting cascade map. Referenced from `CLAUDE.md` so future contributors check the audit before adding new broad catches.
- **Pyright clean-up: 218 → 93 errors,** with the remaining 93 entirely in `services/mpris.py` (D-Bus magic, exempted in CLAUDE.md ruff rules) and `services/macos_eventtap.py` (macOS-only AppKit/Quartz). Fixed real bugs along the way: `playback_bar._FooterButton.__init__` typed `kwargs` as `object` (rejecting all forwarded params), `_RepeatButton.repeat_mode` typed as `str` while assigned an `int`-typed enum value, `track_table._filter_timer` typed as `object | None` so `.stop()` didn't type-check, and several Optional-access defensive gaps.
- **Codebase-wide `self.app.X` typing** — UI widgets/pages now cast `self.app` to `YTMHostBase` at access points so Pyright can see the host's services. ~42 sites across 6 files.
- **README polish** — badges, tagline, Contributors section.
- **Audit-driven follow-up plans** at `docs/superpowers/plans/2026-04-28-audit-driven-error-handling-cleanup.md` and `docs/superpowers/plans/2026-04-28-audit-driven-followup.md` — written via the superpowers writing-plans + subagent-driven-development workflow.

### Unreleased

**Fixes**

- macOS built-in keyboard media keys (prev/next) now work — built-in MacBook keyboards send key codes 19/20 (fast-forward/rewind) instead of the standard 17/18 (next/previous) that external keyboards use. The Quartz event tap now maps both sets.

---

### v1.7.2 (2026-04-27)

A combined release covering broader Python compatibility, a monthly Python release watcher, a full README restructure into a landing page + dedicated docs, and the 3.10 backport shims required to support Ubuntu 22.04.

**New**

- README has been split into a 64-line landing page plus seven dedicated docs (`docs/installation.md`, `docs/configuration.md`, `docs/keybindings.md`, `docs/cli-reference.md`, `docs/spotify-import.md`, `docs/troubleshooting.md`, `docs/architecture.md`). The README is now purely an index — every topic lives in exactly one file with full detail.
- New monthly workflow `check-python-versions.yml` opens a maintenance issue when CPython releases a new stable major.minor version newer than our CI matrix ceiling. Idempotent — won't reopen if an issue is already open. Defensive regex guard rejects RC/beta strings to avoid bogus issues.

**Project**

- Python floor lowered from 3.12 to 3.10. Ubuntu 22.04 LTS users can now `pip install ytm-player` against the system `python3` without installing a newer Python first. Verified locally on Python 3.10 (545/545 tests passing) and via the new CI matrix `[3.10, 3.14]`.
- Note on Python 3.10 lifecycle: CPython 3.10 reaches end-of-life October 2026. Ubuntu 22.04 keeps shipping 3.10 until April 2027 (standard support) or 2032 (Pro), so 22.04 users stay covered well past CPython's EOL. We'll bump the floor when usage data shows nobody on 3.10.
- CI matrix shifted from `[3.12, 3.13]` to `[3.10, 3.14]` — testing the supported floor + the latest stable. Same 6 jobs as before (3 OSes × 2 Pythons), better-targeted coverage.
- Lint job + Python release watcher updated to use Python 3.14 (was 3.12), aligning auxiliary tooling with the test matrix ceiling.
- Pyright + ruff configured to type-check and lint against `py310` so accidentally-introduced 3.11+ syntax fails locally and in CI.
- Classifiers updated: now lists Python 3.10, 3.11, 3.12, 3.13, 3.14.
- `flake.nix` Python pin bumped from 3.12 to 3.13 (a stable middle of the supported range).
- `CLAUDE.md` updated to document v1.7.x additions: 3.10 backport shims, the new watcher workflow, and the `DEFAULT_LYRIC_CURRENT` constant.
- `CONTRIBUTING.md` gained a "Python version compatibility" section explaining the `sys.version_info` shim pattern and the `YTMHostBase` mixin attribute typing pattern for new contributors.
- AUR PKGBUILD maintainer email replaced (was a placeholder).
- Replaced hero screenshot (v4 → v5).
- New `publish.yml` workflow automates the PyPI release. Pushing a `vX.Y.Z` tag now builds wheel + sdist, smoke-tests the wheel by installing it into a fresh venv and running `ytm --version`, uploads to PyPI via OIDC trusted publishing (no API tokens stored anywhere), and creates the matching GitHub Release with the CHANGELOG section attached. A manual `workflow_dispatch` with `target=testpypi` is wired for paranoid dry-runs against test.pypi.org. AUR is still updated by hand afterward.
- Dependabot now opens major-version bumps in their own grouped PR (previously suppressed by `update-types: [minor, patch]`). Both `pip` and `github-actions` ecosystems split into `*-minor-patch` (auto-merge candidates) and `*-major` (review carefully), so security-relevant majors no longer require manual intervention to surface.

**Fixes**

- Theme cache (`_read_theme_toml_cached`) was silently returning `{}` on Python 3.10 because its function-local `import tomllib` was caught by a broad except clause. The bug was masked on 3.12 (where tomllib is stdlib) but would have shipped a non-functional theme cache to 3.10 users. Caught during the 3.10 verification gate; fixed by moving the import to module-level with a `sys.version_info` shim.
- Stale comments cleaned up: `pyproject.toml` Pyright comment now reads as past tense; `services/player.py` Windows note no longer claims a 3.12+ requirement that was never accurate (ucrtbase has been the default since 3.5).
- Sweep findings absorbed into the new docs: `l` keybinding documented (`docs/keybindings.md`), `[playback] resume_on_launch` documented (`docs/configuration.md`), corrected `lyrics_current = "#ff4e45"` in the theme.toml example (was stale `#2ecc71`), `app/_base.py` added to the architecture file tree, full CLI subcommand reference now lists every `ytm` command (was missing `ytm dislike`, `ytm now`, `ytm doctor`, `ytm config`, etc.).

**Compatibility shims**

To support Python 3.10 (where several stdlib symbols don't exist), backport shims were added using `sys.version_info >= (3, 11)` checks (which type-checkers narrow correctly):

- `tomllib` (3.11+) → falls back to `tomli` (PyPI) on 3.10. Files: `config/keymap.py`, `config/settings.py`, `ui/theme.py`, `app/_app.py`, `tests/test_config/test_settings.py`.
- `typing.Self` (3.11+) → falls back to `typing_extensions.Self` on 3.10. Same first 3 files.
- `enum.StrEnum` (3.11+) → falls back to a `(str, Enum)` polyfill that mirrors stdlib's `auto()` lowercase-name behavior. Files: `services/queue.py`, `services/player.py`.
- `tomli` and `typing_extensions` added as conditional dependencies (`python_version < "3.11"` markers) so 3.11+ users don't pull them.

---

### v1.7.0 (2026-04-27)

A polish release focused on resume-on-launch, lyric metadata cleanup, theming
correctness, and a typing overhaul that silences Pyright noise across the
mixin-based App. 30 commits, 545 tests (was 491).

**New**

- Heart toggle on the playback bar — visible `❤` indicator between the track info and volume, filled in the theme accent when the current track is liked, muted when not. Press `l` (or click) to toggle. Backed by `ytmusicapi.rate_song(LIKE/INDIFFERENT)` so the change syncs to your YouTube Music account in real time. Toast confirms ("Added to Liked songs" / "Removed from Liked songs"); pressing `l` while not signed in surfaces a "Sign in to like songs" warning instead of feeling like a dead key. (Closes [#62](https://github.com/peternaame-boop/ytm-player/issues/62), thanks @valkyrieglasc.)
- Last-playing track is remembered across launches — the track + queue + position are saved on every exit (not just unclean ones). Relaunch ytm-player and the playback bar shows the same track you were on, ready to go. Default-on; opt out with `[playback] resume_on_launch = false` in `config.toml`. Two safety guards: tracks paused for under 1 second are no longer saved (avoids a startup-crash overwriting a perfectly good prior resume), and the pending slot survives if you start playing a different track first.
- Artist context page now fetches ALL top songs — `ytmusicapi.get_artist()` returns only ~5 by default with the full list at a separate browseId. ytm-player fetches the first 300 in the background after the page renders, then chains `get_playlist_remaining` for anything beyond. No more silent truncation at 100. (Closes [#55](https://github.com/peternaame-boop/ytm-player/issues/55), thanks @dmnmsc.)
- Search input auto-focuses on fresh entry — pressing `g s` from a fresh state focuses the search bar so you can type immediately. Returning to the page with a cached query leaves focus on the results table so you can keep browsing without re-typing.
- Search Escape now does the right thing — when the input is focused or the predictive-suggestions dropdown is showing, Escape hides the dropdown and moves focus to the songs results table (or blurs entirely if no results yet). Typing a new query also clears the previous results so a subsequent Escape doesn't strand you on stale rows.
- Lyric title sanitization — strips a wide set of YouTube-style noise patterns (`(Official Music Video)`, `[Audio]`, `(HD)`, `(feat. Bob)` / `(ft. Bob)` / `(featuring Bob)`, `(Remix)` / `(Extended Remix)` / `(Radio Remix)`, `(Remastered)` / `(Remastered 2009)`, `(Deluxe)` / `(Deluxe Edition)`, `(Live)` / `(Live at Wembley)`, `(Acoustic)` / `(Acoustic Version)`, etc.) and the `Artist - ` prefix before LRCLIB lookup. Handles nested parens correctly (`(feat. Bob (Junior))` → strips cleanly). Improves match rate for tracks played from YouTube proper (where titles are noisy) without affecting clean YouTube Music tracks. (Closes [#62](https://github.com/peternaame-boop/ytm-player/issues/62), thanks @valkyrieglasc.)
- Notifications match the active theme — toast border colors now use `$primary` / `$warning` / `$error` from the theme instead of Textual's hardcoded green default. Notifications no longer stick out on a custom theme.
- Notifications shift left when the lyrics sidebar is open — toast rack offsets so notifications don't cover the lyrics.
- Lyric line colors derive from theme tokens — current line uses the theme accent (was hardcoded green); upcoming lines are normal foreground (clearly distinct from the dimmed played lines instead of all looking grey-on-grey).
- Now Playing header in the queue + repeat/shuffle "active" state in the playback bar use the theme accent (`$primary`) instead of the hardcoded `$success` (green).

**Fixes**

- Search "Searching..." indicator no longer sticks forever when the worker is cancelled. `asyncio.CancelledError` inherits from `BaseException` in Python 3.8+, so the existing `except Exception:` block didn't catch it; the loading-text-clear was outside the try/finally and never ran on cancel. Now an explicit handler clears the indicator before re-raising.
- Queue footer no longer duplicates the repeat/shuffle state from the playback bar. Footer now just shows `Tracks: N`. (Closes [#62](https://github.com/peternaame-boop/ytm-player/issues/62), thanks @valkyrieglasc.)
- Sidebar play-from-double-click no longer passes a possibly-`None` track to `play_track` — surfaced when the new typing infrastructure (below) tightened the signature. The `None` case now early-returns gracefully.

**Project**

- Allow textual 8.x — `pyproject.toml` upper bound bumped from `<8.0` to `<9.0`. (Closes [#63](https://github.com/peternaame-boop/ytm-player/pull/63).) All tests pass on textual 8.2.4.
- Mixin attribute typing — new `src/ytm_player/app/_base.py` declares `YTMHostBase`, a `TYPE_CHECKING`-only stub class that mirrors `YTMPlayerApp`'s full attribute and cross-mixin method surface. All eight mixins now extend it. At runtime `YTMHostBase = object` (zero behaviour change); under Pyright/Pylance the editor sees a fully typed `App[None]` subclass and stops emitting "Cannot access attribute X for class FooMixin" noise. Net Pyright count in `src/ytm_player/app/`: **0 errors** (was 52 before this release).
- A new `PageWidget` Protocol replaces bare `Widget` returns where pages are looked up — `_get_current_page()` now returns `PageWidget | None` so `handle_action` and `get_nav_state` calls type-check correctly without `cast()` at every site.
- Pyright now finds the project venv — added `[tool.pyright]` to `pyproject.toml` so editor IDEs (VS Code / Pylance / basedpyright) resolve `textual`, `pytest`, `ytmusicapi` etc. without flooding the Problems panel with false-positive "Import could not be resolved" errors.
- Lyric-current default color is now a single `DEFAULT_LYRIC_CURRENT = "#ff4e45"` constant in `theme.py`, referenced by the dataclass default and both fallback paths in `_app.py`. Previously the three sites disagreed (green vs red) — would have surfaced for users who wrote stripped-down custom themes that defined neither `accent` nor `primary`.

**Tests**

- 545 passing (up from 491 in v1.6). New coverage:
  - Lyric title sanitizer — 29 tests covering original noise patterns + the new feat/ft/featuring/remix/remastered/deluxe/live/acoustic patterns + nested parens + negative passthroughs (`Remix Culture`, `Live and Let Die`, `Acoustic Sessions Vol 1` stay untouched).
  - Resume-on-launch flow — restore + position-guard boundaries + pending-resume match/non-match.
  - `_toggle_like_current` — LIKE↔INDIFFERENT, DISLIKE→LIKE, no-op-with-notify when not signed in, no-op when no current track.
- Cleaned up several pre-existing test `ResourceWarning`s (unclosed file handles in `test_auth_multi_account.py`).

---

### v1.6.0 (2026-04-17)

A polish release focused on diagnostics, stability, security, and performance.
51 commits, 65 new tests (491 total), no headline user-facing features — but a
lot of friction removed from "what do I do when something breaks?"

**New**

- `ytm doctor` command — prints a one-paste diagnostic report (version, Python, mpv, OS, recent log lines, most recent crash trace). Drop the output into a bug report and you've given me everything I need to triage.
- File-based logging — logs now go to `~/.config/ytm-player/logs/ytm.log` (rotated, 5×1MB by default). Previously, log output disappeared into Textual's alt-screen and was unrecoverable. New `[logging]` config section exposes level + rotation knobs. New `--debug` CLI flag enables verbose tracing.
- Crash file capture — unhandled exceptions from any thread now write a full traceback to `~/.config/ytm-player/crashes/` so you have something to attach to issues even when the app is dead.
- Local audio cache now serves replays — previously the `CacheManager` indexed downloaded tracks but `play_track()` never asked it. Cached files (downloads + replayed tracks under your `[cache] max_size_gb`) now bypass yt-dlp entirely for instant playback with no network round-trip.
- Update notifications on startup — a background worker checks PyPI once per 24 hours and surfaces a one-time toast when a newer version is available. Silent on network failure, cache lives at `~/.config/ytm-player/update_check.json`. Opt out by setting `check_for_updates = false` in `[general]`.
- Session.json schema versioning — `schema_version` field lets future format changes detect-and-discard incompatible state instead of silently misbehaving.

**Fixes — security**

- IPC socket is now created owner-only via `umask(0o077)` around `bind()`, in addition to the existing `chmod 0600`.

**Fixes — concurrency / stability**

- Player no longer races on track changes — `_current_track` writes during `play()` and `stop()` are now under `_skip_lock`, preventing torn reads from MPRIS, Discord, Last.fm, and the end-of-track callback during rapid skips (C1).
- mpv crash recovery no longer silently breaks subsequent playback — when `_play_sync` catches `mpv.ShutdownError` and `_try_recover` succeeds, `_current_track` and the `TRACK_CHANGE` event payload now stay consistent. (Two issues here: an initial fix went too far and cleared `_current_track` mid-`play()`, breaking MPRIS/Discord/Last.fm and stopping auto-advance after recovery — caught in final review and reverted.)
- `ytmusic.get_playlist(order=...)` is now safe under concurrent calls — the function monkey-patches ytmusicapi's internal `_send_request` to inject the `order` parameter, but two concurrent calls would corrupt the patch state. Now serialized with an `asyncio.Lock` (C3).
- Settings.toml and session.json writes are now atomic — uses `os.replace` after writing to a `.tmp` sibling, so power loss / `kill -9` mid-write can no longer leave you with a half-written file that crashes startup.
- Session.json corruption no longer crashes startup — bad JSON or schema mismatch falls back to defaults silently. First launch (file absent) stays silent; format mismatch on existing files logs a warning.
- Play failures no longer block retry within 1 second — the double-click debounce stamp now clears on stream-resolution failure or `player.play()` exception, so clicking the same track again after a failure isn't silently swallowed.
- Update check no longer pins users to a stale "latest" if the system clock ever ran fast — negative cache age now triggers a re-fetch.
- Bare `except:` swallows promoted to `logger.exception` in 6 high-impact spots (player callbacks, ytmusic API failures, MPRIS/Discord/Last.fm errors, history logging) — failures that previously vanished now show up in `~/.config/ytm-player/logs/ytm.log` with a stack trace.

**Performance**

- Theme switches feel instant — `theme.toml` is now mtime-cached in `get_css_variables` instead of being parsed from disk on every CSS variable lookup. Edits to `theme.toml` still pick up automatically (mtime invalidates the cache).
- Queue page play-indicator update is O(1) instead of O(n) — switching tracks no longer rewrites every row in queues with hundreds of tracks; only the changed cells are touched.
- Album art rendering moved off the event loop — `_image_to_half_blocks` (PIL resize + per-pixel iteration) now runs in `asyncio.to_thread`, so loading a thumbnail never blocks input handling.
- Library page background workers cancel on page removal — the "fetch remaining tracks" worker for large libraries no longer keeps running after you've navigated away.

**Project**

- Multi-OS CI — workflow now runs on Ubuntu / macOS / Windows × Python 3.12 / 3.13 (was Ubuntu / 3.12 only). Pip cache enabled. `dev` branch now triggers CI too.
- Dependabot configured for weekly grouped pip + GitHub Actions updates.
- Issue templates (bug + feature) — bug template requires `ytm doctor` output; feature template asks contributors to bundle related ideas.
- PR template, CODEOWNERS, SECURITY.md (private vulnerability reporting), CONTRIBUTING.md (dev setup, ruff order, logging conventions, RTL guard, PR norms).
- `.gitattributes` enforces LF line endings; `.gitignore` expanded for IDE/OS junk, `result` (Nix), `*.log`, etc.
- Repo metadata: description, homepage, 9 topics (`tui`, `textual`, `music-player`, `youtube-music`, `terminal`, `mpv`, `python`, `vim-keybindings`, `cli`), GitHub Discussions enabled.

**Tests**

- 65 new tests, 491 total (was 426). New coverage areas: logging + excepthook setup, doctor diagnostics, cache contract, session restore resilience, schema versioning, update check + clock skew, IPC dispatch + seek parsing, key normalization, navigation back-stack invariants, playback debounce + cache-hit path, atomic writes, settings load.
- Help-page coverage enforcement test — fails CI if any `Action` enum member is missing from `ACTION_DESCRIPTIONS` or `ACTION_CATEGORIES` in `ui/pages/help.py`. Catches the "added a new keybind, forgot to document it" drift forever.

---

### v1.5.9 (2026-04-16)

**Fixes**
- Fixed `gc` (jump to current track) crashing on Queue and Liked Songs — `scroll_to_cursor()` doesn't exist on Textual's `DataTable`. Removed the bogus calls; `move_cursor()` already scrolls by default (fixes [#52](https://github.com/peternaame-boop/ytm-player/issues/52), thanks @dmnmsc)
- Fixed Queue "Now Playing" header not updating on track change — `call_from_thread()` raises `RuntimeError` when called from the same thread as the app, and the bare `except` was silently swallowing it. Player events are already on the main thread, so we now call the update method directly (fixes [#56](https://github.com/peternaame-boop/ytm-player/issues/56), thanks @dmnmsc)

---

### v1.5.8 (2026-04-16)

**New**
- Track filter (`/`) extended to Queue and Liked Songs pages — search by title or artist, debounced, queue/reorder/delete operations correctly map filtered indices to real positions (fixes [#48](https://github.com/peternaame-boop/ytm-player/issues/48), thanks @dmnmsc)
- Liked Songs loading status — footer now shows "loading more…" while background fetch runs for libraries beyond 300 tracks (fixes [#51](https://github.com/peternaame-boop/ytm-player/issues/51), thanks @dmnmsc)

**Fixes**
- Fixed RTL text bleed across visual boundaries — RTL track titles were appearing duplicated at row edges and bleeding into the playback bar's volume/repeat/shuffle area. All user text fragments are now wrapped with Unicode FSI/PDI isolation marks. Includes a regression test that mechanically prevents the bug from reappearing
- Fixed app crash on artist→album→album navigation — `DuplicateIds` race when navigating between two ContextPages with the same widget ID. Each ContextPage instance now uses a unique sequence-based ID (fixes [#47](https://github.com/peternaame-boop/ytm-player/issues/47), thanks @dmnmsc)
- Fixed `g c` (jump to current track) doing nothing — action was wired to the keymap but no page implemented it. Now works on Library, Context, Browse, Search, Queue, Liked Songs, and Recently Played (fixes [#49](https://github.com/peternaame-boop/ytm-player/issues/49), thanks @dmnmsc)
- Fixed `g space` (current context) crashing — was navigating to ContextPage without required `context_type`/`context_id`. Now extracts album info from the currently playing track, or shows a notification if unavailable (fixes [#50](https://github.com/peternaame-boop/ytm-player/issues/50), thanks @dmnmsc)

---

### v1.5.7 (2026-04-15)

**New**
- Track filter on Library and Context pages — press `/` to filter tracks by title, artist, or album in real-time. Enter keeps filtered view, Escape clears it. Queue integration preserved (fixes [#43](https://github.com/peternaame-boop/ytm-player/issues/43), thanks @dmnmsc; fixes [#46](https://github.com/peternaame-boop/ytm-player/issues/46), thanks @valkyrieglasc)
- Optimistic sidebar updates — creating or deleting a playlist updates the sidebar instantly without an API round-trip or delay (thanks @Villoh, PR [#41](https://github.com/peternaame-boop/ytm-player/pull/41))

**Fixes**
- Fixed app crash when opening album from artist page — `DuplicateIds` caused by uncancelled background workers on page navigation. Workers now cancelled on page removal (fixes [#44](https://github.com/peternaame-boop/ytm-player/issues/44), thanks @dmnmsc)
- Fixed like/unlike toggle always showing "Like" — `likeStatus` was stripped during track normalization and not updated after rating. Now preserved and updated in real-time (fixes [#45](https://github.com/peternaame-boop/ytm-player/issues/45), thanks @dmnmsc)
- Fixed `album_art = false` and `progress_style = "line"` config options being ignored (fixes [#42](https://github.com/peternaame-boop/ytm-player/issues/42), thanks @valkyrieglasc)
- Fixed `theme.toml` base color overrides (background, primary, etc.) not applying after Textual theme integration — user customizations now override the active theme (fixes [#42](https://github.com/peternaame-boop/ytm-player/issues/42))

---

### v1.5.6 (2026-04-10)

**New**
- Bouncing playlist names — long playlist titles in the sidebar now bounce (scroll back and forth) when highlighted, so you can read the full name (thanks @dmnmsc, [#32](https://github.com/peternaame-boop/ytm-player/issues/32))

**Fixes**
- Fixed "Add to Queue" / "Play Next" doing nothing — popup dismissal was triggering a spurious track selection that cleared the queue immediately after adding. Now suppressed with a refocus guard (fixes [#30](https://github.com/peternaame-boop/ytm-player/issues/30), thanks @dmnmsc)
- Fixed sidebar "Add to Queue" only showing a notification without actually adding tracks — now fetches the playlist and queues all tracks (fixes [#30](https://github.com/peternaame-boop/ytm-player/issues/30))
- Fixed theme colors not updating in header bar when switching themes — migrated toggle labels from Rich Text to CSS classes (fixes [#37](https://github.com/peternaame-boop/ytm-player/issues/37), thanks @Villoh)
- Fixed progress bar colors not updating on theme switch — colors now read at render time instead of construction time (fixes [#39](https://github.com/peternaame-boop/ytm-player/issues/39), thanks @Villoh)
- Fixed hover backgrounds making text invisible — all hover states now use `$accent 30%` instead of `$border`
- Removed 500-track limit on session queue restore — full queue now persists regardless of size (fixes [#31](https://github.com/peternaame-boop/ytm-player/issues/31), thanks @dmnmsc)

---

### v1.5.5 (2026-04-09)

**New**
- Textual native theme support — all 18 built-in themes (nord, dracula, gruvbox, catppuccin, etc.) work via `Ctrl+P` → "Change theme". Theme selection persists across sessions. Custom **ytm-dark** theme registered as default (fixes [#23](https://github.com/peternaame-boop/ytm-player/issues/23), thanks @dsafxP)
- CLI like/dislike/unlike commands — `ytm like`, `ytm dislike`, `ytm unlike` to rate the current track via IPC (thanks @moschi, PR [#26](https://github.com/peternaame-boop/ytm-player/pull/26))
- Brand Account support — set `brand_account_id` in `config.toml` under `[general]` to use a YouTube Brand Account (fixes [#25](https://github.com/peternaame-boop/ytm-player/issues/25), thanks @nitsujri)
- Toggle album art — `Ctrl+A` hides/shows album art in the playback bar (thanks @valkyrieglasc, [#28](https://github.com/peternaame-boop/ytm-player/issues/28))

**Fixes**
- Fixed large playlists (1500+) failing to load — progressive loading fetches first 300 tracks immediately, then loads remaining in background with extended timeout. Applies to playlists, liked songs, and sidebar play-all (fixes [#24](https://github.com/peternaame-boop/ytm-player/issues/24), thanks @Jxshua17 @dmnmsc @nitsujri; fixes [#27](https://github.com/peternaame-boop/ytm-player/issues/27), thanks @valkyrieglasc)

---

### v1.5.2 (2026-03-17)

**Fixes**
- Fixed RTL lyrics displaying in wrong word order — disabled manual RTL reordering which was reversing text on both BiDi and non-BiDi terminals. Added `bidi_mode` config option (`auto`/`reorder`/`passthrough`) for users who need explicit control
- Fixed lyrics sidebar ignoring custom `lyrics_played`/`lyrics_current`/`lyrics_upcoming` theme colors — CSS was wired to wrong variables (`$success`, `$text-muted`, `$text` instead of `$lyrics-*`)
- Fixed album art placeholder and context page cursor using hard-coded colors instead of theme — all UI colors now flow through `ThemeColors` for full theme customization support

---

### v1.5.1 (2026-03-12)

**New**
- Multi-account auth support — `ytm setup` now handles Google accounts logged into multiple YouTube Music profiles, probing all `x-goog-authuser` indices automatically (thanks @glywil, PR [#15](https://github.com/peternaame-boop/ytm-player/pull/15))
- Gentoo packaging — available in the GURU overlay via `emerge media-sound/ytm-player` (thanks @dsafxP, PR [#21](https://github.com/peternaame-boop/ytm-player/pull/21))

**Fixes**
- Fixed `nix build` failing — added missing `pillow` to core Nix deps, resolved `python-mpv` vs `mpv` dist-info name mismatch with `pythonRemoveDeps`, added `transliteration` optional dep (fixes [#18](https://github.com/peternaame-boop/ytm-player/issues/18), thanks @muhmud)
- Fixed Browse page showing "Unknown" artist in notifications — raw API items now normalized before playback (fixes [#19](https://github.com/peternaame-boop/ytm-player/issues/19), thanks @Gimar250, PR [#20](https://github.com/peternaame-boop/ytm-player/pull/20))
- Fixed `d d` / `delete` keybind not removing tracks on the Queue page — was matching `TRACK_ACTIONS` instead of `DELETE_ITEM` (fixes [#22](https://github.com/peternaame-boop/ytm-player/issues/22), thanks @CarterSnich)

---

### v1.5.0 (2026-03-09)

**Refactor**
- Decomposed `app.py` (2000+ lines) into a package with 7 focused mixins — playback, navigation, keys, session, sidebar, track actions, MPRIS, IPC. Zero behavioral changes; all 370 tests pass unchanged.

**New**
- Lyrics transliteration — toggle ASCII transliteration of non-Latin lyrics with `T` (Shift+T), useful for Japanese, Korean, Arabic, Cyrillic, etc. Requires optional `anyascii` package (thanks @Kineforce, [#14](https://github.com/peternaame-boop/ytm-player/issues/14))
- Add to Library button — albums and playlists that aren't in your library now show a clickable `[+ Add to Library]` button on their context page
- Delete/remove playlist confirmation — deleting a playlist now asks for confirmation first; also supports removing non-owned playlists from your library
- Search mode toggle is now clickable — click the `Music`/`All` label to toggle (was keyboard-only before)
- Page state preservation — Search, Browse, Liked Songs, and Recently Played pages now remember their state (query, results, cursor position, active tab) when navigating away and back

**Fixes**
- Fixed RTL text word order — restored BiDi reordering for Arabic/Hebrew track titles, artists, and lyrics (UAX #9 algorithm)
- Fixed right-click targeting wrong track — right-click now opens actions for the row under the cursor, not the previously highlighted row (thanks @glywil, PR [#16](https://github.com/peternaame-boop/ytm-player/pull/16))
- Fixed artist search results showing "Unknown" instead of artist name
- Fixed radio tracks crashing playback — radio API responses are now normalized before adding to queue
- Fixed browse page items not opening — capitalized `resultType` values and missing routing for radio/mix entries
- Fixed session restore crash when saved tracks become unavailable (deleted/region-locked videos)
- Fixed actions popup crash when album field is a plain string instead of dict (thanks @glywil, PR [#16](https://github.com/peternaame-boop/ytm-player/pull/16))
- Fixed double-click playing a track twice (1-second debounce)
- Fixed back navigation ping-ponging between two pages
- Fixed lyrics sidebar performance — batch-mount widgets instead of mounting individually
- Fixed transliteration toggle highlight flash — forces immediate lyrics re-sync after toggle
- Transliteration toggle state now persists across restarts via session.json
- Sidebar refreshes after adding or removing playlists from library

---

### v1.4.0 (2026-03-07)

**New**
- Native macOS media key and Now Playing support — hardware media keys (play/pause, next, previous) now work via Quartz event taps, and track metadata appears in macOS Control Center (thanks @Thayrov, PR [#12](https://github.com/peternaame-boop/ytm-player/pull/12))

**Fixes**
- Documented how to install optional features for AUR users — pip doesn't work on Arch due to PEP 668 (fixes [#13](https://github.com/peternaame-boop/ytm-player/issues/13))

---

### v1.3.6 (2026-03-05)

**Windows Fix**
- Fixed mpv crash inside Textual TUI on Windows — locale was being set via the legacy `msvcrt.dll` CRT, but Python 3.12+ uses `ucrtbase.dll`, so the `setlocale(LC_NUMERIC, "C")` call had no effect and mpv refused to initialize (access violation on null handle)
- Fixed mpv DLL not found on Windows when installed via scoop/chocolatey — auto-locates `libmpv-2.dll` in common install directories
- Improved error messages for service init failures

### v1.3.4 (2026-03-05)

**Windows Compatibility**
- Fixed crash on Windows caused by config file encoding (em-dash written as cp1252 instead of UTF-8)
- Added TCP localhost IPC for Windows (Unix sockets unavailable), with proper stale port cleanup
- Fixed PID liveness check on Windows using `OpenProcess` API
- Config now stored in `%APPDATA%\ytm-player`, cache in `%LOCALAPPDATA%\ytm-player`
- Fixed crash log path, libc detection (`msvcrt`), and `ytm config` command for Windows
- Added `encoding="utf-8"` to all file I/O (Windows defaults to cp1252)
- Added clipboard support for Windows (`Set-Clipboard`) and macOS (`pbcopy`)
- Corrupted config files are backed up to `.toml.bak` before recreating defaults

### v1.3.3 (2026-03-05)

**Bug Fixes**
- Disabled media key listener on macOS — pynput can't intercept keys, causing previous track to open iTunes. Media keys on macOS will be implemented properly with MPRemoteCommandCenter in a future release.
- Suppressed noisy warnings on macOS startup ("dbus-next not installed", "process not trusted")

### v1.3.1 (2026-03-05)

**New**
- Cross-platform media key support — play/pause, next, and previous media keys now work on macOS and Windows via `pynput` (Linux already supported via MPRIS)
- Pillow (album art) is now a default dependency — no longer requires `pip install ytm-player[images]`

### v1.3.0 (2026-03-05)

**New**
- `ytm setup --manual` — skip browser detection, paste request headers directly (thanks @uhs-robert, [#10](https://github.com/peternaame-boop/ytm-player/issues/10))
- `ytm setup --browser <name>` — extract cookies from a specific browser (chrome, firefox, brave, etc.)
- Theme variables `$surface` and `$text` now properly defined — fixes unstyled popups, sidebars, and scrollbars (thanks @ahloiscreamo, [#6](https://github.com/peternaame-boop/ytm-player/issues/6))
- NixOS packaging — `flake.nix` with `ytm-player` and `ytm-player-full` packages, dev shell, and overlay
- Free-tier support — tracks without a video ID (Premium-only) are now filtered from playlists/albums/search with an "unavailable tracks hidden" notice, instead of silently failing on click

**Bug Fixes**
- Fixed MPRIS crash (`SignatureBodyMismatchError`) when track metadata contains None values (thanks @markvincze, [#9](https://github.com/peternaame-boop/ytm-player/issues/9))
- Fixed large playlists only loading 200-300 songs — now fetches all tracks via ytmusicapi pagination (thanks @bananarne, [#5](https://github.com/peternaame-boop/ytm-player/issues/5))
- Fixed search results missing `video_id` — songs from search couldn't play (thanks @firedev, PR [#4](https://github.com/peternaame-boop/ytm-player/pull/4))
- Fixed browse/charts page same missing normalization bug
- Fixed macOS `Player` init crash — hardcoded `libc.so.6` replaced with platform-aware detection (thanks @hanandewa5, PR [#2](https://github.com/peternaame-boop/ytm-player/pull/2))
- Fixed auth validation crashing with raw tracebacks on network errors — now shows friendly message with recovery suggestion (thanks @CarterSnich [#7](https://github.com/peternaame-boop/ytm-player/issues/7), @Tohbuu [#11](https://github.com/peternaame-boop/ytm-player/issues/11))
- Rewrote auth validation to use `get_account_info()` instead of monkey-patching — more reliable across platforms and ytmusicapi versions
- Unplayable tracks (no video ID) now auto-skip to the next track instead of stopping playback dead

---

### v1.2.11 (2026-03-03)

**New**
- yt-dlp configuration support: `cookies.txt` auth, `remote_components`, `js_runtimes` via `[yt_dlp]` config section (thanks @gitiy1, [PR #1](https://github.com/peternaame-boop/ytm-player/pull/1))

### v1.2.10 (2026-03-03)

**Bug Fixes**
- Fixed RTL text (Arabic/Hebrew) in track table columns — added BiDi isolation (LRI/PDI) so RTL album/artist names don't bleed into adjacent columns

### v1.2.9 (2026-03-02)

**New**
- Published to PyPI — install with `pip install ytm-player` or `pipx install ytm-player`

**Bug Fixes**
- Fixed track auto-advance stopping after song ends — three root causes: mpv end-file reason read from wrong event object, event loop reference permanently lost under thread race condition, and `CancelledError` not caught in track-end handler
- Fixed RTL text (Arabic/Hebrew) display — removed manual word-reordering that double-reversed text on terminals with native BiDi support; added Unicode directional isolation to prevent RTL titles from displacing playback bar controls
- Fixed shuffle state corrupting queue after clear, and `jump_to()` desyncing the current index when shuffle is on
- Fixed column resize triggering sort, and Title column not staying at user-set width

### v1.2.4 (2026-02-17)

**Bug Fixes**
- Fixed intermittent playback stopping mid-queue — consecutive stream failures (stale yt-dlp session, network hiccup) now reset the stream resolver automatically, preventing the queue index from advancing past all remaining tracks
- Fixed playlists appearing empty after prolonged use — YTMusic API client now auto-reinitializes after 3 consecutive failures (handles expired sessions/cookies)
- Fixed misleading "Queue is empty" message when queue has tracks but playback index reached the end — now says "End of queue"

### v1.2.3 (2026-02-17)

**Bug Fixes**
- Fixed MPRIS silently disabled on Python 3.14 — `from __future__ import annotations` caused dbus-next to reject `-> None` return types, disabling media keys and desktop player widgets
- Fixed RTL lyrics line-wrap reading bottom-to-top — long lines are now pre-wrapped in logical order before reordering, so sentence start is on top

### v1.2.2 (2026-02-15)

**Bug Fixes**
- Fixed play/pause doing nothing after session restore — player had no stream loaded so toggling pause was a no-op; now starts playback from the restored queue position
- Fixed MPRIS play/pause also being a no-op after session restore (same root cause)
- Fixed RTL (Hebrew, Arabic, etc.) lyrics displaying in wrong order — segment-level reordering now renders bidirectional text correctly
- Fixed lyrics sidebar crash from dict-style access on LyricLine objects — switched to attribute access
- Fixed lyrics sidebar unnecessarily reloading when reopened for the same track

**Features**
- Right-click on playback bar (album art or track info) now opens the track actions popup, matching right-click behavior on track tables

### v1.2.1 (2026-02-14)

**Features**
- Synced (timestamped) lyrics — lyrics highlight and auto-scroll with the song in real time
- Click-to-seek on lyrics — click any synced lyric line to jump to that part of the song
- LRCLIB.net fallback — when YouTube Music doesn't provide synced lyrics, fetches them from LRCLIB.net (no API key needed)
- Lyrics auto-center — current lyric line stays centered in the viewport as the song plays

**Bug Fixes**
- Fixed crash on song change with both sidebars open — Textual's `LoadingIndicator` timer raced with widget pruning during track transitions
- Fixed crash from unhandled exceptions in player event callbacks — sync callbacks dispatched via `call_soon_threadsafe` now wrapped in error handlers
- Wrapped `notify()` and `_prefetch_next_track()` in `_on_track_change` with try/except to prevent crashes during app transitions
- Lyrics sidebar always starts closed on launch regardless of previous session state
- Fixed synced lyrics not being requested — `timestamps=True` now passed to ytmusicapi with automatic fallback to plain text

### v1.2.0 (2026-02-14)

**Features**
- Persistent playlist sidebar (left) — visible across all views, toggleable per-view with state memory (`Ctrl+e`)
- Persistent lyrics sidebar (right) — synced lyrics with auto-scroll, replaces the old full-page Lyrics view (`l` to toggle)
- Header bar with toggle buttons for both sidebars
- Pinned navigation items (Liked Songs, Recently Played) in the playlist sidebar
- Per-view sidebar state — sidebar visibility is remembered per page and restored on navigation
- Lyrics sidebar registers player events lazily and skips updates when hidden for performance

**Removed**
- Lyrics page — replaced entirely by the lyrics sidebar
- Lyrics button from footer bar — use header bar toggle or `l` key instead

---

### v1.1.3 (2026-02-14)

**Features**
- Click column headers to sort — click any column header (Title, Artist, Album, Duration, #) to sort; click again to reverse
- Drag-to-resize columns — drag column header borders to adjust widths; Title column auto-fills remaining space
- Playlist sort order — requests "recently added" order from YouTube Music API when loading playlists
- `#` column preserves original playlist position and can be clicked to reset sort order

**Bug Fixes**
- Fixed click-to-sort not working (ColumnKey.value vs str(ColumnKey) mismatch)
- Fixed horizontal scroll position resetting when sorting
- Fixed session restore with shuffle — queue is now populated before enabling shuffle so the saved index points at the correct track
- Fixed `jump_to_real()` fallback when track not in shuffle order (was a silent no-op, now inserts into shuffle order)
- Fixed crash on Python 3.14 from dbus-next annotation parsing (MPRIS gracefully disables)
- Pinned Textual dependency to `>=7.0,<8.0` to protect against internal API breakage

### v1.1.2 (2026-02-14)

**Features**
- Shuffle-aware playlist playback — double-clicking a playlist with shuffle on now starts from a random track instead of always the first
- Table sorting — sort any track list by Title (`s t`), Artist (`s a`), Album (`s A`), Duration (`s d`), or reverse (`s r`)
- Session resume — on startup, restores last queue position and shows the track in the footer (without auto-playing)
- Quit action (`q` / `Ctrl+Q`) — clean exit that clears resume state; unclean exits (terminal close/kill) preserve it

**Bug Fixes**
- Fixed queue position desync when selecting tracks with shuffle enabled (all pages: Library, Context, Liked Songs, Recently Played)
- Fixed search mode toggle showing empty box due to Rich markup interpretation (`[Music]` → `Music`)

### v1.1.1 (2026-02-13)

**Bug Fixes**
- Fixed right-click on track table triggering playback instead of only opening context menu
- Fixed auto-advance bug: songs after the 2nd track would not play due to stale `_end_file_skip` counter
- Fixed thread-safe skip counter — check+increment now atomic under lock
- Fixed duplicate end-file events causing track skipping (debounce guard)
- Fixed `player.play()` failure leaving stale `_current_track` state
- Fixed unhandled exceptions in stream resolution crashing the playback chain
- Fixed `player.play()` exceptions silently stopping all playback
- Fixed Browse page crash from unawaited async mount operations
- Fixed API error tracebacks polluting TUI with red stderr overlay
- Reset skip counter on mpv crash recovery
- Fixed terminal image protocol detection (`TERM_FEATURES` returning wrong protocol)
- Fixed encapsulation break (cache private method called from app)
- Always-visible Lyrics button in footer bar (dimmed when no track playing, active during playback)
- Clicking the active footer page navigates back to the previous page
- Library remembers selected playlist when navigating away and back
- Click outside popups to dismiss — actions menu and Spotify import close when clicking the background

### v1.1.0 (2026-02-12)

**Features**
- Liked Songs page (`g y`) — browse and play your liked music
- Recently Played page (`g r`) — local history from SQLite
- Download for offline — right-click any track → "Download for Offline"
- Discord Rich Presence — show what you're listening to (optional, `pip install -e ".[discord]"`)
- Last.fm scrobbling — automatic scrobbling + Now Playing (optional, `pip install -e ".[lastfm]"`)
- Gapless playback enabled by default
- Queue persistence across restarts (saved in session.json)
- Track change notifications wired to `[notifications]` config section
- New config sections: `[discord]`, `[lastfm]`, `[playback].gapless`, `[playback].api_timeout`
- Configurable column widths via `[ui]` settings (`col_index`, `col_title`, `col_artist`, `col_album`, `col_duration`)
- Liked Songs and Recently Played pinned in library sidebar

**Security & Stability**
- IPC socket security hardening (permissions, command whitelist, input validation)
- File permissions hardened to 0o600 across all config/state files
- Thread safety for queue manager (prevents race conditions)
- mpv crash detection and automatic recovery
- Auth validation distinguishes network errors from invalid credentials
- Disk-full (OSError) handling in cache and history managers
- API timeout handling (15s default, prevents TUI hangs on slow networks)

**Performance**
- Batch DELETE for cache eviction (replaces per-row deletes)
- Deferred cache-hit commits (every 10 hits instead of every hit)
- Reuse yt-dlp instance across stream resolves (was creating new per call)
- Concurrent Spotify import matching with ThreadPoolExecutor
- Stream URL expiry checks before playback

**Testing & CI**
- GitHub Actions CI pipeline (ruff lint + pytest with coverage)
- 231 tests covering queue, IPC, stream resolver, cache, history, auth, downloads, Discord RPC, Last.fm, and settings

---

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
