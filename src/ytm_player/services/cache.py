"""Audio file cache with LRU eviction."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

import aiosqlite

from ytm_player.config.paths import CACHE_DB, SECURE_FILE_MODE
from ytm_player.config.settings import get_settings
from ytm_player.utils.formatting import VALID_VIDEO_ID

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache_index (
    video_id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    format TEXT,
    cached_at TEXT DEFAULT (datetime('now')),
    last_accessed TEXT DEFAULT (datetime('now'))
);
"""


class CacheError(OSError):
    """Raised when a cache write operation fails (e.g. disk full)."""


_COMMIT_EVERY = 10  # Commit after this many cache hits instead of every one.


class CacheManager:
    """Manages a local audio-file cache with LRU eviction."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        db_path: Path = CACHE_DB,
        max_size_mb: int | None = None,
    ) -> None:
        settings = get_settings()
        self._cache_dir = cache_dir or settings.cache_dir
        self._db_path = db_path
        self._max_size_mb = max_size_mb if max_size_mb is not None else settings.cache.max_size_mb
        self._db: aiosqlite.Connection | None = None
        self._hit_count: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init(self) -> None:
        """Create the cache directory, open the database, and ensure the schema."""
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        os.chmod(self._db_path, SECURE_FILE_MODE)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        logger.info(
            "Cache initialised: dir=%s  max=%d MB",
            self._cache_dir,
            self._max_size_mb,
        )

    async def close(self) -> None:
        """Flush pending writes and close the database connection."""
        if self._db is not None:
            if self._hit_count > 0:
                await self._db.commit()
                self._hit_count = 0
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    async def get(self, video_id: str) -> Path | None:
        """Return the cached file path if it exists, else None.

        Updates ``last_accessed`` on hit so the LRU eviction works correctly.
        """
        if self._db is None:
            raise RuntimeError("Database not initialized")
        async with self._db.execute(
            "SELECT file_path FROM cache_index WHERE video_id = ?",
            (video_id,),
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            return None

        path = Path(row["file_path"])
        if not path.exists():
            # Stale index entry -- the file was removed externally.
            await self.remove(video_id)
            return None

        await self._db.execute(
            "UPDATE cache_index SET last_accessed = datetime('now') WHERE video_id = ?",
            (video_id,),
        )
        self._hit_count += 1
        if self._hit_count >= _COMMIT_EVERY:
            await self._db.commit()
            self._hit_count = 0
        return path

    async def put(self, video_id: str, data: bytes, format: str) -> Path:
        """Write raw audio *data* into the cache and return its path."""
        if not VALID_VIDEO_ID.match(video_id):
            raise ValueError(f"Invalid video_id: {video_id!r}")
        dest = self._cache_dir / f"{video_id}.{format}"
        try:
            await asyncio.to_thread(dest.write_bytes, data)
            await self._index(video_id, dest, len(data), format)
            await self.evict()
        except OSError as exc:
            logger.warning("Cache write failed for %s: %s", video_id, exc)
            raise CacheError(f"Failed to cache {video_id}: {exc}") from exc
        return dest

    async def put_file(self, video_id: str, source_path: Path, format: str) -> Path:
        """Copy (or move) *source_path* into the cache directory."""
        if not VALID_VIDEO_ID.match(video_id):
            raise ValueError(f"Invalid video_id: {video_id!r}")
        dest = self._cache_dir / f"{video_id}.{format}"
        try:
            await asyncio.to_thread(shutil.copy2, source_path, dest)
            file_size = dest.stat().st_size
            await self._index(video_id, dest, file_size, format)
            await self.evict()
        except OSError as exc:
            logger.warning("Cache write failed for %s: %s", video_id, exc)
            raise CacheError(f"Failed to cache {video_id}: {exc}") from exc
        return dest

    async def has(self, video_id: str) -> bool:
        """Return True if *video_id* is cached."""
        if self._db is None:
            raise RuntimeError("Database not initialized")
        async with self._db.execute(
            "SELECT 1 FROM cache_index WHERE video_id = ?",
            (video_id,),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def remove(self, video_id: str) -> None:
        """Remove a single cached file and its index entry."""
        if self._db is None:
            raise RuntimeError("Database not initialized")
        try:
            async with self._db.execute(
                "SELECT file_path FROM cache_index WHERE video_id = ?",
                (video_id,),
            ) as cursor:
                row = await cursor.fetchone()

            if row is not None:
                path = Path(row["file_path"])
                path.unlink(missing_ok=True)
                await self._db.execute(
                    "DELETE FROM cache_index WHERE video_id = ?",
                    (video_id,),
                )
                await self._db.commit()
        except OSError as exc:
            logger.warning("Cache remove failed for %s: %s", video_id, exc)
            raise CacheError(f"Failed to remove {video_id} from cache: {exc}") from exc

    async def clear(self) -> None:
        """Wipe the entire cache (files and index)."""
        if self._db is None:
            raise RuntimeError("Database not initialized")
        try:
            async with self._db.execute("SELECT file_path FROM cache_index") as cursor:
                rows = await cursor.fetchall()
            for row in rows:
                Path(row["file_path"]).unlink(missing_ok=True)
            await self._db.execute("DELETE FROM cache_index")
            await self._db.commit()
            logger.info("Cache cleared")
        except OSError as exc:
            logger.warning("Cache clear failed: %s", exc)
            raise CacheError(f"Failed to clear cache: {exc}") from exc

    # ------------------------------------------------------------------
    # Status & eviction
    # ------------------------------------------------------------------

    async def get_status(self) -> dict:
        """Return a summary of cache usage."""
        if self._db is None:
            raise RuntimeError("Database not initialized")
        async with self._db.execute(
            "SELECT COUNT(*) AS file_count, COALESCE(SUM(file_size), 0) AS total_size "
            "FROM cache_index"
        ) as cursor:
            row = await cursor.fetchone()
        return {
            "total_size": row["total_size"],
            "file_count": row["file_count"],
            "max_size": self._max_size_mb * 1024 * 1024,
            "cache_dir": str(self._cache_dir),
        }

    async def evict(self) -> None:
        """Remove least-recently-accessed files until total size is within limits."""
        if self._db is None:
            raise RuntimeError("Database not initialized")
        max_bytes = self._max_size_mb * 1024 * 1024

        async with self._db.execute(
            "SELECT COALESCE(SUM(file_size), 0) AS total FROM cache_index"
        ) as cursor:
            total = (await cursor.fetchone())["total"]

        if total <= max_bytes:
            return

        try:
            # Fetch entries ordered by oldest access first (LRU).
            async with self._db.execute(
                "SELECT video_id, file_path, file_size FROM cache_index ORDER BY last_accessed ASC"
            ) as cursor:
                rows = await cursor.fetchall()

            evict_ids: list[str] = []
            for row in rows:
                if total <= max_bytes:
                    break
                Path(row["file_path"]).unlink(missing_ok=True)
                evict_ids.append(row["video_id"])
                total -= row["file_size"]
                logger.debug("Evicted %s (%d bytes)", row["video_id"], row["file_size"])

            if evict_ids:
                placeholders = ",".join("?" for _ in evict_ids)
                await self._db.execute(
                    f"DELETE FROM cache_index WHERE video_id IN ({placeholders})",
                    evict_ids,
                )
                await self._db.commit()
        except OSError as exc:
            logger.warning("Cache eviction failed: %s", exc)
            raise CacheError(f"Failed to evict cache entries: {exc}") from exc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _index(
        self,
        video_id: str,
        path: Path,
        file_size: int,
        format: str,
    ) -> None:
        """Insert or replace an entry in the cache index."""
        if self._db is None:
            raise RuntimeError("Database not initialized")
        try:
            await self._db.execute(
                """
                INSERT OR REPLACE INTO cache_index
                    (video_id, file_path, file_size, format)
                VALUES (?, ?, ?, ?)
                """,
                (video_id, str(path), file_size, format),
            )
            await self._db.commit()
        except OSError as exc:
            logger.warning("Cache index write failed for %s: %s", video_id, exc)
            raise CacheError(f"Failed to index {video_id} in cache: {exc}") from exc
