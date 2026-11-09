"""`ytm setup` tells the user when a ytm instance is running."""

from __future__ import annotations

from unittest.mock import MagicMock

from click.testing import CliRunner

from ytm_player.cli import main
from ytm_player.config.settings import Settings


def _fake_manager(monkeypatch) -> MagicMock:
    manager = MagicMock()
    manager.is_authenticated.return_value = False
    manager.setup_interactive.return_value = True
    manager.validate.return_value = True
    monkeypatch.setattr("ytm_player.cli.AuthManager", lambda **kwargs: manager)
    monkeypatch.setattr("ytm_player.cli.get_settings", lambda: Settings())
    return manager


def test_setup_says_to_restart_a_running_instance(monkeypatch):
    manager = _fake_manager(monkeypatch)
    monkeypatch.setattr("ytm_player.cli.get_running_pid", lambda: 4242)

    result = CliRunner().invoke(main, ["setup"])

    assert result.exit_code == 0, result.output
    assert "ytm is running (PID 4242). Restart it after setup" in result.output
    manager.setup_interactive.assert_called_once_with(manual=False, browser=None)


def test_setup_is_quiet_when_no_instance_is_running(monkeypatch):
    _fake_manager(monkeypatch)
    monkeypatch.setattr("ytm_player.cli.get_running_pid", lambda: None)

    result = CliRunner().invoke(main, ["setup"])

    assert result.exit_code == 0, result.output
    assert "ytm is running" not in result.output
