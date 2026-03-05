"""Tests for AuthManager.validate() and CLI setup error handling.

Covers the exact failure modes reported by users:
- ConnectionError during validation (Void Linux — was a crash before v1.3.0)
- Validation returns False after fresh extraction (Windows 11 / Firefox)
- Timeout during validation
- get_account_info() returns empty/missing accountName (expired credentials)
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests.exceptions
from click.testing import CliRunner

from ytm_player.services.auth import AuthManager

# ── Helpers ──────────────────────────────────────────────────────────────


def _write_auth_file(path: Path, cookie: str = "SAPISID=abc123") -> None:
    """Write a minimal valid auth JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cookie": cookie}))


def _make_auth(tmp_path: Path) -> AuthManager:
    """Create an AuthManager with a valid auth file in tmp_path."""
    auth_file = tmp_path / "headers_auth.json"
    _write_auth_file(auth_file)
    return AuthManager(auth_file=auth_file)


# ── validate() unit tests ───────────────────────────────────────────────


class TestValidate:
    """Test every branch of AuthManager.validate()."""

    def test_returns_false_when_not_authenticated(self, tmp_path):
        auth = AuthManager(auth_file=tmp_path / "nonexistent.json")
        assert auth.validate() is False

    def test_returns_false_when_auth_file_empty(self, tmp_path):
        auth_file = tmp_path / "headers_auth.json"
        auth_file.write_text("{}")
        auth = AuthManager(auth_file=auth_file)
        assert auth.validate() is False

    def test_returns_true_on_successful_validation(self, tmp_path):
        auth = _make_auth(tmp_path)
        mock_ytm = MagicMock()
        mock_ytm.get_account_info.return_value = {
            "accountName": "Test User",
            "channelHandle": "@TestUser",
            "accountPhotoUrl": "https://example.com/photo.jpg",
        }

        with patch.object(auth, "create_ytmusic_client", return_value=mock_ytm):
            assert auth.validate() is True

    def test_returns_false_when_account_name_is_none(self, tmp_path):
        """Expired or invalid credentials — server returns no account name."""
        auth = _make_auth(tmp_path)
        mock_ytm = MagicMock()
        mock_ytm.get_account_info.return_value = {
            "accountName": None,
            "channelHandle": None,
            "accountPhotoUrl": None,
        }

        with patch.object(auth, "create_ytmusic_client", return_value=mock_ytm):
            assert auth.validate() is False

    def test_returns_false_when_account_name_empty_string(self, tmp_path):
        auth = _make_auth(tmp_path)
        mock_ytm = MagicMock()
        mock_ytm.get_account_info.return_value = {"accountName": ""}

        with patch.object(auth, "create_ytmusic_client", return_value=mock_ytm):
            assert auth.validate() is False

    def test_returns_false_when_account_name_missing(self, tmp_path):
        """get_account_info() returns dict without accountName key."""
        auth = _make_auth(tmp_path)
        mock_ytm = MagicMock()
        mock_ytm.get_account_info.return_value = {}

        with patch.object(auth, "create_ytmusic_client", return_value=mock_ytm):
            assert auth.validate() is False

    def test_returns_true_with_channel_handle_none(self, tmp_path):
        """Some accounts lack a channel handle — validation should still pass."""
        auth = _make_auth(tmp_path)
        mock_ytm = MagicMock()
        mock_ytm.get_account_info.return_value = {
            "accountName": "Test User",
            "channelHandle": None,
            "accountPhotoUrl": "https://example.com/photo.jpg",
        }

        with patch.object(auth, "create_ytmusic_client", return_value=mock_ytm):
            assert auth.validate() is True

    def test_reraises_connection_error(self, tmp_path):
        auth = _make_auth(tmp_path)
        mock_ytm = MagicMock()
        mock_ytm.get_account_info.side_effect = requests.exceptions.ConnectionError(
            "Connection reset by peer"
        )

        with patch.object(auth, "create_ytmusic_client", return_value=mock_ytm):
            with pytest.raises(requests.exceptions.ConnectionError):
                auth.validate()

    def test_reraises_timeout(self, tmp_path):
        auth = _make_auth(tmp_path)
        mock_ytm = MagicMock()
        mock_ytm.get_account_info.side_effect = requests.exceptions.Timeout("timed out")

        with patch.object(auth, "create_ytmusic_client", return_value=mock_ytm):
            with pytest.raises(requests.exceptions.Timeout):
                auth.validate()

    def test_returns_false_on_generic_exception(self, tmp_path):
        """e.g. JSON decode error, ytmusicapi internal error, etc."""
        auth = _make_auth(tmp_path)
        mock_ytm = MagicMock()
        mock_ytm.get_account_info.side_effect = json.JSONDecodeError("bad json", "", 0)

        with patch.object(auth, "create_ytmusic_client", return_value=mock_ytm):
            assert auth.validate() is False

    def test_returns_false_on_runtime_error(self, tmp_path):
        """Covers ytmusicapi raising RuntimeError for malformed responses."""
        auth = _make_auth(tmp_path)
        mock_ytm = MagicMock()
        mock_ytm.get_account_info.side_effect = RuntimeError("unexpected response")

        with patch.object(auth, "create_ytmusic_client", return_value=mock_ytm):
            assert auth.validate() is False

    def test_returns_false_on_check_auth_error(self, tmp_path):
        """YTMusic._check_auth() raises if auth type is UNAUTHORIZED."""
        auth = _make_auth(tmp_path)
        mock_ytm = MagicMock()
        mock_ytm.get_account_info.side_effect = Exception(
            "Please provide authentication before using this function"
        )

        with patch.object(auth, "create_ytmusic_client", return_value=mock_ytm):
            assert auth.validate() is False


