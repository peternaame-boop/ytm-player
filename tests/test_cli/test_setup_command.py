"""`ytm setup`: the running-instance notice, --slots, and repairing a damaged auth.json."""

from __future__ import annotations

import hashlib
import json
from unittest.mock import MagicMock

import pytest
import ytmusicapi
from click.testing import CliRunner

from ytm_player.cli import main
from ytm_player.config.settings import Settings
from ytm_player.services.auth import DEFAULT_ACCOUNT_PROBE_SLOTS, AuthManager, _ProbedAccount


def _fake_manager(monkeypatch) -> MagicMock:
    manager = MagicMock()
    manager.is_authenticated.return_value = False
    manager.setup_interactive.return_value = True
    manager.validate.return_value = True
    monkeypatch.setattr("ytm_player.services.auth.AuthManager", lambda **kwargs: manager)
    monkeypatch.setattr("ytm_player.cli.get_settings", lambda: Settings())
    return manager


def test_setup_says_to_restart_a_running_instance(monkeypatch):
    manager = _fake_manager(monkeypatch)
    monkeypatch.setattr("ytm_player.cli.get_running_pid", lambda: 4242)

    result = CliRunner().invoke(main, ["setup"])

    assert result.exit_code == 0, result.output
    assert "ytm is running (PID 4242). Restart it after setup" in result.output
    manager.setup_interactive.assert_called_once_with(
        manual=False, browser=None, slots=DEFAULT_ACCOUNT_PROBE_SLOTS
    )


def test_setup_is_quiet_when_no_instance_is_running(monkeypatch):
    _fake_manager(monkeypatch)
    monkeypatch.setattr("ytm_player.cli.get_running_pid", lambda: None)

    result = CliRunner().invoke(main, ["setup"])

    assert result.exit_code == 0, result.output
    assert "ytm is running" not in result.output


def test_setup_passes_custom_slots(monkeypatch):
    manager = _fake_manager(monkeypatch)
    monkeypatch.setattr("ytm_player.cli.get_running_pid", lambda: None)

    result = CliRunner().invoke(main, ["setup", "--slots", "15"])

    assert result.exit_code == 0, result.output
    manager.setup_interactive.assert_called_once_with(manual=False, browser=None, slots=15)


@pytest.mark.parametrize("invalid_slot", ["0", "-1", "31", "abc"])
def test_setup_rejects_invalid_slots(monkeypatch, invalid_slot):
    manager = _fake_manager(monkeypatch)
    result = CliRunner().invoke(main, ["setup", "--slots", invalid_slot])
    assert result.exit_code != 0
    manager.setup_interactive.assert_not_called()


@pytest.mark.parametrize("valid_slot", [1, 30])
def test_setup_accepts_boundary_slots(monkeypatch, valid_slot):
    manager = _fake_manager(monkeypatch)
    monkeypatch.setattr("ytm_player.cli.get_running_pid", lambda: None)

    result = CliRunner().invoke(main, ["setup", "--slots", str(valid_slot)])

    assert result.exit_code == 0, result.output
    manager.setup_interactive.assert_called_once_with(manual=False, browser=None, slots=valid_slot)


@pytest.mark.parametrize(
    "damaged",
    [
        b"[]",
        b"\xff\xfe{}",
        b"[" * 1200 + b"]" * 1200,
        b'{"x-goog-authuser": 1e999}',
    ],
    ids=["list", "invalid-utf8", "deep-nesting", "huge-slot"],
)
def test_manual_setup_replaces_a_damaged_auth_file(monkeypatch, tmp_path, damaged):
    """`ytm setup --manual` is the repair for a damaged auth.json: it must not
    crash on the old file, ask to re-authenticate, or keep the old bytes. No
    account.json exists, so the legacy read of the old file runs too."""
    cfg = tmp_path / "config"
    cfg.mkdir()
    manager = AuthManager(
        config_dir=cfg,
        auth_file=cfg / "auth.json",
        cookies_file=None,
        stream_cookies_file=cfg / "stream_cookies.txt",
        account_file=cfg / "account.json",
    )
    manager.auth_file.write_bytes(damaged)
    pasted = {"cookie": "SAPISID=new; __Secure-3PAPISID=new", "x-goog-authuser": "0"}
    account = _ProbedAccount(slot=0, name="Synthetic", handle="", channel_id="UC" + "n" * 22)
    monkeypatch.setattr(ytmusicapi, "setup", lambda **kw: json.dumps(pasted))
    monkeypatch.setattr(manager, "_probe_payload", lambda payload, slot: account)
    monkeypatch.setattr(manager, "validate", lambda: True)
    monkeypatch.setattr("ytm_player.services.auth.AuthManager", lambda **kwargs: manager)
    monkeypatch.setattr("ytm_player.cli.get_settings", lambda: Settings())
    monkeypatch.setattr("ytm_player.cli.get_running_pid", lambda: None)
    monkeypatch.setattr("ytm_player.cli.ensure_dirs", lambda: None)  # keep the user's dirs

    result = CliRunner().invoke(main, ["setup", "--manual"], input="cookie: whatever\n\n")

    assert result.exit_code == 0, (result.output, result.exception)
    assert "re-authenticate" not in result.output
    new_bytes = manager.auth_file.read_bytes()
    assert json.loads(new_bytes)["cookie"] == pasted["cookie"]
    record = json.loads((cfg / "account.json").read_text(encoding="utf-8"))
    assert record["auth_sha256"] == hashlib.sha256(new_bytes).hexdigest()
