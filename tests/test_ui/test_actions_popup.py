"""Tests for the actions popup _build_actions function."""

from ytm_player.ui.popups.actions import _build_actions


def _action_ids(item, item_type="track", in_playlist=False):
    return [a[0] for a in _build_actions(item, item_type, in_playlist=in_playlist)]


# ── go_to_album filtering ────────────────────────────────────────────


def test_go_to_album_with_string_album_no_album_id():
    """Regression: album as string must not crash (was AttributeError)."""
    item = {
        "title": "Test",
        "artists": [{"name": "A", "id": "1"}],
        "album": "Some Album Name",
    }
    assert "go_to_album" not in _action_ids(item)


def test_go_to_album_with_album_id():
    """go_to_album present when album_id exists."""
    item = {
        "title": "Test",
        "artists": [{"name": "A", "id": "1"}],
        "album": "Some Album",
        "album_id": "MPREb_abc123",
    }
    assert "go_to_album" in _action_ids(item)


def test_go_to_album_with_dict_album():
    """go_to_album present when album is a dict with 'id'."""
    item = {
        "title": "Test",
        "artists": [{"name": "A", "id": "1"}],
        "album": {"name": "Album", "id": "album123"},
    }
    assert "go_to_album" in _action_ids(item)


def test_go_to_album_dict_no_id():
    """go_to_album filtered when album dict has no 'id'."""
    item = {
        "title": "Test",
        "artists": [{"name": "A", "id": "1"}],
        "album": {"name": "Album"},
    }
    assert "go_to_album" not in _action_ids(item)


def test_go_to_album_none():
    """go_to_album filtered when album is None."""
    item = {
        "title": "Test",
        "artists": [{"name": "A", "id": "1"}],
        "album": None,
    }
    assert "go_to_album" not in _action_ids(item)


# ── go_to_artist filtering ───────────────────────────────────────────


def test_go_to_artist_no_artist_info():
    """go_to_artist filtered when no artist data."""
    item = {"title": "Test", "artists": [], "album_id": "a1"}
    assert "go_to_artist" not in _action_ids(item)


def test_go_to_artist_with_artists_list():
    """go_to_artist present when artists list is populated."""
    item = {
        "title": "Test",
        "artists": [{"name": "A", "id": "1"}],
        "album_id": "a1",
    }
    assert "go_to_artist" in _action_ids(item)


def test_go_to_artist_with_artist_string():
    """go_to_artist present when 'artist' string exists (no 'artists' list)."""
    item = {"title": "Test", "artists": [], "artist": "Someone", "album_id": "a1"}
    assert "go_to_artist" in _action_ids(item)


# ── Label swaps ───────────────────────────────────────────────────────


def test_liked_track_shows_unlike():
    item = {
        "title": "Test",
        "artists": [{"name": "A", "id": "1"}],
        "album_id": "a1",
        "likeStatus": "LIKE",
    }
    actions = dict(_build_actions(item, "track"))
    assert actions["toggle_like"] == "Unlike"


def test_unliked_track_shows_like():
    item = {
        "title": "Test",
        "artists": [{"name": "A", "id": "1"}],
        "album_id": "a1",
    }
    actions = dict(_build_actions(item, "track"))
    assert actions["toggle_like"] == "Like"


def test_subscribed_artist_shows_unsubscribe():
    item = {"name": "Artist", "subscribed": True}
    actions = dict(_build_actions(item, "artist"))
    assert actions["toggle_subscribe"] == "Unsubscribe"


# ── Item types ────────────────────────────────────────────────────────


def test_unknown_type_falls_back_to_track():
    item = {"title": "Test", "artists": [{"name": "A", "id": "1"}], "album_id": "a1"}
    ids = _action_ids(item, "unknown_type")
    assert "play" in ids
    assert "play_all" not in ids


# ── Remove from playlist filtering ────────────────────────────────────


def test_remove_from_playlist_shown_when_in_playlist():
    item = {"title": "Test", "artists": [{"name": "A", "id": "1"}], "album_id": "a1"}
    ids = _action_ids(item, "track", in_playlist=True)
    assert "remove_from_playlist" in ids


def test_remove_from_playlist_hidden_when_not_in_playlist():
    item = {"title": "Test", "artists": [{"name": "A", "id": "1"}], "album_id": "a1"}
    ids = _action_ids(item, "track", in_playlist=False)
    assert "remove_from_playlist" not in ids
