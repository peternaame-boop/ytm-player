"""T11 (X01/X14/S12/A19/L10): `ytm cache status/clear` and the configured cache.

Both commands must use ``[cache] location`` (``settings.cache_dir``) like the
cache and download services do. ``clear`` must refuse while a TUI is running,
must empty the index through SQLite instead of deleting ``cache.db``, must only
delete indexed files from a user-chosen location, must remove a damaged index
(the TUI cannot start with one), and must leave an index it cannot access alone.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from ytm_player.cli import main
from ytm_player.config.settings import Settings
from ytm_player.services.cache import CacheError, CacheManager

_VIDEO_A = "abcdefghijk"
_VIDEO_B = "bcdefghijkl"


@pytest.fixture
def cache_env(tmp_path, monkeypatch):
    """A configured cache location, a separate default dir and a temp index.

    Every path the CLI could touch points into *tmp_path*.
    """
    default_dir = tmp_path / "default-audio"
    custom_dir = tmp_path / "custom-audio"
    db_path = tmp_path / "cache.db"
    default_dir.mkdir()
    custom_dir.mkdir()

    settings = Settings()
    settings.cache.location = str(custom_dir)
    monkeypatch.setattr("ytm_player.config.settings._settings", settings)
    monkeypatch.setattr("ytm_player.config.settings.CACHE_DIR", default_dir)
    monkeypatch.setattr("ytm_player.cli.CACHE_DIR", default_dir)
    monkeypatch.setattr("ytm_player.cli.CACHE_DB", db_path)
    monkeypatch.setattr("ytm_player.cli.ensure_dirs", lambda: None)
    monkeypatch.setattr("ytm_player.cli.is_tui_running", lambda: False)
    return SimpleNamespace(
        settings=settings, default_dir=default_dir, custom_dir=custom_dir, db_path=db_path
    )


def _seed(cache_dir, db_path, *video_ids: str) -> None:
    """Cache tracks through the real CacheManager so cache.db has rows."""

    async def run() -> None:
        manager = CacheManager(cache_dir=cache_dir, db_path=db_path, max_size_mb=100)
        await manager.init()
        try:
            for video_id in video_ids:
                await manager.put(video_id, b"audio", "opus")
        finally:
            await manager.close()

    asyncio.run(run())


def _index_rows(db_path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT COUNT(*) FROM cache_index").fetchone()[0]
    finally:
        conn.close()


def _files(directory) -> list[str]:
    return sorted(p.name for p in directory.iterdir() if p.is_file())


def test_status_reports_the_configured_location(cache_env):
    (cache_env.custom_dir / "aaaaaaaaaaa.opus").write_bytes(b"123")
    (cache_env.custom_dir / "bbbbbbbbbbb.opus").write_bytes(b"12345")
    (cache_env.default_dir / "ccccccccccc.opus").write_bytes(b"1234567")

    result = CliRunner().invoke(main, ["cache", "status", "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["cache_dir"] == str(cache_env.custom_dir)
    assert data["file_count"] == 2
    assert data["total_bytes"] == 8


def test_clear_refuses_while_the_tui_is_running(cache_env, monkeypatch):
    _seed(cache_env.custom_dir, cache_env.db_path, _VIDEO_A)
    monkeypatch.setattr("ytm_player.cli.is_tui_running", lambda: True)

    result = CliRunner().invoke(main, ["cache", "clear", "--yes"])

    assert result.exit_code != 0
    assert "running" in result.stderr
    assert (cache_env.custom_dir / f"{_VIDEO_A}.opus").exists()
    assert cache_env.db_path.exists()
    assert _index_rows(cache_env.db_path) == 1


def test_clear_custom_location_deletes_only_indexed_files(cache_env):
    _seed(cache_env.custom_dir, cache_env.db_path, _VIDEO_A)
    unrelated = cache_env.custom_dir / "holiday.jpg"
    unrelated.write_bytes(b"not a cache file")

    result = CliRunner().invoke(main, ["cache", "clear", "--yes"])

    assert result.exit_code == 0, result.output
    assert "Cleared 1 cached file(s)." in result.stdout
    assert _files(cache_env.custom_dir) == ["holiday.jpg"]
    assert cache_env.db_path.exists()
    assert _index_rows(cache_env.db_path) == 0


def test_clear_keeps_an_index_that_lives_inside_the_custom_location(cache_env, monkeypatch):
    db_path = cache_env.custom_dir / "cache.db"
    monkeypatch.setattr("ytm_player.cli.CACHE_DB", db_path)
    _seed(cache_env.custom_dir, db_path, _VIDEO_A)
    # An open reader keeps the WAL/SHM sidecars on disk through the clear.
    reader = sqlite3.connect(str(db_path))
    try:
        assert reader.execute("SELECT COUNT(*) FROM cache_index").fetchone()[0] == 1
        sidecars = [db_path.with_name("cache.db-wal"), db_path.with_name("cache.db-shm")]
        assert all(p.exists() for p in sidecars)

        result = CliRunner().invoke(main, ["cache", "clear", "--yes"])

        assert result.exit_code == 0, result.output
        assert "Cleared 1 cached file(s)." in result.stdout
        assert not (cache_env.custom_dir / f"{_VIDEO_A}.opus").exists()
        assert db_path.exists()
        assert all(p.exists() for p in sidecars)
        assert reader.execute("SELECT COUNT(*) FROM cache_index").fetchone()[0] == 0
    finally:
        reader.close()


def test_clear_default_directory_removes_indexed_files_and_orphans(cache_env):
    cache_env.settings.cache.location = ""
    _seed(cache_env.default_dir, cache_env.db_path, _VIDEO_A, _VIDEO_B)
    # A stale index row: its file is already gone, so it is not counted.
    (cache_env.default_dir / f"{_VIDEO_B}.opus").unlink()
    (cache_env.default_dir / "orphan00000.opus").write_bytes(b"orphan")

    result = CliRunner().invoke(main, ["cache", "clear", "--yes"])

    assert result.exit_code == 0, result.output
    assert "Cleared 2 cached file(s)." in result.stdout
    assert _files(cache_env.default_dir) == []
    assert cache_env.db_path.exists()
    assert _index_rows(cache_env.db_path) == 0


def _sidecars(db_path) -> list:
    return [db_path, db_path.with_name("cache.db-wal"), db_path.with_name("cache.db-shm")]


def _spy_close(monkeypatch, db_path) -> list[tuple[bool, bool]]:
    """Record (manager closed, cache.db still on disk) for every close()."""
    calls: list[tuple[bool, bool]] = []
    real_close = CacheManager.close

    async def spy_close(self: CacheManager) -> None:
        await real_close(self)
        calls.append((self._db is None, db_path.exists()))

    monkeypatch.setattr(CacheManager, "close", spy_close)
    return calls


def test_clear_removes_a_damaged_index_in_the_default_layout(cache_env, monkeypatch):
    cache_env.settings.cache.location = ""
    for path in _sidecars(cache_env.db_path):
        path.write_bytes(b"x" * 4096)
    (cache_env.default_dir / "aaaaaaaaaaa.opus").write_bytes(b"audio")
    (cache_env.default_dir / "orphan00000.opus").write_bytes(b"orphan")
    closes = _spy_close(monkeypatch, cache_env.db_path)

    result = CliRunner().invoke(main, ["cache", "clear", "--yes"])

    assert result.exit_code == 0, result.output
    assert result.stderr.splitlines() == [
        "Warning: the cache index was damaged and has been removed."
    ]
    assert "Cleared 2 cached file(s)." in result.stdout
    # The manager was closed while the damaged file was still there.
    assert closes == [(True, True)]
    assert [p for p in _sidecars(cache_env.db_path) if p.exists()] == []
    assert _files(cache_env.default_dir) == []

    async def reopen() -> dict:
        manager = CacheManager(db_path=cache_env.db_path)
        await manager.init()
        try:
            return await manager.get_status()
        finally:
            await manager.close()

    assert asyncio.run(reopen())["file_count"] == 0


def test_clear_removes_a_damaged_index_but_keeps_custom_location_files(cache_env):
    cache_env.db_path.write_bytes(b"x" * 4096)
    (cache_env.custom_dir / "aaaaaaaaaaa.opus").write_bytes(b"audio")
    (cache_env.custom_dir / "holiday.jpg").write_bytes(b"not a cache file")

    result = CliRunner().invoke(main, ["cache", "clear", "--yes"])

    assert result.exit_code == 0, result.output
    assert "damaged" in result.stderr
    assert "Cleared 0 cached file(s)." in result.stdout
    assert not cache_env.db_path.exists()
    assert _files(cache_env.custom_dir) == ["aaaaaaaaaaa.opus", "holiday.jpg"]


def test_clear_removes_an_index_damaged_while_clearing(cache_env, monkeypatch):
    """CacheManager.clear() wraps the sqlite3 error in CacheError; the cause decides."""
    _seed(cache_env.custom_dir, cache_env.db_path, _VIDEO_A)

    async def damaged_clear(self: CacheManager) -> None:
        err = CacheError("Failed to clear cache: database disk image is malformed")
        err.__cause__ = sqlite3.DatabaseError("database disk image is malformed")
        raise err

    monkeypatch.setattr(CacheManager, "clear", damaged_clear)

    result = CliRunner().invoke(main, ["cache", "clear", "--yes"])

    assert result.exit_code == 0, result.output
    assert "damaged" in result.stderr
    assert not cache_env.db_path.exists()


def _locked_cache_error() -> CacheError:
    err = CacheError("Failed to clear cache: database is locked")
    err.__cause__ = sqlite3.OperationalError("database is locked")
    return err


@pytest.mark.parametrize(
    ("method", "make_error"),
    [
        ("init", lambda: sqlite3.OperationalError("database is locked")),
        ("clear", _locked_cache_error),
        ("init", lambda: PermissionError(13, "Permission denied")),
    ],
    ids=["init-locked", "clear-locked", "init-permission"],
)
def test_clear_keeps_an_index_it_cannot_access(cache_env, monkeypatch, method, make_error):
    cache_env.settings.cache.location = ""
    _seed(cache_env.default_dir, cache_env.db_path, _VIDEO_A)
    (cache_env.default_dir / "orphan00000.opus").write_bytes(b"orphan")

    async def fail(self: CacheManager) -> None:
        raise make_error()

    monkeypatch.setattr(CacheManager, method, fail)

    result = CliRunner().invoke(main, ["cache", "clear", "--yes"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)  # not an escaped exception
    lines = result.stderr.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("Error: Could not clear the cache index: ")
    assert "damaged" not in result.output
    assert cache_env.db_path.exists()
    assert _index_rows(cache_env.db_path) == 1
    assert _files(cache_env.default_dir) == [f"{_VIDEO_A}.opus", "orphan00000.opus"]
