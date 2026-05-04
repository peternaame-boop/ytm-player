# Contributing to ytm-player

Thanks for considering a contribution! This document covers what you need
to know to get a PR merged.

## Development setup

```bash
git clone https://github.com/peternaame-boop/ytm-player.git
cd ytm-player
python -m venv .venv
source .venv/bin/activate
pip install -e ".[spotify,mpris,discord,lastfm,transliteration,dev]"
pre-commit install
```

System dependency: `mpv` must be installed system-wide (`sudo pacman -S mpv` on Arch, `brew install mpv` on macOS).

## Pre-commit hooks

The repo uses [pre-commit](https://pre-commit.com/) to run ruff and pyright on commit and pytest on push. `pre-commit install` sets up both hook types automatically (configured via `default_install_hook_types` in `.pre-commit-config.yaml`).

If you need to skip hooks for a WIP commit, use `git commit --no-verify` — but make sure CI passes before requesting review.

## Testing

```bash
pytest                                           # full suite
pytest --cov=ytm_player --cov-report=term-missing  # with coverage
pytest tests/test_services/test_queue.py         # one file
pytest tests/test_services/test_queue.py::test_add_track -v  # one test
```

UI code (`src/ytm_player/ui/*`) is excluded from coverage; services and
config are covered.

## Logging

Logs go to `~/.config/ytm-player/logs/ytm.log`. **Never use `print()`**
in non-CLI code — Textual's alt-screen swallows stderr.

For caught exceptions you want to surface in bug reports, use
`logger.exception("descriptive message")` — *not* `logger.debug(...,
exc_info=True)`, which silently routes to debug.

## Track dict

All services use a standardised track dict: `video_id`, `title`, `artist`,
`artists` (list of `{name, id}` dicts), `album`, `album_id`, `duration`
(seconds, int or None), `thumbnail_url`, `is_video`. Use
`normalize_tracks()` from `utils/formatting.py` when ingesting raw
ytmusicapi data.

## RTL text

Any user-supplied text fragment concatenated into a line with other
fragments (table cells, playback bar widgets) MUST be wrapped with
`isolate_bidi()` from `utils/bidi.py` AFTER `truncate()`. Otherwise RTL
text bleeds across visual boundaries on some terminals. There's a
regression test (`TestIsolateBidiCallSites`) that fails CI if a render
site stops calling `isolate_bidi`.

## Feature requests

Feature requests are welcome. The most useful ones describe the
problem or preference, not the fix — what you were trying to do, what
got in your way, what would feel better. Suggested solutions are fine
as context, but final scope and design decisions follow the overall direction of the project.

If a suggestion doesn't match a stated problem, I'll usually ask
"what's the underlying friction?" before deciding anything. Not
because I'm dismissing the idea — because I want to make sure I build
the right thing for you, not just the thing you asked for.

Bundling multiple ideas into one issue is fine and encouraged.
Bundling bugs with feature requests isn't — file bugs separately so
they don't get buried.

## PR norms

- One concern per PR. Don't bundle "fix bug + add feature + refactor".
- Reference the issue in the PR description (`Closes #123`).
- Update `CHANGELOG.md` if your change is user-visible.
- The CI matrix runs Ubuntu + macOS + Windows on Python 3.10 and 3.14. Make sure all green before requesting review.

## Python version compatibility

The project supports Python 3.10+. Two compatibility patterns matter when editing source:

**3.11+ stdlib symbols** — if you need `tomllib`, `typing.Self`, or `enum.StrEnum`, use a `sys.version_info` shim instead of importing directly:

```python
import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    # Python 3.10 backport via PyPI
    import tomli as tomllib  # pyright: ignore[reportMissingImports]
```

Pyright narrows on `sys.version_info` correctly, so the `# pyright: ignore` only needs to sit on the else branch. `tomli` and `typing_extensions` are already declared as conditional deps in `pyproject.toml` (`python_version < "3.11"`).

**Mixin attribute typing** — mixins in `src/ytm_player/app/_*.py` (PlaybackMixin, SessionMixin, etc.) extend `YTMHostBase` from `app/_base.py`, a `TYPE_CHECKING`-only stub class that mirrors the runtime instance attribute surface. At runtime `YTMHostBase = object` — zero behaviour change. If you add a new instance attribute to `YTMPlayerApp.__init__` and reference it from a mixin, also declare it on `YTMHostBase` so Pyright doesn't emit "Cannot access attribute X" noise.

## Theming & UI

ytm-player ships with a CSS-variable–based theming system. Users can override every color by editing `~/.config/ytm-player/theme.toml`, so widget code must respect that contract.

**Rule: no hardcoded hex colors in widget CSS.**

All UI colors flow through theme variables defined in `src/ytm_player/ui/theme.py:ThemeColors`. Use the variables in your `DEFAULT_CSS` strings:

| Variable | Use for |
|----------|---------|
| `$primary` | Active states, accents, focus indicators |
| `$secondary` | Secondary accents |
| `$text` | Primary readable text |
| `$text-muted` | Subtitles, captions, less-important info |
| `$surface` | Background fills for distinct UI areas |
| `$border` | Lines, separators, edges |
| `$accent` | High-emphasis highlights |

**Don't write:**

```css
border-bottom: solid #444444;
color: #2ecc71;
```

**Do write:**

```css
border-bottom: solid $border;
color: $primary;
```

Hardcoded hex colors break theme switching and make custom `theme.toml` files inconsistent. The exception is `theme.py:ThemeColors` itself, which defines the defaults. Lyrics-current color is themed via `lyrics_current` (default `#2ecc71`) — even that goes through the theme system, never hardcoded in widget CSS.

When adding a new UI element with a color requirement that doesn't fit existing variables, propose adding a new variable to `ThemeColors` (with a sensible default) rather than hardcoding.

## Architecture pointers

- `app/` — main app split into mixins (lifecycle, playback, navigation, etc.)
- `services/` — backend singletons (Player, QueueManager, YTMusicService, etc.)
- `ui/` — Textual widgets (pages, sidebars, popups, widgets)
- `config/` — settings, keymap, theme, paths

For deeper context, read `CLAUDE.md` in the repo root — it's the
project's living architecture doc.
