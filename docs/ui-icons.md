# UI icons

## Purpose

`src/ytm_player/ui/icons.py` is the single place the UI gets its glyphs from. Pages and widgets call `icon("play")` instead of hard-coding a symbol. The module picks between two sets so the TUI looks sharp with a Nerd Font and still works without one.

## Owned concepts

- **Icon name.** A string key such as `play`, `heart_off`, `queue`. Both sets define the same names.
- **Nerd set.** Material Design glyphs from Nerd Fonts (`nf-md-*`, U+F0000 and up). Drawn for icon use, so they align better than mixed Unicode symbols pulled from fallback fonts.
- **Unicode set.** The original glyphs. Needs no special font.
- **Active set.** The set in use for this process, `"nerd"` or `"unicode"`.
- **Probe codepoint.** `f040a` (`nf-md-play`). If a font has it, the font carries the Material Design range.

## Owned data

Two module-level dicts, `_NERD` and `_UNICODE`, and the `_SETS` map of set name to dict. `active_set()` caches its answer with `functools.cache` for the life of the process.

Some names share a glyph on purpose. `shuffle_off` and `repeat_off` reuse the `shuffle` and `repeat` glyphs in the nerd set, and the caller greys them. `liked` reuses `heart_off`.

## Owned decisions

How `active_set()` resolves `[ui] icons`:

1. `"nerd"` or `"unicode"` wins outright.
2. Any other value except `"auto"` logs a warning (`Unknown [ui] icons value ...`) and is treated as `"auto"`.
3. `"auto"` (the default) returns `"nerd"` when `fc-list ":charset=f040a" family` prints anything, else `"unicode"`.

`volume_icon(volume)` owns the volume thresholds: 0 or below is `volume_off`, under 34 is `volume_low`, under 67 is `volume_medium`, otherwise `volume_high`.

## Interfaces

- `icon(name) -> str`. Glyph for `name` in the active set. Raises `KeyError` on an unknown name.
- `volume_icon(volume: int) -> str`. Speaker glyph for a 0 to 100 level.
- `active_set() -> str`. Exposed for tests and diagnostics.
- Config: `[ui] icons` in `config.toml`, a string defaulting to `"auto"`. See [configuration.md](configuration.md).

Callers today: `playback_bar.py`, `header_bar.py`, `pages/{home,library,search,explore,context,liked_songs,recently_played}.py`, `sidebars/playlist_sidebar.py`, `widgets/{album_art,cover,track_table}.py`.

## Dependencies

- `ytm_player.config.settings.get_settings`, imported inside `active_set()` to avoid an import cycle at module load.
- `fc-list` from fontconfig, found with `shutil.which`. Called with a 2 second timeout.
- A terminal font that actually contains the Nerd glyphs. Detection only proves a font is installed on the system, not that the terminal uses it.

## Invariants

- `_NERD` and `_UNICODE` have identical keys. A test enforces it.
- Every nerd glyph sits in U+F0000 to U+F1AF0. A test enforces it.
- Adding an icon means adding it to both dicts in the same change.
- `active_set()` is cached. Changing `[ui] icons` takes effect on the next launch, not live.

## Failure boundaries

- No `fc-list` on `PATH`, a non-zero or empty result, a timeout, or an `OSError` all mean "no Nerd Font". The module returns `"unicode"` and never raises from detection.
- A misspelled `[ui] icons` value degrades to auto detection with one log warning. It does not stop startup.
- An unknown icon name is a programming error and raises `KeyError` at the call site.

## Text weight

Rendering uses the regular face of the terminal font for all text in the app. Rendering bold and italic in kitty needs separate font faces. Mixed faces made the app look like it used several fonts. Rendering now goes through a Textual line filter, added in `ui/regular_weight.py`, that removes bold and italic at render time. Rendering through the filter covers CSS `text-style`, Rich styles and Textual's own widgets. Rendering emphasis still works through colour and dim. Tests: `tests/test_ui/test_regular_weight.py`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Boxes or question marks instead of icons | Auto found a Nerd Font on the system but the terminal uses another font. Set the terminal font to the Nerd Font, or set `icons = "unicode"`. |
| Plain Unicode symbols with a Nerd Font installed | `fc-list` is missing, or the font predates Nerd Fonts v3, which moved Material Design icons to U+F0001 and up. Update the font, or set `icons = "nerd"` to force it. |
| Warning `Unknown [ui] icons value` in `~/.config/ytm-player/logs/ytm.log` | Value is not `nerd`, `unicode` or `auto`. Fix the spelling. |

## See also

- [configuration.md](configuration.md), the `[ui]` section.
- [architecture.md](architecture.md), the `ui/` layer.
- Tests: `tests/test_ui/test_icons.py`.
