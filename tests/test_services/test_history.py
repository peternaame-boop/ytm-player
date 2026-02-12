"""Tests for ytm_player.services.history.HistoryManager."""

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
        # 5 seconds or less should be ignored (threshold is >5).
        await history_manager.log_play(track, listened_seconds=3, source="search")
        await history_manager.log_play(track, listened_seconds=5, source="search")
        plays = await history_manager.get_play_history()
        assert len(plays) == 0
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
