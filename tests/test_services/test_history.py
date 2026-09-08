"""Tests for ytm_player.services.history.HistoryManager."""

import asyncio
import os
import sqlite3
from collections.abc import Callable
from unittest.mock import Mock

import aiosqlite
import pytest

from ytm_player.services.history import HistoryManager


@pytest.fixture
def history_manager(tmp_path):
    """Create a HistoryManager backed by a temporary database."""
    return HistoryManager(db_path=tmp_path / "history.db")


def _make_track(video_id="vid_001", title="Test Song", artist="Test Artist"):
    return {
        "video_id": video_id,
        "title": title,
        "artist": artist,
        "album": "Test Album",
        "duration_seconds": 200,
    }


class TestInit:
    async def test_init_creates_tables(self, history_manager, tmp_path):
        await history_manager.init()
        assert (tmp_path / "history.db").exists()
        await history_manager.close()


class TestSearchHistory:
    async def test_log_search_records_a_search(self, history_manager):
        await history_manager.init()
        await history_manager.log_search("never gonna", "music", 10)
        history = await history_manager.get_search_history()
        assert len(history) == 1
        assert history[0]["query"] == "never gonna"
        assert history[0]["filter_mode"] == "music"
        assert history[0]["result_count"] == 10
        await history_manager.close()

    async def test_get_search_history_returns_searches(self, history_manager):
        await history_manager.init()
        await history_manager.log_search("query one", "music", 5)
        await history_manager.log_search("query two", "video", 3)
        history = await history_manager.get_search_history()
        assert len(history) == 2
        queries = {h["query"] for h in history}
        assert queries == {"query one", "query two"}
        await history_manager.close()

    async def test_log_search_increments_count_on_duplicate(self, history_manager):
        await history_manager.init()
        await history_manager.log_search("same query", "music", 5)
        await history_manager.log_search("same query", "music", 8)
        history = await history_manager.get_search_history()
        assert len(history) == 1
        assert history[0]["search_count"] == 2
        assert history[0]["result_count"] == 8  # Updated to latest count.
        await history_manager.close()

    async def test_get_search_suggestions_returns_matching_queries(self, history_manager):
        await history_manager.init()
        await history_manager.log_search("never gonna give", "music", 10)
        await history_manager.log_search("never mind", "music", 5)
        await history_manager.log_search("something else", "music", 3)

        suggestions = await history_manager.get_search_suggestions("never")
        assert len(suggestions) == 2
        assert "never gonna give" in suggestions
        assert "never mind" in suggestions
        assert "something else" not in suggestions
        await history_manager.close()

    async def test_clear_search_history_wipes_searches(self, history_manager):
        await history_manager.init()
        await history_manager.log_search("a query", "music", 1)
        await history_manager.clear_search_history()
        history = await history_manager.get_search_history()
        assert len(history) == 0
        await history_manager.close()


class TestPlayHistory:
    async def test_log_play_with_sufficient_listen_time(self, history_manager):
        await history_manager.init()
        track = _make_track()
        await history_manager.log_play(track, listened_seconds=30, source="search")
        plays = await history_manager.get_play_history()
        assert len(plays) == 1
        assert plays[0]["video_id"] == "vid_001"
        assert plays[0]["listened_seconds"] == 30
        await history_manager.close()

    async def test_log_play_with_short_listen_time_is_ignored(self, history_manager):
        await history_manager.init()
        track = _make_track()
        # 5 seconds or less should be ignored by the default threshold (>5).
        await history_manager.log_play(track, listened_seconds=3, source="search")
        await history_manager.log_play(track, listened_seconds=5, source="search")
        plays = await history_manager.get_play_history()
        assert len(plays) == 0
        await history_manager.close()

    async def test_log_play_uses_configured_min_listen_seconds(self, history_manager):
        await history_manager.init()
        track = _make_track()
        await history_manager.log_play(
            track,
            listened_seconds=10,
            source="search",
            min_listen_seconds=30,
        )
        await history_manager.log_play(
            track,
            listened_seconds=31,
            source="search",
            min_listen_seconds=30,
        )
        plays = await history_manager.get_play_history()
        assert len(plays) == 1
        assert plays[0]["listened_seconds"] == 31
        await history_manager.close()

    async def test_log_play_zero_threshold_counts_positive_listen_time(self, history_manager):
        await history_manager.init()
        track = _make_track()
        await history_manager.log_play(
            track,
            listened_seconds=0,
            source="search",
            min_listen_seconds=0,
        )
        await history_manager.log_play(
            track,
            listened_seconds=1,
            source="search",
            min_listen_seconds=0,
        )
        plays = await history_manager.get_play_history()
        assert len(plays) == 1
        assert plays[0]["listened_seconds"] == 1
        await history_manager.close()

    async def test_get_play_history_returns_plays(self, history_manager):
        await history_manager.init()
        await history_manager.log_play(_make_track("v1", "A"), 10, "search")
        await history_manager.log_play(_make_track("v2", "B"), 20, "queue")
        plays = await history_manager.get_play_history()
        assert len(plays) == 2
        video_ids = {p["video_id"] for p in plays}
        assert video_ids == {"v1", "v2"}
        await history_manager.close()

    async def test_get_recently_played_deduplicates_by_video_id(self, history_manager):
        await history_manager.init()
        track = _make_track("v1", "Song")
        await history_manager.log_play(track, 10, "search")
        await history_manager.log_play(track, 15, "search")
        recent = await history_manager.get_recently_played()
        assert len(recent) == 1
        assert recent[0]["video_id"] == "v1"
        await history_manager.close()

    async def test_get_played_video_ids_returns_distinct_ids(self, history_manager):
        await history_manager.init()
        await history_manager.log_play(_make_track("v1", "A"), 10, "search")
        await history_manager.log_play(_make_track("v1", "A"), 15, "search")
        await history_manager.log_play(_make_track("v2", "B"), 20, "queue")
        ids = await history_manager.get_played_video_ids()
        assert ids == {"v1", "v2"}
        await history_manager.close()

    async def test_get_played_video_ids_empty_db_returns_empty_set(self, history_manager):
        await history_manager.init()
        ids = await history_manager.get_played_video_ids()
        assert ids == set()
        await history_manager.close()

    async def test_update_play_listened_seconds_updates_row_and_stats(self, history_manager):
        await history_manager.init()
        play_id = await history_manager.log_play(_make_track("v1", "Song"), 10, "tui")
        assert play_id is not None

        await history_manager.update_play_listened_seconds(play_id, 60)

        plays = await history_manager.get_play_history()
        stats = await history_manager.get_stats()
        assert plays[0]["listened_seconds"] == 60
        assert stats["total_plays"] == 1
        assert stats["total_listen_time"] == 60
        await history_manager.close()


