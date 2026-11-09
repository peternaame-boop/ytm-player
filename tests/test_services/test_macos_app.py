"""Tests for ytm_player.services.macos_app."""

from __future__ import annotations

import logging
import threading
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
        app.setActivationPolicy_.return_value = True
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

    def test_rejected_policy_change_returns_false_and_warns(self, caplog) -> None:
        # setActivationPolicy: "true if the policy switch succeeded;
        # otherwise, false" -- a refusal must not be reported as applied.
        app = MagicMock()
        app.setActivationPolicy_.return_value = False
        appkit = MagicMock()
        appkit.NSApplication.sharedApplication.return_value = app
        with (
            patch.object(macos_app.sys, "platform", "darwin"),
            patch.object(macos_app, "_APPKIT_AVAILABLE", True),
            patch.object(macos_app, "_APPKIT", appkit),
            caplog.at_level(logging.WARNING, logger="ytm_player.services.macos_app"),
        ):
            assert hide_dock_icon() is False
        app.setActivationPolicy_.assert_called_once_with(1)
        assert "refused" in caplog.text

    def test_off_main_thread_does_not_touch_appkit(self) -> None:
        appkit = MagicMock()
        results: list[bool] = []
        with (
            patch.object(macos_app.sys, "platform", "darwin"),
            patch.object(macos_app, "_APPKIT_AVAILABLE", True),
            patch.object(macos_app, "_APPKIT", appkit),
        ):
            worker = threading.Thread(target=lambda: results.append(hide_dock_icon()))
            worker.start()
            worker.join()
        assert results == [False]
        appkit.NSApplication.sharedApplication.assert_not_called()

    def test_swallows_appkit_errors(self) -> None:
        appkit = MagicMock()
        appkit.NSApplication.sharedApplication.side_effect = RuntimeError("no window server")
        with (
            patch.object(macos_app.sys, "platform", "darwin"),
            patch.object(macos_app, "_APPKIT_AVAILABLE", True),
            patch.object(macos_app, "_APPKIT", appkit),
        ):
            assert hide_dock_icon() is False
