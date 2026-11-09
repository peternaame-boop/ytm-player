"""Authentication management for YouTube Music.

Extracts cookies automatically from the user's browser (Chrome, Firefox,
Brave, Helium, etc.) using yt-dlp's cookie extraction. Falls back to manual
header paste if auto-extraction fails.

Also writes a separate, wider-scoped (youtube.com+google.com) cookiejar file
consumed by stream.py's yt-dlp resolver — see _build_stream_cookiejar() and
read_vouched_stream_jar().
"""

from __future__ import annotations

import errno
import hashlib
import io
import json
import logging
import os
import re
import stat
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
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
    """Replace *path* atomically through a temp file this call created.

    The temp file is opened O_CREAT | O_EXCL (plus O_NOFOLLOW where the
    platform has it) under a random name, so on every platform — Windows
    included — it is a file of our own: never an existing entry, never a
    planted symlink written through. *write* fills it via the fd opened in
    *mode* (and *encoding*, for text mode); it is chmod'ed to
    SECURE_FILE_MODE and then os.replace()d into place.

    Symlink at the destination: one that is already there is refused
    (OSError ELOOP) and left alone. os.replace() never writes through a
    link, so the target of a link planted between that check and the
    replace is untouched as well — the link entry itself is replaced by the
    file. The guarantee is "a symlink's target is never overwritten", not
    "a planted link always survives".

    On any failure only the temp file this call created is removed and the
    exception re-raised; callers decide what to catch and how to log.
    """
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}-{os.urandom(4).hex()}")
    created = False
    try:
        fd = os.open(
            str(tmp_path),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            SECURE_FILE_MODE,
        )
        created = True
        with os.fdopen(fd, mode, encoding=encoding) as f:
            write(f)
        secure_chmod(tmp_path, SECURE_FILE_MODE)
        if _is_symlink(path):
            raise OSError(errno.ELOOP, "refusing to replace a symlink", str(path))
        os.replace(tmp_path, path)
    except Exception:
        if created:
            tmp_path.unlink(missing_ok=True)
        raise


def _is_symlink(path: Path) -> bool:
    """lstat-based check; on Windows this also reports reparse-point links."""
    try:
        return stat.S_ISLNK(os.lstat(path).st_mode)
    except FileNotFoundError:
        return False


class SessionError(RuntimeError):
    """A sign-in file operation could not complete safely.

    Deliberately NOT an OSError: this module's auth-file fallbacks and read
    guards catch OSError, and none of them may absorb one of these.
    """


class SessionBusyError(SessionError):
    """auth.json / account.json are mid-replacement by another writer."""


class SessionLockTimeoutError(SessionError):
    """Another ytm thread or process held the sign-in lock for the whole wait."""


class SessionUnreadableError(SessionError):
    """account.json exists but cannot be read: the session cannot be
    classified, so no client is built from it (an unreadable record is
    never treated as an absent one)."""


_LOCK_TIMEOUT_SECONDS = 30.0
_LOCK_POLL_SECONDS = 0.05


@dataclass
class _PathLock:
    """In-process half of the sign-in lock for one lock-file path."""

    mutex: threading.Lock = field(default_factory=threading.Lock)
    owner: int | None = None  # threading.get_ident() of the holder


_PATH_LOCKS: dict[str, _PathLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(lock_path: Path) -> _PathLock:
    """The one _PathLock for *lock_path*, shared by every AuthManager in the process."""
    key = os.path.normcase(os.path.abspath(lock_path))
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, _PathLock())


def _open_lock_file(lock_path: Path) -> int:
    """Open the lock file (creating it 0600 if needed), refusing a symlink or
    reparse point at its path on every platform. O_NOFOLLOW closes the
    check→open gap where the platform has it; on Windows that gap remains
    and the worst case is a one-byte range lock on the link's target."""
    if _is_symlink(lock_path):
        raise OSError(errno.ELOOP, "refusing to lock through a symlink", str(lock_path))
    fd = os.open(
        str(lock_path),
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        SECURE_FILE_MODE,
    )
    try:
        if sys.platform != "win32":
            os.fchmod(fd, SECURE_FILE_MODE)
    except OSError:
        os.close(fd)
        raise
    return fd


def _try_os_lock(fd: int) -> bool:
    """One non-blocking attempt at the OS-level exclusive lock on *fd*."""
    if sys.platform == "win32":
        import msvcrt

        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _release_os_lock(fd: int) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        logger.debug("Could not release the sign-in lock cleanly", exc_info=True)
    finally:
        os.close(fd)


