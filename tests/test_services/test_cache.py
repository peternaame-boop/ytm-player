"""Tests for ytm_player.services.cache.CacheManager."""

import sqlite3
from unittest.mock import Mock

import pytest

from ytm_player.services.cache import CacheError, CacheManager


@pytest.fixture
def cache_manager(tmp_path):
    """Create a CacheManager backed by a temporary directory."""
    return CacheManager(
        cache_dir=tmp_path / "cache",
        db_path=tmp_path / "cache.db",
        max_size_mb=1,
    )


# Valid 11-char YouTube-style video IDs for testing.
VID_A = "dQw4w9WgXcQ"
VID_B = "xvFZjo5PgG0"
VID_C = "9bZkp7q19f0"


class TestInit:
    async def test_init_creates_tables(self, cache_manager, tmp_path):
        await cache_manager.init()
        assert (tmp_path / "cache").is_dir()
        assert (tmp_path / "cache.db").exists()
        await cache_manager.close()


class TestPutAndGet:
    async def test_put_stores_data_and_returns_path(self, cache_manager):
        await cache_manager.init()
        path = await cache_manager.put(VID_A, b"fake audio data", "opus")
        assert path.exists()
        assert path.read_bytes() == b"fake audio data"
        assert path.name == f"{VID_A}.opus"
        await cache_manager.close()

    async def test_get_returns_cached_path(self, cache_manager):
        await cache_manager.init()
        expected = await cache_manager.put(VID_A, b"audio", "opus")
        result = await cache_manager.get(VID_A)
        assert result == expected
        await cache_manager.close()

    async def test_get_miss_returns_none(self, cache_manager):
        await cache_manager.init()
        result = await cache_manager.get("nonexistent1")
        assert result is None
        await cache_manager.close()


class TestHas:
    async def test_has_returns_true_when_cached(self, cache_manager):
        await cache_manager.init()
        await cache_manager.put(VID_A, b"data", "opus")
        assert await cache_manager.has(VID_A) is True
        await cache_manager.close()

    async def test_has_returns_false_when_missing(self, cache_manager):
        await cache_manager.init()
        assert await cache_manager.has(VID_A) is False
        await cache_manager.close()


class TestRemove:
    async def test_remove_deletes_entry_and_file(self, cache_manager):
        await cache_manager.init()
        path = await cache_manager.put(VID_A, b"data", "opus")
        assert path.exists()

        await cache_manager.remove(VID_A)

        assert not path.exists()
        assert await cache_manager.has(VID_A) is False
        await cache_manager.close()


class TestClear:
    async def test_clear_wipes_everything(self, cache_manager):
        await cache_manager.init()
        path_a = await cache_manager.put(VID_A, b"aaa", "opus")
        path_b = await cache_manager.put(VID_B, b"bbb", "opus")

        await cache_manager.clear()

        assert not path_a.exists()
        assert not path_b.exists()
        assert await cache_manager.has(VID_A) is False
        assert await cache_manager.has(VID_B) is False
        await cache_manager.close()


class TestPutFile:
    async def test_put_file_recognizes_equivalent_path(self, cache_manager):
        await cache_manager.init()
        source = cache_manager._cache_dir / f"{VID_A}.opus"
        source.write_bytes(b"downloaded audio")
        alias = cache_manager._cache_dir / ".." / "cache" / source.name
        try:
            assert await cache_manager.put_file(VID_A, alias, "opus") == source
            assert await cache_manager.get(VID_A) == source
        finally:
            await cache_manager.close()

    async def test_indexing_existing_file_still_runs_eviction(self, cache_manager):
        await cache_manager.init()
        source = cache_manager._cache_dir / f"{VID_A}.opus"
        source.write_bytes(b"x" * (2 * 1024 * 1024))
        try:
            await cache_manager.put_file(VID_A, source, "opus")
            assert await cache_manager.get(VID_A) is None
            assert not source.exists()
        finally:
            await cache_manager.close()

    async def test_put_file_indexes_download_already_in_cache(self, cache_manager):
        await cache_manager.init()
        source = cache_manager._cache_dir / f"{VID_A}.opus"
        source.write_bytes(b"downloaded audio")
        try:
            dest = await cache_manager.put_file(VID_A, source, "opus")
            assert dest == source
            assert source.read_bytes() == b"downloaded audio"
            assert await cache_manager.get(VID_A) == source
            assert (await cache_manager.get_status())["total_size"] == len(b"downloaded audio")
        finally:
            await cache_manager.close()

    async def test_put_file_copies_file_into_cache(self, cache_manager, tmp_path):
        await cache_manager.init()
        source = tmp_path / "source.opus"
        source.write_bytes(b"file content here")

        dest = await cache_manager.put_file(VID_A, source, "opus")

        assert dest.exists()
        assert dest.read_bytes() == b"file content here"
        assert await cache_manager.has(VID_A) is True
        # Source should still exist (copy, not move).
        assert source.exists()
        await cache_manager.close()


