"""Tests for the Spotify import service (Task T16).

``spotify_import`` shipped two real regressions historically (v1.8.0:
``_get_video_id`` ImportError; a batch-count off-by-99) yet had zero tests.
These cover the pure logic functions — credential I/O, URL/item parsing,
fuzzy scoring, per-track search/scoring, ordered match aggregation, the
spotipy/scraper extraction paths, and the confirmed/skipped accounting that
``run_import`` uses to report results.

The interactive ``run_import`` prompt loop itself (click.prompt / rich Console)
is NOT exercised — only the ``_summarize_results`` accounting helper extracted
from it.

Everything is mocked: ytmusicapi is a ``MagicMock``; ``spotipy`` and
``spotify_scraper`` are injected as fake ``sys.modules`` entries so the tests
run even when those optional packages are not installed. No test hits the
network.
"""

from __future__ import annotations

import io
import json
import time
import types
from difflib import SequenceMatcher
from unittest.mock import MagicMock

import pytest

from ytm_player.services import spotify_import as si


@pytest.fixture(autouse=True)
def _fake_fuzz(monkeypatch):
    """Deterministic stand-in for thefuzz, which is a [spotify]-extra dep.

    CI installs only .[dev], so ``si.fuzz`` may be unbound there
    (``raising=False``). ``_fuzzy_score`` uses nothing but ``fuzz.ratio``,
    and SequenceMatcher has the same shape — 100 for identical strings,
    near-0 for disjoint — so the weighting assertions hold in every
    environment, real thefuzz installed or not.
    """

    class _Fuzz:
        @staticmethod
        def ratio(a: str, b: str) -> int:
            return int(round(SequenceMatcher(None, a, b).ratio() * 100))

    monkeypatch.setattr(si, "fuzz", _Fuzz, raising=False)


# ── helpers ───────────────────────────────────────────────────────────


def _sp_track(name: str = "Song A", artist: str = "Artist A") -> dict:
    """A Spotify-side track dict in this module's internal format."""
    return {"name": name, "artist": artist, "album": "Album A", "duration_ms": 200_000}


def _ytm_candidate(
    title: str = "Song A",
    artist: str = "Artist A",
    duration: int = 200,
    video_id: str = "vid00000001",
    result_type: str = "song",
) -> dict:
    """A YouTube Music search-result candidate (artists-list shape)."""
    return {
        "title": title,
        "artists": [{"name": artist}],
        "duration_seconds": duration,
        "videoId": video_id,
        "resultType": result_type,
    }


def _spotipy_item(name: str, artist: str, album: str = "Al", ms: int = 1000) -> dict:
    """A spotipy playlist item (track nested under 'track')."""
    return {
        "track": {
            "name": name,
            "artists": [{"name": artist}],
            "album": {"name": album},
            "duration_ms": ms,
        }
    }


def _make_result(selected: dict | None, match_type=None) -> si.MatchResult:
    return si.MatchResult(
        spotify_track=_sp_track(),
        match_type=match_type or si.MatchType.EXACT,
        selected=selected,
    )


# ── credential helpers ────────────────────────────────────────────────


class TestCredentialHelpers:
    def test_load_returns_none_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(si, "SPOTIFY_CREDS_FILE", tmp_path / "nope.json")
        assert si.load_spotify_creds() is None

    def test_load_returns_dict_when_valid(self, tmp_path, monkeypatch):
        f = tmp_path / "spotify.json"
        f.write_text(json.dumps({"client_id": "cid", "client_secret": "sec"}), encoding="utf-8")
        monkeypatch.setattr(si, "SPOTIFY_CREDS_FILE", f)
        creds = si.load_spotify_creds()
        assert creds == {"client_id": "cid", "client_secret": "sec"}

    def test_load_returns_none_on_malformed_json(self, tmp_path, monkeypatch):
        f = tmp_path / "spotify.json"
        f.write_text("{ this is not json", encoding="utf-8")
        monkeypatch.setattr(si, "SPOTIFY_CREDS_FILE", f)
        assert si.load_spotify_creds() is None

    def test_load_returns_none_when_secret_missing(self, tmp_path, monkeypatch):
        f = tmp_path / "spotify.json"
        f.write_text(json.dumps({"client_id": "cid"}), encoding="utf-8")
        monkeypatch.setattr(si, "SPOTIFY_CREDS_FILE", f)
        assert si.load_spotify_creds() is None

    def test_save_then_load_round_trips(self, tmp_path, monkeypatch):
        f = tmp_path / "config" / "spotify.json"
        monkeypatch.setattr(si, "SPOTIFY_CREDS_FILE", f)
        monkeypatch.setattr(si, "CONFIG_DIR", tmp_path / "config")
        si.save_spotify_creds("my-id", "my-secret")
        assert f.exists()
        assert si.load_spotify_creds() == {"client_id": "my-id", "client_secret": "my-secret"}

    def test_has_spotify_creds_reflects_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr(si, "SPOTIFY_CREDS_FILE", tmp_path / "missing.json")
        assert si.has_spotify_creds() is False

        f = tmp_path / "spotify.json"
        f.write_text(json.dumps({"client_id": "c", "client_secret": "s"}), encoding="utf-8")
        monkeypatch.setattr(si, "SPOTIFY_CREDS_FILE", f)
        assert si.has_spotify_creds() is True