class _SessionLock:
    """Exclusive lock on the sign-in files: across threads (one _PathLock per
    lock path, shared by every AuthManager in the process) and across
    processes (flock / msvcrt.locking on ``<auth_file>.lock``).

    Not re-entrant: acquiring it again from the holding thread is a
    programming error and raises RuntimeError at once. The lock file is
    never unlinked — removing it would let a later opener lock a different
    inode and defeat the exclusion.
    """

    def __init__(self, lock_path: Path) -> None:
        self._path = lock_path
        self._state = _path_lock(lock_path)
        self._fd: int | None = None
        self.last_error: OSError | None = None

    def _check_not_held_by_me(self) -> None:
        if self._state.owner == threading.get_ident():
            raise RuntimeError("session lock is not re-entrant")

    def acquire(self, timeout: float = _LOCK_TIMEOUT_SECONDS) -> None:
        """Wait up to *timeout* seconds — one deadline covering both the
        thread mutex and the OS lock — then raise SessionLockTimeoutError. An
        OSError opening the lock file propagates."""
        self._check_not_held_by_me()
        deadline = time.monotonic() + timeout
        if not self._state.mutex.acquire(timeout=max(timeout, 0.0)):
            raise SessionLockTimeoutError(f"sign-in lock {self._path} is held by another thread")
        try:
            fd = _open_lock_file(self._path)
            try:
                while not _try_os_lock(fd):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise SessionLockTimeoutError(
                            f"sign-in lock {self._path} is held by another process"
                        )
                    time.sleep(min(_LOCK_POLL_SECONDS, remaining))
            except BaseException:
                os.close(fd)
                raise
        except BaseException:
            self._state.mutex.release()
            raise
        self._fd = fd
        self._state.owner = threading.get_ident()

    def try_acquire(self) -> str:
        """Non-blocking: ``"acquired"``; ``"held"`` by another thread or
        process; or ``"unavailable"`` — the lock file could not be opened,
        which says nothing about other holders (``last_error`` has why)."""
        self._check_not_held_by_me()
        if not self._state.mutex.acquire(blocking=False):
            return "held"
        try:
            fd = _open_lock_file(self._path)
        except OSError as exc:
            self._state.mutex.release()
            self.last_error = exc
            return "unavailable"
        if not _try_os_lock(fd):
            os.close(fd)
            self._state.mutex.release()
            return "held"
        self._fd = fd
        self._state.owner = threading.get_ident()
        return "acquired"

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        self._state.owner = None
        try:
            _release_os_lock(fd)
        finally:
            self._state.mutex.release()

    def __enter__(self) -> _SessionLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


_MISSING_STAT = (-1, -1, -1)


def file_signature(path: str | os.PathLike[str] | None) -> tuple[int, int, int]:
    """``(st_ino, st_mtime_ns, st_size)`` of *path*, or a sentinel when there
    is no such file (or it cannot be stat'ed). The files this module writes
    are only ever replaced atomically, so a changed triple means different
    bytes; the inode guards against a same-nanosecond, same-size rewrite."""
    if path is None:
        return _MISSING_STAT
    try:
        st = os.stat(path)
    except OSError:
        return _MISSING_STAT
    return (st.st_ino, st.st_mtime_ns, st.st_size)


@dataclass(frozen=True)
class _FileRead:
    """One read of a sign-in file: its bytes, or why there are none.

    ``data`` is None both for a missing file and for one that exists but
    could not be read; ``error`` tells the two apart. Consumers must never
    collapse "unreadable" into "absent": absence is a legacy shape that
    inherits trust, an unreadable record forbids everything.
    """

    data: bytes | None
    stat: tuple[int, int, int]
    error: OSError | None = None

    @property
    def missing(self) -> bool:
        return self.data is None and self.error is None

    @property
    def unreadable(self) -> bool:
        return self.error is not None

    @property
    def key(self) -> tuple[bytes | None, bool]:
        """What two reads of the same file are compared on."""
        return self.data, self.unreadable


def _read_with_stat(path: str | os.PathLike[str]) -> _FileRead:
    """Read *path* and fstat the very descriptor the bytes came from."""
    try:
        with open(path, "rb") as f:
            st = os.fstat(f.fileno())
            return _FileRead(f.read(), (st.st_ino, st.st_mtime_ns, st.st_size))
    except FileNotFoundError:
        return _FileRead(None, _MISSING_STAT)
    except OSError as exc:
        return _FileRead(None, file_signature(path), exc)


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