class TestStats:
    async def test_get_stats_returns_aggregate_data(self, history_manager):
        await history_manager.init()
        await history_manager.log_play(_make_track("v1", "A", "Artist X"), 60, "search")
        await history_manager.log_play(_make_track("v2", "B", "Artist Y"), 120, "queue")
        stats = await history_manager.get_stats()
        assert stats["total_plays"] == 2
        assert stats["total_listen_time"] == 180
        assert stats["unique_tracks"] == 2
        assert isinstance(stats["top_tracks"], list)
        assert isinstance(stats["top_artists"], list)
        await history_manager.close()

    async def test_get_top_tracks_ranks_by_play_count(self, history_manager):
        await history_manager.init()
        # Play v1 once, v2 three times.
        await history_manager.log_play(_make_track("v1", "Once"), 10, "s")
        await history_manager.log_play(_make_track("v2", "Thrice"), 10, "s")
        await history_manager.log_play(_make_track("v2", "Thrice"), 10, "s")
        await history_manager.log_play(_make_track("v2", "Thrice"), 10, "s")
        top = await history_manager.get_top_tracks()
        assert top[0]["video_id"] == "v2"
        assert top[0]["play_count"] == 3
        assert top[1]["video_id"] == "v1"
        assert top[1]["play_count"] == 1
        await history_manager.close()


class TestCloseAndReinit:
    async def test_close_and_reinit_works(self, tmp_path):
        manager = HistoryManager(db_path=tmp_path / "history.db")
        await manager.init()
        await manager.log_search("persistent query", "music", 5)
        await manager.close()

        # Re-open the same database and verify data survived.
        manager2 = HistoryManager(db_path=tmp_path / "history.db")
        await manager2.init()
        history = await manager2.get_search_history()
        assert len(history) == 1
        assert history[0]["query"] == "persistent query"
        await manager2.close()


class TestSqliteErrorsWrapped:
    """A locked/corrupt DB raises ``sqlite3.Error``, not ``OSError`` -- confirm
    the write paths still wrap it into RuntimeError instead of leaking raw."""

    @pytest.mark.parametrize(
        "operation",
        [
            lambda m: m.log_search("q", "music", 1),
            lambda m: m.clear_search_history(),
            lambda m: m.log_play(_make_track(), 30, "search"),
        ],
        ids=["log_search", "clear_search_history", "log_play"],
    )
    async def test_write_wraps_sqlite_error(self, history_manager, monkeypatch, operation):
        await history_manager.init()
        monkeypatch.setattr(
            history_manager._db,
            "execute",
            Mock(side_effect=sqlite3.OperationalError("database is locked")),
        )
        with pytest.raises(RuntimeError, match="Failed to write to history database"):
            await operation(history_manager)
        await history_manager.close()

    async def test_init_prune_wraps_sqlite_error(self, history_manager, monkeypatch):
        real_connect = aiosqlite.connect

        async def poisoned_connect(path, *args, **kwargs):
            conn = await real_connect(path, *args, **kwargs)
            real_execute = conn.execute

            def execute_wrapper(sql, *a, **k):
                if "DELETE FROM play_history" in sql:
                    raise sqlite3.OperationalError("database is locked")
                return real_execute(sql, *a, **k)

            conn.execute = execute_wrapper
            return conn

        monkeypatch.setattr("ytm_player.services.history.aiosqlite.connect", poisoned_connect)
        with pytest.raises(RuntimeError, match="Failed to write to history database"):
            await history_manager.init()
        await history_manager.close()


