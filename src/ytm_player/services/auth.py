"""Authentication management for YouTube Music.

Extracts cookies automatically from the user's browser (Chrome, Firefox,
Brave, Helium, etc.) using yt-dlp's cookie extraction. Falls back to manual
header paste if auto-extraction fails.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import requests.exceptions
from ytmusicapi import YTMusic
from ytmusicapi.helpers import get_authorization, initialize_headers, sapisid_from_cookie

from ytm_player.config.paths import (
    AUTH_FILE,
    CONFIG_DIR,
    SECURE_FILE_MODE,
)

logger = logging.getLogger(__name__)

# Browsers to try, in preference order.
_BROWSERS = (
    "helium",
    "chrome",
    "chromium",
    "brave",
    "firefox",
    "edge",
    "vivaldi",
    "opera",
)

# Custom Chromium-based browsers not in yt-dlp's built-in list.
# Maps browser name → (config_dir_name, keyring_name).
_CUSTOM_CHROMIUM_BROWSERS: dict[str, tuple[str, str]] = {
    "helium": ("net.imput.helium", "Chromium"),
}
_yt_dlp_patched = False


def _patch_yt_dlp_browsers() -> None:
    """Register custom Chromium browsers with yt-dlp (idempotent)."""
    global _yt_dlp_patched
    if _yt_dlp_patched:
        return
    try:
        from yt_dlp import cookies as c

        orig_fn = c._get_chromium_based_browser_settings

        def _patched(browser_name: str):  # type: ignore[no-untyped-def]
            if browser_name in _CUSTOM_CHROMIUM_BROWSERS:
                config_dir_name, keyring = _CUSTOM_CHROMIUM_BROWSERS[browser_name]
                config_home = c._config_home()
                return {
                    "browser_dir": os.path.join(config_home, config_dir_name),
                    "keyring_name": keyring,
                    "supports_profiles": True,
                }
            return orig_fn(browser_name)

        c._get_chromium_based_browser_settings = _patched
        c.CHROMIUM_BASED_BROWSERS = c.CHROMIUM_BASED_BROWSERS | set(_CUSTOM_CHROMIUM_BROWSERS)
        _yt_dlp_patched = True
    except (ImportError, AttributeError) as exc:
        logger.warning(
            "Failed to patch yt-dlp for extra browser support "
            "(yt-dlp internals may have changed): %s",
            exc,
        )


class AuthManager:
    """Manages YouTube Music authentication via browser cookie extraction."""

    def __init__(self, config_dir: Path = CONFIG_DIR, auth_file: Path = AUTH_FILE) -> None:
        self._config_dir = config_dir
        self._auth_file = auth_file

    @property
    def auth_file(self) -> Path:
        return self._auth_file

    def is_authenticated(self) -> bool:
        """Check whether a valid auth file exists on disk."""
        if not self._auth_file.exists():
            return False
        try:
            with open(self._auth_file) as f:
                data = json.load(f)
            return bool(data.get("cookie"))
        except (json.JSONDecodeError, OSError):
            return False

    def create_ytmusic_client(self) -> YTMusic:
        """Create a YTMusic client from the stored auth file."""
        return YTMusic(str(self._auth_file))

    def validate(self) -> bool:
        """Verify that the auth credentials actually work.

        Makes a real API call and checks the server's ``logged_in`` flag.
        """
        if not self.is_authenticated():
            return False
        try:
            ytm = self.create_ytmusic_client()
            raw_response = None
            orig_post = ytm._session.post

            def _capture_post(*args, **kwargs):
                nonlocal raw_response
                resp = orig_post(*args, **kwargs)
                if resp.status_code == 200 and "browse" in str(args[0]):
                    raw_response = resp.json()
                return resp

            ytm._session.post = _capture_post
            try:
                ytm.get_library_playlists(limit=1)
            finally:
                ytm._session.post = orig_post

            if raw_response:
                for stp in raw_response.get("responseContext", {}).get("serviceTrackingParams", []):
                    for param in stp.get("params", []):
                        if param.get("key") == "logged_in" and param.get("value") == "0":
                            logger.warning("Auth validation: server says logged_in=0")
                            return False
            return True
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            logger.warning("Auth validation failed — network error: %s", exc)
            raise
        except Exception:
            logger.warning("Auth validation failed — credentials may be expired.")
            return False

    # ── Auto-refresh ──────────────────────────────────────────────────

    def try_auto_refresh(self) -> bool:
        """Attempt to silently refresh auth from the browser.

        Called when the app detects an auth failure at runtime. Returns
        True if fresh cookies were extracted and validation passed.
        """
        browser = self._detect_browser()
        if browser is None:
            return False
        try:
            if self._extract_and_save(browser):
                return self.validate()
        except Exception:
            logger.debug("Auto-refresh failed", exc_info=True)
        return False

    # ── Setup entry point ────────────────────────────────────────────

    def setup_interactive(self) -> bool:
        """Interactive setup — auto-extract from browser, manual paste as fallback."""
        print()
        print("=" * 60)
        print("  YouTube Music Authentication")
        print("=" * 60)
        print()

        browser = self._detect_browser()
        if browser:
            print(f"  Found YouTube cookies in {browser}.")
            print("  Extracting automatically...")
            print()
            if self._extract_and_save(browser):
                return True
            print("  Auto-extraction failed. Falling back to manual setup.")
            print()

        return self._setup_manual()

    # ── Browser cookie extraction ────────────────────────────────────

    @staticmethod
    def _detect_browser() -> str | None:
        """Find a browser that has YouTube cookies."""
        try:
            from yt_dlp.cookies import extract_cookies_from_browser
        except ImportError:
            logger.debug("yt-dlp not available for cookie extraction")
            return None

        _patch_yt_dlp_browsers()

        for browser in _BROWSERS:
            try:
                jar = extract_cookies_from_browser(browser)
                has_sapisid = any(
                    c.name in ("SAPISID", "__Secure-3PAPISID") and c.domain == ".youtube.com"
                    for c in jar
                )
                if has_sapisid:
                    return browser
            except Exception:
                logger.debug("Browser %s not available", browser, exc_info=True)
                continue
        return None

    def _extract_and_save(self, browser: str) -> bool:
        """Extract YouTube cookies from *browser* and write auth.json."""
        try:
            from yt_dlp.cookies import extract_cookies_from_browser

            _patch_yt_dlp_browsers()
            jar = extract_cookies_from_browser(browser)
        except Exception as exc:
            logger.warning("Cookie extraction from %s failed: %s", browser, exc)
            return False

        # Only use .youtube.com cookies — mixing in .google.com cookies
        # causes logged_in=0 when the user has multiple Google accounts.
        yt_cookies = [c for c in jar if c.domain == ".youtube.com"]
        if not yt_cookies:
            logger.warning("No .youtube.com cookies found in %s", browser)
            return False

        cookie_str = "; ".join(f"{c.name}={c.value}" for c in yt_cookies)

        # Verify we have the critical SAPISID cookie.
        try:
            sapisid = sapisid_from_cookie(cookie_str)
        except Exception:
            logger.warning("SAPISID cookie not found in extracted cookies")
            return False

        # Build the headers dict that ytmusicapi expects.
        headers = dict(initialize_headers())
        headers["cookie"] = cookie_str
        headers["x-goog-authuser"] = "0"

        # Generate an initial SAPISIDHASH so ytmusicapi detects BROWSER auth type.
        origin = "https://music.youtube.com"
        headers["authorization"] = get_authorization(sapisid + " " + origin)

        # Save atomically with correct permissions from creation.
        self._config_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self._auth_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, SECURE_FILE_MODE)
        with os.fdopen(fd, "w") as f:
            json.dump(headers, f, ensure_ascii=True, indent=4, sort_keys=True)

        print(f"  Cookies extracted from {browser} and saved.")
        return True

    # ── Manual header paste (fallback) ───────────────────────────────

    def _setup_manual(self) -> bool:
        """Walk the user through extracting browser headers manually."""
        print("  No browser with YouTube cookies detected.")
        print("  Falling back to manual header paste.")
        print()
        print("  Steps:")
        print("  1. Open https://music.youtube.com in your browser")
        print("  2. Open DevTools (F12) > Network tab")
        print("  3. Refresh the page, filter by '/browse'")
        print("  4. Click a music.youtube.com request")
        print("  5. Right-click 'Request Headers' > Copy")
        print()
        print("  Paste headers below, then press Enter on an empty line:")
        print()

        lines: list[str] = []
        try:
            while True:
                line = input()
                if line.strip() == "" and lines:
                    break
                lines.append(line)
        except (EOFError, KeyboardInterrupt):
            print("\n  Cancelled.")
            return False

        if not lines:
            print("  No headers provided.")
            return False

        raw = "\n".join(lines)
        normalized = _normalize_raw_headers(raw)

        if "cookie" not in normalized.lower():
            print()
            print("  Warning: no 'cookie' header found.")
            print("  Make sure you copied from a music.youtube.com request.")
            print()

        self._config_dir.mkdir(parents=True, exist_ok=True)
        try:
            import ytmusicapi

            ytmusicapi.setup(filepath=str(self._auth_file), headers_raw=normalized)
            os.chmod(self._auth_file, SECURE_FILE_MODE)
            print()
            print("  Browser authentication saved.")
            return True
        except Exception as exc:
            logger.error("Failed to parse headers: %s", exc)
            print(f"\n  Error: {exc}")
            return False


# ── Header normalization (for manual paste) ──────────────────────────

_PSEUDO_HEADERS = {":authority", ":method", ":path", ":scheme", ":status"}


def _normalize_raw_headers(raw: str) -> str:
    """Pre-process raw headers into ``Name: Value\\n`` format.

    Handles Chrome DevTools copy formats:
    1. Single-line ^[E-separated (terminal paste)
    2. Alternating lines (Chrome "Copy request headers")
    3. Standard ``Name: Value`` per line (Firefox / older Chrome)
    """
    if "^[E" in raw or "\x1bE" in raw or "\x1b" in raw:
        sep = "^[E" if "^[E" in raw else ("\x1bE" if "\x1bE" in raw else "\x1b")
        parts = raw.split(sep)
        lines = []
        i = 0
        while i + 1 < len(parts):
            name = parts[i].strip()
            value = parts[i + 1].strip()
            i += 2
            if not name or name in _PSEUDO_HEADERS:
                continue
            lines.append(f"{name}: {value}")
        return "\n".join(lines)

    raw_lines = [line for line in raw.split("\n") if line.strip()]
    colon_lines = sum(1 for line in raw_lines if ": " in line)
    is_alternating = len(raw_lines) > 2 and colon_lines < len(raw_lines) * 0.2

    if is_alternating:
        lines = []
        i = 0
        while i + 1 < len(raw_lines):
            name = raw_lines[i].strip()
            value = raw_lines[i + 1].strip()
            i += 2
            if name in _PSEUDO_HEADERS:
                continue
            lines.append(f"{name}: {value}")
        return "\n".join(lines)

    result = []
    for line in raw_lines:
        stripped = line.strip()
        if stripped.startswith(":"):
            continue
        result.append(stripped)
    return "\n".join(result)


def get_auth_manager() -> AuthManager:
    """Return a module-level AuthManager instance."""
    return AuthManager()
