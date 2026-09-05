"""Authentication management for YouTube Music.

Extracts cookies automatically from the user's browser (Chrome, Firefox,
Brave, Helium, etc.) using yt-dlp's cookie extraction. Falls back to manual
header paste if auto-extraction fails.

Also writes a separate, wider-scoped (youtube.com+google.com) cookiejar file
consumed by stream.py's yt-dlp resolver — see _save_stream_cookiejar().
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
from collections.abc import Callable, Iterable
from http.cookiejar import Cookie, MozillaCookieJar
from pathlib import Path
from typing import IO, Any

import requests.exceptions
from ytmusicapi import YTMusic
from ytmusicapi.helpers import get_authorization, initialize_headers, sapisid_from_cookie

from ytm_player.config.paths import (
    AUTH_FILE,
    CONFIG_DIR,
    SECURE_FILE_MODE,
    STREAM_COOKIES_FILE,
    secure_chmod,
)
from ytm_player.services.yt_dlp_options import normalize_cookiefile

logger = logging.getLogger(__name__)

# Browsers to try, in preference order.
_BROWSERS = (
    "helium",
    "chrome",
    "chromium",
    "brave",
    "firefox",
    "zen",
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


def _zen_profile_roots() -> list[Path]:
    """Return candidate Zen profile roots for the current platform."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        return [Path(appdata) / "zen"] if appdata else []

    home = Path.home()
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support" / "zen"]

    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    xdg_root = Path(xdg_config_home) if xdg_config_home else home / ".config"
    return [
        home / ".zen",
        xdg_root / "zen",
        home / ".var" / "app" / "app.zen_browser.zen" / ".zen",
        home / ".var" / "app" / "app.zen_browser.zen" / "zen",
    ]


def _extract_browser_jar(browser: str):  # type: ignore[no-untyped-def]
    """Extract cookies from a supported browser, including Zen profiles."""
    from yt_dlp.cookies import extract_cookies_from_browser

    if browser == "zen":
        for root in _zen_profile_roots():
            if root.exists():
                return extract_cookies_from_browser("firefox", profile=str(root))
        raise FileNotFoundError("no Zen profile directory found")

    return extract_cookies_from_browser(browser)


def _patch_yt_dlp_browsers() -> None:
    """Register custom Chromium browsers with yt-dlp (idempotent)."""
    global _yt_dlp_patched
    if _yt_dlp_patched:
        return
    try:
        # Patching yt-dlp's private cookies API to add support for more
        # Chromium browser variants. Pyright doesn't see private symbols;
        # the surrounding try/except (ImportError, AttributeError) handles
        # the case where yt-dlp's internals change.
        from yt_dlp import cookies as c

        orig_fn = c._get_chromium_based_browser_settings  # type: ignore[attr-defined]

        def _patched(browser_name: str):  # type: ignore[no-untyped-def]
            if browser_name in _CUSTOM_CHROMIUM_BROWSERS:
                config_dir_name, keyring = _CUSTOM_CHROMIUM_BROWSERS[browser_name]
                config_home = c._config_home()  # type: ignore[attr-defined]
                return {
                    "browser_dir": os.path.join(config_home, config_dir_name),
                    "keyring_name": keyring,
                    "supports_profiles": True,
                }
            return orig_fn(browser_name)

        c._get_chromium_based_browser_settings = _patched  # type: ignore[attr-defined]
        c.CHROMIUM_BASED_BROWSERS = c.CHROMIUM_BASED_BROWSERS | set(_CUSTOM_CHROMIUM_BROWSERS)
        _yt_dlp_patched = True
    except (ImportError, AttributeError) as exc:
        logger.warning(
            "Failed to patch yt-dlp for extra browser support "
            "(yt-dlp internals may have changed): %s",
            exc,
        )


