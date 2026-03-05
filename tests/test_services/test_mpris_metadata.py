"""Tests for MPRIS metadata sanitization (issue #9 fix)."""

import pytest


@pytest.fixture
def _player_iface_cls():
    """Import the _PlayerInterface class, skipping if dbus-next unavailable."""
    try:
        # Force the dbus-next imports so the class is defined.
        # Re-import mpris to get the class built with dbus-next present.
        import importlib

        from dbus_next import Variant  # noqa: F401

        import ytm_player.services.mpris as mpris_mod

        importlib.reload(mpris_mod)
        # The class is defined inside a try block; access via module globals.
        cls = getattr(mpris_mod, "_PlayerInterface", None)
        if cls is None:
            pytest.skip("_PlayerInterface not available")
        return cls
    except ImportError:
        pytest.skip("dbus-next not installed")


def test_set_metadata_with_none_values(_player_iface_cls):
    """Passing None for all fields should not crash — produces empty strings."""
    from dbus_next import Variant

    iface = object.__new__(_player_iface_cls)
    iface._metadata = {}
    iface.set_metadata(None, None, None, None, None)

    assert iface._metadata["xesam:title"] == Variant("s", "")
    assert iface._metadata["xesam:artist"] == Variant("as", [""])
    assert iface._metadata["xesam:album"] == Variant("s", "")
    assert iface._metadata["mpris:artUrl"] == Variant("s", "")
    assert iface._metadata["mpris:length"] == Variant("x", 0)


def test_set_metadata_with_valid_values(_player_iface_cls):
    """Normal string values pass through unchanged."""
    from dbus_next import Variant

    iface = object.__new__(_player_iface_cls)
    iface._metadata = {}
    iface.set_metadata("Title", "Artist", "Album", "http://art.url", 180_000_000)

    assert iface._metadata["xesam:title"] == Variant("s", "Title")
    assert iface._metadata["xesam:artist"] == Variant("as", ["Artist"])
    assert iface._metadata["xesam:album"] == Variant("s", "Album")
    assert iface._metadata["mpris:artUrl"] == Variant("s", "http://art.url")
    assert iface._metadata["mpris:length"] == Variant("x", 180_000_000)
