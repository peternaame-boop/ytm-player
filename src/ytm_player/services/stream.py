"""Stream URL resolution using yt-dlp."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ytm_player.config.settings import get_settings
from ytm_player.services.yt_dlp_options import apply_configured_yt_dlp_options
from ytm_player.utils.formatting import VALID_VIDEO_ID

logger = logging.getLogger(__name__)

# Cache resolved URLs for 5 hours (YouTube URLs typically expire after ~6 hours).
_CACHE_TTL_SECONDS = 5 * 60 * 60

# Maximum number of entries to keep in cache.  When exceeded, the oldest
# entries are evicted regardless of TTL.
_CACHE_MAX_SIZE = 128

# Treat a cached URL as stale this many seconds before its actual expiry so
# playback is never handed a URL that dies mid-stream.  Applied consistently
# by every read path (_get_cached, is_expired, resolve).
_EXPIRY_BUFFER_SECONDS = 300


def _is_stale(expires_at: float) -> bool:
    """True when a cached URL has passed, or is within the buffer of, expiry."""
    return time.time() >= expires_at - _EXPIRY_BUFFER_SECONDS


# Cached for the process lifetime — see _detect_stream_cookies. Guarded by
# _stream_cookies_lock: _get_ydl() only ever calls this from a background
# thread (via asyncio.to_thread), so holding a lock for the full slow
# extraction is safe — it can never block the UI thread. Without it, two
# resolves starting close together (the startup warmup and a direct play
# action, say) both see "not yet probed" and both run the full Keychain
# extraction concurrently: confirmed in practice as one thread finishing
# in ~19s while the other, contending for the same Keychain access, took
# ~48s — plus each thread independently deciding to show its own "this
# might take a while" toast, since neither had finished yet either.
_stream_browser_probed = False
_cached_stream_browser: str | None = None
_cached_stream_cookiefile: str | None = None
_stream_cookies_lock = threading.Lock()
_cookie_extraction_notify_claimed = False

# How long a previously-written cookiefile is trusted before re-extracting.
# _stream_browser_probed only tracks "have we run this in THIS process" —
# it resets on every launch — so without this, every fresh launch redid
# the full browser-detection + extraction dance regardless of a valid
# cache file sitting right there from the last launch, seconds or minutes
# earlier. 1 hour balances that against actual staleness risk (a browser
# logout or account switch invalidating the cached session) — short
# enough that a genuinely stale cache doesn't linger for long, long
# enough that closely-spaced restarts (this entire testing session, or
# just normal quit/relaunch usage) actually benefit from it.
_STREAM_COOKIES_MAX_AGE_SECONDS = 60 * 60


def _fresh_cached_cookiefile() -> str | None:
    """Return the on-disk cookiefile path if it exists and isn't stale.

    Read-only and side-effect-free — safe to call from both the toast
    claim check and _detect_stream_cookies() without them disagreeing
    about whether a fresh extraction is about to happen.
    """
    from ytm_player.config.paths import CONFIG_DIR

    path = CONFIG_DIR / "stream_cookies_cache.txt"
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return None
    if age >= _STREAM_COOKIES_MAX_AGE_SECONDS:
        return None
    return str(path)


def claim_cookie_extraction_notification() -> bool:
    """Atomically claim the right to show a "this might take a while" toast
    for the upcoming one-time cookie decryption.

    Returns True for at most one caller per process. A plain peek here
    (an earlier version of this returned True/False without claiming
    anything) let two resolves starting close together — the startup
    warmup and a direct play action, say — both see "not yet probed" and
    both show their own toast, even after _stream_cookies_lock started
    correctly serializing the actual extraction between them: confirmed
    directly, two identical toasts on screen at once. Claiming here,
    separately from the lock that guards the real work, means whichever
    caller asks first shows the toast and every other concurrent caller
    just stays quiet — their resolve still correctly waits on the shared
    extraction, it just doesn't announce it a second time.

    Also returns False when a fresh cached cookiefile already exists —
    _detect_stream_cookies() is about to reuse it instead of extracting,
    so there's nothing slow to warn about even though this is the first
    call this process has made.
    """
    global _cookie_extraction_notify_claimed
    with _stream_cookies_lock:
        if _stream_browser_probed or _cookie_extraction_notify_claimed:
            return False
        if _fresh_cached_cookiefile() is not None:
            return False
        _cookie_extraction_notify_claimed = True
        return True


def _detect_stream_cookies() -> tuple[str | None, str | None]:
    """Find a browser with YouTube cookies and persist them to a file once.

    Delegates to AuthManager's existing browser detection (used by `ytm
    setup`) so stream resolution and ytmusicapi search agree on which
    browser represents this user, instead of maintaining a second
    independent cookie-detection path.

    Returns (browser_name, cookiefile_path). Memoized at module scope —
    but memoizing just the browser NAME (as an earlier version of this
    function did) turned out to be an incomplete fix: yt-dlp's own
    cookiesfrombrowser option re-decrypts the ENTIRE browser cookie store
    from scratch on every fresh YoutubeDL instance, and _build_ydl_opts()
    builds a fresh instance on every _reset_ydl() — including "reset
    after 5 consecutive failures", a quality change, or accepting the
    remote_components prompt (which calls clear_cache() to apply the new
    setting). Confirmed in practice: Vivaldi's ~3500-cookie store got
    fully re-decrypted (~7-13s) a second time moments after the first,
    triggered by exactly that clear_cache() call. Extracting once here
    and writing a plain cookiefile means every later YoutubeDL instance
    just reads a small text file instead of re-hitting the OS keychain.
    """
    global _stream_browser_probed, _cached_stream_browser, _cached_stream_cookiefile
    with _stream_cookies_lock:
        if _stream_browser_probed:
            return _cached_stream_browser, _cached_stream_cookiefile

        # _stream_browser_probed only tracks this process's own lifetime,
        # so without this check every fresh launch re-extracted from
        # scratch regardless of a valid file already sitting on disk from
        # the previous launch, seconds or minutes earlier — confirmed
        # directly: with pycryptodomex installed, extraction dropped from
        # ~26s to ~2.4s, but the "setting up playback" toast (and the
        # ~2.4s itself) still fired on every single relaunch.
        cached = _fresh_cached_cookiefile()
        if cached is not None:
            logger.debug("Reusing cached stream cookiefile: %s", cached)
            _cached_stream_cookiefile = cached
            _stream_browser_probed = True
            return _cached_stream_browser, _cached_stream_cookiefile

        from ytm_player.services.auth import AuthManager

        try:
            _cached_stream_browser = AuthManager._detect_browser()
        except Exception:
            logger.debug("Browser cookie detection failed for stream resolution", exc_info=True)
            _cached_stream_browser = None

        if _cached_stream_browser is not None:
            _cached_stream_cookiefile = _extract_and_cache_cookiefile(_cached_stream_browser)

        _stream_browser_probed = True
        return _cached_stream_browser, _cached_stream_cookiefile


def _extract_and_cache_cookiefile(browser: str) -> str | None:
    """Extract *browser*'s cookies once and persist them to a local file.

    Returns the path on success, or None on any failure — callers fall
    back to cookiesfrombrowser in that case (slower, but still correct).
    """
    try:
        from yt_dlp.cookies import extract_cookies_from_browser

        from ytm_player.config.paths import CONFIG_DIR, SECURE_FILE_MODE, secure_chmod

        # Without an explicit logger, this defaults to yt-dlp's own
        # YDLLogger, which prints straight to stdout/stderr instead of
        # going through ytm.log — meaning the "Extracting cookies from X"
        # / "Extracted N cookies" lines that would have shown two
        # concurrent extractions fighting over the Keychain were silently
        # invisible. _YtDlpLogger routes them into the normal log instead.
        jar = extract_cookies_from_browser(browser, logger=_YtDlpLogger())  # type: ignore[arg-type]
        path = CONFIG_DIR / "stream_cookies_cache.txt"
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        jar.save(str(path), ignore_discard=True, ignore_expires=True)
        secure_chmod(path, SECURE_FILE_MODE)
        return str(path)
    except Exception:
        logger.debug("Failed to persist browser cookies to a local file", exc_info=True)
        return None


def looks_like_js_solver_ready() -> bool:
    """Fast, local-only guess at whether yt-dlp can already solve JS
    challenges — no cookies, no network, no yt-dlp resolve call at all.

    Whether a JS challenge solver is available is a property of yt-dlp's
    local environment (config + on-disk cache), not of which YouTube
    client or cookies end up being used for a given resolve — so this
    can answer the "will remote_components be needed" question entirely
    independently of (and faster than) the cookie-decryption-gated
    warmup in _detect_stream_cookies.

    True (skip the prompt) when either:
    - remote_components is already configured — yt-dlp will fetch/refresh
      the solver itself; nothing to check locally.
    - yt-dlp's own on-disk challenge-solver cache already has something
      in it, from a prior session that already downloaded one.

    False means "probably needs remote_components" — not a certainty (a
    cached solver could be for a stale player version and still need a
    fresh fetch), which is why the resolve-based detection in
    _YtDlpLogger still runs as ground truth. This only short-circuits the
    common, obvious case so the prompt doesn't wait behind an unrelated
    ~10-20s cookie decrypt just to tell you something answerable locally.
    """
    from ytm_player.services.yt_dlp_options import normalize_remote_components

    settings = get_settings().yt_dlp
    if normalize_remote_components(settings.remote_components):
        return True

    try:
        # Mirrors yt_dlp.cache.Cache._get_root_dir()'s default resolution.
        # ytm_player never sets a custom 'cachedir' yt-dlp option, so this
        # matches what yt-dlp itself would compute — reimplemented instead
        # of instantiating yt_dlp.cache.Cache to avoid depending on that
        # internal class's constructor (it wants a full ydl-like object).
        cache_root = Path(os.getenv("XDG_CACHE_HOME", "~/.cache")).expanduser()
        solver_dir = cache_root / "yt-dlp" / "challenge-solver"
        return solver_dir.is_dir() and any(solver_dir.iterdir())
    except Exception:
        logger.debug("Could not check yt-dlp challenge-solver cache", exc_info=True)
        return False


class _YtDlpLogger:
    """Routes yt-dlp's own diagnostic messages into our logger.

    With quiet=True/no_warnings=True and no logger, yt-dlp discards its own
    warnings and errors entirely (report_warning/to_stderr only check
    params['logger'] before checking no_warnings/quiet) — so failures like
    "Signature solving failed" or "Skipping client ... since it does not
    support cookies" never reached ytm.log. Mirrors the log_handler pattern
    already used for mpv in player.py.
    """

    def __init__(self, on_missing_remote_components: Callable[[], None] | None = None) -> None:
        self._on_missing_remote_components = on_missing_remote_components

    def debug(self, message: str) -> None:
        # yt-dlp routes both normal progress lines ("Downloading webpage")
        # and its own "[debug] ..."-prefixed messages through here.
        logger.debug("yt-dlp: %s", message)

    def info(self, message: str) -> None:
        # yt_dlp.cookies' browser extraction (extract_cookies_from_browser,
        # called directly by _extract_and_cache_cookiefile above, not via
        # YoutubeDL) logs its "Extracting cookies from X" / "Extracted N
        # cookies" lines at info level, on a default logger that doesn't
        # implement to_screen(). Without this method, passing this class
        # as that logger silently dropped those lines entirely — the exact
        # step that turned out to be worth seeing when two resolves raced
        # to extract cookies concurrently.
        logger.info("yt-dlp: %s", message)

    def warning(self, message: str, **_kwargs: object) -> None:
        logger.warning("yt-dlp: %s", message)
        lowered = message.lower()
        # yt-dlp's own recommended-fix warning ("...You can enable these
        # downloads with --remote-components ejs:github") is the clearest
        # signal, but it's NOT re-emitted on every failure — confirmed via
        # a real run where video A got it and video B, resolved seconds
        # later in the same process, didn't, despite hitting the identical
        # missing-solver failure. Only "Signature/n challenge solving
        # failed" repeats every time, so treat that as an equally valid
        # trigger — it's yt-dlp's own wording, not something we're
        # inferring from a generic error.
        is_missing_remote_components = (
            "remote components" in lowered and "skipped" in lowered
        ) or ("challenge" in lowered and "solving failed" in lowered)
        if self._on_missing_remote_components is not None and is_missing_remote_components:
            self._on_missing_remote_components()

    def error(self, message: str) -> None:
        logger.error("yt-dlp: %s", message)


# Quality presets mapping to yt-dlp format strings.
QUALITY_FORMATS: dict[str, str] = {
    "high": "bestaudio/best",
    "medium": "bestaudio[abr<=128]/bestaudio/best",
    "low": "bestaudio[abr<=64]/bestaudio/best",
}


@dataclass(frozen=True, slots=True)
class StreamInfo:
    """Resolved stream information for a YouTube Music track."""

    url: str
    video_id: str
    format: str  # e.g., "opus", "m4a"
    bitrate: int  # kbps
    duration: int  # seconds
    expires_at: float  # unix timestamp
    thumbnail_url: str | None = None


class StreamResolver:
    """Resolves YouTube Music video IDs to direct audio stream URLs.

    Uses the yt-dlp Python API to extract stream information without
    downloading. Caches results in memory with automatic expiry.
    """

    def __init__(self, quality: str = "high") -> None:
        self._quality = quality
        self._cache: dict[str, StreamInfo] = {}
        self._cache_lock = threading.Lock()
        self._pending: dict[str, asyncio.Future[StreamInfo | None]] = {}
        # yt_dlp.YoutubeDL — typed Any because yt-dlp ships no stubs.
        self._ydl: Any | None = None
        self._ydl_lock = threading.Lock()
        # Count of extract_info() calls currently running against self._ydl,
        # on background threads — guards _reset_ydl() against closing an
        # instance a resolve is still actively using. See _reset_ydl.
        self._active_resolves = 0
        # Bumped by _reset_ydl(). _get_ydl() snapshots this before its
        # (slow, unlocked) opts build and re-checks it after — if it
        # changed, a reset (e.g. accepting the remote_components prompt)
        # landed while the build was in flight, and the just-built opts
        # already have stale settings baked in (settings are read at the
        # START of _build_ydl_opts(), long before its slow cookie-cache
        # step even runs). Without this check that reset was silently
        # lost: nothing was cached yet for it to clear, so the stale
        # instance got cached anyway right after — confirmed in practice
        # as EVERY subsequent track failing identically, not just one,
        # until 5 consecutive failures forced an unrelated reset.
        self._ydl_generation = 0
        # Set by _YtDlpLogger when yt-dlp reports it skipped downloading its
        # JS challenge-solver script (remote_components unset). One-shot —
        # read via consume_missing_remote_components(), which resets it, so
        # callers only act on a *fresh* occurrence.
        self._missing_remote_components = False

    @property
    def quality(self) -> str:
        return self._quality

    @quality.setter
    def quality(self, value: str) -> None:
        if value not in QUALITY_FORMATS:
            raise ValueError(f"Unknown quality '{value}'. Choose from: {list(QUALITY_FORMATS)}")
        if value != self._quality:
            self._quality = value
            self._reset_ydl()

    def _build_ydl_opts(self) -> dict:
        """Build yt-dlp options for audio extraction."""
        settings = get_settings().yt_dlp
        opts = {
            "format": QUALITY_FORMATS.get(self._quality, QUALITY_FORMATS["high"]),
            "quiet": True,
            "no_warnings": True,
            "logger": _YtDlpLogger(self._flag_missing_remote_components),
            "extract_flat": False,
            "noplaylist": True,
            # Skip video-related processing.
            "skip_download": True,
            # Avoid writing any files to disk.
            "writeinfojson": False,
            "writethumbnail": False,
            # Android client provides a non-PoT fallback path (legacy format 18)
            # that unblocks madeForKids content which the default web client refuses.
            "extractor_args": {"youtube": {"player_client": ["default", "android"]}},
        }
        opts = apply_configured_yt_dlp_options(opts, settings)
        # Without cookies, yt-dlp treats itself as unauthenticated and its
        # default YouTube client set always includes android_vr — currently
        # subject to an intermittent PO-token-gated 403 on the CDN request
        # (upstream yt-dlp #16796/#17395). Authenticated default clients
        # (tv_downgraded/web_safari) avoid android_vr entirely. Reuse the
        # same browser-cookie detection `ytm setup` already relies on, so
        # streaming inherits auth the way search/browse already does.
        if "cookiefile" not in opts and "cookiesfrombrowser" not in opts:
            browser, cookiefile = _detect_stream_cookies()
            if cookiefile:
                opts["cookiefile"] = cookiefile
            elif browser:
                opts["cookiesfrombrowser"] = (browser, None, None, None)
        return opts

    def _flag_missing_remote_components(self) -> None:
        """Callback for _YtDlpLogger: record that a resolve needs remote_components."""
        self._missing_remote_components = True

    def consume_missing_remote_components(self) -> bool:
        """Return and clear whether the last resolve hit the missing-remote-components case.

        One-shot by design — a caller that acts on ``True`` (e.g. prompting
        the user) should see ``False`` on subsequent calls until it recurs.
        """
        was_missing = self._missing_remote_components
        self._missing_remote_components = False
        return was_missing

    @property
    def missing_remote_components(self) -> bool:
        """Non-destructive peek at the missing-remote-components flag.

        Used by _resolve_sync to short-circuit its own retry loop without
        stealing the signal consume_missing_remote_components() later
        delivers to the caller (e.g. the UI's one-time prompt).
        """
        return self._missing_remote_components

    def _get_ydl(self) -> Any:
        """Return a reusable YoutubeDL instance, creating it lazily.

        _build_ydl_opts() is called OUTSIDE self._ydl_lock on purpose: it
        can trigger _detect_stream_cookies()'s one-time browser cookie
        extraction, which is slow (Keychain-backed, 5-20s+ in practice).
        _reset_ydl() (called from the UI thread — e.g. accepting the
        remote_components prompt) needs the same lock; holding it for the
        whole opts-build meant accepting the prompt while a resolve was
        mid cookie-extraction blocked the UI thread on lock.acquire() for
        however long that extraction took. Confirmed directly: a ~13-15s
        UI stall lined up exactly with the cookie-extraction window in
        the log. Building opts unlocked and only locking for the actual
        (fast, in-memory) instance construction fixes that; the brief
        double-checked-locking race (two threads both seeing self._ydl is
        None and both building opts) is harmless — whichever assigns
        first wins, the loser's opts are simply discarded.

        The generation check below covers a second, worse race the above
        alone doesn't: _build_ydl_opts() reads settings (e.g.
        remote_components) in its first line, long before its slow
        cookie-cache step even starts. If a reset lands while self._ydl
        is still None (nothing built yet to reset), _reset_ydl() has
        nothing to do and the reset is silently lost — the in-flight
        build then finishes and caches an instance with the *pre-reset*
        settings anyway, permanently, since nothing else was going to
        clear it out again. Confirmed in practice: accepting the
        remote_components prompt did nothing, and EVERY subsequent track
        failed identically until 5 consecutive failures forced an
        unrelated reset. Looping on a generation mismatch instead means a
        reset that lands mid-build invalidates that build's result, and
        the retry re-reads current settings — cheap the second time,
        since _detect_stream_cookies() is separately memoized and won't
        redo the slow cookie extraction.
        """
        import yt_dlp  # Lazy import

        while True:
            with self._ydl_lock:
                if self._ydl is not None:
                    return self._ydl
                generation = self._ydl_generation

            # yt-dlp's _Params TypedDict is internal; the dict we build is
            # a plain options dict that yt-dlp accepts at runtime.
            opts = self._build_ydl_opts()

            with self._ydl_lock:
                if self._ydl is not None:
                    return self._ydl
                if self._ydl_generation != generation:
                    continue  # reset landed mid-build; opts are stale, retry
                self._ydl = yt_dlp.YoutubeDL(opts)  # type: ignore[arg-type]
                return self._ydl

    def _reset_ydl(self) -> None:
        """Discard the cached YoutubeDL instance, closing it only if safe.

        clear_cache() (and therefore this) can now be triggered by the UI
        thread — e.g. accepting the remote_components prompt — while a
        background resolve (the startup warmup, or another in-flight
        play/prefetch) is still mid extract_info() on the SAME instance.
        YoutubeDL.close() tears down the shared request director's
        connection pool; closing it out from under a live request left
        that request hanging until some underlying socket/read timeout
        eventually gave up (confirmed: a ~30s stall the instant "Enable"
        was clicked, gone once this stopped closing a live instance).
        Detaching the reference (self._ydl = None) is enough to make the
        *next* _get_ydl() build a fresh instance — the in-flight call
        keeps its own local reference to the old one and finishes
        normally; with no __del__ on YoutubeDL, skipping close() here
        just means that specific instance's connection pool is cleaned up
        by normal garbage collection instead of explicitly, once nothing
        (including the finishing resolve) references it anymore.

        Always bumps _ydl_generation, even when self._ydl is already None
        — that's exactly the case (nothing cached yet to reset) that used
        to lose the reset silently. See _get_ydl for the other half.
        """
        with self._ydl_lock:
            self._ydl_generation += 1
            if self._ydl is None:
                return
            if self._active_resolves > 0:
                self._ydl = None
                return
            try:
                self._ydl.close()  # type: ignore[union-attr]
            except Exception:
                pass
            self._ydl = None

    def _resolve_sync(self, video_id: str) -> StreamInfo | None:
        """Synchronous stream resolution (runs in a thread) with retry."""

        if not VALID_VIDEO_ID.match(video_id):
            logger.warning("Invalid video_id rejected: %r", video_id)
            return None
        url = f"https://music.youtube.com/watch?v={video_id}"
        delays = [0, 1.0, 2.0]  # initial attempt + 2 retries

        for attempt, delay in enumerate(delays):
            if delay > 0:
                time.sleep(delay)
            info = self._try_resolve(url, video_id, attempt)
            if info is not None:
                return info
            if self.missing_remote_components:
                # Deterministic, config-level failure (remote_components
                # unset) — every remaining attempt would hit yt-dlp's same
                # unmet prerequisite and fail identically. Retrying only
                # burns ~4s of network round-trips per attempt for nothing.
                logger.debug("Stopping retries for %s: missing remote_components", video_id)
                break
        return None

    def _try_resolve(self, url: str, video_id: str, attempt: int) -> StreamInfo | None:
        """Single resolution attempt."""
        import yt_dlp  # Lazy import: needed for exception types

        try:
            ydl = self._get_ydl()
            with self._ydl_lock:
                self._active_resolves += 1
            try:
                info = ydl.extract_info(url, download=False)
            finally:
                with self._ydl_lock:
                    self._active_resolves -= 1

            if info is None:
                logger.error("yt-dlp returned no info for video_id=%s", video_id)
                return None

            stream_url: str = info.get("url", "")
            if not stream_url:
                # Some formats nest the URL under requested_formats.
                formats = info.get("requested_formats") or []
                for fmt in formats:
                    if fmt.get("vcodec") == "none" or fmt.get("acodec") != "none":
                        stream_url = fmt.get("url", "")
                        break

            if not stream_url:
                logger.error("No stream URL found for video_id=%s", video_id)
                return None

            # Determine audio format and bitrate from the info dict.
            acodec = info.get("acodec", "unknown")
            audio_ext = info.get("audio_ext") or info.get("ext", "unknown")
            abr = int(info.get("abr") or info.get("tbr") or 0)
            duration = int(info.get("duration") or 0)
            thumbnail = info.get("thumbnail")

            # Pick a readable format name.
            fmt_name = acodec if acodec != "none" else audio_ext

            expires_at = time.time() + _CACHE_TTL_SECONDS

            return StreamInfo(
                url=stream_url,
                video_id=video_id,
                format=fmt_name,
                bitrate=abr,
                duration=duration,
                expires_at=expires_at,
                thumbnail_url=thumbnail,
            )

        except yt_dlp.utils.DownloadError as exc:  # type: ignore[attr-defined]
            logger.warning(
                "yt-dlp download error for video_id=%s (attempt %d): %s",
                video_id,
                attempt + 1,
                exc,
            )
            return None
        except Exception:
            logger.warning(
                "Unexpected error resolving stream for video_id=%s (attempt %d)",
                video_id,
                attempt + 1,
                exc_info=True,
            )
            return None

    def _get_cached(self, video_id: str) -> StreamInfo | None:
        """Return cached StreamInfo if it exists and isn't (near-)expired."""
        with self._cache_lock:
            cached = self._cache.get(video_id)
            if cached is None:
                return None
            if _is_stale(cached.expires_at):
                del self._cache[video_id]
                return None
            return cached

    def _put_cache(self, info: StreamInfo) -> None:
        """Store a StreamInfo in the cache, evicting stale/excess entries."""
        with self._cache_lock:
            self._cache[info.video_id] = info

            # Prune expired entries on every write to prevent unbounded growth.
            now = time.time()
            expired = [vid for vid, si in self._cache.items() if now >= si.expires_at]
            for vid in expired:
                del self._cache[vid]

            # If still over the cap, evict the oldest entries by expires_at.
            if len(self._cache) > _CACHE_MAX_SIZE:
                sorted_ids = sorted(self._cache, key=lambda vid: self._cache[vid].expires_at)
                excess = len(self._cache) - _CACHE_MAX_SIZE
                for vid in sorted_ids[:excess]:
                    del self._cache[vid]

    def resolve_sync(self, video_id: str) -> StreamInfo | None:
        """Resolve a video ID to a StreamInfo, using cache when possible.

        This is the synchronous version. Prefer `resolve()` in async code.
        """
        cached = self._get_cached(video_id)
        if cached is not None:
            logger.debug("Cache hit for video_id=%s", video_id)
            return cached

        logger.debug("Cache miss for video_id=%s, resolving via yt-dlp", video_id)
        info = self._resolve_sync(video_id)
        if info is not None:
            self._put_cache(info)
        return info

    def is_expired(self, video_id: str) -> bool:
        """Check if a cached stream URL has expired or will expire soon."""
        with self._cache_lock:
            cached = self._cache.get(video_id)
            if cached is None:
                return True
            return _is_stale(cached.expires_at)

    async def resolve(self, video_id: str) -> StreamInfo | None:
        """Resolve a video ID to a StreamInfo asynchronously.

        Runs the synchronous yt-dlp extraction in a thread to avoid
        blocking the event loop. Deduplicates concurrent requests for
        the same video_id.
        """
        # _get_cached already applies the expiry buffer, so a hit here is fresh
        # enough to hand to playback; near-expired entries were evicted and fall
        # through to a fresh resolve below.
        cached = self._get_cached(video_id)
        if cached is not None:
            logger.debug("Cache hit for video_id=%s", video_id)
            return cached

        # Deduplicate concurrent requests for the same video.  Shield the
        # shared future: cancelling a waiter task would otherwise propagate
        # into the bare future (Task.cancel cancels what it awaits) and break
        # the owner's set_result with InvalidStateError.
        if video_id in self._pending:
            return await asyncio.shield(self._pending[video_id])

        logger.debug("Cache miss for video_id=%s, resolving via yt-dlp", video_id)
        future: asyncio.Future[StreamInfo | None] = asyncio.get_running_loop().create_future()
        self._pending[video_id] = future
        try:
            info = await asyncio.to_thread(self._resolve_sync, video_id)
            if info is not None:
                self._put_cache(info)
            if not future.done():
                future.set_result(info)
            return info
        except Exception as exc:
            if not future.done():
                future.set_exception(exc)
            raise
        finally:
            self._pending.pop(video_id, None)
            if not future.done():
                # Owner was cancelled before the future resolved (a BaseException
                # such as CancelledError bypasses the except above).  Cancel the
                # shared future so concurrent waiters get CancelledError instead
                # of hanging forever on an orphaned, never-resolved future.
                future.cancel()

    async def prefetch(self, video_id: str) -> None:
        """Resolve a video ID in the background without blocking the caller.

        Used to pre-cache the next track's stream URL so playback starts
        instantly when the user hits next or the current track ends.
        """
        if self._get_cached(video_id) is not None:
            return  # Already cached, nothing to do.
        if video_id in self._pending:
            return  # Already being resolved.
        try:
            await self.resolve(video_id)
        except Exception:
            logger.debug("Prefetch failed for video_id=%s", video_id, exc_info=True)

    @staticmethod
    def warm_import() -> None:
        """Import yt_dlp eagerly to avoid the 200-400ms cold-start penalty."""
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            logger.warning("yt-dlp is not installed")

    def invalidate(self, video_id: str) -> None:
        """Remove a specific video ID from the cache."""
        with self._cache_lock:
            self._cache.pop(video_id, None)

    def clear_cache(self) -> None:
        """Remove all entries from the cache and reset the yt-dlp instance."""
        with self._cache_lock:
            self._cache.clear()
        self._reset_ydl()

    def prune_expired(self) -> int:
        """Remove expired entries from the cache. Returns number removed."""
        with self._cache_lock:
            now = time.time()
            expired = [vid for vid, info in self._cache.items() if now >= info.expires_at]
            for vid in expired:
                del self._cache[vid]
            return len(expired)
