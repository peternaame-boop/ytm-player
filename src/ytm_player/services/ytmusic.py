"""Async wrapper around ytmusicapi providing all YouTube Music functionality."""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from pathlib import Path
from typing import Any, Literal

import requests.exceptions
from ytmusicapi import YTMusic
from ytmusicapi.exceptions import YTMusicServerError, YTMusicUserError

from ytm_player.config.paths import AUTH_FILE
from ytm_player.config.settings import get_settings
from ytm_player.services.auth import AuthManager

logger = logging.getLogger(__name__)

# After this many consecutive API failures, re-create the YTMusic client
# in case the session has gone stale (expired cookies, broken connection).
_MAX_API_FAILURES_BEFORE_REINIT = 3

# Exception types that count as "expected API/network failure" — these increment
# the failure counter and may trigger a client reinit. Programming-error
# exceptions (TypeError, AttributeError, etc.) propagate without bumping the
# counter so bugs surface in development instead of silently triggering reinit.
#
# Note: KeyError is here because ytmusicapi's parsers raise KeyError on
# renderer-key mismatches whenever YouTube tweaks its response shape — that's
# an upstream API drift, not our bug. Without this entry the KeyError
# propagates out of _call as an "unexpected exception" and may surface as a
# hard crash in worker contexts.
_EXPECTED_API_EXCEPTIONS = (
    requests.exceptions.RequestException,  # covers ConnectionError, Timeout, HTTPError
    asyncio.TimeoutError,  # from wait_for
    KeyError,  # ytmusicapi parser drift on YouTube response shape changes
)

# Same as ``_EXPECTED_API_EXCEPTIONS`` plus the typed errors raised by
# ytmusicapi itself. Used by mutation methods (rate_song, add_playlist_items,
# remove_playlist_items) which classify the failure cause for per-cause
# UI toasts (see Task 4.11).
_EXPECTED_MUTATION_EXCEPTIONS = (
    *_EXPECTED_API_EXCEPTIONS,
    YTMusicServerError,
    YTMusicUserError,
)

# Result type for mutation methods (rate_song, add_playlist_items,
# remove_playlist_items). Replaces the bool contract introduced in Task 4.3
# so callers can show a per-cause toast instead of a generic failure message.
#
# - "success":       server accepted the mutation
# - "auth_required": user has no auth set up at all (run `ytm setup`)
# - "auth_expired":  HTTP 401/403 from the server (cookies/session stale)
# - "network":       requests.RequestException or asyncio.TimeoutError
# - "server_error":  any other YTMusicServerError (4xx/5xx other than auth)
MutationResult = Literal[
    "success",
    "auth_required",
    "auth_expired",
    "network",
    "server_error",
]

# ytmusicapi formats _send_request errors as:
#     "Server returned HTTP <code>: <reason>.\n<body-error>"
# This regex captures the status code from the start of the message. If
# upstream changes the format, we fall through to "server_error", which is
# a sensible default.
_HTTP_STATUS_RE = re.compile(r"^Server returned HTTP (\d{3})\b")


def _classify_mutation_failure(exc: BaseException) -> MutationResult:
    """Map an exception raised by a mutation call to a MutationResult.

    Never returns "success".

    The auth-vs-server-error distinction is made by parsing the HTTP status
    out of YTMusicServerError's message string — ytmusicapi does not expose
    a typed AuthenticationError subclass, so this is the only way short of
    monkey-patching ``_send_request`` to surface the status code. The format
    is stable in ytmusicapi 1.x but explicitly fall through to
    "server_error" if the regex doesn't match.
    """
    if isinstance(exc, _EXPECTED_API_EXCEPTIONS):
        return "network"
    if isinstance(exc, YTMusicUserError):
        # _check_auth() raises this when auth_type is UNAUTHORIZED — i.e.
        # the user never set up auth at all. Other YTMusicUserError raises
        # are programming errors (invalid args) and should NOT reach here
        # because the mutation methods don't catch them; but if one does
        # slip through, "auth_required" is a reasonable fallback.
        return "auth_required"
    if isinstance(exc, YTMusicServerError):
        match = _HTTP_STATUS_RE.match(str(exc))
        if match:
            status = int(match.group(1))
            if status in (401, 403):
                return "auth_expired"
        return "server_error"
    # Shouldn't be reached — caller filters to known types — but be safe.
    return "server_error"


