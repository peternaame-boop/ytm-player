"""Tests for AuthManager._save_stream_cookiejar() and its call sites.

Covers the new stream-cookiejar artifact written alongside auth.json:
symlink defense (O_NOFOLLOW + atomic replace), domain scoping
(youtube.com/google.com family, confusable-suffix rejection), secure file
mode, independent-failure behavior (a cookiejar write failure must never
affect auth.json's own success/failure signal), _cookies_from_raw_header
parsing for the manual-paste path, _setup_manual's end-to-end wiring, the
account-scoping gate (no valid account => no cookiejar write), and
_refresh_from_cookies_file's backup/restore extension for the cookiejar.

Every test constructs AuthManager with config_dir, auth_file, AND
stream_cookies_file all explicitly pointed at tmp_path — stream_cookies_file
defaults to the real CONFIG_DIR at import time, so omitting it here would
write to the developer's/CI's actual ~/.config/ytm-player/.
"""

from __future__ import annotations

import sys
import time
from http.cookiejar import Cookie, MozillaCookieJar
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ytm_player.config.paths import SECURE_FILE_MODE
from ytm_player.services.auth import AuthManager, _cookies_from_raw_header

_PATCH_SAPISID = patch("ytm_player.services.auth.sapisid_from_cookie", return_value="fake_sapisid")


def _make_auth(tmp_path: Path, **overrides: Path) -> AuthManager:
    kwargs: dict[str, Path] = {
        "config_dir": tmp_path,
        "auth_file": tmp_path / "auth.json",
        "stream_cookies_file": tmp_path / "stream_cookies.txt",
    }
    kwargs.update(overrides)
    return AuthManager(**kwargs)  # type: ignore[arg-type]


def _cookie(domain: str, name: str = "cookie", value: str = "value") -> Cookie:
    return Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path="/",
        path_specified=True,
        secure=False,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={},
    )


def _read_cookie_names(path: Path) -> set[str]:
    jar = MozillaCookieJar(str(path))
    jar.load(ignore_discard=True, ignore_expires=True)
    return {c.name for c in jar}


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


# ── Step 1: symlink defense ─────────────────────────────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="O_NOFOLLOW is a POSIX-only defense")
def test_symlink_target_is_not_followed(tmp_path):
    """A symlink planted at the target path must not be written through —
    proves the write path (O_NOFOLLOW temp file + atomic os.replace) is
    actually wired in, not just documented."""
    stream_cookies_file = tmp_path / "stream_cookies.txt"
    victim = tmp_path / "victim.txt"
    victim.write_text("do not touch")
    stream_cookies_file.symlink_to(victim)

    auth = _make_auth(tmp_path, stream_cookies_file=stream_cookies_file)

    result = auth._save_stream_cookiejar([_cookie(".youtube.com")])

    assert result is None
    assert victim.read_text() == "do not touch"


# ── Step 2: domain scoping ───────────────────────────────────────────────────


def test_success_writes_secure_file_scoped_to_youtube_google(tmp_path):
    auth = _make_auth(tmp_path)
    jar = [
        _cookie(".youtube.com", name="yt_cookie"),
        _cookie("accounts.google.com", name="google_cookie"),
        _cookie(".chase.com", name="chase_cookie"),
        _cookie("unrelated-shop.example", name="shop_cookie"),
    ]

    auth._save_stream_cookiejar(jar)

    assert auth._stream_cookies_file.exists()
    names = _read_cookie_names(auth._stream_cookies_file)
    assert names == {"yt_cookie", "google_cookie"}


def test_rejects_confusable_domain_suffix(tmp_path):
    """A naive endswith('youtube.com') filter (no label-boundary check)
    would incorrectly admit notyoutube.com."""
    auth = _make_auth(tmp_path)
    jar = [
        _cookie(".youtube.com", name="yt_cookie"),
        _cookie("notyoutube.com", name="confusable_cookie"),
    ]

    auth._save_stream_cookiejar(jar)

    names = _read_cookie_names(auth._stream_cookies_file)
    assert names == {"yt_cookie"}