def _atomic_write(
    path: Path,
    mode: str,
    write: Callable[[IO[Any]], None],
    encoding: str | None = None,
) -> None:
    """Write *path* atomically via an O_NOFOLLOW temp file + os.replace.

    Shared by AuthManager._restore_or_remove and
    AuthManager._save_stream_cookiejar, the two sites that need
    atomic-replace semantics for a security-sensitive file: open a
    PID-suffixed temp file with O_NOFOLLOW (refusing to follow a symlink
    planted at the temp path), let *write* fill it via the fd opened in
    *mode* (and *encoding*, for text mode), chmod it to SECURE_FILE_MODE,
    then os.replace() it into place.

    *write* owns the actual content — a raw bytes write, a cookiejar's
    .save(), etc. — so this helper stays agnostic to what's being written.
    On any failure the temp file is removed and the exception re-raised;
    callers decide what to catch and how to log, since they disagree on
    which exceptions are recoverable (OSError only vs. broad Exception).
    """
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        fd = os.open(
            str(tmp_path),
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
            SECURE_FILE_MODE,
        )
        with os.fdopen(fd, mode, encoding=encoding) as f:
            write(f)
        secure_chmod(tmp_path, SECURE_FILE_MODE)
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


class AuthManager:
    """Manages YouTube Music authentication via browser cookie extraction,
    and the yt-dlp stream cookiejar consumed by stream.py."""

    def __init__(
        self,
        config_dir: Path = CONFIG_DIR,
        auth_file: Path = AUTH_FILE,
        cookies_file: str | None = None,
        stream_cookies_file: Path = STREAM_COOKIES_FILE,
    ) -> None:
        self._config_dir = config_dir
        self._auth_file = auth_file
        self._cookies_file = normalize_cookiefile(cookies_file)
        self._stream_cookies_file = stream_cookies_file

    @property
    def auth_file(self) -> Path:
        return self._auth_file

    def is_authenticated(self) -> bool:
        """Check whether a valid auth file exists on disk."""
        if not self._auth_file.exists():
            return False
        try:
            with open(self._auth_file, encoding="utf-8") as f:
                data = json.load(f)
            return bool(data.get("cookie"))
        except (json.JSONDecodeError, OSError):
            return False

    def create_ytmusic_client(self, user: str | None = None) -> YTMusic:
        """Create a YTMusic client from the stored auth file."""
        return YTMusic(str(self._auth_file), user=user)

    def validate(self) -> bool:
        """Verify that the auth credentials actually work.

        Calls the account menu endpoint which is inherently auth-bound —
        it returns the logged-in user's name or fails clearly.
        """
        if not self.is_authenticated():
            return False
        try:
            ytm = self.create_ytmusic_client()
            account = ytm.get_account_info()
            return bool(account.get("accountName"))
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            logger.debug("Auth validation failed — network error: %s", exc)
            raise
        except Exception:
            logger.debug("Auth validation failed — credentials may be expired.", exc_info=True)
            return False

    # ── Auto-refresh ──────────────────────────────────────────────────

    def try_auto_refresh(self) -> bool:
        """Attempt to silently refresh auth from cookies/browser.

        Called when the app detects an auth failure at runtime. Returns
        True if fresh cookies were extracted and validation passed.
        """
        if self._cookies_file and self._refresh_from_cookies_file(Path(self._cookies_file)):
            return True

        detected = self._detect_browser()
        if detected is None:
            return False
        browser, cookies, jar = detected
        try:
            return self._save_youtube_cookies(cookies, first_valid=True, stream_jar=jar)
        except Exception:
            logger.debug("Auto-refresh failed", exc_info=True)
        return False

    # ── Setup entry point ────────────────────────────────────────────

    def setup_interactive(self, manual: bool = False, browser: str | None = None) -> bool:
        """Interactive setup — auto-extract from browser, manual paste as fallback.

        Args:
            manual: Skip browser detection, go straight to manual header paste.
            browser: Extract from a specific browser instead of auto-detecting.
        """
        print()
        print("=" * 60)
        print("  YouTube Music Authentication")
        print("=" * 60)
        print()

        if manual:
            return self._setup_manual()

        # Try cookies file first (unless a specific browser was requested).
        if self._cookies_file and not browser:
            print(f"  Trying cookies file: {self._cookies_file}")
            if self._refresh_from_cookies_file(Path(self._cookies_file), interactive=True):
                return True
            print("  Cookies file extraction failed. Falling back to browser/manual setup.")
            print()

        if browser:
            # User specified a browser explicitly.
            print(f"  Trying browser: {browser}")
            print()
            if self._extract_and_save(browser, interactive=True):
                return True
            print(f"  Could not extract from {browser}. Falling back to manual setup.")
            print()
            return self._setup_manual()

        # Auto-detect browser.
        detected = self._detect_browser()
        if detected:
            browser, cookies, jar = detected
            print(f"  Found YouTube cookies in {browser}.")
            print("  Extracting automatically...")
            print()
            if self._save_youtube_cookies(cookies, interactive=True, stream_jar=jar):
                return True
            print("  Auto-extraction failed. Falling back to manual setup.")
            print()

        return self._setup_manual()

    # ── Browser cookie extraction ────────────────────────────────────

    @staticmethod
    def _detect_browser() -> tuple[str, list, list] | None:
        """Find a browser that has YouTube cookies.

        Returns ``(browser, youtube_cookies, full_jar)`` — the full jar feeds
        the stream cookiejar (youtube.com + google.com) via _save_stream_cookiejar.
        """
        _patch_yt_dlp_browsers()

        for browser in _BROWSERS:
            try:
                jar = _extract_browser_jar(browser)
                yt_cookies = [c for c in jar if c.domain == ".youtube.com"]
                if any(c.name in ("SAPISID", "__Secure-3PAPISID") for c in yt_cookies):
                    return browser, yt_cookies, list(jar)
            except Exception:
                logger.debug("Browser %s not available", browser, exc_info=True)
                continue
        return None

    @staticmethod
    def _backup_bytes(path: Path, label: str) -> bytes | None:
        """Snapshot *path*'s contents so a failed refresh can restore them."""
        if not path.exists():
            return None
        try:
            return path.read_bytes()
        except OSError:
            logger.debug("Could not backup existing %s", label, exc_info=True)
            return None

    @staticmethod
    def _restore_or_remove(path: Path, backup: bytes | None, label: str) -> None:
        """Undo a failed refresh: restore *path* from *backup*, or remove it
        if there was no prior backup (the refresh wrote it fresh).

        Restores via _atomic_write(), the shared O_NOFOLLOW-temp-file-plus-
        os.replace helper also used by _save_stream_cookiejar — a plain
        write_bytes() would follow a symlink planted at *path* during the
        network-bound window between backup and restore.
        """
        try:
            if backup is not None:
                payload = backup

                def _write(f: IO[Any]) -> None:
                    f.write(payload)

                _atomic_write(path, "wb", _write)
                logger.debug("Restored previous %s after cookies file validation failure", label)
            elif path.exists():
                path.unlink()
        except OSError:
            logger.warning("Failed to restore previous %s", label, exc_info=True)

    def _refresh_from_cookies_file(self, cookies_file: Path, interactive: bool = False) -> bool:
        """Refresh auth from cookies file without losing working credentials."""
        backup = self._backup_bytes(self._auth_file, "auth file")
        stream_backup = self._backup_bytes(self._stream_cookies_file, "stream cookiejar")

        if not self._extract_and_save_from_cookies_file(cookies_file, interactive=interactive):
            return False

        try:
            if self.validate():
                return True
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            logger.warning("Network error during cookies-file validation; restoring backup")

        self._restore_or_remove(self._auth_file, backup, "auth file")
        self._restore_or_remove(self._stream_cookies_file, stream_backup, "stream cookiejar")
        return False

    def _extract_and_save_from_cookies_file(
        self, cookies_file: Path, interactive: bool = False
    ) -> bool:
        """Extract YouTube cookies from a Netscape cookies.txt file and write auth.json."""
        if not cookies_file.exists():
            logger.warning("Cookies file does not exist: %s", cookies_file)
            return False

        jar = MozillaCookieJar(str(cookies_file))
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Failed to load cookies file %s: %s", cookies_file, exc)
            return False

        if sys.platform != "win32":
            try:
                mode = cookies_file.stat().st_mode
                if mode & 0o077:
                    logger.warning(
                        "Cookies file has broad permissions (%o): %s",
                        mode & 0o777,
                        cookies_file,
                    )
            except OSError:
                logger.debug(
                    "Could not stat cookies file permissions: %s", cookies_file, exc_info=True
                )
        yt_cookies = [
            c for c in jar if c.domain == ".youtube.com" or c.domain.endswith(".youtube.com")
        ]
        if not yt_cookies:
            logger.warning("No youtube.com cookies found in %s", cookies_file)
            return False

        if self._save_youtube_cookies(yt_cookies, interactive=interactive, stream_jar=jar):
            if interactive:
                print(f"  Cookies extracted from file and saved: {cookies_file}")
            return True
        return False

    def _extract_and_save(self, browser: str, interactive: bool = False) -> bool:
        """Extract YouTube cookies from *browser* and write auth.json."""
        try:
            _patch_yt_dlp_browsers()
            jar = _extract_browser_jar(browser)
        except Exception as exc:
            logger.warning("Cookie extraction from %s failed: %s", browser, exc)
            return False

        # Only use .youtube.com cookies — mixing in .google.com cookies
        # causes logged_in=0 when the user has multiple Google accounts.
        yt_cookies = [c for c in jar if c.domain == ".youtube.com"]
        if not yt_cookies:
            logger.warning("No .youtube.com cookies found in %s", browser)
            return False

        if self._save_youtube_cookies(yt_cookies, interactive=interactive, stream_jar=jar):
            if interactive:
                print(f"  Cookies extracted from {browser} and saved.")
            return True
        return False

    def _save_youtube_cookies(
        self,
        cookies: list,
        interactive: bool = False,
        first_valid: bool = False,
        stream_jar: Iterable[Cookie] | None = None,
    ) -> bool:
        """Persist YouTube cookie headers into auth.json."""
        cookie_str = "; ".join(f"{c.name}={c.value}" for c in cookies)

        # Verify we have the critical SAPISID cookie.
        try:
            sapisid = sapisid_from_cookie(cookie_str)
        except Exception:
            logger.warning("SAPISID cookie not found in extracted cookies")
            return False

        # Build the base headers dict that ytmusicapi expects.
        origin = "https://music.youtube.com"
        base_headers = dict(initialize_headers())
        base_headers["cookie"] = cookie_str
        base_headers["authorization"] = get_authorization(sapisid + " " + origin)

        # Capture any previously saved account preference before probing.
        preferred_index_before_probe: int | None = None
        if self._auth_file.exists():
            try:
                existing = json.loads(self._auth_file.read_text(encoding="utf-8"))
                preferred_index_before_probe = int(existing.get("x-goog-authuser", 0))
            except (OSError, json.JSONDecodeError, ValueError):
                pass

        # Auto-refresh only needs the preferred account (or the first valid
        # fallback), while setup lists every available account.
        authuser_indices = list(range(5))
        if first_valid and preferred_index_before_probe in authuser_indices:
            authuser_indices.remove(preferred_index_before_probe)
            authuser_indices.insert(0, preferred_index_before_probe)

        self._config_dir.mkdir(parents=True, exist_ok=True)
        valid_accounts: list[tuple[int, str, str]] = []  # (authuser_index, accountName, handle)
        for authuser in authuser_indices:
            headers = {**base_headers, "x-goog-authuser": str(authuser)}
            tmp_path: str | None = None
            try:
                fd, tmp_path = tempfile.mkstemp(suffix=".json", dir=str(self._config_dir))
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(headers, f, ensure_ascii=True, indent=4, sort_keys=True)
                ytm = YTMusic(tmp_path)
                account = ytm.get_account_info()
                name = account.get("accountName")
                handle = account.get("channelHandle") or ""
                if name:
                    valid_accounts.append((authuser, name, handle))
                    if first_valid:
                        break
            except Exception:
                logger.debug("x-goog-authuser=%d did not work, skipping", authuser)
            finally:
                if tmp_path is not None:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass

        if not valid_accounts:
            logger.warning(
                "No valid YouTube Music account found in extracted cookies (tried indices %s)",
                authuser_indices,
            )
            return False

        def _label(authuser: int, name: str, handle: str) -> str:
            parts = [name]
            if handle:
                parts.append(handle)
            parts.append(f"browser slot {authuser}")
            return "  ·  ".join(parts)

        if interactive and len(valid_accounts) == 1:
            chosen_index, chosen_name, chosen_handle = valid_accounts[0]
            print(f"  Authenticated as: {_label(chosen_index, chosen_name, chosen_handle)}")
        elif interactive:
            # Interactive setup — let the user pick (e.g. to select a Premium account).
            print()
            print("  Multiple Google accounts found. Select your YouTube Music account.")
            print("  If you have YouTube Music Premium, pick that account.")
            print()
            print("  Note: 'browser slot N' shows the position of each account in your")
            print("  browser's account list — slot 0 is the first account you added,")
            print("  slot 1 the second, and so on. To check, click your profile picture")
            print("  in Chrome/Firefox: accounts are listed in the same order.")
            print()
            for i, (authuser, name, handle) in enumerate(valid_accounts):
                print(f"  [{i + 1}] {_label(authuser, name, handle)}")
            print()
            while True:
                try:
                    raw = input(f"  Enter number [1-{len(valid_accounts)}]: ").strip()
                    choice = int(raw) - 1
                    if 0 <= choice < len(valid_accounts):
                        break
                except ValueError:
                    pass
                except (EOFError, KeyboardInterrupt):
                    print("\n  Cancelled.")
                    return False
                print(f"  Please enter a number between 1 and {len(valid_accounts)}.")
            chosen_index, chosen_name, chosen_handle = valid_accounts[choice]
            print(f"  Selected: {_label(chosen_index, chosen_name, chosen_handle)}")
        else:
            # Silent auto-refresh: preserve the previously chosen account index.
            # Fall back to the first valid account if no preference is recorded.
            preferred = next(
                (a for a in valid_accounts if a[0] == preferred_index_before_probe),
                valid_accounts[0],
            )
            chosen_index, chosen_name, chosen_handle = preferred
            logger.debug(
                "Auto-refresh: using account index %d (%s)",
                chosen_index,
                _label(chosen_index, chosen_name, chosen_handle),
            )

        # Write the final auth file for the chosen account.
        # O_NOFOLLOW (POSIX-only; getattr fallback for Windows) refuses to
        # follow a symlink at the target path — defense-in-depth against
        # a malicious local user planting a symlink in CONFIG_DIR.
        headers = {**base_headers, "x-goog-authuser": str(chosen_index)}
        try:
            fd = os.open(
                str(self._auth_file),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
                SECURE_FILE_MODE,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(headers, f, ensure_ascii=True, indent=4, sort_keys=True)
        except OSError:
            logger.exception("Failed to write auth file %s", self._auth_file)
            return False

        if stream_jar is not None:
            self._save_stream_cookiejar(stream_jar)
        return True

    def _save_stream_cookiejar(self, jar: Iterable[Cookie]) -> bool:
        """Write a wide youtube.com/google.com cookiejar for stream.py's yt-dlp resolver.

        Builds a fresh jar rather than mutating *jar* in place — callers may
        own or share that iterable, and this method has no reason to assume
        it's safe to consume destructively.

        On failure any existing jar is removed: auth.json now belongs to
        this session, and streaming must not keep using the previous one.
        """
        try:
            from yt_dlp.cookies import YoutubeDLCookieJar

            stream_jar = YoutubeDLCookieJar()
            for cookie in jar:
                bare = cookie.domain.lstrip(".")
                if bare in ("youtube.com", "google.com") or bare.endswith(
                    (".youtube.com", ".google.com")
                ):
                    value = cookie.value or ""
                    if _has_control_chars(cookie.name, value):
                        continue
                    stream_jar.set_cookie(cookie)

            self._config_dir.mkdir(parents=True, exist_ok=True)

            def _write(f: IO[Any]) -> None:
                # save()'s stub types filename as str | None, but its open()
                # accepts a file object directly at runtime (non-path-like
                # branch truncates and reuses it) — verified against yt-dlp
                # source.
                stream_jar.save(f, ignore_discard=True, ignore_expires=True)  # type: ignore[arg-type]

            _atomic_write(self._stream_cookies_file, "w", _write, encoding="utf-8")
        except Exception:
            logger.exception("Failed to write stream cookiejar to %s", self._stream_cookies_file)
            try:
                self._stream_cookies_file.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "Could not remove stale stream cookiejar %s",
                    self._stream_cookies_file,
                    exc_info=True,
                )
            return False
        return True

    # ── Manual header paste (fallback) ───────────────────────────────

    def _setup_manual(self) -> bool:
        """Walk the user through extracting browser headers manually."""
        print("  Manual header paste mode.")
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

        cookie_value: str | None = None
        for line in normalized.split("\n"):
            name, sep, value = line.partition(":")
            if sep and name.strip().lower() == "cookie":
                cookie_value = value.strip()
                break

        if "cookie" not in normalized.lower():
            print()
            print("  Warning: no 'cookie' header found.")
            print("  Make sure you copied from a music.youtube.com request.")
            print()

        self._config_dir.mkdir(parents=True, exist_ok=True)
        try:
            import ytmusicapi

            ytmusicapi.setup(filepath=str(self._auth_file), headers_raw=normalized)
            secure_chmod(self._auth_file, SECURE_FILE_MODE)
            if cookie_value is not None:
                self._save_stream_cookiejar(_cookies_from_raw_header(cookie_value))
            print()
            print("  Browser authentication saved.")
            return True
        except Exception as exc:
            logger.error("Failed to parse headers: %s", exc)
            print(f"\n  Error: {exc}")
            return False


