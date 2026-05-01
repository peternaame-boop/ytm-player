"""Tests for MPRIS metadata sanitization (issue #9 fix)."""

import pytest


@pytest.fixture
def _player_iface():
    """Create a _PlayerInterface instance, skipping if dbus-fast unavailable."""
    try:
        import importlib

        from dbus_fast import Variant  # noqa: F401

        import ytm_player.services.mpris as mpris_mod

        importlib.reload(mpris_mod)
        cls = getattr(mpris_mod, "_PlayerInterface", None)
        if cls is None:
            pytest.skip("_PlayerInterface not available")
        return cls(callbacks={})
    except ImportError:
        pytest.skip("dbus-fast not installed")


def test_set_metadata_with_none_values(_player_iface):
    """Passing None for all fields should not crash — produces empty strings."""
    from dbus_fast import Variant

    _player_iface._metadata = {}
    _player_iface.set_metadata(None, None, None, None, None)

    assert _player_iface._metadata["xesam:title"] == Variant("s", "")
    assert _player_iface._metadata["xesam:artist"] == Variant("as", [""])
    assert _player_iface._metadata["xesam:album"] == Variant("s", "")
    assert _player_iface._metadata["mpris:artUrl"] == Variant("s", "")
    assert _player_iface._metadata["mpris:length"] == Variant("x", 0)


def test_set_metadata_with_valid_values(_player_iface):
    """Normal string values pass through unchanged."""
    from dbus_fast import Variant

    _player_iface._metadata = {}
    _player_iface.set_metadata("Title", "Artist", "Album", "http://art.url", 180_000_000)

    assert _player_iface._metadata["xesam:title"] == Variant("s", "Title")
    assert _player_iface._metadata["xesam:artist"] == Variant("as", ["Artist"])
    assert _player_iface._metadata["xesam:album"] == Variant("s", "Album")
    assert _player_iface._metadata["mpris:artUrl"] == Variant("s", "http://art.url")
    assert _player_iface._metadata["mpris:length"] == Variant("x", 180_000_000)