def test_rejects_cookie_with_embedded_control_character(tmp_path):
    """_save_stream_cookiejar's own filter loop must reject control chars,
    not just _cookies_from_raw_header's manual-paste path (SEC-001) --
    the browser-extraction and cookies.txt-import call sites both reach
    this method directly, so this is the coverage that actually protects
    them."""
    auth = _make_auth(tmp_path)
    jar = [
        _cookie(".youtube.com", name="clean_cookie"),
        _cookie(".youtube.com", name="bad_cookie", value="ab\tcd"),
    ]

    auth._save_stream_cookiejar(jar)

    names = _read_cookie_names(auth._stream_cookies_file)
    assert names == {"clean_cookie"}


# ── Step 2b: wide-jar wiring through the real extraction call sites ─────────


def test_extract_and_save_threads_wide_jar_to_stream_cookiejar(tmp_path):
    """_extract_and_save must pass the WIDE, unfiltered browser jar as
    stream_jar -- not the narrower .youtube.com-exact yt_cookies list used
    for auth.json -- so a google.com-family cookie invisible to yt_cookies
    still lands in the stream cookiejar. A future edit that "simplifies" by
    reusing yt_cookies for stream_jar would silently narrow the stream
    cookiejar to youtube.com-only and this test would catch it."""
    auth = _make_auth(tmp_path)
    jar = [
        _cookie(".youtube.com", name="SAPISID", value="secret"),
        _cookie("accounts.google.com", name="google_cookie"),
        _cookie(".chase.com", name="chase_cookie"),
    ]

    mock_ytm = MagicMock()
    mock_ytm.get_account_info.return_value = {"accountName": "Alice"}

    with (
        _PATCH_SAPISID,
        patch("yt_dlp.cookies.extract_cookies_from_browser", return_value=jar),
        patch("ytm_player.services.auth.YTMusic", return_value=mock_ytm),
    ):
        result = auth._extract_and_save("vivaldi")

    assert result is True
    assert auth.auth_file.exists()
    names = _read_cookie_names(auth._stream_cookies_file)
    assert names == {"SAPISID", "google_cookie"}


def test_extract_and_save_from_cookies_file_threads_wide_jar_to_stream_cookiejar(tmp_path):
    """Analogous to the browser-extraction case above: _extract_and_save_from_cookies_file
    must pass the WIDE, unfiltered MozillaCookieJar loaded from the cookies.txt file as
    stream_jar, not the narrower .youtube.com-only yt_cookies list. Unlike the
    validate()-failure rollback tests elsewhere in this file, this is a genuine
    happy path — validate() is never invoked by this call site."""
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text(
        "\n".join(
            [
                "# Netscape HTTP Cookie File",
                ".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSAPISID\tabc123",
                "accounts.google.com\tFALSE\t/\tTRUE\t2147483647\tgoogle_cookie\tg123",
                ".chase.com\tTRUE\t/\tTRUE\t2147483647\tchase_cookie\tc123",
            ]
        )
        + "\n"
    )

    auth = _make_auth(tmp_path)

    mock_ytm = MagicMock()
    mock_ytm.get_account_info.return_value = {"accountName": "Alice"}

    with (
        _PATCH_SAPISID,
        patch("ytm_player.services.auth.YTMusic", return_value=mock_ytm),
    ):
        result = auth._extract_and_save_from_cookies_file(cookies_file)

    assert result is True
    assert auth.auth_file.exists()
    names = _read_cookie_names(auth._stream_cookies_file)
    assert names == {"SAPISID", "google_cookie"}


# ── Step 3: file mode ────────────────────────────────────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="NTFS ignores POSIX mode bits")
def test_file_mode_is_secure(tmp_path):
    auth = AuthManager(
        config_dir=tmp_path,
        auth_file=tmp_path / "auth.json",
        stream_cookies_file=tmp_path / "stream_cookies.txt",
    )

    auth._save_stream_cookiejar([_cookie(".youtube.com")])

    mode = (tmp_path / "stream_cookies.txt").stat().st_mode & 0o777
    assert mode == SECURE_FILE_MODE