@dataclass(frozen=True)
class _SessionOwner:
    """What a renewal attempt saw on disk when it started: the auth.json
    hash and the record's revision. A commit guarded by an owner refuses
    when either differs — a newer ``ytm setup``, even one that wrote
    byte-identical headers, is never overwritten."""

    auth_sha256: str
    revision: str | None


def _identity_from_record(record: dict[str, Any] | None) -> _RecordedIdentity | None:
    if record is None or record.get("verified") is False:
        return None
    channel_id = record.get("channel_id")
    slot = record.get("x-goog-authuser")
    if not (isinstance(channel_id, str) and _CHANNEL_ID_RE.fullmatch(channel_id)):
        return None
    if not (isinstance(slot, str) and slot.isdigit()):
        return None
    return _RecordedIdentity(slot=int(slot), channel_id=channel_id)


@dataclass(frozen=True)
class _SessionPair:
    """One consistent snapshot of auth.json and the record describing those
    exact bytes (``record`` is None when there is no usable one)."""

    auth_bytes: bytes
    record: dict[str, Any] | None

    @property
    def identity(self) -> _RecordedIdentity | None:
        return _identity_from_record(self.record)

    @property
    def owner(self) -> _SessionOwner:
        revision = self.record.get("revision") if self.record is not None else None
        return _SessionOwner(
            _sha256(self.auth_bytes), revision if isinstance(revision, str) else None
        )


def _owner_of(auth_bytes: bytes | None, record_raw: bytes | None) -> _SessionOwner | None:
    """The owner the files on disk currently describe (None without auth.json)."""
    if auth_bytes is None:
        return None
    record = _parse_record(record_raw)
    auth_sha256 = _sha256(auth_bytes)
    revision = (
        record.get("revision") if record and record.get("auth_sha256") == auth_sha256 else None
    )
    return _SessionOwner(auth_sha256, revision if isinstance(revision, str) else None)


def _parse_record(raw: bytes | None) -> dict[str, Any] | None:
    """account.json as a dict, or None when missing, malformed or another schema."""
    if raw is None:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("schema_version") != _ACCOUNT_SCHEMA_VERSION:
        return None
    return data


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _serialize_headers(headers: dict) -> bytes:
    """The one serialisation of an auth.json candidate: probed and committed as is."""
    return json.dumps(headers, ensure_ascii=True, indent=4, sort_keys=True).encode("utf-8")


def _candidate_payload(base_headers: dict, slot: int) -> bytes:
    return _serialize_headers({**base_headers, "x-goog-authuser": str(slot)})


def _build_record(
    account: _ProbedAccount, auth_payload: bytes, stream_sha256: str | None, *, verified: bool
) -> dict[str, Any]:
    """The account.json record for exactly *auth_payload* and the jar hashed
    as *stream_sha256* (None: this session has no stream jar)."""
    return {
        "schema_version": _ACCOUNT_SCHEMA_VERSION,
        "x-goog-authuser": str(account.slot),
        "channel_id": account.channel_id,
        "name": account.name,
        "handle": account.handle or None,
        "verified": verified,
        "auth_sha256": _sha256(auth_payload),
        "stream_sha256": stream_sha256,
        "revision": os.urandom(16).hex(),
    }


def _record_writer(record: dict[str, Any]) -> Callable[[IO[Any]], None]:
    def _write(f: IO[Any]) -> None:
        json.dump(record, f, ensure_ascii=True, indent=4, sort_keys=True)

    return _write


def _bytes_writer(data: bytes) -> Callable[[IO[Any]], None]:
    def _write(f: IO[Any]) -> None:
        f.write(data)

    return _write


def _build_stream_cookiejar(jar: Iterable[Cookie]) -> bytes:
    """Netscape-format bytes of the wide youtube.com/google.com cookiejar that
    stream.py's yt-dlp resolver loads.

    Builds a fresh jar rather than mutating *jar* in place — callers may
    own or share that iterable, and this function has no reason to assume
    it's safe to consume destructively.
    """
    from yt_dlp.cookies import YoutubeDLCookieJar

    stream_jar = YoutubeDLCookieJar()
    for cookie in jar:
        bare = cookie.domain.lstrip(".")
        if bare in ("youtube.com", "google.com") or bare.endswith((".youtube.com", ".google.com")):
            value = cookie.value or ""
            if _has_control_chars(cookie.name, value):
                continue
            stream_jar.set_cookie(cookie)
    buffer = io.StringIO()
    # save()'s stub types filename as str | None, but its open() accepts a
    # file object directly at runtime (non-path-like branch truncates and
    # reuses it) — verified against yt-dlp source.
    stream_jar.save(buffer, ignore_discard=True, ignore_expires=True)  # type: ignore[arg-type]
    return buffer.getvalue().encode("utf-8")