# ── CLI setup command tests ──────────────────────────────────────────────


class TestSetupCLI:
    """Test the `ytm setup` CLI command's error handling paths.

    These reproduce the exact user-reported scenarios.
    """

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def _invoke_setup(self, runner, monkeypatch, validate_side_effect):
        """Run `ytm setup` with mocked auth, returning the CLI result."""
        from ytm_player.cli import main

        mock_auth = MagicMock(spec=AuthManager)
        mock_auth.is_authenticated.return_value = False
        mock_auth.setup_interactive.return_value = True

        if isinstance(validate_side_effect, Exception):
            mock_auth.validate.side_effect = validate_side_effect
        else:
            mock_auth.validate.return_value = validate_side_effect

        monkeypatch.setattr("ytm_player.cli.AuthManager", lambda **kwargs: mock_auth)
        mock_settings = MagicMock()
        mock_settings.yt_dlp.cookies_file = None
        monkeypatch.setattr("ytm_player.cli.get_settings", lambda: mock_settings)

        return runner.invoke(main, ["setup"])

    def test_successful_validation(self, runner, monkeypatch):
        result = self._invoke_setup(runner, monkeypatch, validate_side_effect=True)
        assert result.exit_code == 0
        assert "You're all set" in result.output

    def test_validation_returns_false(self, runner, monkeypatch):
        """Windows 11 scenario: validate() returns False after extraction."""
        result = self._invoke_setup(runner, monkeypatch, validate_side_effect=False)
        assert result.exit_code == 0
        assert "cookies were saved and may still work" in result.output
        assert "ytm setup --manual" in result.output

    def test_connection_error_no_crash(self, runner, monkeypatch):
        """Void Linux scenario: ConnectionError during validation must not crash."""
        result = self._invoke_setup(
            runner,
            monkeypatch,
            validate_side_effect=requests.exceptions.ConnectionError("Connection reset by peer"),
        )
        assert result.exit_code == 0
        assert "Could not reach YouTube Music servers" in result.output
        assert "try launching `ytm`" in result.output
        # Must NOT contain a traceback
        assert "Traceback" not in result.output

    def test_timeout_no_crash(self, runner, monkeypatch):
        """Timeout during validation must not crash."""
        result = self._invoke_setup(
            runner,
            monkeypatch,
            validate_side_effect=requests.exceptions.Timeout("read timed out"),
        )
        assert result.exit_code == 0
        assert "Could not reach YouTube Music servers" in result.output
        assert "Traceback" not in result.output

    def test_setup_failure_exits_with_error(self, runner, monkeypatch):
        """If setup_interactive() itself fails, we exit before validate."""
        from ytm_player.cli import main

        mock_auth = MagicMock(spec=AuthManager)
        mock_auth.is_authenticated.return_value = False
        mock_auth.setup_interactive.return_value = False

        monkeypatch.setattr("ytm_player.cli.AuthManager", lambda **kwargs: mock_auth)
        mock_settings = MagicMock()
        mock_settings.yt_dlp.cookies_file = None
        monkeypatch.setattr("ytm_player.cli.get_settings", lambda: mock_settings)

        result = runner.invoke(main, ["setup"])
        assert result.exit_code != 0
        assert "setup failed" in result.output.lower()

    def test_reauth_prompt_decline(self, runner, monkeypatch):
        """User declines re-authentication."""
        from ytm_player.cli import main

        mock_auth = MagicMock(spec=AuthManager)
        mock_auth.is_authenticated.return_value = True

        monkeypatch.setattr("ytm_player.cli.AuthManager", lambda **kwargs: mock_auth)
        mock_settings = MagicMock()
        mock_settings.yt_dlp.cookies_file = None
        monkeypatch.setattr("ytm_player.cli.get_settings", lambda: mock_settings)

        result = runner.invoke(main, ["setup"], input="N\n")
        assert result.exit_code == 0
        assert "cancelled" in result.output.lower()