# ── Step 4: independent failure ──────────────────────────────────────────────


def test_cookiejar_write_failure_does_not_affect_auth_json(tmp_path):
    """_save_stream_cookiejar's own try/except must swallow a real write
    failure without affecting auth.json's success signal. Point
    stream_cookies_file's parent at a directory that never gets created
    (only config_dir is mkdir'd) so the real internal open() fails."""
    auth = _make_auth(
        tmp_path, stream_cookies_file=tmp_path / "missing_subdir" / "stream_cookies.txt"
    )
    jar = [_cookie(".youtube.com", name="SAPISID", value="secret")]

    mock_ytm = MagicMock()
    mock_ytm.get_account_info.return_value = {"accountName": "Alice"}

    with (
        _PATCH_SAPISID,
        patch("yt_dlp.cookies.extract_cookies_from_browser", return_value=jar),
        patch("ytm_player.services.auth.YTMusic", return_value=mock_ytm),
    ):
        result = auth._extract_and_save("vivaldi")

    assert result is True
    assert auth.auth_file.exists()
    assert not (tmp_path / "missing_subdir" / "stream_cookies.txt").exists()
    assert not (tmp_path / "missing_subdir").exists()


# ── Step 5: empty / no-matching-cookies ──────────────────────────────────────


def test_no_matching_cookies_writes_empty_jar(tmp_path):
    auth = _make_auth(tmp_path)
    jar = [_cookie(".chase.com"), _cookie("unrelated-shop.example")]

    auth._save_stream_cookiejar(jar)

    assert auth._stream_cookies_file.exists()
    assert _read_cookie_names(auth._stream_cookies_file) == set()


# ── Step 6: _cookies_from_raw_header parsing ─────────────────────────────────


class TestCookiesFromRawHeader:
    def test_parses_multiple_cookie_pairs(self):
        cookies = _cookies_from_raw_header("SAPISID=abc123; HSID=def456")

        assert [(c.name, c.value, c.domain, c.secure) for c in cookies] == [
            ("SAPISID", "abc123", ".youtube.com", True),
            ("HSID", "def456", ".youtube.com", True),
        ]

    def test_handles_value_containing_equals_sign(self):
        cookies = _cookies_from_raw_header("TOKEN=abc=123=xyz")

        assert len(cookies) == 1
        assert cookies[0].name == "TOKEN"
        assert cookies[0].value == "abc=123=xyz"

    def test_empty_string_returns_empty_list(self):
        assert _cookies_from_raw_header("") == []

    def test_skips_malformed_pair_without_equals(self):
        cookies = _cookies_from_raw_header("SAPISID=abc123; garbage; HSID=def456")

        assert [c.name for c in cookies] == ["SAPISID", "HSID"]

    def test_rejects_pair_with_embedded_control_characters(self):
        cookies = _cookies_from_raw_header("SAPISID=abc\t123; HSID=def456")

        assert [c.name for c in cookies] == ["HSID"]

    def test_expiry_is_roughly_two_years_out(self):
        before = time.time()

        cookies = _cookies_from_raw_header("SAPISID=abc123")

        expected = before + 2 * 365 * 24 * 60 * 60
        assert abs(cookies[0].expires - expected) < 60


# ── Step 7: _setup_manual integration ────────────────────────────────────────


def test_setup_manual_saves_stream_cookiejar_from_pasted_cookie_header(tmp_path, monkeypatch):
    auth = _make_auth(tmp_path)
    responses = iter(["Host: music.youtube.com", "Cookie: SAPISID=abc123", ""])
    monkeypatch.setattr("builtins.input", lambda: next(responses))

    def _fake_setup(filepath, headers_raw):
        Path(filepath).write_text('{"cookie": "SAPISID=abc123"}', encoding="utf-8")

    with patch("ytmusicapi.setup", side_effect=_fake_setup):
        result = auth.setup_interactive(manual=True)

    assert result is True
    assert auth._stream_cookies_file.exists()
    assert _read_cookie_names(auth._stream_cookies_file) == {"SAPISID"}


