"""Stream URL resolution using yt-dlp."""

from __future__ import annotations

import asyncio
import logging
import os
import re
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


def _detect_stream_cookies() -> str | None:
    """Return the AuthManager-written stream cookiejar path if present, else None.

    AuthManager (services/auth.py) is the only component that ever decrypts
    browser cookies — during `ytm setup` or a validate()-triggered
    try_auto_refresh(). This function never triggers extraction itself; if
    the file doesn't exist (e.g. extraction failed, or extraction hasn't
    happened yet), resolution proceeds without cookies rather than
    decrypting independently.
    """
    from ytm_player.config.paths import STREAM_COOKIES_FILE

    return str(STREAM_COOKIES_FILE) if STREAM_COOKIES_FILE.exists() else None


# Shared wording for the remote_components ("JS challenge solver") prompt
# shown by _show_remote_components_prompt() (app/_playback.py). Both
# app/_session.py (startup warmup) and app/_playback.py (mid-playback
# failure) build a message from this same body text — the only difference
# between call sites is the lead-in clause naming what needs it ("Playback"
# vs. the specific track title) — so only that clause is left to each
# caller as an f-string prefix, and a future wording/URL change only has
# one place to land instead of three.
REMOTE_COMPONENTS_PROMPT_BODY = (
    "needs yt-dlp's JS challenge solver to decode YouTube's "
    "stream signatures. Download and run it from GitHub "
    "(yt-dlp/ejs, sandboxed via Deno)?"
)


