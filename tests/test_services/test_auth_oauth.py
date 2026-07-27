"""Tests for AuthManager's OAuth device-flow support."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from ytmusicapi.auth.oauth import RefreshingToken

from ytm_player.config.paths import SECURE_FILE_MODE
from ytm_player.services.auth import AuthManager


def _make_auth(tmp_path: Path) -> AuthManager:
    return AuthManager(
        config_dir=tmp_path,
        auth_file=tmp_path / "auth.json",
        oauth_file=tmp_path / "oauth.json",
        oauth_creds_file=tmp_path / "oauth_creds.json",
    )


# ── load_oauth_creds / has_oauth ────────────────────────────────────────


class TestLoadOAuthCreds:
    def test_returns_none_when_missing(self, tmp_path):
        auth = _make_auth(tmp_path)
        assert auth.load_oauth_creds() is None

    def test_returns_saved_creds(self, tmp_path):
        auth = _make_auth(tmp_path)
        (tmp_path / "oauth_creds.json").write_text(
            json.dumps({"client_id": "id123", "client_secret": "secret456"})
        )
        assert auth.load_oauth_creds() == {"client_id": "id123", "client_secret": "secret456"}

    def test_returns_none_on_corrupted_json(self, tmp_path):
        auth = _make_auth(tmp_path)
        (tmp_path / "oauth_creds.json").write_text("{not valid json")
        assert auth.load_oauth_creds() is None

    def test_returns_none_when_fields_empty(self, tmp_path):
        auth = _make_auth(tmp_path)
        (tmp_path / "oauth_creds.json").write_text(
            json.dumps({"client_id": "", "client_secret": ""})
        )
        assert auth.load_oauth_creds() is None


class TestHasOAuth:
    def test_false_when_nothing_saved(self, tmp_path):
        assert _make_auth(tmp_path).has_oauth() is False

    def test_false_when_only_creds_saved(self, tmp_path):
        auth = _make_auth(tmp_path)
        (tmp_path / "oauth_creds.json").write_text(
            json.dumps({"client_id": "id", "client_secret": "secret"})
        )
        assert auth.has_oauth() is False

    def test_false_when_only_token_saved(self, tmp_path):
        auth = _make_auth(tmp_path)
        (tmp_path / "oauth.json").write_text("{}")
        assert auth.has_oauth() is False

    def test_true_when_both_saved(self, tmp_path):
        auth = _make_auth(tmp_path)
        (tmp_path / "oauth_creds.json").write_text(
            json.dumps({"client_id": "id", "client_secret": "secret"})
        )
        (tmp_path / "oauth.json").write_text("{}")
        assert auth.has_oauth() is True


# ── is_authenticated / create_ytmusic_client branching ──────────────────


class TestAuthBranching:
    def test_is_authenticated_true_via_oauth_even_without_cookie_file(self, tmp_path):
        auth = _make_auth(tmp_path)
        (tmp_path / "oauth_creds.json").write_text(
            json.dumps({"client_id": "id", "client_secret": "secret"})
        )
        (tmp_path / "oauth.json").write_text("{}")
        assert auth.is_authenticated() is True

    def test_create_ytmusic_client_uses_oauth_when_available(self, tmp_path):
        auth = _make_auth(tmp_path)
        (tmp_path / "oauth_creds.json").write_text(
            json.dumps({"client_id": "id123", "client_secret": "secret456"})
        )
        (tmp_path / "oauth.json").write_text("{}")

        with (
            patch("ytm_player.services.auth.OAuthCredentials") as mock_creds_cls,
            patch("ytm_player.services.auth.YTMusic") as mock_ytmusic_cls,
        ):
            auth.create_ytmusic_client(user="brand-1")

            mock_creds_cls.assert_called_once_with("id123", "secret456")
            mock_ytmusic_cls.assert_called_once_with(
                str(tmp_path / "oauth.json"),
                user="brand-1",
                oauth_credentials=mock_creds_cls.return_value,
            )

    def test_create_ytmusic_client_falls_back_to_cookies(self, tmp_path):
        auth = _make_auth(tmp_path)

        with patch("ytm_player.services.auth.YTMusic") as mock_ytmusic_cls:
            auth.create_ytmusic_client(user=None)

            mock_ytmusic_cls.assert_called_once_with(str(tmp_path / "auth.json"), user=None)


# ── oauth_start / oauth_poll ─────────────────────────────────────────────


class TestOAuthStart:
    def test_returns_credentials_and_code(self, tmp_path):
        auth = _make_auth(tmp_path)
        code = {
            "device_code": "dev-code",
            "user_code": "ABCD-1234",
            "verification_url": "https://www.google.com/device",
            "interval": 5,
            "expires_in": 1800,
        }

        with patch("ytm_player.services.auth.OAuthCredentials") as mock_creds_cls:
            mock_creds_cls.return_value.get_code.return_value = code
            credentials, result = auth.oauth_start("id123", "secret456")

            mock_creds_cls.assert_called_once_with("id123", "secret456")
            assert credentials is mock_creds_cls.return_value
            assert result == code


class TestOAuthPoll:
    def test_returns_none_on_authorization_pending(self, tmp_path):
        auth = _make_auth(tmp_path)
        credentials = MagicMock()
        credentials.token_from_code.return_value = {"error": "authorization_pending"}

        assert auth.oauth_poll(credentials, "dev-code") is None

    def test_returns_none_on_slow_down(self, tmp_path):
        auth = _make_auth(tmp_path)
        credentials = MagicMock()
        credentials.token_from_code.return_value = {"error": "slow_down"}

        assert auth.oauth_poll(credentials, "dev-code") is None

    def test_raises_on_terminal_error(self, tmp_path):
        auth = _make_auth(tmp_path)
        credentials = MagicMock()
        credentials.token_from_code.return_value = {"error": "expired_token"}

        with pytest.raises(RuntimeError, match="expired_token"):
            auth.oauth_poll(credentials, "dev-code")

    def test_returns_token_on_success(self, tmp_path):
        auth = _make_auth(tmp_path)
        credentials = MagicMock()
        credentials.token_from_code.return_value = {
            "access_token": "access-abc",
            "refresh_token": "refresh-xyz",
            "scope": "https://www.googleapis.com/auth/youtube",
            "token_type": "Bearer",
            "expires_in": 3600,
        }

        token = auth.oauth_poll(credentials, "dev-code")

        assert token is not None
        assert token.access_token == "access-abc"
        assert token.refresh_token == "refresh-xyz"
        assert token.credentials is credentials

    def test_uses_refresh_token_expires_in_when_present(self, tmp_path):
        """refresh_token_expires_in (if Google sends it) sizes the token's
        own expires_in, distinct from the access token's expiry set by update()."""
        auth = _make_auth(tmp_path)
        credentials = MagicMock()
        credentials.token_from_code.return_value = {
            "access_token": "access-abc",
            "refresh_token": "refresh-xyz",
            "scope": "https://www.googleapis.com/auth/youtube",
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token_expires_in": 15552000,
        }

        token = auth.oauth_poll(credentials, "dev-code")

        assert token is not None