class TestEvict:
    async def test_evict_removes_lru_entries_when_over_limit(self, tmp_path):
        """With max_size_mb=1 (1 MB), inserting >1 MB should evict oldest entries."""
        manager = CacheManager(
            cache_dir=tmp_path / "cache",
            db_path=tmp_path / "cache.db",
            max_size_mb=1,  # 1 MB limit
        )
        await manager.init()

        # Each chunk is ~600 KB; two of them exceed 1 MB.
        chunk = b"x" * (600 * 1024)
        await manager.put(VID_A, chunk, "opus")
        await manager.put(VID_B, chunk, "opus")

        # Access VID_B so VID_A stays as the LRU candidate.
        await manager.get(VID_B)

        # This third put should trigger eviction of VID_A.
        await manager.put(VID_C, chunk, "opus")

        assert await manager.has(VID_A) is False
        assert await manager.has(VID_C) is True
        await manager.close()


class TestGetStatus:
    async def test_get_status_returns_correct_counts(self, cache_manager):
        await cache_manager.init()

        status = await cache_manager.get_status()
        assert status["file_count"] == 0
        assert status["total_size"] == 0
        assert status["max_size"] == 1 * 1024 * 1024

        await cache_manager.put(VID_A, b"hello", "opus")
        status = await cache_manager.get_status()
        assert status["file_count"] == 1
        assert status["total_size"] == 5
        await cache_manager.close()


class TestCacheRoundTrip:
    """Verify that put_file + get returns the same path (sanity check
    for the cache wire-up in app/_playback.py)."""

    async def test_put_file_then_get_returns_path(self, tmp_path):
        cache_dir = tmp_path / "cache"
        db_path = tmp_path / "cache.db"
        cm = CacheManager(cache_dir=cache_dir, db_path=db_path, max_size_mb=10)
        await cm.init()

        # Create a fake source audio file.
        src = tmp_path / "src.opus"
        src.write_bytes(b"fake audio data")

        # Put it into the cache.
        cached_path = await cm.put_file(VID_A, src, "opus")
        assert cached_path.exists()

        # Get it back.
        retrieved = await cm.get(VID_A)
        assert retrieved == cached_path
        assert retrieved.read_bytes() == b"fake audio data"

        # Get for a missing id returns None.
        missing = await cm.get("nonexistent01")
        assert missing is None

        await cm.close()


class TestSqliteErrorsWrapped:
    """A locked/corrupt DB raises ``sqlite3.Error``, not ``OSError`` -- confirm
    the write paths still wrap it into CacheError instead of leaking raw."""

    @pytest.mark.parametrize(
        "operation",
        [
            lambda m: m.put(VID_A, b"data", "opus"),
            lambda m: m.remove(VID_A),
            lambda m: m.clear(),
        ],
        ids=["put", "remove", "clear"],
    )
    async def test_write_wraps_sqlite_error(self, cache_manager, monkeypatch, operation):
        await cache_manager.init()
        monkeypatch.setattr(
            cache_manager._db,
            "execute",
            Mock(side_effect=sqlite3.OperationalError("database is locked")),
        )
        with pytest.raises(CacheError):
            await operation(cache_manager)
        await cache_manager.close()

    async def test_put_file_wraps_sqlite_error(self, cache_manager, monkeypatch, tmp_path):
        await cache_manager.init()
        source = tmp_path / "source.opus"
        source.write_bytes(b"data")
        monkeypatch.setattr(
            cache_manager._db,
            "execute",
            Mock(side_effect=sqlite3.OperationalError("database is locked")),
        )
        with pytest.raises(CacheError):
            await cache_manager.put_file(VID_A, source, "opus")
        await cache_manager.close()
