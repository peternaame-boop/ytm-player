"""Plugin discovery for user-supplied visualizer modes.

Scans ``~/.config/ytm-player/visualizers/*.py``, imports each file in a
private namespace, and collects any module-level subclass of
VisualizerMode. Plugins are instantiated once and re-used per frame.

A broken plugin (import error, missing `name`, name collision with a
built-in) is logged and skipped — never crashes the TUI.
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from pathlib import Path

from ytm_player.config.paths import CONFIG_DIR
from ytm_player.ui.widgets._visualizer_modes import BUILTIN_MODES, VisualizerMode

logger = logging.getLogger(__name__)

PLUGINS_DIR = CONFIG_DIR / "visualizers"


def discover_plugins() -> dict[str, VisualizerMode]:
    """Import every .py file under PLUGINS_DIR and return name→instance.

    Built-in mode names take precedence; a plugin claiming a built-in
    name is logged and skipped so users get a clear collision warning
    instead of silent override.
    """
    out: dict[str, VisualizerMode] = {}
    if not PLUGINS_DIR.is_dir():
        return out

    for path in sorted(PLUGINS_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue  # skip _foo.py — convention for shared/private code
        try:
            plugin = _load_plugin_file(path)
        except Exception:
            logger.exception("Visualizer plugin %s failed to import", path.name)
            continue
        for name, instance in plugin.items():
            if name in BUILTIN_MODES:
                logger.warning(
                    "Visualizer plugin %s tried to override built-in mode %r — skipped",
                    path.name,
                    name,
                )
                continue
            if name in out:
                logger.warning(
                    "Visualizer plugin %s defines duplicate mode name %r — keeping first",
                    path.name,
                    name,
                )
                continue
            out[name] = instance
    return out


def _load_plugin_file(path: Path) -> dict[str, VisualizerMode]:
    """Import one plugin file in isolation; return its declared modes."""
    spec_name = f"ytm_player._user_visualizers.{path.stem}"
    spec = importlib.util.spec_from_file_location(spec_name, path)
    if spec is None or spec.loader is None:
        return {}
    module = importlib.util.module_from_spec(spec)
    # Register under a unique key so the import system can resolve relative
    # imports if the plugin uses them. We do NOT pollute sys.modules globally
    # with the plain stem name.
    sys.modules[spec_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec_name, None)
        raise

    found: dict[str, VisualizerMode] = {}
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if obj is VisualizerMode:
            continue
        if not issubclass(obj, VisualizerMode):
            continue
        if obj.__module__ != spec_name:
            continue  # re-exported, not declared here
        name = getattr(obj, "name", "")
        if not name:
            logger.warning(
                "Visualizer plugin %s: %s has empty `name` — skipped", path.name, obj.__name__
            )
            continue
        try:
            found[name] = obj()
        except Exception:
            logger.exception(
                "Visualizer plugin %s: %s failed to instantiate", path.name, obj.__name__
            )
    return found