# ── URL / item parsing ────────────────────────────────────────────────


class TestParsing:
    def test_extract_playlist_id_from_playlist_url(self):
        url = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
        assert si._extract_playlist_id(url) == "37i9dQZF1DXcBWIGoYBM5M"

    def test_extract_playlist_id_from_album_url(self):
        url = "https://open.spotify.com/album/1DFixLWuPkv3KT3TnV35m3?si=x"
        assert si._extract_playlist_id(url) == "1DFixLWuPkv3KT3TnV35m3"

    def test_extract_playlist_id_returns_empty_on_garbage(self):
        assert si._extract_playlist_id("https://example.com/nothing") == ""

    def test_parse_spotipy_item_nested_track(self):
        item = _spotipy_item("Title", "Artist", album="TheAlbum", ms=180_000)
        parsed = si._parse_spotipy_item(item)
        assert parsed == {
            "name": "Title",
            "artist": "Artist",
            "album": "TheAlbum",
            "duration_ms": 180_000,
        }

    def test_parse_spotipy_item_direct_track(self):
        # Some payloads have no "track" wrapper (album tracks endpoint).
        item = {
            "name": "Direct",
            "artists": [{"name": "A"}],
            "album": {"name": "Al"},
            "duration_ms": 5,
        }
        parsed = si._parse_spotipy_item(item)
        assert parsed["name"] == "Direct"
        assert parsed["artist"] == "A"

    def test_parse_spotipy_item_empty_when_no_name(self):
        assert si._parse_spotipy_item({"track": {"name": ""}}) == {}
        assert si._parse_spotipy_item({"track": None}) == {}

    def test_parse_spotipy_item_album_not_dict(self):
        item = {"track": {"name": "N", "artists": [{"name": "A"}], "album": None}}
        parsed = si._parse_spotipy_item(item)
        assert parsed["album"] == ""


# ── fuzzy scoring ─────────────────────────────────────────────────────


class TestFuzzyScore:
    def test_identical_title_and_artist_scores_max(self):
        score = si._fuzzy_score(
            _sp_track("Bohemian Rhapsody", "Queen"), _ytm_candidate("Bohemian Rhapsody", "Queen")
        )
        assert score == 100

    def test_title_weighted_more_than_artist(self):
        # Perfect title, wrong artist vs wrong title, perfect artist.
        title_only = si._fuzzy_score(
            _sp_track("Exactly This Title", "Right Artist"),
            _ytm_candidate("Exactly This Title", "zzzzzzzzzzzz"),
        )
        artist_only = si._fuzzy_score(
            _sp_track("Exactly This Title", "Right Artist"),
            _ytm_candidate("qqqqqqqqqqqq", "Right Artist"),
        )
        assert title_only > artist_only, "title weight (0.6) must dominate artist (0.4)"

    def test_disjoint_strings_score_low(self):
        score = si._fuzzy_score(
            _sp_track("aaaaaaaa", "bbbbbbbb"),
            _ytm_candidate("wxyzwxyz", "qpqpqpqp"),
        )
        assert score < si.AUTO_MATCH_THRESHOLD

    def test_missing_ytm_title_does_not_raise(self):
        # ytm_track with None title must be coerced, not crash.
        score = si._fuzzy_score(_sp_track("X", "Y"), {"title": None, "artists": []})
        assert isinstance(score, int)


# ── _search_and_score ─────────────────────────────────────────────────


