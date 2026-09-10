"""Tests for ytm_player.services.macos_app."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ytm_player.services import macos_app
from ytm_player.services.macos_app import hide_dock_icon


class TestHideDockIcon:
    def test_noop_off_macos(self) -> None:
        with patch.object(macos_app.sys, "platform", "linux"):
            assert hide_dock_icon() is False

    def test_noop_when_appkit_unavailable(self) -> None:
        with (
            patch.object(macos_app.sys, "platform", "darwin"),
            patch.object(macos_app, "_APPKIT_AVAILABLE", False),
        ):
            assert hide_dock_icon() is False

    def test_sets_accessory_policy_on_macos(self) -> None:
        app = MagicMock()
        appkit = MagicMock()
        appkit.NSApplication.sharedApplication.return_value = app
        with (
            patch.object(macos_app.sys, "platform", "darwin"),
            patch.object(macos_app, "_APPKIT_AVAILABLE", True),
            patch.object(macos_app, "_APPKIT", appkit),
        ):
            assert hide_dock_icon() is True
        # Accessory (1), not Prohibited (2): Prohibited would also make the
        # process ineligible as a Now Playing source.
        app.setActivationPolicy_.assert_called_once_with(1)

    def test_swallows_appkit_errors(self) -> None:
        appkit = MagicMock()
        appkit.NSApplication.sharedApplication.side_effect = RuntimeError("no window server")
        with (
            patch.object(macos_app.sys, "platform", "darwin"),
            patch.object(macos_app, "_APPKIT_AVAILABLE", True),
            patch.object(macos_app, "_APPKIT", appkit),
        ):
            assert hide_dock_icon() is False
