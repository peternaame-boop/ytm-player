"""Icon glyphs for the UI, in one place.

Two sets share the same names. The ``nerd`` set uses Material Design icons from
Nerd Fonts (the ``nf-md-*`` range, U+F0000 and up). Those glyphs are drawn for
icon use, so they look sharper and line up better than the mixed Unicode symbols
a terminal pulls from fallback fonts. The ``unicode`` set is the original glyphs
and needs no special font.

``[ui] icons`` picks the set: ``"nerd"``, ``"unicode"``, or ``"auto"`` (default).
Auto uses Nerd Font icons when fontconfig reports a font that has them.
"""

from __future__ import annotations

import functools
import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)

_NERD: dict[str, str] = {
    # Transport and playback state.
    "play": "\U000f040a",  # nf-md-play
    "pause": "\U000f03e4",  # nf-md-pause
    "stop": "\U000f04db",  # nf-md-stop
    "prev": "\U000f04ae",  # nf-md-skip_previous
    "next": "\U000f04ad",  # nf-md-skip_next
    "shuffle": "\U000f049d",  # nf-md-shuffle
    "shuffle_off": "\U000f049d",  # same icon, greyed by the caller
    "repeat": "\U000f0456",  # nf-md-repeat
    "repeat_off": "\U000f0456",  # same icon, greyed by the caller
    "repeat_one": "\U000f0458",  # nf-md-repeat_once
    "volume_off": "\U000f0581",  # nf-md-volume_off
    "volume_low": "\U000f057f",  # nf-md-volume_low
    "volume_medium": "\U000f0580",  # nf-md-volume_medium
    "volume_high": "\U000f057e",  # nf-md-volume_high
    "heart": "\U000f02d1",  # nf-md-heart
    "heart_off": "\U000f02d5",  # nf-md-heart_outline
    # Actions.
    "radio": "\U000f043b",  # nf-md-radio_tower
    "save": "\U000f0412",  # nf-md-playlist_plus
    "saved": "\U000f05c7",  # nf-md-playlist_check
    # Navigation.
    "menu": "\U000f035c",  # nf-md-menu
    "back": "\U000f004d",  # nf-md-arrow_left
    "forward": "\U000f0054",  # nf-md-arrow_right
    "search": "\U000f0349",  # nf-md-magnify
    "lyrics": "\U000f0387",  # nf-md-music_note
    "chevron_left": "\U000f0141",  # nf-md-chevron_left
    "chevron_right": "\U000f0142",  # nf-md-chevron_right
    "home": "\U000f02dc",  # nf-md-home
    "explore": "\U000f018c",  # nf-md-compass_outline
    "library": "\U000f0333",  # nf-md-music_box_multiple
    "liked": "\U000f02d5",  # nf-md-heart_outline
    "recent": "\U000f02da",  # nf-md-history
    "discovery": "\U000f0674",  # nf-md-creation
    "queue": "\U000f0cb8",  # nf-md-playlist_music
    "import": "\U000f01da",  # nf-md-download
    "new_releases": "\U000f0394",  # nf-md-new_box
    "charts": "\U000f0535",  # nf-md-trending_up
    "moods": "\U000f01f5",  # nf-md-emoticon_happy_outline
    # Player bar, as on the web app.
    "thumb_up": "\U000f0513",  # nf-md-thumb_up
    "thumb_up_off": "\U000f0514",  # nf-md-thumb_up_outline
    "thumb_down": "\U000f0511",  # nf-md-thumb_down
    "thumb_down_off": "\U000f0512",  # nf-md-thumb_down_outline
    "more": "\U000f01d9",  # nf-md-dots_vertical
    "expand": "\U000f0143",  # nf-md-chevron_up
    "collapse": "\U000f0140",  # nf-md-chevron_down
    # Placeholders.
    "note": "\U000f075a",  # nf-md-music
}

_UNICODE: dict[str, str] = {
    "play": "▶",
    "pause": "⏸",
    "stop": "■",
    "prev": "⏮",
    "next": "⏭",
    "shuffle": "⤨",
    "shuffle_off": "⤨",
    "repeat": "↻",
    "repeat_off": "↻",
    "repeat_one": "↻¹",
    "volume_off": "◁",
    "volume_low": "◁)",
    "volume_medium": "◁))",
    "volume_high": "◁))",
    "heart": "♥",
    "heart_off": "♡",
    "thumb_up": "▲",
    "thumb_up_off": "△",
    "thumb_down": "▼",
    "thumb_down_off": "▽",
    "more": "⋮",
    "expand": "⌃",
    "collapse": "⌄",
    "radio": "((·))",
    "save": "+",
    "saved": "✓",
    "menu": "☰",
    "back": "←",
    "forward": "→",
    "search": "⌕",
    "lyrics": "♫",
    "chevron_left": "◀",
    "chevron_right": "▶",
    "home": "⌂",
    "explore": "◎",
    "library": "▤",
    "liked": "♡",
    "recent": "◷",
    "discovery": "✦",
    "queue": "≡",
    "import": "⇩",
    "new_releases": "✦",
    "charts": "▲",
    "moods": "☺",
    "note": "♫",
}

_SETS = {"nerd": _NERD, "unicode": _UNICODE}

# Any nf-md glyph proves the font carries the Material Design range.
_PROBE_CODEPOINT = "f040a"


def _nerd_font_installed() -> bool:
    """True when fontconfig lists a font with the nf-md play glyph."""
    fc_list = shutil.which("fc-list")
    if fc_list is None:
        return False
    try:
        out = subprocess.run(
            [fc_list, f":charset={_PROBE_CODEPOINT}", "family"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(out.strip())


@functools.cache
def active_set() -> str:
    """The icon set in use: ``"nerd"`` or ``"unicode"``."""
    from ytm_player.config.settings import get_settings

    mode = get_settings().ui.icons
    if mode in _SETS:
        return mode
    if mode != "auto":
        logger.warning("Unknown [ui] icons value %r, using auto", mode)
    return "nerd" if _nerd_font_installed() else "unicode"


def icon(name: str) -> str:
    """The glyph for ``name`` in the active set."""
    return _SETS[active_set()][name]


def volume_icon(volume: int) -> str:
    """A speaker glyph that shows the volume level."""
    if volume <= 0:
        return icon("volume_off")
    if volume < 34:
        return icon("volume_low")
    if volume < 67:
        return icon("volume_medium")
    return icon("volume_high")