# ── save_oauth ────────────────────────────────────────────────────────────


class _FakeToken:
    """Mimics RefreshingToken.local_cache's real setter (write-on-set).

    A bare MagicMock records the assignment but never touches disk, which
    would make save_oauth's follow-up secure_chmod() call fail against a
    file that was never actually created -- this fake reproduces the real
    write-on-assign behaviour just enough to exercise that code path.
    """

    def __init__(self) -> None:
        self._local_cache: Path | None = None

    @property
    def local_cache(self) -> Path | None:
        return self._local_cache

    @local_cache.setter
    def local_cache(self, path: Path) -> None:
        self._local_cache = path
        Path(path).write_text("{}")


class TestSaveOAuth:
    def test_persists_creds_and_token(self, tmp_path):
        auth = _make_auth(tmp_path)
        token = _FakeToken()

        result = auth.save_oauth("id123", "secret456", cast(RefreshingToken, token))

        assert result is True
        creds_file = tmp_path / "oauth_creds.json"
        assert json.loads(creds_file.read_text()) == {
            "client_id": "id123",
            "client_secret": "secret456",
        }
        assert token.local_cache == tmp_path / "oauth.json"
        assert (tmp_path / "oauth.json").exists()

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes only")
    def test_creds_file_has_secure_permissions(self, tmp_path):
        auth = _make_auth(tmp_path)
        auth.save_oauth("id123", "secret456", cast(RefreshingToken, _FakeToken()))

        mode = stat.S_IMODE((tmp_path / "oauth_creds.json").stat().st_mode)
        assert mode == SECURE_FILE_MODE

    def test_returns_false_on_write_failure(self, tmp_path):
        # Point the creds file at a path whose parent is a plain file, not
        # a directory -- os.open() can't create a file under it.
        blocked = tmp_path / "not-a-dir"
        blocked.write_text("blocking file")
        auth = AuthManager(
            config_dir=tmp_path,
            auth_file=tmp_path / "auth.json",
            oauth_file=tmp_path / "oauth.json",
            oauth_creds_file=blocked / "oauth_creds.json",
        )

        assert auth.save_oauth("id123", "secret456", cast(RefreshingToken, MagicMock())) is False


# ── try_auto_refresh short-circuits for OAuth ────────────────────────────


class TestTryAutoRefreshOAuth:
    def test_returns_false_immediately_when_oauth_active(self, tmp_path):
        auth = _make_auth(tmp_path)
        (tmp_path / "oauth_creds.json").write_text(
            json.dumps({"client_id": "id", "client_secret": "secret"})
        )
        (tmp_path / "oauth.json").write_text("{}")

        with patch.object(auth, "_detect_browser") as mock_detect:
            assert auth.try_auto_refresh() is False
            mock_detect.assert_not_called()
