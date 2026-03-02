"""Helpers for adapting app config to yt-dlp Python API options."""

from __future__ import annotations

from os import PathLike
from pathlib import Path


def _split_csv_or_space(value: str) -> list[str]:
    """Split a string by commas/whitespace and drop empties."""
    normalized = value.replace(",", " ")
    return [part for part in (item.strip() for item in normalized.split()) if part]


def normalize_cookiefile(value: object) -> str | None:
    """Return expanded cookie file path for yt-dlp, or None when unset."""
    if isinstance(value, PathLike | Path):
        expanded = Path(value).expanduser()
        return str(expanded)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        return str(Path(stripped).expanduser())
    return None


def normalize_remote_components(value: object) -> list[str] | None:
    """Return yt-dlp compatible remote_components list."""
    if isinstance(value, str):
        parts = _split_csv_or_space(value)
        return parts or None
    if isinstance(value, (list, tuple, set)):
        parts = [str(part).strip() for part in value if str(part).strip()]
        return parts or None
    return None


def normalize_js_runtimes(value: object) -> dict[str, dict] | None:
    """Return yt-dlp compatible js_runtimes dict.

    yt-dlp Python API expects: {"runtime": {<config>}}
    """
    if isinstance(value, dict):
        result: dict[str, dict] = {}
        for runtime, config in value.items():
            name = str(runtime).strip()
            if not name:
                continue
            result[name] = config if isinstance(config, dict) else {}
        return result or None

    runtimes: list[str] = []
    if isinstance(value, str):
        runtimes = _split_csv_or_space(value)
    elif isinstance(value, (list, tuple, set)):
        runtimes = [str(part).strip() for part in value if str(part).strip()]

    if not runtimes:
        return None
    return {runtime: {} for runtime in runtimes}


def apply_configured_yt_dlp_options(opts: dict, yt_dlp_settings: object) -> dict:
    """Mutate and return yt-dlp options with app-configured extras."""
    cookies_file = normalize_cookiefile(getattr(yt_dlp_settings, "cookies_file", ""))
    if cookies_file:
        opts["cookiefile"] = cookies_file

    remote_components = normalize_remote_components(
        getattr(yt_dlp_settings, "remote_components", "")
    )
    if remote_components:
        opts["remote_components"] = remote_components

    js_runtimes = normalize_js_runtimes(getattr(yt_dlp_settings, "js_runtimes", ""))
    if js_runtimes:
        opts["js_runtimes"] = js_runtimes

    return opts
