"""BiDi text utilities for RTL display in terminals.

Modern terminals (Konsole, foot, WezTerm, etc.) implement the Unicode
BiDi algorithm natively — they reorder RTL text for correct visual
display automatically.  Manual word-reordering in application code
causes *double reversal* (our reorder + terminal reorder = wrong).

This module provides ``has_rtl()`` for detection (used to apply
right-alignment CSS) but intentionally does NOT reorder text.  The
``reorder_rtl_line()`` and ``wrap_rtl_line()`` functions are kept as
pass-through stubs so callers don't need to be changed.
"""

from __future__ import annotations

import re

# Matches any character from RTL scripts (Arabic, Hebrew, Thaana, Syriac, N'Ko).
_RTL_RE = re.compile(
    r"[\u0590-\u05FF\u0600-\u06FF\u0700-\u074F\u0750-\u077F"
    r"\u0780-\u07BF\u07C0-\u07FF\u08A0-\u08FF"
    r"\uFB1D-\uFB4F\uFB50-\uFDFF\uFE70-\uFEFF]"
)


def has_rtl(text: str) -> bool:
    """Return True if text contains any RTL script characters."""
    return bool(_RTL_RE.search(text))


def reorder_rtl_line(text: str) -> str:
    """No-op — terminal handles BiDi natively.

    Kept as a stub so call sites don't need to be removed.
    """
    return text


def wrap_rtl_line(text: str, width: int) -> str:
    """No-op — terminal handles BiDi natively.

    Kept as a stub so call sites don't need to be removed.
    """
    return text