# ── Header normalization (for manual paste) ──────────────────────────

_PSEUDO_HEADERS = {":authority", ":method", ":path", ":scheme", ":status"}

# Chrome annotates ``x-client-data`` with a pretty-printed protobuf, opened by
# a bare ``Decoded:`` line and closed by a bare ``}``.
_DECODED_OPEN = "Decoded:"
_DECODED_CLOSE = "}"


def _strip_decoded_blocks(lines: list[str]) -> list[str]:
    """Drop Chrome's decoded-protobuf annotation for ``x-client-data``.

    Chrome DevTools renders that header as its name, its value, then a
    ``Decoded:`` line followed by a pretty-printed protobuf block ending in
    ``}``.  Those lines are not headers, and the block spans an odd number of
    lines, so leaving it in shifts the name/value pairing of every header
    after it — including ``x-goog-authuser``, which ytmusicapi requires and
    which sorts after ``x-client-data``.

    A block with no closing ``}`` is left untouched: dropping to end-of-input
    would discard real headers, and the caller degrades better on a parity
    shift than on missing lines.
    """
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() != _DECODED_OPEN:
            out.append(lines[i])
            i += 1
            continue

        close = next(
            (j for j in range(i + 1, len(lines)) if lines[j].strip() == _DECODED_CLOSE),
            None,
        )
        if close is None:
            out.append(lines[i])
            i += 1
            continue
        i = close + 1

    return out


