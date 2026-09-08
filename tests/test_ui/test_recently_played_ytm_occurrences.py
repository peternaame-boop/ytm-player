"""The YT Music tab's rows keep their identity across background refreshes.

``get_history()`` lists a track once per day it was played, so the account
feed can name one video ID on several rows. Each row of the account cache
carries its own occurrence id from the moment it enters the cache, and the
YT Music tab keys its loads by it, so a play landing in the background
(a keyed refresh) carries every mark and the cursor over. Repeated rows
stay: a play the account accepts supersedes only the track's most recent
row, which keeps its identity and moves to the top; older rows are left
where they are. That is a provisional view of the cache — the next fetch
replaces it with the server's list.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from textual.app import App, ComposeResult

from ytm_player.app._playback import PlaybackMixin
from ytm_player.config.keymap import Action
from ytm_player.ui.pages.recently_played import (
    _TAB_YTM,
    RecentlyPlayedPage,
    _occurrence_key,
    _stamp,
    _with_accepted_play,
)
from ytm_player.ui.widgets.track_table import TrackTable


def _raw(video_id: str, title: str, played: str) -> dict:
    return {
        "videoId": video_id,
        "title": title,
        "artists": [{"name": "X", "id": "1"}],
        "played": played,
    }


# One shelf per day: Song A was played today and yesterday, so it comes
# back twice. normalize_tracks() drops "played"; the rows remain.
FEED = [
    _raw("bbb", "Song B", "Today"),
    _raw("aaa", "Song A", "Today"),
    _raw("aaa", "Song A", "Yesterday"),
    _raw("ccc", "Song C", "Yesterday"),
]


class _Host(App):
    """Minimal host exposing the attributes RecentlyPlayedPage reads."""

    def __init__(self) -> None:
        super().__init__()
        self.history = MagicMock()
        self.history.get_recently_played = AsyncMock(return_value=[])
        self.ytmusic = MagicMock()
        self.ytmusic.get_history = AsyncMock(return_value=FEED)
        self._ytm_history = None
        self._ytm_history_pending = []
        self._ytm_history_pending_seq = 0

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        yield RecentlyPlayedPage(id="page", active_tab=_TAB_YTM)

    def _get_current_page(self):
        return self.query_one("#page", RecentlyPlayedPage)


def _glyphs(table: TrackTable) -> str:
    return "".join(str(table.get_row_at(i)[0]) for i in range(table.row_count))


def _ids(table: TrackTable) -> list[str]:
    return [t["video_id"] for t in table.visible_tracks]


def _keys(table: TrackTable) -> list:
    return [table.occurrence_key(i) for i in range(table.row_count)]


def _accept(host: _Host, track: dict) -> None:
    """The account accepted a play of *track* (the app's cache update)."""
    PlaybackMixin._add_to_ytm_history_cache.__get__(host)(track)


async def _loaded(host: _Host, pilot) -> tuple[RecentlyPlayedPage, TrackTable]:
    await host.workers.wait_for_complete()
    await pilot.pause()
    page = host.query_one("#page", RecentlyPlayedPage)
    table = page.query_one("#recent-table", TrackTable)
    table.focus()
    return page, table


async def _mark(page: RecentlyPlayedPage, table: TrackTable, *rows: int) -> None:
    for row in rows:
        table.move_cursor(row=row)
        await page.handle_action(Action.MARK_TOGGLE)


# ── The page ──────────────────────────────────────────────────────────


async def test_repeated_rows_stay_and_each_has_its_own_key():
    host = _Host()
    async with host.run_test(size=(100, 24)) as pilot:
        _page, table = await _loaded(host, pilot)
        assert _ids(table) == ["bbb", "aaa", "aaa", "ccc"]
        assert len(set(_keys(table))) == 4


async def test_marks_on_both_rows_of_a_track_and_the_cursor_survive_a_play_landing():
    host = _Host()
    async with host.run_test(size=(100, 24)) as pilot:
        page, table = await _loaded(host, pilot)
        await _mark(page, table, 0, 1, 2)  # Song B, Song A (today), Song A (yesterday)
        table.move_cursor(row=2)  # the second Song A
        keys = _keys(table)

        _accept(host, {"video_id": "zzz", "title": "Just played"})
        await pilot.pause()

        assert _ids(table) == ["zzz", "bbb", "aaa", "aaa", "ccc"]
        assert _glyphs(table) == " ✓✓✓ "
        assert table.cursor_row == 3
        assert _keys(table)[1:] == keys


async def test_a_play_of_a_repeated_track_moves_its_latest_row_and_keeps_the_older_one():
    host = _Host()
    async with host.run_test(size=(100, 24)) as pilot:
        page, table = await _loaded(host, pilot)
        await _mark(page, table, 1, 2)  # both Song A rows
        latest, older = _keys(table)[1], _keys(table)[2]

        _accept(host, {"video_id": "aaa", "title": "Song A (replayed)"})
        await pilot.pause()

        assert _ids(table) == ["aaa", "bbb", "aaa", "ccc"]
        assert table.visible_tracks[0]["title"] == "Song A (replayed)"
        assert _keys(table)[0] == latest
        assert _keys(table)[2] == older
        assert _glyphs(table) == "✓ ✓ "  # each mark followed its own row


async def test_a_play_from_an_older_row_still_supersedes_the_latest_one():
    """The played track carries the stamp of the row it was played from (the
    older Song A); the cache decides identity, not the incoming dict."""
    host = _Host()
    async with host.run_test(size=(100, 24)) as pilot:
        page, table = await _loaded(host, pilot)
        await _mark(page, table, 2)  # the older Song A
        latest, older = _keys(table)[1], _keys(table)[2]
        played = dict(table.visible_tracks[2])
        assert played["_occurrence"] == older

        _accept(host, played)
        await pilot.pause()

        assert _ids(table) == ["aaa", "bbb", "aaa", "ccc"]
        assert _keys(table)[0] == latest
        assert _keys(table)[2] == older
        assert _glyphs(table) == "  ✓ "  # the mark stayed on the older row


async def test_a_play_accepted_during_the_fetch_supersedes_one_row_only():
    """Parked while the fetch ran, the play goes on top and supersedes the
    feed's most recent Song A row; the older one stays."""
    host = _Host()
    release = asyncio.Event()

    async def slow_history():
        await release.wait()
        return FEED

    host.ytmusic.get_history = AsyncMock(side_effect=slow_history)
    async with host.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        _accept(host, {"video_id": "aaa", "title": "Just played"})  # parked: no cache yet
        assert host._ytm_history is None
        release.set()
        _page, table = await _loaded(host, pilot)

        assert _ids(table) == ["aaa", "bbb", "aaa", "ccc"]
        assert table.visible_tracks[0]["title"] == "Just played"
        assert len(set(_keys(table))) == 4


# ── The cache helpers ─────────────────────────────────────────────────


def _cached(video_id: str, title: str) -> dict:
    return _stamp({"video_id": video_id, "title": title})


def test_every_stamp_is_distinct_and_the_key_is_the_stamp():
    rows = [_cached("aaa", "A"), _cached("aaa", "A"), _cached("bbb", "B")]
    keys = [_occurrence_key(row) for row in rows]
    assert keys == [row["_occurrence"] for row in rows]
    assert len(set(keys)) == 3


def test_an_unstamped_row_is_keyed_by_its_video_id():
    assert _occurrence_key({"video_id": "aaa"}) == "aaa"


def test_a_play_supersedes_the_latest_row_of_the_track_only():
    latest, older = _cached("aaa", "A"), _cached("aaa", "A")
    cache = [_cached("bbb", "B"), latest, older]

    updated = _with_accepted_play(cache, {"video_id": "aaa", "title": "A again"})

    assert [row["video_id"] for row in updated] == ["aaa", "bbb", "aaa"]
    assert updated[0]["title"] == "A again"
    assert updated[0]["_occurrence"] == latest["_occurrence"]
    assert updated[2] is older
    assert cache == [cache[0], latest, older]  # the input is left alone


def test_a_play_of_a_track_not_in_the_cache_gets_a_new_stamp():
    cache = [_cached("bbb", "B")]

    updated = _with_accepted_play(cache, {"video_id": "aaa", "title": "A"})

    assert [row["video_id"] for row in updated] == ["aaa", "bbb"]
    assert updated[0]["_occurrence"] not in {row["_occurrence"] for row in cache}


def test_the_cache_decides_identity_not_the_incoming_track():
    latest, older = _cached("aaa", "A"), _cached("aaa", "A")
    stale = dict(older, title="played from the older row")

    updated = _with_accepted_play([latest, older], stale)
    assert updated[0]["_occurrence"] == latest["_occurrence"]
    assert updated[1] is older

    fresh = _with_accepted_play([], dict(older, title="cache emptied meanwhile"))
    assert fresh[0]["_occurrence"] != older["_occurrence"]