# ── Stream cookiejar vouching (consumed by stream.py) ────────────────────


@dataclass(frozen=True)
class VouchedJar:
    """Result of read_vouched_stream_jar: the jar bytes the resolver may load
    (None → stream anonymously) and the signature of the exact file snapshot
    that decision was made on."""

    payload: bytes | None
    signature: tuple[Any, ...]


def _vouch_failure(auth: _FileRead, record: _FileRead, jar: _FileRead) -> str | None:
    """Why the jar must not be used, or None when it may.

    A jar is vouched for when the record describing the current auth.json
    carries its hash. Two legacy shapes are accepted as today's trust:
    an install with auth.json and NO record file at all, and a record that
    parses but has no ``stream_sha256`` key — both predate this version,
    and AuthManager._leave_legacy_locked makes sure this version never
    creates either of them (a strict record is published before any jar).
    A record that exists but cannot be read, or does not parse, is neither:
    it refuses.
    """
    if jar.data is None:
        return "the stream cookie file is unreadable"
    if auth.unreadable:
        return "auth.json is unreadable"
    if auth.data is None:
        return "auth.json is missing"
    if record.unreadable:
        return f"account.json cannot be read ({record.error})"
    if record.data is None:
        return None
    parsed = _parse_record(record.data)
    if parsed is None:
        return "account.json is malformed"
    record_dict = parsed
    if record_dict.get("auth_sha256") != _sha256(auth.data):
        return "account.json does not describe the current auth.json"
    if "stream_sha256" not in record_dict:
        return None
    expected = record_dict["stream_sha256"]
    if expected is None:
        return "the saved sign-in has no stream cookie file"
    if expected != _sha256(jar.data):
        return "the stream cookie file does not match the saved sign-in"
    return None