# Suffix templates for the failure kinds. Cascade sites combine these with
# their own action-specific prefix (e.g. "Couldn't like — <suffix>").
_MUTATION_TOAST_SUFFIX: dict[MutationResult, str] = {
    "success": "",
    "auth_required": "sign in first (run `ytm setup`)",
    "auth_expired": "session expired, run `ytm setup` to sign in again",
    "network": "check your connection",
    "server_error": "YouTube Music had a problem, try again",
}


def mutation_failure_suffix(kind: MutationResult) -> str:
    """Return the user-facing suffix text for a non-success MutationResult.

    Empty string for ``"success"``. Cascade sites compose this with their
    own action prefix and a sensible separator.
    """
    return _MUTATION_TOAST_SUFFIX.get(kind, "")


class YTMusicService:
    """Async wrapper around ytmusicapi.YTMusic.

    All public methods are async and delegate to ytmusicapi's synchronous API
    through ``asyncio.to_thread`` so they never block the event loop.
    """

    def __init__(
        self,
        auth_path: Path = AUTH_FILE,
        auth_manager: AuthManager | None = None,
        user: str | None = None,
    ) -> None:
        self._auth_path = auth_path
        self._auth_manager = auth_manager
        self._user = user or None  # normalise "" → None
        self._ytm: YTMusic | None = None
        self._consecutive_api_failures: int = 0
        # Guards lazy init of self._ytm against concurrent first-access from
        # asyncio.to_thread workers.
        self._client_init_lock = threading.Lock()
        # Serializes get_playlist(order=...) monkey-patches so concurrent
        # calls don't stack patches on client._send_request.
        self._order_lock = asyncio.Lock()
        self._last_discovery_source: int = 0
        self._last_chart_shelf: int = 0

    @property
    def client(self) -> YTMusic:
        """Lazily initialise and return the underlying YTMusic client.

        Thread-safe under concurrent first-access via asyncio.to_thread.
        """
        if self._ytm is None:
            with self._client_init_lock:
                # Double-check: another thread may have initialised it
                # between our None check and acquiring the lock.
                if self._ytm is None:
                    if self._auth_manager is not None:
                        self._ytm = self._auth_manager.create_ytmusic_client(user=self._user)
                    else:
                        self._ytm = YTMusic(str(self._auth_path), user=self._user)
        return self._ytm

    async def _call(self, func: Any, *args: Any, timeout: int | None = None, **kwargs: Any) -> Any:
        """Run a sync ytmusicapi method in a thread with timeout."""
        effective_timeout = timeout if timeout is not None else get_settings().playback.api_timeout
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(func, *args, **kwargs),
                timeout=effective_timeout,
            )
            self._consecutive_api_failures = 0
            return result
        except _EXPECTED_API_EXCEPTIONS:
            logger.exception(
                "ytmusicapi call failed (func=%s, consecutive_failures=%d)",
                getattr(func, "__name__", str(func)),
                self._consecutive_api_failures + 1,
            )
            self._consecutive_api_failures += 1
            if self._consecutive_api_failures >= _MAX_API_FAILURES_BEFORE_REINIT:
                logger.warning(
                    "Re-initializing YTMusic client after %d consecutive API failures",
                    self._consecutive_api_failures,
                )
                # Hold the lock while clearing _ytm so a concurrent .client
                # access doesn't race with the reinit signal.
                with self._client_init_lock:
                    self._ytm = None
                self._consecutive_api_failures = 0
            raise

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        filter: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search YouTube Music.

        Args:
            query: Search query string.
            filter: One of ``"songs"``, ``"videos"``, ``"albums"``,
                ``"artists"``, ``"playlists"``, or ``None`` for all.
            limit: Maximum number of results.

        Returns:
            List of result dicts.
        """
        try:
            return await self._call(self.client.search, query, filter=filter, limit=limit)
        except asyncio.TimeoutError:
            logger.warning("Search timed out for query=%r", query)
            return []
        except Exception:
            logger.exception("Search failed for query=%r filter=%r", query, filter)
            return []

    async def get_search_suggestions(self, query: str) -> list[str]:
        """Return autocomplete suggestions for *query*."""
        try:
            return await self._call(self.client.get_search_suggestions, query)
        except Exception:
            logger.exception("get_search_suggestions failed for query=%r", query)
            return []

    # ------------------------------------------------------------------
    # Library
    # ------------------------------------------------------------------

    async def get_library_playlists(self, limit: int = 25) -> list[dict[str, Any]]:
        """Return the user's library playlists."""
        try:
            return await self._call(self.client.get_library_playlists, limit=limit)
        except Exception:
            logger.exception("get_library_playlists failed")
            return []

    async def get_library_albums(self, limit: int = 25) -> list[dict[str, Any]]:
        """Return the user's saved albums."""
        try:
            return await self._call(self.client.get_library_albums, limit=limit)
        except Exception:
            logger.exception("get_library_albums failed")
            return []

    async def get_library_artists(self, limit: int = 25) -> list[dict[str, Any]]:
        """Return the user's subscribed/followed artists."""
        try:
            return await self._call(self.client.get_library_subscriptions, limit=limit)
        except Exception:
            logger.exception("get_library_artists failed")
            return []

    async def get_liked_songs(
        self, limit: int | None = None, timeout: int | None = None
    ) -> list[dict[str, Any]]:
        """Return tracks from the user's Liked Music playlist."""
        try:
            playlist = await self._call(self.client.get_liked_songs, timeout=timeout, limit=limit)
            return playlist.get("tracks", []) if isinstance(playlist, dict) else []
        except Exception:
            logger.exception("get_liked_songs failed")
            return []

    # ------------------------------------------------------------------
    # Browsing
    # ------------------------------------------------------------------

    async def get_home(self, limit: int = 3) -> list[dict[str, Any]]:
        """Return personalised home page recommendations."""
        try:
            return await self._call(self.client.get_home, limit=limit)
        except Exception:
            logger.exception("get_home failed")
            return []

    async def get_charts(self, country: str = "ZZ") -> dict[str, Any]:
        """Return chart data for *country* (ISO 3166-1 alpha-2, e.g. ``"GB"``).

        ``"ZZ"`` is YouTube's catch-all "no specific region" code which
        returns no chart data. The default of ``"GB"`` matches the
        ``settings.ui.region`` default; production code passes the
        configured region explicitly. Locale-style codes (``ES-ES``,
        ``en-GB``) are normalised to bare two-letter codes — YouTube's
        endpoint silently falls back to Global on locale-shaped input.
        """
        from ytm_player.services.regions import normalise_region

        country = normalise_region(country)
        try:
            return await self._call(self.client.get_charts, country=country)
        except Exception:
            logger.exception("get_charts failed for country=%r", country)
            return {}

    async def get_new_releases(self) -> list[dict[str, Any]]:
        """Return new album releases.

        ytmusicapi has no dedicated ``get_new_releases`` endpoint; the
        explore page bundles the data under ``new_releases``.
        """
        try:
            result = await self._call(self.client.get_explore)
            if isinstance(result, dict):
                releases = result.get("new_releases", [])
                if isinstance(releases, list):
                    return releases
            return []
        except Exception:
            logger.exception("get_new_releases failed")
            return []

    # ------------------------------------------------------------------
    # Content details
    # ------------------------------------------------------------------

    async def get_album(self, album_id: str) -> dict[str, Any]:
        """Return full album details including track listing."""
        try:
            return await self._call(self.client.get_album, album_id)
        except Exception:
            logger.exception("get_album failed for %r", album_id)
            return {}

    async def get_artist(self, artist_id: str) -> dict[str, Any]:
        """Return artist page data (top songs, albums, related, etc.)."""
        try:
            return await self._call(self.client.get_artist, artist_id)
        except Exception:
            logger.exception("get_artist failed for %r", artist_id)
            return {}

    _ORDER_PARAMS = {
        "a_to_z": "ggMGKgQIARAA",
        "z_to_a": "ggMGKgQIARAB",
        "recently_added": "ggMGKgQIABAB",
    }

    # Timeout (seconds) for background fetches of large playlists.
    _LARGE_PLAYLIST_TIMEOUT = 120

    async def get_playlist(
        self,
        playlist_id: str,
        limit: int | None = None,
        order: str | None = None,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """Return playlist metadata and tracks.

        Args:
            playlist_id: Playlist ID.
            limit: Max tracks to return.  ``None`` retrieves all.
            order: Sort order — ``"a_to_z"``, ``"z_to_a"``, or
                ``"recently_added"``.  ``None`` uses the playlist's
                server-side default.
            timeout: Override the default API timeout (seconds).
        """
        try:
            params = self._ORDER_PARAMS.get(order or "")
            if params:
                # Temporarily inject sort params into the browse request.
                # Serialize this section so two concurrent get_playlist(order=...)
                # calls don't stack patches on client._send_request and leak.
                async with self._order_lock:
                    client = self.client
                    original_send = client._send_request

                    def _patched_send(endpoint: str, body: dict, *a: Any, **kw: Any) -> Any:
                        if endpoint == "browse" and isinstance(body, dict):
                            body["params"] = params
                        return original_send(endpoint, body, *a, **kw)

                    try:
                        client._send_request = _patched_send
                        return await self._call(
                            client.get_playlist, playlist_id, timeout=timeout, limit=limit
                        )
                    finally:
                        client._send_request = original_send
            return await self._call(
                self.client.get_playlist, playlist_id, timeout=timeout, limit=limit
            )
        except Exception:
            logger.exception("get_playlist failed for %r", playlist_id)
            return {}

    async def get_playlist_remaining(
        self, playlist_id: str, already_have: int, order: str | None = None
    ) -> list[dict[str, Any]]:
        """Fetch all tracks for a playlist and return only those beyond *already_have*.

        Uses an extended timeout since large playlists (1500+) need 30-60s.
        """
        data = await self.get_playlist(
            playlist_id, limit=None, order=order, timeout=self._LARGE_PLAYLIST_TIMEOUT
        )
        all_tracks = data.get("tracks", [])
        return all_tracks[already_have:]

    async def get_song(self, video_id: str) -> dict[str, Any]:
        """Return detailed info for a single song/video."""
        try:
            return await self._call(self.client.get_song, video_id)
        except Exception:
            logger.exception("get_song failed for %r", video_id)
            return {}

    async def get_lyrics(self, video_id: str) -> dict[str, Any] | None:
        """Return lyrics for a song, or None if unavailable.

        Requires the *browseId* for the lyrics tab. We first fetch the watch
        playlist to obtain it, then request the actual lyrics.
        Tries timestamped (mobile) first, falls back to plain text.
        """
        try:
            watch = await self._call(self.client.get_watch_playlist, video_id)
            lyrics_browse_id = watch.get("lyrics")
            if not lyrics_browse_id:
                return None
            # Try timed lyrics first (uses mobile client internally)
            try:
                result = await self._call(self.client.get_lyrics, lyrics_browse_id, timestamps=True)
                if result is not None:
                    return result
            except Exception:
                logger.debug("Timed lyrics request failed for %r, trying plain", video_id)
            # Fall back to plain lyrics
            return await self._call(self.client.get_lyrics, lyrics_browse_id)
        except Exception:
            logger.exception("get_lyrics failed for %r", video_id)
            return None

    # ------------------------------------------------------------------
    # Playback related
    # ------------------------------------------------------------------

    async def get_watch_playlist(
        self,
        video_id: str | None = None,
        playlist_id: str | None = None,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Return the "Up Next" queue for a song or the tracks of a playlist.

        Two call shapes are supported:
        - ``video_id`` + optional ``playlist_id``: original "what plays next
          after this video" use, optionally seeded with playlist context.
        - ``playlist_id`` only: fetch a playlist's tracks via the watch
          endpoint. Used as a fallback for OLAK5-prefixed auto-generated
          playlists (e.g. YouTube Music's Trending charts) where
          ``get_playlist`` hits an upstream parser bug in ytmusicapi
          (``parse_audio_playlist`` dereferences ``tracks[0]["album"]``
          which is None, raising TypeError).

        Returns:
            List of track dicts.
        """
        if video_id is None and playlist_id is None:
            return []
        try:
            kwargs: dict[str, Any] = {"limit": limit}
            if video_id is not None:
                kwargs["videoId"] = video_id
            if playlist_id is not None:
                kwargs["playlistId"] = playlist_id
            result = await self._call(self.client.get_watch_playlist, **kwargs)
            return result.get("tracks", []) if isinstance(result, dict) else []
        except Exception:
            logger.exception(
                "get_watch_playlist failed for video=%r playlist=%r",
                video_id,
                playlist_id,
            )
            return []

    async def get_chart_shelf_tracks(
        self, playlist_id: str, limit: int = 25
    ) -> list[dict[str, Any]]:
        """Resolve a chart shelf playlist into tracks.

        OLAK5-prefixed playlists use ``get_watch_playlist`` because
        ytmusicapi's ``parse_audio_playlist`` dereferences
        ``tracks[0]['album']`` which is None for these auto-generated
        playlists, raising TypeError.
        """
        if playlist_id.startswith("OLAK5"):
            return await self.get_watch_playlist(playlist_id=playlist_id, limit=limit)
        playlist = await self.get_playlist(playlist_id, limit=limit)
        if isinstance(playlist, dict):
            raw = playlist.get("tracks", []) or []
            return raw if isinstance(raw, list) else []
        return []

    async def get_radio(self, video_ids: str | list[str], limit: int = 25) -> list[dict]:
        """Fetch radio tracks from one or more seeds and return a deduplicated mix.

        Fetches all seeds in parallel. Individual seed failures are
        swallowed so a single bad ID doesn't abort the batch.
        Returns the pool normalized, shuffled, and trimmed to *limit*.
        """
        import random

        from ytm_player.utils.formatting import normalize_tracks

        if isinstance(video_ids, str):
            video_ids = [video_ids]
        if not video_ids:
            return []

        async def _fetch_one(video_id: str) -> list[dict]:
            try:
                result = await self._call(
                    self.client.get_watch_playlist, videoId=video_id, radio=True
                )
                return result.get("tracks", []) if isinstance(result, dict) else []
            except Exception:
                logger.debug("get_radio: seed %r failed, skipping", video_id)
                return []

        results = await asyncio.gather(*[_fetch_one(vid) for vid in video_ids])

        pool: list[dict] = []
        seen_ids: set[str] = set()
        for tracks in results:
            for track in tracks:
                vid = track.get("videoId") or track.get("video_id", "")
                if vid and vid not in seen_ids:
                    seen_ids.add(vid)
                    pool.append(track)

        # Single-seed callers (e.g., "Start Radio on this track") expect the seed
        # track to lead. Only shuffle when there are multiple seeds (refill, discovery).
        if len(video_ids) > 1:
            random.shuffle(pool)
        return normalize_tracks(pool[:limit])

    async def get_discovery_mix(self) -> tuple[list[dict], str]:
        """Select seed tracks from one of six sources for discovery playback.

        Round-robins through sources so each press of D cycles to the
        next source.  On failure, skips to the next in sequence.
        Returns (seed_tracks, label).  Returns ([], "") if all fail.
        """
        import random

        total = 6
        start = (self._last_discovery_source % total) + 1
        sources = [(start + i - 1) % total + 1 for i in range(total)]

        async def _charts_mix() -> tuple[list[dict], str]:
            from ytm_player.config.settings import get_settings

            region = get_settings().ui.region
            charts = await self._call(self.client.get_charts, country=region)
            shelves: list[dict] = []
            for key in ("daily", "weekly", "videos"):
                arr = charts.get(key)
                if isinstance(arr, list):
                    shelves.extend(s for s in arr if isinstance(s, dict))
            if not shelves:
                return [], ""
            idx = (self._last_chart_shelf + 1) % len(shelves)
            self._last_chart_shelf = idx
            shelf = shelves[idx]
            playlist_id = shelf.get("playlistId", "")
            from ytm_player.utils.formatting import clean_shelf_title

            shelf_title = clean_shelf_title(shelf.get("title", "Charts"))
            if not playlist_id:
                return [], ""
            tracks = await self.get_chart_shelf_tracks(playlist_id)
            playable = [
                t for t in tracks if isinstance(t, dict) and (t.get("videoId") or t.get("video_id"))
            ]
            if not playable:
                return [], ""
            sampled = random.sample(playable, min(3, len(playable)))
            return sampled, f"{region} {shelf_title}"

        async def _trending_mix() -> tuple[list[dict], str]:
            result = await self._call(self.client.get_explore)
            playlist_id = result.get("trending", {}).get("playlist")
            if not playlist_id:
                return [], ""
            wl = await self._call(
                self.client.get_watch_playlist, playlistId=playlist_id, shuffle=True
            )
            items = wl.get("tracks", []) if isinstance(wl, dict) else []
            playable = [t for t in items if isinstance(t, dict) and t.get("videoId")]
            if not playable:
                return [], ""
            sampled = random.sample(playable, min(3, len(playable)))
            return sampled, "Trending"

        async def _home_mix() -> tuple[list[dict], str]:
            shelves = await self._call(self.client.get_home, limit=3)
            all_playable: list[dict] = []
            for shelf in shelves:
                items = shelf.get("contents", []) if isinstance(shelf, dict) else []
                all_playable.extend(i for i in items if isinstance(i, dict) and i.get("videoId"))
            if not all_playable:
                return [], ""
            sampled = random.sample(all_playable, min(3, len(all_playable)))
            return sampled, "For You"

        async def _liked_songs_mix() -> tuple[list[dict], str]:
            liked = await self._call(self.client.get_liked_songs, limit=50)
            items = liked.get("tracks", []) if isinstance(liked, dict) else []
            playable = [t for t in items if isinstance(t, dict) and t.get("videoId")]
            if not playable:
                return [], ""
            sampled = random.sample(playable, min(3, len(playable)))
            return sampled, "Your Liked Songs"

        async def _artist_mix() -> tuple[list[dict], str]:
            artists = await self._call(self.client.get_library_artists, limit=25)
            if not artists:
                return [], ""
            artist = random.choice(artists)
            channel_id = artist.get("browseId", "")
            if not channel_id:
                return [], ""
            artist_data = await self._call(self.client.get_artist, channel_id)
            songs = artist_data.get("songs", {})
            results = songs.get("results", []) if isinstance(songs, dict) else []
            playable = [t for t in results if isinstance(t, dict) and t.get("videoId")]
            if not playable:
                return [], ""
            sampled = random.sample(playable, min(3, len(playable)))
            artist_name = artist.get("artist", "Artist")
            return sampled, f"Artist: {artist_name}"

        async def _history_mix() -> tuple[list[dict], str]:
            history = await self._call(self.client.get_history)
            if not history:
                return [], ""
            playable = [t for t in history if isinstance(t, dict) and t.get("videoId")]
            if not playable:
                return [], ""
            recent = playable[:20]
            sampled = random.sample(recent, min(3, len(recent)))
            return sampled, "Recently Played"

        _source_fns: dict[int, Any] = {
            1: _charts_mix,
            2: _trending_mix,
            3: _home_mix,
            4: _liked_songs_mix,
            5: _artist_mix,
            6: _history_mix,
        }

        for source in sources:
            try:
                seeds, label = await _source_fns[source]()
                if seeds:
                    self._last_discovery_source = source
                    return seeds, label
            except Exception:
                logger.exception("get_discovery_mix: source %d failed", source)

        return [], ""

    async def get_playlist_radio(self, playlist_id: str) -> list[dict]:
        """Start a radio from a playlist via RDAMPL prefix."""
        stripped = playlist_id.removeprefix("VL")
        try:
            result = await self._call(
                self.client.get_watch_playlist,
                playlistId="RDAMPL" + stripped,
                radio=True,
            )
        except Exception:
            logger.exception("Playlist radio failed for %s", playlist_id)
            return []
        tracks = result.get("tracks", []) if isinstance(result, dict) else []
        if not tracks:
            logger.warning("Playlist radio returned no tracks for %s", playlist_id)
        return tracks

    # ------------------------------------------------------------------
    # Library actions
    # ------------------------------------------------------------------

    async def rate_song(self, video_id: str, rating: str) -> MutationResult:
        """Rate a song.

        Args:
            video_id: The video ID to rate.
            rating: ``"LIKE"``, ``"DISLIKE"``, or ``"INDIFFERENT"`` (remove rating).

        Returns:
            ``"success"`` if the server accepted the rating, otherwise one
            of ``"auth_required"``, ``"auth_expired"``, ``"network"``,
            ``"server_error"``. Unexpected exceptions propagate.
        """
        try:
            await self._call(self.client.rate_song, video_id, rating)
            return "success"
        except _EXPECTED_MUTATION_EXCEPTIONS as exc:
            kind = _classify_mutation_failure(exc)
            logger.exception("rate_song failed for %r rating=%r (kind=%s)", video_id, rating, kind)
            return kind

    async def add_playlist_items(self, playlist_id: str, video_ids: list[str]) -> MutationResult:
        """Add songs to an existing playlist.

        Returns:
            ``"success"`` if the server accepted the add, otherwise one of
            ``"auth_required"``, ``"auth_expired"``, ``"network"``,
            ``"server_error"``. Unexpected exceptions propagate.
        """
        try:
            await self._call(self.client.add_playlist_items, playlist_id, video_ids)
            return "success"
        except _EXPECTED_MUTATION_EXCEPTIONS as exc:
            kind = _classify_mutation_failure(exc)
            logger.exception(
                "add_playlist_items failed for playlist=%r (kind=%s)", playlist_id, kind
            )
            return kind

    async def create_playlist(
        self,
        title: str,
        description: str = "",
        privacy: str = "PRIVATE",
    ) -> str:
        """Create a new playlist and return its ID."""
        try:
            result = await self._call(
                self.client.create_playlist, title, description, privacy_status=privacy
            )
            return result if isinstance(result, str) else ""
        except Exception:
            logger.debug("create_playlist failed for title=%r", title)
            return ""

    async def delete_playlist(self, playlist_id: str) -> MutationResult:
        """Delete a playlist by its ID.

        Returns:
            ``"success"`` if the server accepted the delete, otherwise one of
            ``"auth_required"``, ``"auth_expired"``, ``"network"``,
            ``"server_error"``. Unexpected exceptions propagate.
        """
        try:
            result = await self._call(self.client.delete_playlist, playlist_id)
            succeeded = result == "STATUS_SUCCEEDED" if isinstance(result, str) else bool(result)
            if not succeeded:
                logger.warning(
                    "delete_playlist returned non-success for %r: %r", playlist_id, result
                )
                return "server_error"
            return "success"
        except _EXPECTED_MUTATION_EXCEPTIONS as exc:
            kind = _classify_mutation_failure(exc)
            logger.exception("delete_playlist failed for %r (kind=%s)", playlist_id, kind)
            return kind

    async def add_to_library(self, playlist_id: str) -> MutationResult:
        """Add an album or playlist to the user's library via rate_playlist(LIKE).

        Args:
            playlist_id: The album's or playlist's playlistId.

        Returns:
            ``"success"`` if the server accepted the add, otherwise one of
            ``"auth_required"``, ``"auth_expired"``, ``"network"``,
            ``"server_error"``. Unexpected exceptions propagate.
        """
        try:
            await self._call(self.client.rate_playlist, playlist_id, "LIKE")
            return "success"
        except _EXPECTED_MUTATION_EXCEPTIONS as exc:
            kind = _classify_mutation_failure(exc)
            logger.exception("add_to_library failed for %r (kind=%s)", playlist_id, kind)
            return kind

    async def remove_album_from_library(self, playlist_id: str) -> MutationResult:
        """Remove an album from the user's library via rate_playlist(INDIFFERENT).

        Args:
            playlist_id: The album's playlistId (browseId often starts with
                ``MPREb_``; the corresponding playlistId starts with ``OLAK5``).

        Returns:
            ``"success"`` if the server accepted the remove, otherwise one of
            ``"auth_required"``, ``"auth_expired"``, ``"network"``,
            ``"server_error"``. Unexpected exceptions propagate.
        """
        try:
            await self._call(self.client.rate_playlist, playlist_id, "INDIFFERENT")
            return "success"
        except _EXPECTED_MUTATION_EXCEPTIONS as exc:
            kind = _classify_mutation_failure(exc)
            logger.exception("remove_album_from_library failed for %r (kind=%s)", playlist_id, kind)
            return kind

    async def unsubscribe_artist(self, channel_id: str) -> MutationResult:
        """Unsubscribe from an artist (remove from library).

        Returns:
            ``"success"`` if the server accepted the unsubscribe, otherwise
            one of ``"auth_required"``, ``"auth_expired"``, ``"network"``,
            ``"server_error"``. Unexpected exceptions propagate.
        """
        try:
            await self._call(self.client.unsubscribe_artists, [channel_id])
            return "success"
        except _EXPECTED_MUTATION_EXCEPTIONS as exc:
            kind = _classify_mutation_failure(exc)
            logger.exception("unsubscribe_artist failed for %r (kind=%s)", channel_id, kind)
            return kind

    async def remove_playlist_items(
        self, playlist_id: str, videos: list[dict[str, Any]]
    ) -> MutationResult:
        """Remove items from a playlist.

        Args:
            playlist_id: The playlist to modify.
            videos: List of video dicts as returned by ``get_playlist()`` — each
                must contain ``videoId`` and ``setVideoId``.

        Returns:
            ``"success"`` if the server accepted the remove, otherwise one
            of ``"auth_required"``, ``"auth_expired"``, ``"network"``,
            ``"server_error"``. Unexpected exceptions propagate.
        """
        try:
            await self._call(self.client.remove_playlist_items, playlist_id, videos)
            return "success"
        except _EXPECTED_MUTATION_EXCEPTIONS as exc:
            kind = _classify_mutation_failure(exc)
            logger.exception(
                "remove_playlist_items failed for playlist=%r (kind=%s)", playlist_id, kind
            )
            return kind

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    async def get_history(self) -> list[dict[str, Any]]:
        """Return the user's recently played tracks."""
        try:
            return await self._call(self.client.get_history)
        except Exception:
            logger.exception("get_history failed")
            return []