class _CapturingConnect:
    """``aiosqlite.connect`` wrapper exposing the connection a test created.

    Cleanup is asserted against that connection — its ``close`` awaited once
    and its worker thread finished — never against the absence of every
    SQLite thread in the process.
    """

    def __init__(self, on_connect: Callable[[aiosqlite.Connection], None] | None = None) -> None:
        self.conn: aiosqlite.Connection | None = None
        self.close_calls = 0
        self._on_connect = on_connect
        self._real = aiosqlite.connect

    async def __call__(self, path, *args, **kwargs):
        # ``aiosqlite.connect`` returns the Connection unstarted; its thread
        # starts on await. Capture before the await so a connect that fails
        # (the file is a directory) still leaves a thread to join.
        conn = self._real(path, *args, **kwargs)
        self.conn = conn
        real_close = conn.close

        async def close():
            self.close_calls += 1
            await real_close()

        conn.close = close
        await conn
        if self._on_connect is not None:
            self._on_connect(conn)
        return conn

    def join_thread(self) -> None:
        """Wait (bounded) for the connection's worker thread, if one was created."""
        if self.conn is not None:
            self.conn._thread.join(timeout=2.0)

    def assert_thread_finished(self) -> None:
        assert self.conn is not None, "no connection was created"
        self.join_thread()
        assert not self.conn._thread.is_alive()

    def assert_released(self) -> None:
        assert self.conn is not None, "no connection was created"
        assert self.close_calls == 1
        self.assert_thread_finished()


class TestInitCleanup:
    """A failed or cancelled ``init()`` closes its connection and does not delete or
    replace the file (SQLite itself may still touch journal/WAL state).
    """

    async def test_garbage_file_raises_runtime_error_keeps_bytes_and_closes_the_connection(
        self, tmp_path, monkeypatch
    ):
        db_path = tmp_path / "history.db"
        garbage = os.urandom(64)
        db_path.write_bytes(garbage)
        capture = _CapturingConnect()
        monkeypatch.setattr("ytm_player.services.history.aiosqlite.connect", capture)
        manager = HistoryManager(db_path=db_path)

        with pytest.raises(RuntimeError, match="Failed to open history database"):
            await manager.init()

        # This fixture only: a 64-byte non-database file stays byte-identical.
        assert db_path.read_bytes() == garbage
        assert manager._db is None
        capture.assert_released()
        await manager.close()  # no-op after a failed init

    async def test_directory_path_raises_runtime_error(self, tmp_path, monkeypatch):
        # A failed connect stops aiosqlite's worker thread through the event
        # loop. Join that thread while the loop is alive, or it dies later
        # with "Event loop is closed" attributed to whichever test is running.
        capture = _CapturingConnect()
        monkeypatch.setattr("ytm_player.services.history.aiosqlite.connect", capture)
        manager = HistoryManager(db_path=tmp_path)  # an existing directory

        try:
            with pytest.raises(RuntimeError, match="Failed to open history database"):
                await manager.init()

            assert manager._db is None
            assert tmp_path.is_dir()
        finally:
            capture.join_thread()
        capture.assert_thread_finished()

    async def test_prune_failure_closes_the_connection(self, history_manager, monkeypatch):
        def poison_prune(conn: aiosqlite.Connection) -> None:
            real_execute = conn.execute

            def execute_wrapper(sql, *a, **k):
                if "DELETE FROM play_history" in sql:
                    raise sqlite3.OperationalError("database is locked")
                return real_execute(sql, *a, **k)

            conn.execute = execute_wrapper

        capture = _CapturingConnect(on_connect=poison_prune)
        monkeypatch.setattr("ytm_player.services.history.aiosqlite.connect", capture)

        with pytest.raises(RuntimeError, match="Failed to write to history database"):
            await history_manager.init()

        assert history_manager._db is None
        capture.assert_released()

    async def test_cancellation_during_init_releases_the_connection(
        self, history_manager, monkeypatch
    ):
        reached_schema = asyncio.Event()

        def block_schema(conn: aiosqlite.Connection) -> None:
            async def blocked(*_args, **_kwargs):
                reached_schema.set()
                await asyncio.Event().wait()  # never set: init() hangs here

            conn.executescript = blocked

        capture = _CapturingConnect(on_connect=block_schema)
        monkeypatch.setattr("ytm_player.services.history.aiosqlite.connect", capture)
        task = asyncio.create_task(history_manager.init())
        await reached_schema.wait()

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert history_manager._db is None
        capture.assert_released()