class TestSearchAndScore:
    def test_no_results_yields_none_match(self):
        ytm = MagicMock()
        ytm.search.return_value = []
        idx, result = si._search_and_score(ytm, _sp_track(), 3)
        assert idx == 3
        assert result.match_type is si.MatchType.NONE
        assert result.selected is None

    def test_search_exception_is_treated_as_no_results(self):
        ytm = MagicMock()
        ytm.search.side_effect = RuntimeError("network boom")
        idx, result = si._search_and_score(ytm, _sp_track(), 0)
        assert result.match_type is si.MatchType.NONE

    def test_high_score_yields_exact_with_selection(self):
        ytm = MagicMock()
        ytm.search.return_value = [_ytm_candidate("Song A", "Artist A", video_id="perfect")]
        _, result = si._search_and_score(ytm, _sp_track("Song A", "Artist A"), 0)
        assert result.match_type is si.MatchType.EXACT
        assert result.selected is not None
        assert result.selected["videoId"] == "perfect"

    def test_low_score_yields_multiple_without_selection(self):
        ytm = MagicMock()
        ytm.search.return_value = [_ytm_candidate("nothing alike", "different too")]
        _, result = si._search_and_score(ytm, _sp_track("Song A", "Artist A"), 0)
        assert result.match_type is si.MatchType.MULTIPLE
        assert result.selected is None
        assert len(result.candidates) == 1

    def test_candidates_sorted_best_first(self):
        ytm = MagicMock()
        ytm.search.return_value = [
            _ytm_candidate("totally wrong", "totally wrong", video_id="bad"),
            _ytm_candidate("Song A", "Artist A", video_id="good"),
        ]
        _, result = si._search_and_score(ytm, _sp_track("Song A", "Artist A"), 0)
        assert result.match_type is si.MatchType.EXACT
        assert result.candidates[0]["videoId"] == "good"
        assert result.selected["videoId"] == "good"

    def test_query_combines_name_and_artist(self):
        ytm = MagicMock()
        ytm.search.return_value = []
        si._search_and_score(ytm, _sp_track("My Song", "My Artist"), 0)
        called_query = ytm.search.call_args[0][0]
        assert "My Song" in called_query and "My Artist" in called_query


# ── match_tracks (aggregation + ordering) ─────────────────────────────


def _quiet_console():
    from rich.console import Console

    return Console(file=io.StringIO(), force_terminal=False)


class TestMatchTracks:
    def test_returns_empty_when_deps_missing(self, monkeypatch):
        monkeypatch.setattr(si, "_HAS_SPOTIFY_DEPS", False)
        assert si.match_tracks(MagicMock(), [_sp_track()], MagicMock()) == []

    def test_preserves_input_order(self, monkeypatch):
        monkeypatch.setattr(si, "_HAS_SPOTIFY_DEPS", True)
        tracks = [_sp_track(f"Song {i}", f"Artist {i}") for i in range(6)]

        ytm = MagicMock()

        def fake_search(query, **_kw):
            # Echo back a candidate whose title matches the queried song.
            # Earlier tracks sleep LONGER so thread completion order is the
            # reverse of input order — the slot-by-index logic must still
            # return results in input order.
            name = query.rsplit(" ", 2)[0]
            idx = int(name.split()[-1])
            time.sleep((len(tracks) - idx) * 0.01)
            return [_ytm_candidate(name, "x")]

        ytm.search.side_effect = fake_search
        results = si.match_tracks(ytm, tracks, _quiet_console())

        assert len(results) == len(tracks)
        assert [r.spotify_track["name"] for r in results] == [t["name"] for t in tracks]

    def test_categorizes_matches(self, monkeypatch):
        monkeypatch.setattr(si, "_HAS_SPOTIFY_DEPS", True)
        tracks = [_sp_track("Hit", "Star"), _sp_track("Miss", "Nobody")]

        ytm = MagicMock()

        def fake_search(query, **_kw):
            if query.startswith("Hit"):
                return [_ytm_candidate("Hit", "Star")]
            return []  # "Miss" finds nothing

        ytm.search.side_effect = fake_search
        results = si.match_tracks(ytm, tracks, _quiet_console())
        by_name = {r.spotify_track["name"]: r for r in results}
        assert by_name["Hit"].match_type is si.MatchType.EXACT
        assert by_name["Miss"].match_type is si.MatchType.NONE


# ── _display_candidate ────────────────────────────────────────────────


class TestDisplayCandidate:
    def test_formats_title_artist_and_duration(self):
        line = si._display_candidate(1, _ytm_candidate("Track", "Band", duration=125))
        assert "1." in line and "Track" in line and "Band" in line and "2:05" in line

    def test_unknown_duration_renders_question_mark(self):
        cand = {"title": "T", "artists": [{"name": "A"}]}
        line = si._display_candidate(2, cand)
        assert "(?)" in line

    def test_non_song_result_type_gets_suffix(self):
        line = si._display_candidate(1, _ytm_candidate(result_type="video"))
        assert "[video]" in line

    def test_song_result_type_has_no_suffix(self):
        line = si._display_candidate(1, _ytm_candidate(result_type="song"))
        assert "[song]" not in line