def test_no_cookie_header_skips_cookiejar_write(tmp_path, monkeypatch):
    auth = _make_auth(tmp_path)
    responses = iter(["Host: music.youtube.com", "Accept: */*", ""])
    monkeypatch.setattr("builtins.input", lambda: next(responses))

    def _fake_setup(filepath, headers_raw):
        Path(filepath).write_text('{"cookie": ""}', encoding="utf-8")

    with patch("ytmusicapi.setup", side_effect=_fake_setup):
        result = auth.setup_interactive(manual=True)

    assert result is True
    assert not auth._stream_cookies_file.exists()


# ── Step 8: account-scoping gate ─────────────────────────────────────────────


def test_no_valid_account_skips_stream_cookiejar_write(tmp_path):
    auth = _make_auth(tmp_path)
    cookies = [_cookie(".youtube.com", name="SAPISID", value="secret")]
    stream_jar = [_cookie(".youtube.com", name="SAPISID", value="secret")]

    mock_ytm = MagicMock()
    mock_ytm.get_account_info.return_value = {}  # no accountName at any index

    with (
        _PATCH_SAPISID,
        patch("ytm_player.services.auth.YTMusic", return_value=mock_ytm),
        patch.object(
            auth, "_save_stream_cookiejar", wraps=auth._save_stream_cookiejar
        ) as mock_save,
    ):
        result = auth._save_youtube_cookies(cookies, stream_jar=stream_jar)

    assert result is False
    mock_save.assert_not_called()
    assert not auth._stream_cookies_file.exists()


# ── Step 9: _refresh_from_cookies_file backup/restore ────────────────────────


def test_refresh_from_cookies_file_restores_stream_cookiejar_on_validate_failure(
    tmp_path, monkeypatch
):
    cookies_file = tmp_path / "cookies.txt"
    _write_netscape_cookie_file(cookies_file)

    auth_file = tmp_path / "auth.json"
    auth_file.write_text('{"cookie": "old=1"}')
    stream_cookies_file = tmp_path / "stream_cookies.txt"
    stream_cookies_file.write_text("# sentinel stream cookies\n")

    auth = _make_auth(tmp_path, auth_file=auth_file, stream_cookies_file=stream_cookies_file)
    monkeypatch.setattr(auth, "validate", lambda: False)

    mock_ytm = MagicMock()
    mock_ytm.get_account_info.return_value = {"accountName": "Alice"}

    with (
        _PATCH_SAPISID,
        patch("ytm_player.services.auth.YTMusic", return_value=mock_ytm),
    ):
        result = auth._refresh_from_cookies_file(cookies_file)

    assert result is False
    assert auth_file.read_text() == '{"cookie": "old=1"}'
    assert stream_cookies_file.read_text() == "# sentinel stream cookies\n"


def test_refresh_from_cookies_file_removes_new_stream_cookiejar_when_no_prior_backup(
    tmp_path, monkeypatch
):
    cookies_file = tmp_path / "cookies.txt"
    _write_netscape_cookie_file(cookies_file)

    auth_file = tmp_path / "auth.json"
    stream_cookies_file = tmp_path / "stream_cookies.txt"
    # Neither auth_file nor stream_cookies_file exists beforehand.

    auth = _make_auth(tmp_path, auth_file=auth_file, stream_cookies_file=stream_cookies_file)
    monkeypatch.setattr(auth, "validate", lambda: False)

    mock_ytm = MagicMock()
    mock_ytm.get_account_info.return_value = {"accountName": "Alice"}

    with (
        _PATCH_SAPISID,
        patch("ytm_player.services.auth.YTMusic", return_value=mock_ytm),
    ):
        result = auth._refresh_from_cookies_file(cookies_file)

    assert result is False
    assert not auth_file.exists()
    assert not stream_cookies_file.exists()
