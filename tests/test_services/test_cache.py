"""Tests for ytm_player.services.cache.CacheManager."""

import pytest

from ytm_player.services.cache import CacheManager


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
