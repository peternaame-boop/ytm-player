"""Tests for AuthManager cookies-file refresh behavior."""

from pathlib import Path

import requests.exceptions

from ytm_player.services.auth import AuthManager


def _write_netscape_cookie_file(path: Path, domain: str = ".youtube.com") -> None:
    path.write_text(
        "\n".join(
            [
                "# Netscape HTTP Cookie File",
                f"{domain}\t{'TRUE' if domain.startswith('.') else 'FALSE'}\t/\tTRUE\t2147483647\tSAPISID\tabc123",
            ]
        )
        + "\n"
    )


def test_extract_from_cookies_file_rejects_non_youtube_suffix(tmp_path):
    cookies_file = tmp_path / "cookies.txt"
    _write_netscape_cookie_file(cookies_file, domain="notyoutube.com")

    auth_file = tmp_path / "headers_auth.json"
    auth = AuthManager(auth_file=auth_file)

    assert auth._extract_and_save_from_cookies_file(cookies_file) is False
    assert not auth_file.exists()


def test_refresh_from_cookies_file_restores_previous_auth_on_validate_failure(
    tmp_path, monkeypatch
):
    cookies_file = tmp_path / "cookies.txt"
    _write_netscape_cookie_file(cookies_file)

    auth_file = tmp_path / "headers_auth.json"
    original = '{"cookie": "old=1"}'
    auth_file.write_text(original)

    auth = AuthManager(auth_file=auth_file)

    monkeypatch.setattr(auth, "validate", lambda: False)

    assert auth._refresh_from_cookies_file(cookies_file) is False
    assert auth_file.read_text() == original


def test_refresh_from_cookies_file_restores_backup_on_network_error(tmp_path, monkeypatch):
    """If validate() raises a network error, backup should still be restored."""
    cookies_file = tmp_path / "cookies.txt"
    _write_netscape_cookie_file(cookies_file)

    auth_file = tmp_path / "headers_auth.json"
    original = '{"cookie": "old=1"}'
    auth_file.write_text(original)

    auth = AuthManager(auth_file=auth_file)

    def _raise_network_error():
        raise requests.exceptions.ConnectionError("connection reset")

    monkeypatch.setattr(auth, "validate", _raise_network_error)

    assert auth._refresh_from_cookies_file(cookies_file) is False
    assert auth_file.read_text() == original