# ── _summarize_results (accounting — off-by-99 / partial-failure) ─────


class TestSummarizeResults:
    def test_empty_results(self):
        confirmed, video_ids, skipped = si._summarize_results([])
        assert confirmed == [] and video_ids == [] and skipped == 0

    def test_all_confirmed(self):
        results = [_make_result({"videoId": f"v{i}"}) for i in range(3)]
        confirmed, video_ids, skipped = si._summarize_results(results)
        assert len(confirmed) == 3
        assert video_ids == ["v0", "v1", "v2"]
        assert skipped == 0

    def test_unselected_counted_as_skipped(self):
        results = [
            _make_result({"videoId": "keep"}),
            _make_result(None, si.MatchType.NONE),
            _make_result(None, si.MatchType.NONE),
        ]
        confirmed, video_ids, skipped = si._summarize_results(results)
        assert video_ids == ["keep"]
        assert skipped == 2

    def test_confirmed_with_empty_video_id_dropped_but_not_skipped(self):
        # Manual video-ID entry could leave an empty id: it drops out of the
        # playable list but is NOT double-counted as a skip.
        results = [
            _make_result({"videoId": "good"}),
            _make_result({"videoId": ""}),
        ]
        confirmed, video_ids, skipped = si._summarize_results(results)
        assert video_ids == ["good"]
        assert len(confirmed) == 2
        assert skipped == 0

    def test_one_skip_out_of_hundred_is_not_off_by_99(self):
        # Guards the historical off-by-99: 100 tracks, exactly one unmatched.
        results = [_make_result({"videoId": f"v{i}"}) for i in range(99)]
        results.append(_make_result(None, si.MatchType.NONE))
        confirmed, video_ids, skipped = si._summarize_results(results)
        assert len(video_ids) == 99, "must add 99, not 1"
        assert skipped == 1, "must skip 1, not 99"

    def test_reads_both_videoid_key_conventions(self):
        results = [
            _make_result({"videoId": "camel"}),
            _make_result({"video_id": "snake"}),
        ]
        _, video_ids, _ = si._summarize_results(results)
        assert video_ids == ["camel", "snake"]


# ── extract_spotify_tracks_spotipy (Web API path) ─────────────────────


@pytest.fixture
def fake_spotipy(monkeypatch):
    """Inject fake ``spotipy`` + ``spotipy.oauth2`` modules.

    Returns the ``sp`` MagicMock so tests can set ``.playlist`` / ``.album``
    / ``.next`` return values. ``SpotifyClientCredentials`` is a MagicMock so
    call args can be asserted.
    """
    sp = MagicMock(name="spotify-client")
    creds_ctor = MagicMock(name="SpotifyClientCredentials", return_value="auth")

    mod = types.ModuleType("spotipy")
    mod.Spotify = MagicMock(return_value=sp)  # type: ignore[attr-defined]
    oauth = types.ModuleType("spotipy.oauth2")
    oauth.SpotifyClientCredentials = creds_ctor  # type: ignore[attr-defined]
    mod.oauth2 = oauth  # type: ignore[attr-defined]

    monkeypatch.setitem(__import__("sys").modules, "spotipy", mod)
    monkeypatch.setitem(__import__("sys").modules, "spotipy.oauth2", oauth)

    sp._creds_ctor = creds_ctor  # stash for assertions
    sp._Spotify = mod.Spotify
    return sp


