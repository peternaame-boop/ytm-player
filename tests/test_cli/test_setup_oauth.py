"""Tests for `ytm setup --oauth`."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from ytm_player.cli import _setup_oauth_cli, main
from ytm_player.services.auth import AuthManager

# ── _setup_oauth_cli unit tests ───────────────────────────────────────────


class TestSetupOAuthCLI:
    def _mock_auth(self, saved_creds=None) -> MagicMock:
        auth = MagicMock(spec=AuthManager)
        auth.load_oauth_creds.return_value = saved_creds
        return auth

    def test_reuses_saved_creds_without_prompting(self):
        auth = self._mock_auth(saved_creds={"client_id": "id123", "client_secret": "secret456"})
        auth.oauth_start.return_value = (
            MagicMock(),
            {
                "device_code": "dev-code",
                "user_code": "ABCD-1234",
                "verification_url": "https://www.google.com/device",
                "interval": 0,
                "expires_in": 60,
            },
        )
        token = MagicMock()
        auth.oauth_poll.return_value = token
        auth.save_oauth.return_value = True

        with (
            patch("ytm_player.cli.click.prompt") as mock_prompt,
            patch("ytm_player.cli.time.sleep"),
        ):
            result = _setup_oauth_cli(auth, None, None)

        assert result is True
        mock_prompt.assert_not_called()
        auth.oauth_start.assert_called_once_with("id123", "secret456")
        auth.save_oauth.assert_called_once_with("id123", "secret456", token)

    def test_prompts_when_nothing_saved(self):
        auth = self._mock_auth(saved_creds=None)
        auth.oauth_start.return_value = (
            MagicMock(),
            {
                "device_code": "dev-code",
                "user_code": "ABCD-1234",
                "verification_url": "https://www.google.com/device",
                "interval": 0,
                "expires_in": 60,
            },
        )
        auth.oauth_poll.return_value = MagicMock()
        auth.save_oauth.return_value = True

        with (
            patch("ytm_player.cli.click.prompt", side_effect=["typed-id", "typed-secret"]),
            patch("ytm_player.cli.time.sleep"),
        ):
            result = _setup_oauth_cli(auth, None, None)

        assert result is True
        auth.oauth_start.assert_called_once_with("typed-id", "typed-secret")

    def test_explicit_flags_skip_prompt_and_saved_creds(self):
        auth = self._mock_auth(
            saved_creds={"client_id": "saved-id", "client_secret": "saved-secret"}
        )
        auth.oauth_start.return_value = (
            MagicMock(),
            {
                "device_code": "dev-code",
                "user_code": "ABCD-1234",
                "verification_url": "https://www.google.com/device",
                "interval": 0,
                "expires_in": 60,
            },
        )
        auth.oauth_poll.return_value = MagicMock()
        auth.save_oauth.return_value = True

        with (
            patch("ytm_player.cli.click.prompt") as mock_prompt,
            patch("ytm_player.cli.time.sleep"),
        ):
            result = _setup_oauth_cli(auth, "flag-id", "flag-secret")

        assert result is True
        mock_prompt.assert_not_called()
        auth.oauth_start.assert_called_once_with("flag-id", "flag-secret")

    def test_returns_false_when_oauth_start_raises(self):
        auth = self._mock_auth(saved_creds={"client_id": "id", "client_secret": "secret"})
        auth.oauth_start.side_effect = RuntimeError("network unreachable")

        result = _setup_oauth_cli(auth, None, None)

        assert result is False
        auth.save_oauth.assert_not_called()

    def test_returns_false_on_terminal_poll_error(self):
        auth = self._mock_auth(saved_creds={"client_id": "id", "client_secret": "secret"})
        auth.oauth_start.return_value = (
            MagicMock(),
            {
                "device_code": "dev-code",
                "user_code": "ABCD-1234",
                "verification_url": "https://www.google.com/device",
                "interval": 0,
                "expires_in": 60,
            },
        )
        auth.oauth_poll.side_effect = RuntimeError("OAuth device flow failed: access_denied")

        with (
            patch("ytm_player.cli.click.prompt"),
            patch("ytm_player.cli.time.sleep"),
        ):
            result = _setup_oauth_cli(auth, None, None)

        assert result is False

    def test_returns_false_when_code_expires(self):
        auth = self._mock_auth(saved_creds={"client_id": "id", "client_secret": "secret"})
        auth.oauth_start.return_value = (
            MagicMock(),
            {
                "device_code": "dev-code",
                "user_code": "ABCD-1234",
                "verification_url": "https://www.google.com/device",
                "interval": 1,
                "expires_in": 1,
            },
        )
        # Never resolves -- the loop should exit once elapsed >= expires_in.
        auth.oauth_poll.return_value = None

        with (
            patch("ytm_player.cli.click.prompt"),
            patch("ytm_player.cli.time.sleep"),
        ):
            result = _setup_oauth_cli(auth, None, None)

        assert result is False
        auth.save_oauth.assert_not_called()

    def test_returns_false_when_save_fails(self):
        auth = self._mock_auth(saved_creds={"client_id": "id", "client_secret": "secret"})
        auth.oauth_start.return_value = (
            MagicMock(),
            {
                "device_code": "dev-code",
                "user_code": "ABCD-1234",
                "verification_url": "https://www.google.com/device",
                "interval": 0,
                "expires_in": 60,
            },
        )
        auth.oauth_poll.return_value = MagicMock()
        auth.save_oauth.return_value = False

        with (
            patch("ytm_player.cli.click.prompt"),
            patch("ytm_player.cli.time.sleep"),
        ):
            result = _setup_oauth_cli(auth, None, None)

        assert result is False


# ── `ytm setup --oauth` CLI wiring ────────────────────────────────────────


class TestSetupOAuthFlag:
    def _mock_settings(self):
        mock_settings = MagicMock()
        mock_settings.yt_dlp.cookies_file = None
        mock_settings.logging.level = "WARNING"
        mock_settings.logging.max_bytes = 5 * 1024 * 1024
        mock_settings.logging.backup_count = 3
        mock_settings.logging.keep_crashes = 10
        return mock_settings

    def test_oauth_flag_routes_to_oauth_setup(self, monkeypatch):
        mock_auth = MagicMock(spec=AuthManager)
        mock_auth.is_authenticated.return_value = False
        mock_auth.validate.return_value = True

        monkeypatch.setattr("ytm_player.cli.AuthManager", lambda **kwargs: mock_auth)
        monkeypatch.setattr("ytm_player.cli.get_settings", lambda: self._mock_settings())

        with patch("ytm_player.cli._setup_oauth_cli", return_value=True) as mock_oauth_setup:
            result = CliRunner().invoke(main, ["setup", "--oauth"])

        assert result.exit_code == 0
        mock_oauth_setup.assert_called_once_with(mock_auth, None, None)

    def test_oauth_flag_passes_client_id_and_secret_through(self, monkeypatch):
        mock_auth = MagicMock(spec=AuthManager)
        mock_auth.is_authenticated.return_value = False
        mock_auth.validate.return_value = True

        monkeypatch.setattr("ytm_player.cli.AuthManager", lambda **kwargs: mock_auth)
        monkeypatch.setattr("ytm_player.cli.get_settings", lambda: self._mock_settings())

        with patch("ytm_player.cli._setup_oauth_cli", return_value=True) as mock_oauth_setup:
            result = CliRunner().invoke(
                main,
                ["setup", "--oauth", "--client-id", "cli-id", "--client-secret", "cli-secret"],
            )

        assert result.exit_code == 0
        mock_oauth_setup.assert_called_once_with(mock_auth, "cli-id", "cli-secret")

    def test_oauth_setup_failure_exits_with_error(self, monkeypatch):
        mock_auth = MagicMock(spec=AuthManager)
        mock_auth.is_authenticated.return_value = False

        monkeypatch.setattr("ytm_player.cli.AuthManager", lambda **kwargs: mock_auth)
        monkeypatch.setattr("ytm_player.cli.get_settings", lambda: self._mock_settings())

        with patch("ytm_player.cli._setup_oauth_cli", return_value=False):
            result = CliRunner().invoke(main, ["setup", "--oauth"])

        assert result.exit_code != 0
        assert "setup failed" in result.output.lower()

    def test_without_oauth_flag_uses_cookie_flow(self, monkeypatch):
        """--oauth is opt-in; the default `ytm setup` must still use cookies."""
        mock_auth = MagicMock(spec=AuthManager)
        mock_auth.is_authenticated.return_value = False
        mock_auth.setup_interactive.return_value = True
        mock_auth.validate.return_value = True

        monkeypatch.setattr("ytm_player.cli.AuthManager", lambda **kwargs: mock_auth)
        monkeypatch.setattr("ytm_player.cli.get_settings", lambda: self._mock_settings())

        with patch("ytm_player.cli._setup_oauth_cli") as mock_oauth_setup:
            result = CliRunner().invoke(main, ["setup"])

        assert result.exit_code == 0
        mock_oauth_setup.assert_not_called()
        mock_auth.setup_interactive.assert_called_once()