def looks_like_js_solver_ready() -> bool:
    """Fast, local-only guess at whether yt-dlp can already solve JS
    challenges — no cookies, no network, no yt-dlp resolve call at all.

    Whether a JS challenge solver is available is a property of yt-dlp's
    local environment (config + on-disk cache), not of which YouTube
    client or cookies end up being used for a given resolve — so this
    can answer the "will remote_components be needed" question
    independently of _detect_stream_cookies()'s own (now fast) file check.

    True (skip the prompt) when either:
    - remote_components is already configured — yt-dlp will fetch/refresh
      the solver itself; nothing to check locally.
    - yt-dlp's own on-disk challenge-solver cache already has something
      in it, from a prior session that already downloaded one.

    False means "probably needs remote_components" — not a certainty (a
    cached solver could be for a stale player version and still need a
    fresh fetch), which is why the resolve-based detection in
    _YtDlpLogger still runs as ground truth. This only short-circuits the
    common, obvious case so the prompt doesn't wait behind an actual
    yt-dlp resolve just to tell you something answerable locally.
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
        # A default logger doesn't implement to_screen(), so without this
        # method, yt-dlp's own info-level messages during extract_info()
        # would be silently dropped rather than surfacing in ytm.log.
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
        #
        # This couples us to yt-dlp's exact log wording, with no structured
        # alternative (exception type/error code) available upstream. Source
        # of the two strings in yt-dlp's EJS system (introduced 2025.11.12):
        #   - "remote components ... skipped":
        #     yt_dlp/extractor/youtube/jsc/_director.py (_director.py's
        #     "Remote components {...} were skipped" message)
        #   - "challenge ... solving failed":
        #     yt_dlp/extractor/youtube/_video.py ("n challenge solving
        #     failed: Some formats may be missing." message)
        # yt-dlp's own boosty.py extractor test hardcodes both strings in its
        # expected_warnings list, so an upstream wording change would break
        # yt-dlp's own test suite too — a real, if informal, stability
        # signal. If this stops firing after a yt-dlp upgrade, check those
        # two files first for wording drift.
        is_missing_remote_components = (
            "remote components" in lowered and "skipped" in lowered
        ) or ("challenge" in lowered and "solving failed" in lowered)
        if self._on_missing_remote_components is not None and is_missing_remote_components:
            self._on_missing_remote_components()

    def error(self, message: str) -> None:
        logger.error("yt-dlp: %s", message)


# tv_simply/tv_downgraded never exposes more than one non-storyboard
# COMBINED format per video (confirmed empirically: itag 18, a progressive
# MP4 with AAC mp4a.40.2 audio — no HLS ladder). For a request without
# cookies, that's the only option, so `bestaudio` never matches anything
# and the selector falls through to it.
#
# For an AUTHENTICATED request (real session cookies present), yt-dlp
# automatically appends `web_music` to the client list for any
# music.youtube.com URL (its own client-selection logic, not something we
# configure) — this exposes genuine audio-only DASH formats up to ~280kbps
# opus (itag 774), a real quality improvement over itag 18's ~128kbps AAC
# muxed with a never-rendered video track (player.py initializes mpv with
# video=False). `bestaudio` was previously excluded here entirely — a
# prior finding (see CHANGELOG.md) held it "PO-Token-gated regardless of
# client or cookies", based on testing against web_safari/web_embedded/
# default/android, none of which is `web_music`. Validated here
# specifically for this codepath (tv_simply/tv_downgraded + auto-appended
# web_music) against 18 real, distinct tracks from actual play history
# under a YouTube Music PREMIUM account, probing each resolved URL with
# two HTTP Range requests (immediately and 2MB into the file, to catch a
# delayed-failure pattern too, not just an immediate one): 18/18
# succeeded. Premium accounts are exempt from web_music's PO-Token
# requirement (yt-dlp's own not_required_for_premium policy flag) —
# web_music's policy is otherwise identical to web_safari/web_embedded's
# (the pair proven erratic above), so this validation does NOT cover
# non-Premium authenticated accounts, which may see the same intermittent
# GVS-403-after-resolution failures. See CHANGELOG.md for full detail.
_FORMAT = "bestaudio/best[vcodec!=none][acodec!=none]"


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

    def __init__(self) -> None:
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
        # unlocked opts build and re-checks it after — if it changed, a
        # reset (e.g. accepting the remote_components prompt) landed
        # while the build was in flight, and the just-built opts already
        # have stale settings baked in (settings are read at the START of
        # _build_ydl_opts(), before _detect_stream_cookies()'s fast
        # Path.exists() check even runs). Without this check that reset
        # was silently lost: nothing was cached yet for it to clear, so
        # the stale instance got cached anyway right after — confirmed in
        # practice as EVERY subsequent track failing identically, not
        # just one, until 5 consecutive failures forced an unrelated
        # reset.
        self._ydl_generation = 0
        # Set by _YtDlpLogger when yt-dlp reports it skipped downloading its
        # JS challenge-solver script (remote_components unset). Keyed by
        # video_id rather than a single shared flag: the cached YoutubeDL
        # instance (and therefore its logger callback) is reused across
        # every resolve, and two DIFFERENT videos can be resolving
        # concurrently (e.g. the startup warmup and a direct play action
        # on another track) — a single boolean can't tell them apart, so
        # whichever caller drains it first gets the accurate diagnosis and
        # the other silently falls through to a generic failure message
        # even though its own resolve hit the identical cause. One-shot
        # per video_id — read via consume_missing_remote_components(video_id),
        # which discards that entry, so callers only act on a *fresh*
        # occurrence for their own video.
        self._missing_remote_components_lock = threading.Lock()
        self._missing_remote_components_video_ids: set[str] = set()
        # Set on the CURRENT thread by _try_resolve() right before calling
        # extract_info(), so _flag_missing_remote_components() — invoked
        # synchronously by yt-dlp's logger from inside that same call, on
        # that same thread — knows which video_id to attribute the
        # failure to. Needed because the shared logger callback carries no
        # video context of its own.
        self._resolving_video_id = threading.local()

    def _build_ydl_opts(self) -> dict:
        """Build yt-dlp options for audio extraction."""
        settings = get_settings().yt_dlp
        opts = {
            "format": _FORMAT,
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
            # web_safari/web_embedded (yt-dlp's cookie-compatible web clients)
            # proved erratic on real playback despite resolving formats fine —
            # GVS rejects HTTP Range requests against their itag-18 URLs with
            # an immediate 403 (confirmed via direct Range-request testing
            # against a 25-track sample of real play history: 1/25 succeeded).
            # tv_downgraded alone fails extraction outright ("The page needs
            # to be reloaded") when unauthenticated; it must be paired with
            # tv_simply, which contributes no formats of its own but is
            # required for extraction to succeed. tv_simply+tv_downgraded
            # together resolved and played back successfully on 25/25 of the
            # same sample. See CHANGELOG.md for the full investigation
            # (independently validates maintainer @Villoh's PR #136 review
            # finding and PR #137's fix).
            #
            # tv_simply has SUPPORTS_COOKIES=False (yt-dlp's own
            # INNERTUBE_CLIENTS metadata) — for an AUTHENTICATED request
            # (real session cookies present), yt-dlp's own client-selection
            # logic silently drops it ("Skipping client tv_simply since it
            # does not support cookies"), leaving tv_downgraded alone. This
            # does NOT reproduce the unauthenticated tv_downgraded-alone
            # failure: authenticated resolution succeeds regardless, because
            # yt-dlp additionally auto-appends `web_music` for any
            # authenticated music.youtube.com request (its own logic, not
            # configured here), which supplies working formats on its own.
            # Confirmed against a real authenticated session (not a
            # fabricated one — a fabricated session can't be used to test
            # this because the actual player-response/format requests
            # would be rejected by Google using invalid credentials, not
            # because the local client-selection filter is skipped).
            "extractor_args": {"youtube": {"player_client": ["tv_simply", "tv_downgraded"]}},
        }
        opts = apply_configured_yt_dlp_options(opts, settings)
        if "cookiefile" not in opts:
            cookiefile = _detect_stream_cookies()
            if cookiefile:
                opts["cookiefile"] = cookiefile
        return opts

    def _flag_missing_remote_components(self) -> None:
        """Callback for _YtDlpLogger: record that the resolve currently
        running on this thread needs remote_components.

        Reads the video_id _try_resolve() stashed on this thread just
        before calling extract_info() — see _resolving_video_id. If that's
        unset for some reason, there's nothing safe to attribute the flag
        to, so it's dropped rather than guessed.
        """
        video_id = getattr(self._resolving_video_id, "value", None)
        if video_id is None:
            return
        with self._missing_remote_components_lock:
            self._missing_remote_components_video_ids.add(video_id)

    def consume_missing_remote_components(self, video_id: str) -> bool:
        """Return and clear whether *video_id*'s last resolve hit the
        missing-remote-components case.

        One-shot per video_id by design — a caller that acts on ``True``
        (e.g. prompting the user) should see ``False`` on subsequent calls
        for that video until it recurs.
        """
        with self._missing_remote_components_lock:
            if video_id in self._missing_remote_components_video_ids:
                self._missing_remote_components_video_ids.discard(video_id)
                return True
            return False

    def _peek_missing_remote_components(self, video_id: str) -> bool:
        """Non-destructive check of whether *video_id* hit the
        missing-remote-components case.

        Used by _resolve_sync to short-circuit its own retry loop without
        stealing the signal consume_missing_remote_components(video_id) later
        delivers to the caller (e.g. the UI's one-time prompt).
        """
        with self._missing_remote_components_lock:
            return video_id in self._missing_remote_components_video_ids

    def _get_ydl(self) -> Any:
        """Return a reusable YoutubeDL instance, creating it lazily, with
        _active_resolves already incremented on the caller's behalf.

        Incrementing _active_resolves here — still inside self._ydl_lock,
        at every return point — instead of via a separate, later lock
        acquisition in _try_resolve() closes a TOCTOU gap: previously the
        lock was released between obtaining the reference and bumping the
        counter, during which _reset_ydl() could observe
        _active_resolves == 0 for an instance a caller already held and
        was about to call extract_info() on, and close it out from under
        that in-flight call — reintroducing the exact "closing a live
        instance mid-resolve" bug this counter exists to prevent. Callers
        must still decrement it in a finally block (see _try_resolve).

        _build_ydl_opts() is called OUTSIDE self._ydl_lock: even though
        _detect_stream_cookies() is now a fast Path.exists() check rather
        than a slow browser cookie extraction (that extraction now lives
        solely in AuthManager, run once at `ytm setup`/try_auto_refresh
        time — see services/auth.py), there's still no reason to hold a
        lock whose only job is guarding the (fast, in-memory) YoutubeDL()
        construction for any longer than that. _reset_ydl() (called from
        the UI thread — e.g. accepting the remote_components prompt)
        needs the same lock; the brief double-checked-locking race (two
        threads both seeing self._ydl is None and both building opts) is
        harmless — whichever assigns first wins, the loser's opts are
        simply discarded.

        The generation check below covers a second, worse race the above
        alone doesn't: _build_ydl_opts() reads settings (e.g.
        remote_components) in its first line, before it even calls
        _detect_stream_cookies()'s file check. If a reset lands while
        self._ydl is still None (nothing built yet to reset), _reset_ydl()
        has nothing to do and the reset is silently lost — the in-flight
        build then finishes and caches an instance with the *pre-reset*
        settings anyway, permanently, since nothing else was going to
        clear it out again. Confirmed in practice: accepting the
        remote_components prompt did nothing, and EVERY subsequent track
        failed identically until 5 consecutive failures forced an
        unrelated reset. Looping on a generation mismatch instead means a
        reset that lands mid-build invalidates that build's result, and
        the retry re-reads current settings — cheap on retry regardless,
        since _detect_stream_cookies() is now a fast file check rather
        than something that would need to redo slow work.
        """
        import yt_dlp  # Lazy import

        while True:
            with self._ydl_lock:
                if self._ydl is not None:
                    self._active_resolves += 1
                    return self._ydl
                generation = self._ydl_generation

            # yt-dlp's _Params TypedDict is internal; the dict we build is
            # a plain options dict that yt-dlp accepts at runtime.
            opts = self._build_ydl_opts()

            with self._ydl_lock:
                if self._ydl is not None:
                    self._active_resolves += 1
                    return self._ydl
                if self._ydl_generation != generation:
                    continue  # reset landed mid-build; opts are stale, retry
                self._ydl = yt_dlp.YoutubeDL(opts)  # type: ignore[arg-type]
                self._active_resolves += 1
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
            if self._peek_missing_remote_components(video_id):
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
            # Set before _get_ydl() rather than after: nothing should sit
            # between the increment _get_ydl() does on our behalf and the
            # try/finally that guarantees its matching decrement below —
            # if this attribute-set were ever to raise, doing it first
            # means _get_ydl() (and its increment) never even runs.
            self._resolving_video_id.value = video_id
            ydl = self._get_ydl()  # increments _active_resolves; see _get_ydl's docstring
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
            hint = ""
            if _detect_stream_cookies() and re.search(
                r"sign in|login_required|confirm you", str(exc), re.I
            ):
                hint = " (stream cookiejar may be stale/revoked — try `ytm setup` to refresh it)"
            logger.warning(
                "yt-dlp download error for video_id=%s (attempt %d): %s%s",
                video_id,
                attempt + 1,
                exc,
                hint,
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
        # A prefetched-then-skipped-without-playing video's flag is never
        # consumed (only play_track()/the resolver-warmup path consume by
        # video_id), so entries can otherwise strand here indefinitely.
        # clear_cache() already fires exactly when that staleness stops
        # mattering — accepting the remote_components prompt, or the
        # failure-recovery reset after repeated consecutive play failures,
        # both mean any pending diagnosis is moot; the next resolve
        # re-flags it fresh if the cause recurs.
        with self._missing_remote_components_lock:
            self._missing_remote_components_video_ids.clear()

    def prune_expired(self) -> int:
        """Remove expired entries from the cache. Returns number removed."""
        with self._cache_lock:
            now = time.time()
            expired = [vid for vid, info in self._cache.items() if now >= info.expires_at]
            for vid in expired:
                del self._cache[vid]
            return len(expired)
