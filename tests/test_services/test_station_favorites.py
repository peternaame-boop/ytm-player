"""Unit tests for the StationFavorites JSON persistence layer."""

from __future__ import annotations

import json

import pytest

from ytm_player.services import station_favorites as sf_module
from ytm_player.services.radio_browser import Station
from ytm_player.services.station_favorites import StationFavorites


def _station(uuid: str = "u1", name: str = "Station One") -> Station:
    return Station(
        uuid=uuid,
        name=name,
        url=f"http://stream.example.com/{uuid}",
        homepage="",
        favicon="",
        country="",
        country_code="",
        language="",
        tags=["test"],
        codec="MP3",
        bitrate=128,
        votes=0,
        click_count=0,
        last_check_ok=True,
    )


@pytest.fixture(autouse=True)
def _isolate_singleton(tmp_path, monkeypatch):
    """Reset the singleton + redirect path for every test."""
    StationFavorites._instance = None
    monkeypatch.setattr(sf_module, "STATIONS_FILE", tmp_path / "stations.json")
    yield
    StationFavorites._instance = None


def test_starts_empty_when_no_file(tmp_path):
    fav = StationFavorites(tmp_path / "stations.json")
    assert fav.list() == []
    assert fav.is_favorite("u1") is False


def test_add_then_persist_round_trip(tmp_path):
    path = tmp_path / "stations.json"
    fav = StationFavorites(path)
    s = _station()

    assert fav.add(s) is True
    assert fav.is_favorite("u1") is True
    assert [x.uuid for x in fav.list()] == ["u1"]

    # Adding the same UUID is a no-op.
    assert fav.add(s) is False

    # On-disk JSON is a list of station dicts.
    on_disk = json.loads(path.read_text())
    assert isinstance(on_disk, list) and len(on_disk) == 1
    assert on_disk[0]["uuid"] == "u1"


def test_reload_restores_entries(tmp_path):
    path = tmp_path / "stations.json"
    fav1 = StationFavorites(path)
    fav1.add(_station("u1", "One"))
    fav1.add(_station("u2", "Two"))

    # New singleton instance must see the same entries.
    StationFavorites._instance = None
    fav2 = StationFavorites(path)
    assert {s.uuid for s in fav2.list()} == {"u1", "u2"}


def test_remove(tmp_path):
    fav = StationFavorites(tmp_path / "stations.json")
    fav.add(_station("u1"))
    fav.add(_station("u2"))

    assert fav.remove("missing") is False  # nothing to remove
    assert fav.remove("u1") is True
    assert {s.uuid for s in fav.list()} == {"u2"}


def test_toggle_returns_new_state(tmp_path):
    fav = StationFavorites(tmp_path / "stations.json")
    s = _station()

    assert fav.toggle(s) is True  # added
    assert fav.is_favorite("u1") is True

    assert fav.toggle(s) is False  # removed
    assert fav.is_favorite("u1") is False


def test_malformed_disk_data_does_not_crash(tmp_path):
    """A corrupt stations.json should result in an empty list, not a traceback."""
    path = tmp_path / "stations.json"
    path.write_text("{not a list: definitely}")
    fav = StationFavorites(path)
    assert fav.list() == []


def test_partial_entries_dropped_quietly(tmp_path):
    """Entries with bad shapes are skipped; good ones survive."""
    path = tmp_path / "stations.json"
    payload = [
        {"uuid": "u1", "name": "Good", "url": "http://good"},
        "not-an-object",  # type-wise hostile
        {"uuid": "u2", "name": "Also Good", "url": "http://also"},
    ]
    path.write_text(json.dumps(payload))
    fav = StationFavorites(path)
    assert {s.uuid for s in fav.list()} == {"u1", "u2"}
