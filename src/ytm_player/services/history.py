"""Play and search history tracking using async SQLite."""

from __future__ import annotations

import logging

import aiosqlite

from ytm_player.config.paths import HISTORY_DB

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS search_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    filter_mode TEXT DEFAULT 'music',
    result_count INTEGER,
    search_count INTEGER DEFAULT 1,
    first_searched TEXT DEFAULT (datetime('now')),
    last_searched TEXT DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_search_query
    ON search_history(query, filter_mode);

CREATE TABLE IF NOT EXISTS play_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    title TEXT NOT NULL,
    artist TEXT,
    album TEXT,
    duration_seconds INTEGER,
    listened_seconds INTEGER,
    source TEXT,
    played_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS play_stats (
    video_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    artist TEXT,
    play_count INTEGER DEFAULT 0,
    total_listened_seconds INTEGER DEFAULT 0,
    last_played TEXT
);
"""

# Minimum listen duration (seconds) before a play counts.
_MIN_LISTEN_SECONDS = 5


class HistoryManager:
    """Manages play and search history in a local SQLite database."""

    def __init__(self, db_path: Path = HISTORY_DB) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init(self) -> None:
        """Open the database and create tables if they don't exist."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        logger.info("History database initialised at %s", self._db_path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # Search history
    # ------------------------------------------------------------------

    async def log_search(
        self,
        query: str,
        filter_mode: str,
        result_count: int,
    ) -> None:
        """Record a search query, incrementing the counter on duplicates."""
        if self._db is None:
            raise RuntimeError("Database not initialized")
        await self._db.execute(
            """
            INSERT INTO search_history (query, filter_mode, result_count)
            VALUES (?, ?, ?)
            ON CONFLICT(query, filter_mode) DO UPDATE SET
                search_count = search_count + 1,
                result_count = excluded.result_count,
                last_searched = datetime('now')
            """,
            (query, filter_mode, result_count),
        )
        await self._db.commit()

    async def get_search_history(self, limit: int = 50) -> list[dict]:
        """Return recent searches ordered by last_searched descending."""
        if self._db is None:
            raise RuntimeError("Database not initialized")
        async with self._db.execute(
            """
            SELECT query, filter_mode, result_count, search_count,
                   first_searched, last_searched
            FROM search_history
            ORDER BY last_searched DESC
            LIMIT ?
            """,
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_search_suggestions(self, prefix: str, limit: int = 10) -> list[str]:
        """Return matching query strings for autocomplete."""
        if self._db is None:
            raise RuntimeError("Database not initialized")
        async with self._db.execute(
            """
            SELECT query
            FROM search_history
            WHERE query LIKE ? || '%'
            ORDER BY search_count DESC, last_searched DESC
            LIMIT ?
            """,
            (prefix, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [row["query"] for row in rows]

    async def clear_search_history(self) -> None:
        """Delete all search history records."""
        if self._db is None:
            raise RuntimeError("Database not initialized")
        await self._db.execute("DELETE FROM search_history")
        await self._db.commit()

    # ------------------------------------------------------------------
    # Play history
    # ------------------------------------------------------------------

    async def log_play(
        self,
        track: dict,
        listened_seconds: int,
        source: str,
    ) -> None:
        """Record a track play event.

        Skips are ignored: the play is only logged when *listened_seconds*
        exceeds the minimum threshold.
        """
        if listened_seconds <= _MIN_LISTEN_SECONDS:
            return

        if self._db is None:
            raise RuntimeError("Database not initialized")

        video_id = track["video_id"]
        title = track.get("title", "")
        artist = track.get("artist", "")
        album = track.get("album", "")
        duration = track.get("duration_seconds", 0)

        await self._db.execute(
            """
            INSERT INTO play_history
                (video_id, title, artist, album, duration_seconds,
                 listened_seconds, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (video_id, title, artist, album, duration, listened_seconds, source),
        )

        await self._db.execute(
            """
            INSERT INTO play_stats (video_id, title, artist, play_count,
                                    total_listened_seconds, last_played)
            VALUES (?, ?, ?, 1, ?, datetime('now'))
            ON CONFLICT(video_id) DO UPDATE SET
                title = excluded.title,
                artist = excluded.artist,
                play_count = play_count + 1,
                total_listened_seconds = total_listened_seconds + excluded.total_listened_seconds,
                last_played = datetime('now')
            """,
            (video_id, title, artist, listened_seconds),
        )

        await self._db.commit()

    async def get_play_history(self, limit: int = 100) -> list[dict]:
        """Return play history ordered by most recent first."""
        if self._db is None:
            raise RuntimeError("Database not initialized")
        async with self._db.execute(
            """
            SELECT video_id, title, artist, album, duration_seconds,
                   listened_seconds, source, played_at
            FROM play_history
            ORDER BY played_at DESC
            LIMIT ?
            """,
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_recently_played(self, limit: int = 50) -> list[dict]:
        """Return recently-played tracks, deduplicated by video_id."""
        if self._db is None:
            raise RuntimeError("Database not initialized")
        async with self._db.execute(
            """
            SELECT video_id, title, artist, album, duration_seconds,
                   MAX(played_at) AS played_at
            FROM play_history
            GROUP BY video_id
            ORDER BY played_at DESC
            LIMIT ?
            """,
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    async def get_stats(self) -> dict:
        """Return aggregate listening statistics."""
        if self._db is None:
            raise RuntimeError("Database not initialized")

        async with self._db.execute(
            "SELECT COUNT(*) AS total_plays FROM play_history"
        ) as cur:
            total_plays = (await cur.fetchone())["total_plays"]

        async with self._db.execute(
            "SELECT COALESCE(SUM(listened_seconds), 0) AS s FROM play_history"
        ) as cur:
            total_listen_time = (await cur.fetchone())["s"]

        async with self._db.execute(
            "SELECT COUNT(*) AS c FROM play_stats"
        ) as cur:
            unique_tracks = (await cur.fetchone())["c"]

        top_tracks = await self.get_top_tracks(limit=10)
        top_artists = await self.get_top_artists(limit=10)

        return {
            "total_plays": total_plays,
            "total_listen_time": total_listen_time,
            "unique_tracks": unique_tracks,
            "top_tracks": top_tracks,
            "top_artists": top_artists,
        }

    async def get_top_tracks(self, limit: int = 10) -> list[dict]:
        """Return the most-played tracks."""
        if self._db is None:
            raise RuntimeError("Database not initialized")
        async with self._db.execute(
            """
            SELECT video_id, title, artist, play_count,
                   total_listened_seconds, last_played
            FROM play_stats
            ORDER BY play_count DESC
            LIMIT ?
            """,
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_top_artists(self, limit: int = 10) -> list[dict]:
        """Return the most-played artists ranked by total play count."""
        if self._db is None:
            raise RuntimeError("Database not initialized")
        async with self._db.execute(
            """
            SELECT artist,
                   SUM(play_count) AS play_count,
                   SUM(total_listened_seconds) AS total_listened_seconds
            FROM play_stats
            WHERE artist IS NOT NULL AND artist != ''
            GROUP BY artist
            ORDER BY play_count DESC
            LIMIT ?
            """,
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
