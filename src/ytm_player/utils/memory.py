"""Process-wide allocator tuning."""

from __future__ import annotations

import ctypes
import logging
import os
import sys

logger = logging.getLogger(__name__)

# glibc's mallopt option for the arena limit, from malloc.h.
_M_ARENA_MAX = -8
# Arenas glibc may create. Its default is eight per CPU core.
MALLOC_ARENAS = 2


def limit_malloc_arenas() -> bool:
    """Cap glibc at MALLOC_ARENAS malloc arenas. Call it before any thread starts.

    Each thread that allocates can get its own arena, and freed memory stays
    in the arena that held it. A 16-hour run held 197 MB free this way against
    77 MB in use. A MALLOC_ARENA_MAX value in the environment wins. Returns
    True when the cap was set.
    """
    if not sys.platform.startswith("linux") or "MALLOC_ARENA_MAX" in os.environ:
        return False
    try:
        libc = ctypes.CDLL("libc.so.6")
        return bool(libc.mallopt(_M_ARENA_MAX, MALLOC_ARENAS))
    except (OSError, AttributeError):
        # Not glibc, for example musl.
        logger.debug("mallopt unavailable, malloc arenas left at the default", exc_info=True)
        return False
