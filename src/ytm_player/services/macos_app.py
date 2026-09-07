"""
Purpose: Keep the ytm-player process out of the macOS Dock and app switcher.
Interface: hide_dock_icon().
Invariants: No-op off macOS, when AppKit is unavailable, and off the main thread;
never raises.
Decisions: Use Accessory rather than Prohibited so the process stays a real,
activatable app and can still serve MPRemoteCommandCenter / Now Playing.
"""

from __future__ import annotations

import importlib
import logging
import sys
import threading
from typing import Any

logger = logging.getLogger(__name__)

try:
    _APPKIT: Any = importlib.import_module("AppKit")

    _APPKIT_AVAILABLE = True
except ImportError:
    _APPKIT = None
    _APPKIT_AVAILABLE = False

# NSApplicationActivationPolicyAccessory: the process runs without a Dock tile
# and without an app-switcher entry, but stays activatable — unlike Prohibited,
# which would also make it ineligible as a Now Playing source.
_ACTIVATION_POLICY_ACCESSORY = 1


def hide_dock_icon() -> bool:
    """Demote this process to an accessory app, so macOS gives it no Dock tile.

    Returns True only when AppKit reports the policy switch succeeded;
    False when it wasn't needed (non-macOS), couldn't be attempted (AppKit
    missing, not on the main thread -- AppKit is main-thread-only, so this
    is never called from a worker), or was refused or failed.
    """
    if sys.platform != "darwin" or not _APPKIT_AVAILABLE:
        return False
    if threading.current_thread() is not threading.main_thread():
        logger.debug("Not setting the macOS activation policy off the main thread")
        return False
    try:
        app = _APPKIT.NSApplication.sharedApplication()
        switched = app.setActivationPolicy_(_ACTIVATION_POLICY_ACCESSORY)
    except Exception:
        logger.debug("Could not set macOS activation policy", exc_info=True)
        return False
    if not switched:
        logger.warning("macOS refused the accessory activation policy; the Dock icon stays")
        return False
    logger.info("macOS activation policy set to accessory (no Dock icon)")
    return True