class TestExtractSpotipy:
    def test_raises_without_credentials(self, fake_spotipy, monkeypatch):
        monkeypatch.setattr(si, "load_spotify_creds", lambda: None)
        with pytest.raises(RuntimeError, match="credentials not configured"):
            si.extract_spotify_tracks_spotipy("https://open.spotify.com/playlist/abc")

    def test_raises_on_unparseable_url(self, fake_spotipy, monkeypatch):
        monkeypatch.setattr(
            si, "load_spotify_creds", lambda: {"client_id": "c", "client_secret": "s"}
        )
        with pytest.raises(RuntimeError, match="Could not parse playlist ID"):
            si.extract_spotify_tracks_spotipy("https://open.spotify.com/foo/")

    def test_playlist_with_pagination(self, fake_spotipy, monkeypatch):
        monkeypatch.setattr(
            si, "load_spotify_creds", lambda: {"client_id": "c", "client_secret": "s"}
        )
        page1 = {"items": [_spotipy_item("S1", "A1"), _spotipy_item("S2", "A2")], "next": "url2"}
        page2 = {"items": [_spotipy_item("S3", "A3")], "next": None}
        fake_spotipy.playlist.return_value = {"name": "My Playlist", "tracks": page1}
        fake_spotipy.next.return_value = page2

        name, tracks = si.extract_spotify_tracks_spotipy(
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
        )
        assert name == "My Playlist"
        assert [t["name"] for t in tracks] == ["S1", "S2", "S3"]
        fake_spotipy.next.assert_called_once_with(page1)
        fake_spotipy._creds_ctor.assert_called_once_with(client_id="c", client_secret="s")

    def test_album_path(self, fake_spotipy, monkeypatch):
        monkeypatch.setattr(
            si, "load_spotify_creds", lambda: {"client_id": "c", "client_secret": "s"}
        )
        fake_spotipy.album.return_value = {
            "name": "My Album",
            "tracks": {"items": [_spotipy_item("T1", "A1")], "next": None},
        }
        name, tracks = si.extract_spotify_tracks_spotipy(
            "https://open.spotify.com/album/1DFixLWuPkv3KT3TnV35m3"
        )
        assert name == "My Album"
        assert [t["name"] for t in tracks] == ["T1"]
        fake_spotipy.album.assert_called_once()

    def test_raises_when_album_api_returns_none(self, fake_spotipy, monkeypatch):
        monkeypatch.setattr(
            si, "load_spotify_creds", lambda: {"client_id": "c", "client_secret": "s"}
        )
        fake_spotipy.album.return_value = None
        with pytest.raises(RuntimeError, match="no data for album"):
            si.extract_spotify_tracks_spotipy("https://open.spotify.com/album/abc123")


# ── extract_spotify_tracks (dispatcher + scraper fallback) ────────────


def _install_fake_scraper(monkeypatch, playlist_info):
    """Inject a fake ``spotify_scraper`` module; return the client mock."""
    client = MagicMock(name="scraper-client")
    client.get_playlist_info.return_value = playlist_info
    mod = types.ModuleType("spotify_scraper")
    mod.SpotifyClient = MagicMock(return_value=client)  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "spotify_scraper", mod)
    return client


class TestExtractDispatcher:
    def test_uses_spotipy_when_creds_present(self, monkeypatch):
        monkeypatch.setattr(si, "has_spotify_creds", lambda: True)
        monkeypatch.setattr(
            si, "extract_spotify_tracks_spotipy", lambda url: ("Full PL", [{"name": "x"}])
        )
        name, tracks = si.extract_spotify_tracks("https://open.spotify.com/playlist/x")
        assert name == "Full PL"
        assert tracks == [{"name": "x"}]

    def test_falls_back_to_scraper_when_spotipy_fails(self, monkeypatch):
        monkeypatch.setattr(si, "has_spotify_creds", lambda: True)

        def boom(url):
            raise RuntimeError("spotipy exploded")

        monkeypatch.setattr(si, "extract_spotify_tracks_spotipy", boom)
        client = _install_fake_scraper(
            monkeypatch,
            {
                "name": "Scraped",
                "track_count": 2,
                "tracks": [
                    _spotipy_item("S1", "A1"),
                    {"name": "S2", "artists": [{"name": "A2"}], "album": {"name": "Al"}},
                ],
            },
        )
        name, tracks = si.extract_spotify_tracks("https://open.spotify.com/playlist/x")
        assert name == "Scraped"
        assert [t["name"] for t in tracks] == ["S1", "S2"]
        client.close.assert_called_once()

    def test_scraper_used_directly_without_creds(self, monkeypatch):
        monkeypatch.setattr(si, "has_spotify_creds", lambda: False)
        # If spotipy were consulted this would blow up; assert it is not.
        monkeypatch.setattr(
            si,
            "extract_spotify_tracks_spotipy",
            MagicMock(side_effect=AssertionError("spotipy must not be called")),
        )
        client = _install_fake_scraper(
            monkeypatch, {"name": "S", "track_count": 1, "tracks": [_spotipy_item("Only", "A")]}
        )
        name, tracks = si.extract_spotify_tracks("https://open.spotify.com/playlist/x")
        assert name == "S"
        assert [t["name"] for t in tracks] == ["Only"]
        client.close.assert_called_once()

    def test_truncation_is_logged(self, monkeypatch, caplog):
        import logging

        monkeypatch.setattr(si, "has_spotify_creds", lambda: False)
        _install_fake_scraper(
            monkeypatch,
            {"name": "Big", "track_count": 500, "tracks": [_spotipy_item("One", "A")]},
        )
        with caplog.at_level(logging.WARNING, logger=si.logger.name):
            si.extract_spotify_tracks("https://open.spotify.com/playlist/x")
        assert any("limit" in rec.message.lower() for rec in caplog.records)