def _has_control_chars(*values: str) -> bool:
    """True if any of *values* contains a tab/newline/carriage-return.

    Both cookiejar formats this module writes (Netscape, and the raw-header
    fallback) are line-oriented — an embedded control character in a cookie
    name/value would corrupt the file. Shared by _save_stream_cookiejar and
    _cookies_from_raw_header, the two places that build Cookie objects from
    untrusted input (browser-extracted and user-pasted, respectively).
    """
    return any(ch in value for value in values for ch in ("\t", "\n", "\r"))


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

    raw_lines = _strip_decoded_blocks([line for line in raw.split("\n") if line.strip()])
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


def _cookies_from_raw_header(cookie_header_value: str) -> list[Cookie]:
    """Build Cookie objects from a raw ``Cookie:`` header value pasted by the user.

    A raw header carries no per-cookie domain/path/expiry, so every cookie is
    scoped to ``.youtube.com`` — the domain family the browser already
    restricted this exact header to when it was copied.
    """
    expires = int(time.time()) + 2 * 365 * 24 * 60 * 60
    cookies: list[Cookie] = []
    for part in cookie_header_value.strip().split(";"):
        part = part.strip()
        if not part:
            continue
        pieces = part.split("=", 1)
        if len(pieces) != 2:
            continue
        name, value = pieces[0].strip(), pieces[1].strip()
        if _has_control_chars(name, value):
            continue
        cookies.append(
            Cookie(
                version=0,
                name=name,
                value=value,
                port=None,
                port_specified=False,
                domain=".youtube.com",
                domain_specified=True,
                domain_initial_dot=True,
                path="/",
                path_specified=True,
                secure=True,
                expires=expires,
                discard=False,
                comment=None,
                comment_url=None,
                rest={},
            )
        )
    return cookies
