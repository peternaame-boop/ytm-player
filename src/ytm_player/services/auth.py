"""Authentication management for YouTube Music.

Extracts cookies automatically from the user's browser (Chrome, Firefox,
Brave, Helium, etc.) using yt-dlp's cookie extraction. Falls back to manual
header paste if auto-extraction fails.

Also writes a separate, wider-scoped (youtube.com+google.com) cookiejar file
consumed by stream.py's yt-dlp resolver — see _save_stream_cookiejar().
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
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


_ACCOUNT_SCHEMA_VERSION = 1

# A YouTube channel ID: "UC" + 22 URL-safe base64 characters.
_CHANNEL_ID_RE = re.compile(r"UC[A-Za-z0-9_-]{22}")

_NO_RENEWAL_NOTE = (
    "  Note: automatic session renewal is not available for this account; "
    "re-run `ytm setup` when the session expires."
)


@dataclass(frozen=True)
class _ProbedAccount:
    """What YouTube Music reported for one browser account slot."""

    slot: int
    name: str
    handle: str
    channel_id: str | None

    def label(self) -> str:
        parts = [self.name]
        if self.handle:
            parts.append(self.handle)
        parts.append(f"browser slot {self.slot}")
        return "  ·  ".join(parts)


@dataclass(frozen=True)
class _RecordedIdentity:
    slot: int
    channel_id: str


def _channel_id_from_account_menu(response: Any) -> str | None:
    """Extract the signed-in account's channel ID from an ``account/account_menu``
    response, or None when the response doesn't have the expected shape.

    Reads only the active account's own menu (``activeAccountHeaderRenderer``
    must be present) and accepts exactly one "Your channel" link: the
    ``ACCOUNT_BOX`` entry whose browse endpoint is a ``UC…`` id for a
    ``MUSIC_PAGE_TYPE_USER_CHANNEL`` page. Anything else — missing header,
    no such link, two of them, a non-channel id — is None, so the caller
    fails closed rather than trusting an arbitrary id found elsewhere.
    """
    try:
        menu = response["actions"][0]["openPopupAction"]["popup"]["multiPageMenuRenderer"]
        if not isinstance(menu, dict) or "activeAccountHeaderRenderer" not in menu["header"]:
            return None
        items = menu["sections"][0]["multiPageMenuSectionRenderer"]["items"]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(items, list):
        return None

    found: list[str] = []
    for item in items:
        link = item.get("compactLinkRenderer") if isinstance(item, dict) else None
        if not isinstance(link, dict):
            continue
        icon = link.get("icon")
        if not isinstance(icon, dict) or icon.get("iconType") != "ACCOUNT_BOX":
            continue
        endpoint = link.get("navigationEndpoint")
        browse = endpoint.get("browseEndpoint") if isinstance(endpoint, dict) else None
        if not isinstance(browse, dict):
            continue
        browse_id = browse.get("browseId")
        page_type = (
            (browse.get("browseEndpointContextSupportedConfigs") or {})
            .get("browseEndpointContextMusicConfig", {})
            .get("pageType")
        )
        if (
            isinstance(browse_id, str)
            and _CHANNEL_ID_RE.fullmatch(browse_id)
            and page_type == "MUSIC_PAGE_TYPE_USER_CHANNEL"
        ):
            found.append(browse_id)
    if len(found) != 1:
        return None
    return found[0]


def _probe_account(auth_path: str, slot: int) -> _ProbedAccount | None:
    """Ask YouTube Music who the session in *auth_path* is.

    Raises whatever ytmusicapi raises for an invalid session; returns None
    when the account has no name. The channel ID comes from the same
    account-menu endpoint ``get_account_info`` reads, requested again in
    raw form because ytmusicapi only parses the display fields out of it.
    """
    ytm = YTMusic(auth_path)
    account = ytm.get_account_info()
    name = account.get("accountName")
    if not name:
        return None
    handle = account.get("channelHandle") or ""
    channel_id: str | None = None
    try:
        channel_id = _channel_id_from_account_menu(ytm._send_request("account/account_menu", {}))
    except Exception:
        logger.debug("Could not read the account menu for a channel ID", exc_info=True)
    return _ProbedAccount(slot=slot, name=str(name), handle=str(handle), channel_id=channel_id)


class AuthManager:
    """Manages YouTube Music authentication via browser cookie extraction,
    and the yt-dlp stream cookiejar consumed by stream.py."""

    def __init__(
        self,
        config_dir: Path = CONFIG_DIR,
        auth_file: Path = AUTH_FILE,
        cookies_file: str | None = None,
        stream_cookies_file: Path = STREAM_COOKIES_FILE,
        account_file: Path | None = None,
    ) -> None:
        self._config_dir = config_dir
        self._auth_file = auth_file
        self._cookies_file = normalize_cookiefile(cookies_file)
        self._stream_cookies_file = stream_cookies_file
        # Identity of the session in auth.json (see _write_account_file).
        # Lives next to auth.json (ACCOUNT_FILE for the default location) so
        # tests pointing auth_file at a temp dir never touch the real one.
        self._account_file = (
            account_file if account_file is not None else auth_file.with_name("account.json")
        )

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
            return self._save_youtube_cookies(cookies, stream_jar=jar)
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
        account_backup = self._backup_bytes(self._account_file, "account file")
        stream_backup = self._backup_bytes(self._stream_cookies_file, "stream cookiejar")

        if not self._extract_and_save_from_cookies_file(cookies_file, interactive=interactive):
            return False

        try:
            if self.validate():
                return True
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            logger.warning("Network error during cookies-file validation; restoring backup")

        self._restore_or_remove(self._auth_file, backup, "auth file")
        self._restore_or_remove(self._account_file, account_backup, "account file")
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
        stream_jar: Iterable[Cookie] | None = None,
    ) -> bool:
        """Persist YouTube cookie headers into auth.json and record the account.

        Interactive (``ytm setup``): probe every browser slot, let the user
        pick, and record the chosen account's identity in account.json.

        Silent (automatic renewal): only replace the session with the SAME
        account. The channel ID recorded by the last setup must be found,
        in the saved slot or — if the browser re-ordered its accounts — in
        exactly one other slot. No recorded identity, no channel ID, no
        match, or an ambiguous match refuses the renewal; the caller then
        treats the session as expired and the user runs ``ytm setup`` once.
        """
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

        self._config_dir.mkdir(parents=True, exist_ok=True)

        if interactive:
            chosen = self._select_account_interactively(base_headers)
        else:
            chosen = self._find_recorded_account(base_headers)
        if chosen is None:
            return False

        headers = {**base_headers, "x-goog-authuser": str(chosen.slot)}
        if not self._write_session(headers, chosen):
            return False

        if stream_jar is not None:
            self._save_stream_cookiejar(stream_jar)
        return True

    # ── Account probing / selection ──────────────────────────────────

    def _probe_slot(self, base_headers: dict, slot: int) -> _ProbedAccount | None:
        """Ask YouTube Music who ``x-goog-authuser=slot`` is, or None."""
        headers = {**base_headers, "x-goog-authuser": str(slot)}
        tmp_path: str | None = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".json", dir=str(self._config_dir))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(headers, f, ensure_ascii=True, indent=4, sort_keys=True)
            return _probe_account(tmp_path, slot)
        except Exception:
            logger.debug("x-goog-authuser=%d did not work, skipping", slot)
            return None
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _select_account_interactively(self, base_headers: dict) -> _ProbedAccount | None:
        """``ytm setup``: list every account the browser is signed in to and pick one."""
        valid_accounts = [
            account
            for slot in range(5)
            if (account := self._probe_slot(base_headers, slot)) is not None
        ]
        if not valid_accounts:
            logger.warning(
                "No valid YouTube Music account found in extracted cookies (tried indices %s)",
                list(range(5)),
            )
            return None

        if len(valid_accounts) == 1:
            chosen = valid_accounts[0]
            print(f"  Authenticated as: {chosen.label()}")
        else:
            # Let the user pick (e.g. to select a Premium account).
            print()
            print("  Multiple Google accounts found. Select your YouTube Music account.")
            print("  If you have YouTube Music Premium, pick that account.")
            print()
            print("  Note: 'browser slot N' shows the position of each account in your")
            print("  browser's account list — slot 0 is the first account you added,")
            print("  slot 1 the second, and so on. To check, click your profile picture")
            print("  in Chrome/Firefox: accounts are listed in the same order.")
            print()
            for i, account in enumerate(valid_accounts):
                print(f"  [{i + 1}] {account.label()}")
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
                    return None
                print(f"  Please enter a number between 1 and {len(valid_accounts)}.")
            chosen = valid_accounts[choice]
            print(f"  Selected: {chosen.label()}")

        if chosen.channel_id is None:
            print(_NO_RENEWAL_NOTE)
        return chosen

    def _find_recorded_account(self, base_headers: dict) -> _ProbedAccount | None:
        """Silent renewal: locate the account recorded by the last setup, or None."""
        recorded = self._load_recorded_identity()
        if recorded is None:
            logger.warning(
                "Automatic session renewal refused: no account identity is recorded for "
                "this session. Run `ytm setup` once to enable it."
            )
            return None

        # The saved slot first; the rest only if the browser re-ordered its accounts.
        probed = self._probe_slot(base_headers, recorded.slot)
        if probed is not None and probed.channel_id == recorded.channel_id:
            return probed

        matches = [
            account
            for slot in range(5)
            if slot != recorded.slot
            and (account := self._probe_slot(base_headers, slot)) is not None
            and account.channel_id == recorded.channel_id
        ]
        if len(matches) == 1:
            logger.info(
                "Automatic session renewal: account moved from browser slot %d to %d",
                recorded.slot,
                matches[0].slot,
            )
            return matches[0]

        logger.warning(
            "Automatic session renewal refused: the browser's accounts %s the one this "
            "session was set up with. Run `ytm setup` to sign in again.",
            "no longer include" if not matches else "ambiguously match",
        )
        return None

    # ── Session + account record persistence ─────────────────────────

    def _write_session(self, headers: dict, account: _ProbedAccount) -> bool:
        """Write auth.json for *account*, then account.json describing it.

        The previous account record is dropped first: if the auth write
        fails part-way, stale metadata must never vouch for whatever is
        left in auth.json. A failed account.json write only disables
        automatic renewal (the session itself works), never the setup.
        """
        self._remove_account_file()
        payload = json.dumps(headers, ensure_ascii=True, indent=4, sort_keys=True).encode("utf-8")
        # O_NOFOLLOW (POSIX-only; getattr fallback for Windows) refuses to
        # follow a symlink at the target path — defense-in-depth against
        # a malicious local user planting a symlink in CONFIG_DIR.
        try:
            fd = os.open(
                str(self._auth_file),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
                SECURE_FILE_MODE,
            )
            with os.fdopen(fd, "wb") as f:
                f.write(payload)
        except OSError:
            logger.exception("Failed to write auth file %s", self._auth_file)
            return False

        self._write_account_file(account, payload)
        return True

    def _write_account_file(self, account: _ProbedAccount, auth_payload: bytes) -> bool:
        """Record *account* as the identity of the auth.json whose bytes are *auth_payload*.

        The record carries a hash of those bytes so metadata that no longer
        matches auth.json (a partial write, an auth.json replaced by hand)
        is ignored by _load_recorded_identity instead of authorising a
        renewal.
        """
        record = {
            "schema_version": _ACCOUNT_SCHEMA_VERSION,
            "x-goog-authuser": str(account.slot),
            "channel_id": account.channel_id,
            "name": account.name,
            "handle": account.handle or None,
            "auth_sha256": hashlib.sha256(auth_payload).hexdigest(),
        }

        def _write(f: IO[Any]) -> None:
            json.dump(record, f, ensure_ascii=True, indent=4, sort_keys=True)

        try:
            _atomic_write(self._account_file, "w", _write, encoding="utf-8")
        except Exception:
            logger.exception(
                "Failed to write %s; automatic session renewal is disabled until the next "
                "`ytm setup`",
                self._account_file,
            )
            self._remove_account_file()
            return False
        if account.channel_id is None:
            logger.warning(
                "No channel ID for this account; automatic session renewal is disabled until "
                "the next `ytm setup`"
            )
        return True

    def _remove_account_file(self) -> None:
        try:
            self._account_file.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove %s", self._account_file, exc_info=True)

    def _load_recorded_identity(self) -> _RecordedIdentity | None:
        """The identity account.json records for the CURRENT auth.json, or None.

        None (renewal refused) when the record is missing, malformed, has
        no channel ID, or was written for different auth.json bytes.
        """
        try:
            data = json.loads(self._account_file.read_text(encoding="utf-8"))
            current = self._auth_file.read_bytes()
        except (OSError, json.JSONDecodeError):
            logger.debug("No usable account record at %s", self._account_file, exc_info=True)
            return None
        if not isinstance(data, dict) or data.get("schema_version") != _ACCOUNT_SCHEMA_VERSION:
            return None
        channel_id = data.get("channel_id")
        slot = data.get("x-goog-authuser")
        if not (isinstance(channel_id, str) and _CHANNEL_ID_RE.fullmatch(channel_id)):
            return None
        if not (isinstance(slot, str) and slot.isdigit()):
            return None
        if data.get("auth_sha256") != hashlib.sha256(current).hexdigest():
            logger.warning(
                "%s does not describe the current %s; ignoring it",
                self._account_file.name,
                self._auth_file.name,
            )
            return None
        return _RecordedIdentity(slot=int(slot), channel_id=channel_id)

    def _record_manual_identity(self) -> None:
        """After a manual header paste, probe the pasted session for its identity."""
        try:
            saved = json.loads(self._auth_file.read_text(encoding="utf-8"))
            slot = int(saved.get("x-goog-authuser", 0))
            probed = _probe_account(str(self._auth_file), slot)
        except Exception:
            logger.debug("Could not probe the pasted session's account", exc_info=True)
            probed = None
        recorded = (
            probed is not None
            and self._write_account_file(probed, self._auth_file.read_bytes())
            and probed.channel_id is not None
        )
        if not recorded:
            print(_NO_RENEWAL_NOTE)

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
        # The pasted headers are a new session: the previous account record
        # must not survive to describe it.
        self._remove_account_file()
        try:
            import ytmusicapi

            ytmusicapi.setup(filepath=str(self._auth_file), headers_raw=normalized)
            secure_chmod(self._auth_file, SECURE_FILE_MODE)
            self._record_manual_identity()
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
