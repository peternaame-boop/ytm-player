"""BiDi text utilities for correct RTL display in terminals.

Terminals mostly lack full BiDi support, so RTL text appears with wrong
word order.  This module reorders directional *segments* (groups of
consecutive same-direction words) while keeping characters within each
word in logical order for the terminal's HarfBuzz shaping engine.
"""

from __future__ import annotations

import re
import unicodedata

# Matches any character from RTL scripts (Arabic, Hebrew, Thaana, Syriac, N'Ko).
_RTL_RE = re.compile(
    r"[\u0590-\u05FF\u0600-\u06FF\u0700-\u074F\u0750-\u077F"
    r"\u0780-\u07BF\u07C0-\u07FF\u08A0-\u08FF"
    r"\uFB1D-\uFB4F\uFB50-\uFDFF\uFE70-\uFEFF]"
)


def has_rtl(text: str) -> bool:
    """Return True if text contains any RTL script characters."""
    return bool(_RTL_RE.search(text))


def _word_is_rtl(word: str) -> bool:
    """Return True if a word's first strong character is RTL."""
    for ch in word:
        bidi = unicodedata.bidirectional(ch)
        if bidi in ("R", "AL", "AN"):
            return True
        if bidi == "L":
            return False
    return False


def reorder_rtl_line(text: str) -> str:
    """Reorder an RTL line for left-to-right terminal display.

    Groups consecutive same-direction words into segments, reverses the
    segment order, and reverses words within each RTL segment.  Characters
    inside each word stay in logical order so HarfBuzz shaping is preserved.

    Pure LTR text passes through unchanged.
    """
    if not text or not has_rtl(text):
        return text

    words = text.split()
    if not words:
        return text

    # Group consecutive words by direction.
    segments: list[tuple[bool, list[str]]] = []
    for word in words:
        rtl = _word_is_rtl(word)
        if segments and segments[-1][0] == rtl:
            segments[-1][1].append(word)
        else:
            segments.append((rtl, [word]))

    # Reverse the segment order (RTL paragraph layout).
    segments.reverse()

    # Within RTL segments, also reverse word order.
    parts: list[str] = []
    for is_rtl, seg_words in segments:
        if is_rtl:
            seg_words.reverse()
        parts.extend(seg_words)

    return " ".join(parts)


def wrap_rtl_line(text: str, width: int) -> str:
    """Pre-wrap and reorder RTL text for correct multi-line terminal display.

    Terminals wrap text left-to-right, which reverses the line order when a
    reordered RTL string exceeds the display width.  This wraps the text in
    *logical* (reading) order first, then reorders each wrapped segment
    independently so that visual line 1 = start of sentence, line 2 =
    continuation, etc.

    Pure LTR or short text passes through to ``reorder_rtl_line`` directly.
    """
    if not text or not has_rtl(text):
        return text

    if len(text) <= width or width <= 0:
        return reorder_rtl_line(text)

    words = text.split()
    if not words:
        return text

    lines: list[str] = []
    current_words: list[str] = []
    current_len = 0

    for word in words:
        word_len = len(word)
        needed = word_len if not current_words else current_len + 1 + word_len
        if needed <= width:
            current_words.append(word)
            current_len = needed
        else:
            if current_words:
                lines.append(" ".join(current_words))
            current_words = [word]
            current_len = word_len

    if current_words:
        lines.append(" ".join(current_words))

    return "\n".join(reorder_rtl_line(line) for line in lines)
