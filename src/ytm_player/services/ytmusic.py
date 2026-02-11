"""Async wrapper around ytmusicapi providing all YouTube Music functionality."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from ytmusicapi import YTMusic

from ytm_player.config.paths import AUTH_FILE
from ytm_player.services.auth import AuthManager

logger = logging.getLogger(__name__)


class YTMusicService:
    """Async wrapper around ytmusicapi.YTMusic.

    All public methods are async and delegate to ytmusicapi's synchronous API
    through ``asyncio.to_thread`` so they never block the event loop.
    """

    def __init__(self, auth_path: Path = AUTH_FILE, auth_manager: AuthManager | None = None) -> None:
        self._auth_path = auth_path
        self._auth_manager = auth_manager
        self._ytm: YTMusic | None = None

    @property
    def client(self) -> YTMusic:
        """Lazily initialise and return the underlying YTMusic client."""
        if self._ytm is None:
            if self._auth_manager is not None:
                self._ytm = self._auth_manager.create_ytmusic_client()
            else:
                self._ytm = YTMusic(str(self._auth_path))
        return self._ytm

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
            return await asyncio.to_thread(
                self.client.search, query, filter=filter, limit=limit
            )
        except Exception:
            logger.exception("Search failed for query=%r filter=%r", query, filter)
            return []

    async def get_search_suggestions(self, query: str) -> list[str]:
        """Return autocomplete suggestions for *query*."""
        try:
            return await asyncio.to_thread(self.client.get_search_suggestions, query)
        except Exception:
            logger.exception("get_search_suggestions failed for query=%r", query)
            return []

    # ------------------------------------------------------------------
    # Library
    # ------------------------------------------------------------------

    async def get_library_playlists(self, limit: int = 25) -> list[dict[str, Any]]:
        """Return the user's library playlists."""
        try:
            return await asyncio.to_thread(self.client.get_library_playlists, limit=limit)
        except Exception:
            logger.exception("get_library_playlists failed")
            return []

    async def get_library_albums(self, limit: int = 25) -> list[dict[str, Any]]:
        """Return the user's saved albums."""
        try:
            return await asyncio.to_thread(self.client.get_library_albums, limit=limit)
        except Exception:
            logger.exception("get_library_albums failed")
            return []

    async def get_library_artists(self, limit: int = 25) -> list[dict[str, Any]]:
        """Return the user's subscribed/followed artists."""
        try:
            return await asyncio.to_thread(
                self.client.get_library_subscriptions, limit=limit
            )
        except Exception:
            logger.exception("get_library_artists failed")
            return []

    async def get_liked_songs(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return tracks from the user's Liked Music playlist."""
        try:
            playlist = await asyncio.to_thread(
                self.client.get_liked_songs, limit=limit
            )
            return playlist.get("tracks", []) if isinstance(playlist, dict) else []
        except Exception:
            logger.exception("get_liked_songs failed")
            return []

    # ------------------------------------------------------------------
    # Browsing
    # ------------------------------------------------------------------

    async def get_home(self) -> list[dict[str, Any]]:
        """Return personalised home page recommendations."""
        try:
            return await asyncio.to_thread(self.client.get_home, limit=6)
        except Exception:
            logger.exception("get_home failed")
            return []

    async def get_mood_categories(self) -> list[dict[str, Any]]:
        """Return available mood/genre categories."""
        try:
            return await asyncio.to_thread(self.client.get_mood_categories)
        except Exception:
            logger.exception("get_mood_categories failed")
            return []

    async def get_mood_playlists(self, category_id: str) -> list[dict[str, Any]]:
        """Return playlists for a given mood/genre *category_id*."""
        try:
            return await asyncio.to_thread(
                self.client.get_mood_playlists, category_id
            )
        except Exception:
            logger.exception("get_mood_playlists failed for %r", category_id)
            return []

    async def get_charts(self, country: str = "ZZ") -> dict[str, Any]:
        """Return chart data for *country* (``ZZ`` = global)."""
        try:
            return await asyncio.to_thread(self.client.get_charts, country=country)
        except Exception:
            logger.exception("get_charts failed for country=%r", country)
            return {}

    async def get_new_releases(self) -> list[dict[str, Any]]:
        """Return new album releases."""
        try:
            result = await asyncio.to_thread(self.client.get_new_releases)
            # ytmusicapi may return a list directly or a dict with a key.
            if isinstance(result, list):
                return result
            if isinstance(result, dict):
                return result.get("albums", result.get("results", []))
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
            return await asyncio.to_thread(self.client.get_album, album_id)
        except Exception:
            logger.exception("get_album failed for %r", album_id)
            return {}

    async def get_artist(self, artist_id: str) -> dict[str, Any]:
        """Return artist page data (top songs, albums, related, etc.)."""
        try:
            return await asyncio.to_thread(self.client.get_artist, artist_id)
        except Exception:
            logger.exception("get_artist failed for %r", artist_id)
            return {}

    async def get_playlist(
        self, playlist_id: str, limit: int = 100
    ) -> dict[str, Any]:
        """Return playlist metadata and tracks."""
        try:
            return await asyncio.to_thread(
                self.client.get_playlist, playlist_id, limit=limit
            )
        except Exception:
            logger.exception("get_playlist failed for %r", playlist_id)
            return {}

    async def get_song(self, video_id: str) -> dict[str, Any]:
        """Return detailed info for a single song/video."""
        try:
            return await asyncio.to_thread(self.client.get_song, video_id)
        except Exception:
            logger.exception("get_song failed for %r", video_id)
            return {}

    async def get_lyrics(self, video_id: str) -> dict[str, Any] | None:
        """Return lyrics for a song, or None if unavailable.

        Requires the *browseId* for the lyrics tab. We first fetch the watch
        playlist to obtain it, then request the actual lyrics.
        """
        try:
            watch = await asyncio.to_thread(
                self.client.get_watch_playlist, video_id
            )
            lyrics_browse_id = watch.get("lyrics")
            if not lyrics_browse_id:
                return None
            return await asyncio.to_thread(self.client.get_lyrics, lyrics_browse_id)
        except Exception:
            logger.exception("get_lyrics failed for %r", video_id)
            return None

    # ------------------------------------------------------------------
    # Playback related
    # ------------------------------------------------------------------

    async def get_watch_playlist(
        self,
        video_id: str,
        playlist_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return the "Up Next" queue for a song.

        Args:
            video_id: The currently-playing video ID.
            playlist_id: Optional playlist context for queue generation.

        Returns:
            List of track dicts.
        """
        try:
            kwargs: dict[str, Any] = {"videoId": video_id}
            if playlist_id is not None:
                kwargs["playlistId"] = playlist_id
            result = await asyncio.to_thread(
                self.client.get_watch_playlist, **kwargs
            )
            return result.get("tracks", []) if isinstance(result, dict) else []
        except Exception:
            logger.exception(
                "get_watch_playlist failed for video=%r playlist=%r",
                video_id,
                playlist_id,
            )
            return []

    async def get_radio(self, video_id: str) -> list[dict[str, Any]]:
        """Start a radio queue from a song and return its tracks."""
        try:
            result = await asyncio.to_thread(
                self.client.get_watch_playlist, videoId=video_id, radio=True
            )
            return result.get("tracks", []) if isinstance(result, dict) else []
        except Exception:
            logger.exception("get_radio failed for %r", video_id)
            return []

    # ------------------------------------------------------------------
    # Library actions
    # ------------------------------------------------------------------

    async def rate_song(self, video_id: str, rating: str) -> None:
        """Rate a song.

        Args:
            video_id: The video ID to rate.
            rating: ``"LIKE"``, ``"DISLIKE"``, or ``"INDIFFERENT"`` (remove rating).
        """
        try:
            await asyncio.to_thread(self.client.rate_song, video_id, rating)
        except Exception:
            logger.exception("rate_song failed for %r rating=%r", video_id, rating)

    async def add_playlist_items(
        self, playlist_id: str, video_ids: list[str]
    ) -> None:
        """Add songs to an existing playlist."""
        try:
            await asyncio.to_thread(
                self.client.add_playlist_items, playlist_id, video_ids
            )
        except Exception:
            logger.exception(
                "add_playlist_items failed for playlist=%r", playlist_id
            )

    async def create_playlist(
        self,
        title: str,
        description: str = "",
        privacy: str = "PRIVATE",
    ) -> str:
        """Create a new playlist and return its ID."""
        try:
            result = await asyncio.to_thread(
                self.client.create_playlist,
                title,
                description,
                privacy_status=privacy,
            )
            return result if isinstance(result, str) else ""
        except Exception:
            logger.exception("create_playlist failed for title=%r", title)
            return ""

    async def delete_playlist(self, playlist_id: str) -> bool:
        """Delete a playlist by its ID.

        Returns:
            True if deletion succeeded, False otherwise.
        """
        try:
            result = await asyncio.to_thread(
                self.client.delete_playlist, playlist_id
            )
            return result == "STATUS_SUCCEEDED" if isinstance(result, str) else bool(result)
        except Exception:
            logger.exception("delete_playlist failed for %r", playlist_id)
            return False

    async def remove_album_from_library(self, playlist_id: str) -> bool:
        """Remove an album from the user's library via rate_playlist(INDIFFERENT).

        Args:
            playlist_id: The album's playlistId (browseId often starts with
                ``MPREb_``; the corresponding playlistId starts with ``OLAK5``).
        """
        try:
            await asyncio.to_thread(
                self.client.rate_playlist, playlist_id, "INDIFFERENT"
            )
            return True
        except Exception:
            logger.exception("remove_album_from_library failed for %r", playlist_id)
            return False

    async def unsubscribe_artist(self, channel_id: str) -> bool:
        """Unsubscribe from an artist (remove from library)."""
        try:
            await asyncio.to_thread(
                self.client.unsubscribe_artists, [channel_id]
            )
            return True
        except Exception:
            logger.exception("unsubscribe_artist failed for %r", channel_id)
            return False

    async def remove_playlist_items(
        self, playlist_id: str, videos: list[dict[str, Any]]
    ) -> None:
        """Remove items from a playlist.

        Args:
            playlist_id: The playlist to modify.
            videos: List of video dicts as returned by ``get_playlist()`` — each
                must contain ``videoId`` and ``setVideoId``.
        """
        try:
            await asyncio.to_thread(
                self.client.remove_playlist_items, playlist_id, videos
            )
        except Exception:
            logger.exception(
                "remove_playlist_items failed for playlist=%r", playlist_id
            )

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    async def get_history(self) -> list[dict[str, Any]]:
        """Return the user's recently played tracks."""
        try:
            return await asyncio.to_thread(self.client.get_history)
        except Exception:
            logger.exception("get_history failed")
            return []