def read_vouched_stream_jar(jar_path: str, session: tuple[str, str] | None) -> VouchedJar:
    """Bytes of the stream cookie jar at *jar_path* that the sign-in files
    vouch for, or None (stream anonymously). *session* is
    ``(auth_path, account_path)``.

    Every file is read as open → fstat → read, so the returned signature
    ``(jar_path, jar, auth, record)`` of ``(st_ino, st_mtime_ns, st_size)``
    triples describes the bytes that were actually checked (these files are
    only ever replaced atomically). The record is read before and after the
    others and must not have changed in between; three inconsistent
    attempts refuse for this snapshot — the next signature change reruns
    the check. Every refusal logs one warning naming the reason.
    """
    if session is None:
        logger.warning(
            "Stream cookie file %s is not used: no sign-in files to check it against", jar_path
        )
        return VouchedJar(None, (jar_path, file_signature(jar_path), _MISSING_STAT, _MISSING_STAT))
    auth_path, record_path = session
    signature: tuple[Any, ...] = (jar_path, _MISSING_STAT, _MISSING_STAT, _MISSING_STAT)
    for _ in range(3):
        record = _read_with_stat(record_path)
        auth = _read_with_stat(auth_path)
        jar = _read_with_stat(jar_path)
        record_again = _read_with_stat(record_path)
        signature = (jar_path, jar.stat, auth.stat, record.stat)
        if record.key != record_again.key:
            continue
        reason = _vouch_failure(auth, record, jar)
        if reason is None:
            return VouchedJar(jar.data, signature)
        logger.warning(
            "Stream cookie file %s is not used: %s. Streaming without session cookies; "
            "run `ytm setup` to refresh it.",
            jar_path,
            reason,
        )
        return VouchedJar(None, signature)
    logger.warning(
        "Stream cookie file %s is not used: the sign-in files changed while they were "
        "being checked",
        jar_path,
    )
    return VouchedJar(None, signature)


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
        # Identity of the session in auth.json (see _commit_session).
        # Lives next to auth.json (ACCOUNT_FILE for the default location) so
        # tests pointing auth_file at a temp dir never touch the real one.
        self._account_file = (
            account_file if account_file is not None else auth_file.with_name("account.json")
        )
        # Cross-process/thread lock for every write to the files above; also
        # next to auth.json, for the same reason. Never unlinked.
        self._lock_path = auth_file.with_name(auth_file.name + ".lock")

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

    def create_bound_client(self, user: str | None = None) -> tuple[YTMusic, str | None]:
        """Create a client from ONE snapshot of auth.json, together with the
        channel ID account.json records for exactly that snapshot (None
        without a matching record).

        The client is built from the snapshot's parsed headers, never from
        the path: a constructor re-reading the file could load a different
        session than the one the record was checked against (another
        process's ``ytm setup`` landing in between), and a client tagged
        with the wrong identity would defeat the retry guard in
        YTMusicService.

        Raises SessionBusyError while another writer is mid-replacement; that is
        not an OSError and is never turned into an unbound client here.
        """
        try:
            pair = self._read_session_pair()
        except OSError:
            # Missing or unreadable auth.json only: the client built from the
            # path reports that itself, as before.
            return YTMusic(str(self._auth_file), user=user), None
        identity = pair.identity
        client = YTMusic(json.loads(pair.auth_bytes), user=user)
        return client, identity.channel_id if identity is not None else None

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

    def try_auto_refresh(self, expected_channel_id: str | None = None) -> bool:
        """Attempt to silently refresh auth from cookies/browser.

        Called when the app detects an auth failure at runtime. Returns
        True if fresh cookies were extracted, probed and committed. Only the
        account recorded by the last ``ytm setup`` is accepted; when the
        caller knows which account its failed client belonged to, pass it
        as *expected_channel_id* and the recorded account must be that one.

        The pair on disk is read ONCE here; both sources (cookies file, then
        browser) check the recorded identity from that snapshot and commit
        only while the files still match it (see _commit_session).
        """
        try:
            pair = self._read_session_pair()
        except SessionBusyError:
            logger.warning(
                "Automatic session renewal refused: another ytm process is updating the sign-in"
            )
            return False
        except SessionUnreadableError as exc:
            logger.warning("Automatic session renewal refused: %s", exc)
            return False
        except OSError:
            logger.warning(
                "Automatic session renewal refused: no saved session at %s", self._auth_file
            )
            return False
        recorded, owner = pair.identity, pair.owner

        if self._cookies_file and self._refresh_from_cookies_file(
            Path(self._cookies_file),
            expected_channel_id=expected_channel_id,
            recorded=recorded,
            owner=owner,
        ):
            return True

        detected = self._detect_browser()
        if detected is None:
            return False
        browser, cookies, jar = detected
        try:
            return self._save_youtube_cookies(
                cookies,
                stream_jar=jar,
                expected_channel_id=expected_channel_id,
                recorded=recorded,
                owner=owner,
            )
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
        the stream cookiejar (youtube.com + google.com) via _commit_session.
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

    def _refresh_from_cookies_file(
        self,
        cookies_file: Path,
        interactive: bool = False,
        expected_channel_id: str | None = None,
        *,
        recorded: _RecordedIdentity | None = None,
        owner: _SessionOwner | None = None,
    ) -> bool:
        """Refresh auth from a cookies file.

        The candidate session is probed before anything is written and the
        commit is guarded by *owner*, so there is nothing to back up or roll
        back: a refused or failed refresh leaves the working credentials as
        they were, and a session another process committed meanwhile is
        never restored over.
        """
        return self._extract_and_save_from_cookies_file(
            cookies_file,
            interactive=interactive,
            expected_channel_id=expected_channel_id,
            recorded=recorded,
            owner=owner,
        )

    def _extract_and_save_from_cookies_file(
        self,
        cookies_file: Path,
        interactive: bool = False,
        expected_channel_id: str | None = None,
        *,
        recorded: _RecordedIdentity | None = None,
        owner: _SessionOwner | None = None,
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

        if self._save_youtube_cookies(
            yt_cookies,
            interactive=interactive,
            stream_jar=jar,
            expected_channel_id=expected_channel_id,
            recorded=recorded,
            owner=owner,
        ):
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
        expected_channel_id: str | None = None,
        *,
        recorded: _RecordedIdentity | None = None,
        owner: _SessionOwner | None = None,
    ) -> bool:
        """Persist YouTube cookie headers into auth.json and record the account.

        Interactive (``ytm setup``): probe every browser slot, let the user
        pick, and record the chosen account's identity in account.json.

        Silent (automatic renewal): only replace the session with the SAME
        account. The channel ID recorded by the last setup (*recorded*, from
        the snapshot the attempt started with) must be found, in the saved
        slot or — if the browser re-ordered its accounts — in exactly one
        other slot. No recorded identity, no channel ID, no match, or an
        ambiguous match refuses the renewal; the caller then treats the
        session as expired and the user runs ``ytm setup`` once. The commit
        itself refuses when the files no longer match *owner*.
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
            chosen = self._find_recorded_account(base_headers, expected_channel_id, recorded)
        if chosen is None:
            return False

        # The same bytes that were probed for *chosen* are committed.
        payload = _candidate_payload(base_headers, chosen.slot)
        return self._commit_session(payload, chosen, stream_jar, owner=owner)

    # ── Account probing / selection ──────────────────────────────────

    def _probe_payload(self, payload: bytes, slot: int) -> _ProbedAccount | None:
        """Ask YouTube Music who the session in *payload* (the exact auth.json
        bytes a commit would write) is, or None."""
        tmp_path: str | None = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".json", dir=str(self._config_dir))
            with os.fdopen(fd, "wb") as f:
                f.write(payload)
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
            if (account := self._probe_payload(_candidate_payload(base_headers, slot), slot))
            is not None
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

    def _find_recorded_account(
        self,
        base_headers: dict,
        expected_channel_id: str | None = None,
        recorded: _RecordedIdentity | None = None,
    ) -> _ProbedAccount | None:
        """Silent renewal: locate the account recorded by the last setup, or None.

        *recorded* is the identity from the snapshot the renewal attempt
        started from (never re-read here, so the check and the ownership
        guard in _commit_session see the same pair). With
        *expected_channel_id* (the account of the client whose call
        failed), the recorded account must be that one: a ``ytm setup`` for
        another account that landed in the meantime must not renew on its
        behalf.
        """
        if recorded is None:
            logger.warning(
                "Automatic session renewal refused: no account identity is recorded for "
                "this session. Run `ytm setup` once to enable it."
            )
            return None
        if expected_channel_id is not None and recorded.channel_id != expected_channel_id:
            logger.warning(
                "Automatic session renewal refused: the saved session now belongs to a "
                "different account than the one that failed."
            )
            return None

        # The saved slot first; the rest only if the browser re-ordered its accounts.
        probed = self._probe_payload(_candidate_payload(base_headers, recorded.slot), recorded.slot)
        if probed is not None and probed.channel_id == recorded.channel_id:
            return probed

        matches = [
            account
            for slot in range(5)
            if slot != recorded.slot
            and (account := self._probe_payload(_candidate_payload(base_headers, slot), slot))
            is not None
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

    def _read_record_raw(self) -> bytes | None:
        """account.json bytes; None when there is no such file. A file that
        exists but cannot be read raises — it is never reported as absent."""
        try:
            return self._account_file.read_bytes()
        except FileNotFoundError:
            return None

    def _read_record_or_refuse(self) -> bytes | None:
        try:
            return self._read_record_raw()
        except OSError as exc:
            raise SessionUnreadableError(
                f"{self._account_file} exists but cannot be read: {exc}"
            ) from exc

    def _read_pair_locked(self) -> tuple[bytes | None, bytes | None]:
        """Raw auth.json and account.json bytes. The caller holds the session
        lock, so the two reads are one consistent snapshot. An unreadable
        record raises, which fails the commit."""
        try:
            auth_bytes: bytes | None = self._auth_file.read_bytes()
        except FileNotFoundError:
            auth_bytes = None
        return auth_bytes, self._read_record_raw()

    def _classify_pair(
        self, auth_bytes: bytes, record_raw: bytes | None, *, writer_possible: bool
    ) -> _SessionPair | None:
        """Pair a snapshot of auth.json with the record that describes it.

        A record for other auth bytes is either a commit in flight (auth.json
        replaced, account.json not yet — *writer_possible*, so the caller
        retries) or a permanent leftover of a crash between those two writes
        (no writer holds the lock): then the session is usable but unbound,
        exactly what a record-less session is today, and the record is
        ignored with a warning. Nothing here ever invents an identity.
        """
        if record_raw is None:
            return _SessionPair(auth_bytes, None)
        record = _parse_record(record_raw)
        if record is None:
            # Never a mid-commit shape (records are replaced whole): permanent.
            logger.warning("%s is malformed; ignoring it", self._account_file.name)
            return _SessionPair(auth_bytes, None)
        if record.get("auth_sha256") == _sha256(auth_bytes):
            return _SessionPair(auth_bytes, record)
        if writer_possible:
            return None
        logger.warning(
            "%s does not describe the current %s; ignoring it",
            self._account_file.name,
            self._auth_file.name,
        )
        return _SessionPair(auth_bytes, None)

    def _read_session_pair(self) -> _SessionPair:
        """One consistent snapshot of auth.json + its record, without waiting.

        Takes the session lock only when it is free right now (non-blocking
        try). Acquired: the two files are read under it. Held by another
        thread or process, or the lock file cannot be opened at all (which
        proves nothing about other holders): a lock-free record → auth →
        record read is used, consistent when both record reads agree and the
        record describes the auth bytes. Three inconsistent attempts raise
        SessionBusyError — a RuntimeError, never an OSError — so no fallback can
        quietly build a client from a half-replaced pair. A record that exists
        but cannot be read raises SessionUnreadableError the same way.

        Raises OSError only for a missing/unreadable auth.json.
        """
        lock = _SessionLock(self._lock_path)
        state = lock.try_acquire()
        if state == "acquired":
            try:
                auth_bytes = self._auth_file.read_bytes()
                record_raw = self._read_record_or_refuse()
            finally:
                lock.release()
            pair = self._classify_pair(auth_bytes, record_raw, writer_possible=False)
            assert pair is not None  # writer_possible=False always classifies
            return pair
        for _ in range(3):
            first = self._read_record_or_refuse()
            auth_bytes = self._auth_file.read_bytes()
            second = self._read_record_or_refuse()
            if first != second:
                continue
            pair = self._classify_pair(auth_bytes, first, writer_possible=True)
            if pair is not None:
                return pair
        if state == "unavailable":
            logger.warning(
                "Sign-in files are inconsistent and the lock %s cannot be opened (%s)",
                self._lock_path,
                lock.last_error,
            )
        raise SessionBusyError(f"{self._auth_file.name} is being replaced by another writer")

    def _load_recorded_identity(self, auth_bytes: bytes | None = None) -> _RecordedIdentity | None:
        """The identity account.json records for the CURRENT auth.json, or None.

        None (renewal refused) when the record is missing, malformed, has
        no channel ID, is unverified, or was written for different
        auth.json bytes. *auth_bytes* lets a caller that already read
        auth.json check the record against exactly that snapshot.
        """
        try:
            pair = self._read_session_pair()
        except (OSError, SessionError):
            logger.debug("No usable account record at %s", self._account_file, exc_info=True)
            return None
        if auth_bytes is not None and auth_bytes != pair.auth_bytes:
            return None
        return pair.identity

    def _commit_session(
        self,
        payload: bytes,
        account: _ProbedAccount,
        jar: Iterable[Cookie] | None,
        *,
        owner: _SessionOwner | None = None,
        verified: bool = True,
    ) -> bool:
        """Publish a new session: stream jar, auth.json, then account.json.

        Runs under the session lock. Steps, each one atomic replace:

        0. With *owner* (an automatic renewal), refuse unless the pair on
           disk is still the one the attempt started from — a newer
           ``ytm setup`` is never overwritten. Setup passes no owner.
        0b. A legacy pair (no record, or a record without ``stream_sha256``)
           first gets a strict record for the *current* files, so the jar
           published next can never be accepted under the legacy rule.
        1. The stream jar for *jar* (removed when *jar* is None).
        2. auth.json = *payload* — the exact bytes that were probed.
        3. account.json for exactly those bytes, with a fresh revision and
           the hash of the jar from step 1.

        A failure or crash leaves every earlier step in place and every file
        whole: after step 1 the record still describes the old auth.json (the
        new jar is unvouched and refused by the resolver); after step 2 the
        record describes nothing (the session works unbound, renewal is
        refused until ``ytm setup``). No rollback, no repair, no cleanup.
        """
        lock = _SessionLock(self._lock_path)
        try:
            self._config_dir.mkdir(parents=True, exist_ok=True)
            lock.acquire(timeout=_LOCK_TIMEOUT_SECONDS)
        except SessionLockTimeoutError as exc:
            logger.warning("Another ytm process is updating the sign-in; not saving (%s)", exc)
            return False
        except OSError as exc:
            logger.warning("Cannot lock the sign-in files: %s: %s", self._lock_path, exc)
            return False
        step = "reading the current sign-in"
        try:
            auth_bytes, record_raw = self._read_pair_locked()
            if owner is not None and _owner_of(auth_bytes, record_raw) != owner:
                logger.warning(
                    "Automatic session renewal refused: the saved session changed while it "
                    "was being renewed"
                )
                return False
            step = "recording the current stream cookie file"
            self._leave_legacy_locked(auth_bytes, record_raw)
            step = "writing the stream cookie file"
            stream_sha256: str | None = None
            if jar is not None:
                jar_bytes = _build_stream_cookiejar(jar)
                _atomic_write(self._stream_cookies_file, "wb", _bytes_writer(jar_bytes))
                stream_sha256 = _sha256(jar_bytes)
            else:
                self._stream_cookies_file.unlink(missing_ok=True)
            step = f"writing {self._auth_file.name}"
            _atomic_write(self._auth_file, "wb", _bytes_writer(payload))
            step = f"writing {self._account_file.name}"
            record = _build_record(account, payload, stream_sha256, verified=verified)
            _atomic_write(self._account_file, "w", _record_writer(record), encoding="utf-8")
        except Exception:
            logger.exception("Failed to save the sign-in while %s", step)
            return False
        finally:
            lock.release()
        if account.channel_id is None or not verified:
            logger.warning(
                "No verified account identity for this session; automatic session renewal "
                "is disabled until the next `ytm setup`"
            )
        return True

    def _leave_legacy_locked(self, auth_bytes: bytes | None, record_raw: bytes | None) -> None:
        """Step 0b of _commit_session: make a legacy pair strict before any
        new file is published.

        Genuine legacy = no record file at all, or a record that parses (this
        schema) but has no ``stream_sha256`` key — the two shapes that predate
        this version. Only those inherit today's implicit trust: the record
        written here keeps every field an existing record has and adds the
        key with the hash of the jar on disk *now* (or null). With no record
        it carries no identity (``channel_id`` null, ``verified`` false) —
        renewal stays refused exactly as for a record-less session today.

        A record that exists but does not parse, or is of another schema, is
        NOT legacy: it is replaced by a provisional record that vouches for
        no jar at all (``stream_sha256`` null) and no identity. Likewise on a
        fresh install (no auth.json) the provisional record has
        ``auth_sha256`` null too, so it vouches for nothing that is not yet
        there.
        """
        parsed = _parse_record(record_raw)
        if parsed is not None and "stream_sha256" in parsed:
            return
        invalid = record_raw is not None and parsed is None
        if parsed is None:
            slot = "0"
            if auth_bytes is not None:
                try:
                    slot = str(int(json.loads(auth_bytes).get("x-goog-authuser", 0)))
                except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
                    pass
            record = {
                "schema_version": _ACCOUNT_SCHEMA_VERSION,
                "x-goog-authuser": slot,
                "channel_id": None,
                "name": "",
                "handle": None,
                "verified": False,
                "auth_sha256": _sha256(auth_bytes) if auth_bytes is not None else None,
            }
        else:
            record = dict(parsed)
        jar_sha256: str | None = None
        if auth_bytes is not None and not invalid:
            try:
                jar_sha256 = _sha256(self._stream_cookies_file.read_bytes())
            except FileNotFoundError:
                jar_sha256 = None
        record["stream_sha256"] = jar_sha256
        _atomic_write(self._account_file, "w", _record_writer(record), encoding="utf-8")
        if invalid:
            logger.warning(
                "%s was malformed; replaced it with a record that vouches for no stream "
                "cookie file and no account identity",
                self._account_file.name,
            )
        else:
            logger.info("Recorded the current stream cookie file for the saved sign-in")

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

            headers = json.loads(ytmusicapi.setup(headers_raw=normalized))
            slot = int(headers.get("x-goog-authuser", 0))
        except Exception as exc:
            # A rejected paste changes nothing on disk: the previous session
            # and the record describing it stay exactly as they were.
            logger.error("Failed to parse headers: %s", exc)
            print(f"\n  Error: {exc}")
            return False

        # The bytes probed below are the bytes committed: the slot is
        # normalised before the single serialisation.
        headers["x-goog-authuser"] = str(slot)
        payload = _serialize_headers(headers)
        cookie_value = headers.get("cookie")
        jar = _cookies_from_raw_header(cookie_value) if cookie_value else None

        probed = self._probe_payload(payload, slot)
        verified = probed is not None
        if probed is None:
            logger.warning(
                "Could not verify the pasted session; saving it without an account identity"
            )
            probed = _ProbedAccount(slot=slot, name="", handle="", channel_id=None)
        if not self._commit_session(payload, probed, jar, verified=verified):
            print("\n  Error: could not save the sign-in. See the log for details.")
            return False
        print()
        print("  Browser authentication saved.")
        if probed.channel_id is None:
            print(_NO_RENEWAL_NOTE)
        return True


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
    name/value would corrupt the file. Shared by _build_stream_cookiejar and
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
