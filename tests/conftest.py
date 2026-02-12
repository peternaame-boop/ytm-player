"""Shared test fixtures for ytm-player."""


import pytest


@pytest.fixture
def tmp_config_dir(tmp_path):
    """Create a temporary config directory."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    return config_dir


@pytest.fixture
def tmp_cache_dir(tmp_path):
    """Create a temporary cache directory."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    return cache_dir


def _make_track(
    video_id: str = "dQw4w9WgXcQ",
    title: str = "Never Gonna Give You Up",
    artist: str = "Rick Astley",
    duration: int = 213,
    album: str = "Whenever You Need Somebody",
) -> dict:
    return {
        "video_id": video_id,
        "title": title,
        "artist": artist,
        "artists": [{"name": artist, "id": "UC-9-kyTW8ZkZNDHQJ6FgpwQ"}],
        "album": album,
        "album_id": "MPREb_gTFcFi8OrIc",
        "duration": duration,
        "thumbnail_url": f"https://lh3.googleusercontent.com/video_id={video_id}",
        "is_video": False,
    }


@pytest.fixture
def sample_track() -> dict:
    return _make_track()


@pytest.fixture
def sample_tracks() -> list[dict]:
    return [
        _make_track("vid_01", "Track One", "Artist A", 180),
        _make_track("vid_02", "Track Two", "Artist B", 240),
        _make_track("vid_03", "Track Three", "Artist C", 120),
        _make_track("vid_04", "Track Four", "Artist D", 300),
        _make_track("vid_05", "Track Five", "Artist E", 200),
    ]


@pytest.fixture
def queue_manager():
    from ytm_player.services.queue import QueueManager
    return QueueManager()
