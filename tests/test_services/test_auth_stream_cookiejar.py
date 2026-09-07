"""Tests for the stream cookiejar AuthManager._commit_session() writes
(built by _build_stream_cookiejar()) and its call sites.

Covers the stream-cookiejar artifact written alongside auth.json:
symlink defense (O_EXCL temp file + atomic replace, symlink destination
refused), domain scoping (youtube.com/google.com family, confusable-suffix
rejection), secure file mode, failure ordering (a cookiejar write failure
stops the commit before auth.json is touched), _cookies_from_raw_header
parsing for the manual-paste path, _setup_manual's end-to-end wiring, the
account-scoping gate (no valid account => no cookiejar write), and
_refresh_from_cookies_file's probe-before-write behaviour.

Every test constructs AuthManager with config_dir, auth_file, AND
stream_cookies_file all explicitly pointed at tmp_path — stream_cookies_file
defaults to the real CONFIG_DIR at import time, so omitting it here would
write to the developer's/CI's actual ~/.config/ytm-player/.
"""

from __future__ import annotations

import json
import sys
import time
from http.cookiejar import Cookie, MozillaCookieJar
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ytm_player.config.paths import SECURE_FILE_MODE
from ytm_player.services.auth import (
    AuthManager,
    _atomic_write,
    _cookies_from_raw_header,
    _ProbedAccount,
)

_PATCH_SAPISID = patch("ytm_player.services.auth.sapisid_from_cookie", return_value="fake_sapisid")
_ACCOUNT = _ProbedAccount(slot=0, name="Alice", handle="", channel_id=None)
_PAYLOAD = b'{"cookie": "SAPISID=synthetic", "x-goog-authuser": "0"}'


def _commit(auth: AuthManager, jar) -> bool:
    """Commit a synthetic session together with *jar* (the only way a stream
    cookiejar is written)."""
    return auth._commit_session(_PAYLOAD, _ACCOUNT, jar)


def _slot_zero_only(mock_ytm):
    """YTMusic stand-in that is a valid account in browser slot 0 only, so the
    interactive selection auto-picks it instead of prompting."""

    def factory(path, user=None):
        if json.loads(Path(path).read_text(encoding="utf-8")).get("x-goog-authuser") != "0":
            raise Exception("no account in this slot")
        return mock_ytm

    return factory


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


def _write_str(content: str):
    """Build a write callback for _atomic_write() matching its declared
    Callable[[IO[Any]], None] type — f.write() returns int (chars written),
    which a bare lambda would leak as the callback's inferred return type."""

    def _write(f) -> None:
        f.write(content)

    return _write


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


def test_symlink_target_is_not_followed(tmp_path):
    """A symlink planted at the target path is neither written through nor
    replaced: _atomic_write refuses it, and the commit stops there — before
    auth.json — so the previous session is untouched too."""
    stream_cookies_file = tmp_path / "stream_cookies.txt"
    victim = tmp_path / "victim.txt"
    victim.write_text("do not touch")
    try:
        stream_cookies_file.symlink_to(victim)
    except OSError as exc:  # pragma: no cover - Windows without the privilege
        pytest.skip(f"cannot create a symlink here: {exc}")

    auth = _make_auth(tmp_path, stream_cookies_file=stream_cookies_file)

    result = _commit(auth, [_cookie(".youtube.com")])

    assert result is False
    assert victim.read_text() == "do not touch"
    assert stream_cookies_file.is_symlink()
    assert not auth.auth_file.exists()


# ── Step 2: domain scoping ───────────────────────────────────────────────────


def test_success_writes_secure_file_scoped_to_youtube_google(tmp_path):
    auth = _make_auth(tmp_path)
    jar = [
        _cookie(".youtube.com", name="yt_cookie"),
        _cookie("accounts.google.com", name="google_cookie"),
        _cookie(".chase.com", name="chase_cookie"),
        _cookie("unrelated-shop.example", name="shop_cookie"),
    ]

    assert _commit(auth, jar) is True

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

    _commit(auth, jar)

    names = _read_cookie_names(auth._stream_cookies_file)
    assert names == {"yt_cookie"}


def test_rejects_cookie_with_embedded_control_character(tmp_path):
    """_build_stream_cookiejar's own filter loop must reject control chars,
    not just _cookies_from_raw_header's manual-paste path (SEC-001) --
    the browser-extraction and cookies.txt-import call sites both reach
    it through _commit_session, so this is the coverage that actually
    protects them."""
    auth = _make_auth(tmp_path)
    jar = [
        _cookie(".youtube.com", name="clean_cookie"),
        _cookie(".youtube.com", name="bad_cookie", value="ab\tcd"),
    ]

    _commit(auth, jar)

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
        patch("ytm_player.services.auth.YTMusic", side_effect=_slot_zero_only(mock_ytm)),
    ):
        result = auth._extract_and_save("vivaldi", interactive=True)

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
        patch("ytm_player.services.auth.YTMusic", side_effect=_slot_zero_only(mock_ytm)),
    ):
        result = auth._extract_and_save_from_cookies_file(cookies_file, interactive=True)

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

    _commit(auth, [_cookie(".youtube.com")])

    mode = (tmp_path / "stream_cookies.txt").stat().st_mode & 0o777
    assert mode == SECURE_FILE_MODE


# ── Step 4: failure ordering ─────────────────────────────────────────────────


def test_cookiejar_write_failure_stops_the_commit_before_auth_json(tmp_path):
    """The jar is the first file a commit publishes. When that write fails
    nothing else is written: auth.json still holds the previous session (or,
    as here, does not exist yet). Point stream_cookies_file's parent at a
    directory that never gets created (only config_dir is mkdir'd) so the
    real internal open() fails."""
    auth = _make_auth(
        tmp_path, stream_cookies_file=tmp_path / "missing_subdir" / "stream_cookies.txt"
    )
    jar = [_cookie(".youtube.com", name="SAPISID", value="secret")]

    mock_ytm = MagicMock()
    mock_ytm.get_account_info.return_value = {"accountName": "Alice"}

    with (
        _PATCH_SAPISID,
        patch("yt_dlp.cookies.extract_cookies_from_browser", return_value=jar),
        patch("ytm_player.services.auth.YTMusic", side_effect=_slot_zero_only(mock_ytm)),
    ):
        result = auth._extract_and_save("vivaldi", interactive=True)

    assert result is False
    assert not auth.auth_file.exists()
    assert not (tmp_path / "missing_subdir" / "stream_cookies.txt").exists()
    assert not (tmp_path / "missing_subdir").exists()


def test_cookiejar_write_failure_keeps_the_previous_jar_and_session(tmp_path):
    """A failed jar write leaves the previous session complete: its
    auth.json is not replaced, so its jar still belongs to it and stays."""
    auth = _make_auth(tmp_path)
    auth.auth_file.write_bytes(b'{"cookie": "SAPISID=old"}')
    auth._stream_cookies_file.write_text("# old account\n", encoding="utf-8")
    jar = [_cookie(".youtube.com", name="SAPISID", value="secret")]

    with patch(
        "ytm_player.services.auth._atomic_write", side_effect=OSError("simulated write failure")
    ):
        result = _commit(auth, jar)

    assert result is False
    assert auth._stream_cookies_file.read_text(encoding="utf-8") == "# old account\n"
    assert auth.auth_file.read_bytes() == b'{"cookie": "SAPISID=old"}'


def test_cookiejar_write_success_returns_true(tmp_path):
    auth = _make_auth(tmp_path)

    assert _commit(auth, [_cookie(".youtube.com")]) is True
    assert auth._stream_cookies_file.exists()


# ── Step 5: empty / no-matching-cookies ──────────────────────────────────────


def test_no_matching_cookies_writes_empty_jar(tmp_path):
    auth = _make_auth(tmp_path)
    jar = [_cookie(".chase.com"), _cookie("unrelated-shop.example")]

    _commit(auth, jar)

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

    def _fake_setup(headers_raw=None, filepath=None):
        return '{"cookie": "SAPISID=abc123", "x-goog-authuser": "0"}'

    with (
        patch("ytmusicapi.setup", side_effect=_fake_setup),
        patch("ytm_player.services.auth.YTMusic", return_value=MagicMock()),
    ):
        result = auth.setup_interactive(manual=True)

    assert result is True
    assert auth._stream_cookies_file.exists()
    assert _read_cookie_names(auth._stream_cookies_file) == {"SAPISID"}


def test_paste_without_cookie_header_is_rejected_and_writes_nothing(tmp_path, monkeypatch):
    """ytmusicapi itself refuses headers without a cookie (and an
    x-goog-authuser), so such a paste never reaches a commit."""
    auth = _make_auth(tmp_path)
    responses = iter(["Host: music.youtube.com", "Accept: */*", ""])
    monkeypatch.setattr("builtins.input", lambda: next(responses))

    with patch("ytm_player.services.auth.YTMusic", return_value=MagicMock()):
        result = auth.setup_interactive(manual=True)

    assert result is False
    assert not auth.auth_file.exists()
    assert not auth._stream_cookies_file.exists()
    assert not auth._account_file.exists()


# ── Step 8: account-scoping gate ─────────────────────────────────────────────


def test_no_valid_account_skips_stream_cookiejar_write(tmp_path):
    auth = _make_auth(tmp_path)
    cookies = [_cookie(".youtube.com", name="SAPISID", value="secret")]
    stream_jar = [_cookie(".youtube.com", name="SAPISID", value="secret")]

    mock_ytm = MagicMock()
    mock_ytm.get_account_info.return_value = {}  # no accountName at any index

    with (
        _PATCH_SAPISID,
        patch("ytm_player.services.auth.YTMusic", side_effect=_slot_zero_only(mock_ytm)),
        patch.object(auth, "_commit_session", wraps=auth._commit_session) as mock_commit,
    ):
        result = auth._save_youtube_cookies(cookies, interactive=True, stream_jar=stream_jar)

    assert result is False
    mock_commit.assert_not_called()
    assert not auth._stream_cookies_file.exists()


# ── Step 9: _refresh_from_cookies_file probes before it writes ───────────────


def test_refresh_from_cookies_file_refused_probe_leaves_auth_and_jar_untouched(tmp_path):
    cookies_file = tmp_path / "cookies.txt"
    _write_netscape_cookie_file(cookies_file)

    auth_file = tmp_path / "auth.json"
    auth_file.write_text('{"cookie": "old=1"}')
    stream_cookies_file = tmp_path / "stream_cookies.txt"
    stream_cookies_file.write_text("# sentinel stream cookies\n")

    auth = _make_auth(tmp_path, auth_file=auth_file, stream_cookies_file=stream_cookies_file)

    mock_ytm = MagicMock()
    mock_ytm.get_account_info.return_value = {}  # no account in any slot

    with (
        _PATCH_SAPISID,
        patch("ytm_player.services.auth.YTMusic", side_effect=_slot_zero_only(mock_ytm)),
    ):
        result = auth._refresh_from_cookies_file(cookies_file, interactive=True)

    assert result is False
    assert auth_file.read_text() == '{"cookie": "old=1"}'
    assert stream_cookies_file.read_text() == "# sentinel stream cookies\n"


def test_refresh_from_cookies_file_refused_probe_on_fresh_install_writes_nothing(tmp_path):
    cookies_file = tmp_path / "cookies.txt"
    _write_netscape_cookie_file(cookies_file)

    auth_file = tmp_path / "auth.json"
    stream_cookies_file = tmp_path / "stream_cookies.txt"
    # Neither auth_file nor stream_cookies_file exists beforehand.

    auth = _make_auth(tmp_path, auth_file=auth_file, stream_cookies_file=stream_cookies_file)

    mock_ytm = MagicMock()
    mock_ytm.get_account_info.return_value = {}

    with (
        _PATCH_SAPISID,
        patch("ytm_player.services.auth.YTMusic", side_effect=_slot_zero_only(mock_ytm)),
    ):
        result = auth._refresh_from_cookies_file(cookies_file, interactive=True)

    assert result is False
    assert not auth_file.exists()
    assert not stream_cookies_file.exists()
    assert not auth._account_file.exists()


# ── _atomic_write() direct coverage ──────────────────────────────────────────
#
# _commit_session only reaches _atomic_write's
# exception path via failures that occur BEFORE the temp file is created
# (e.g. a missing parent directory raises in os.open itself) — coverage
# tools mark the cleanup line as "hit" without ever exercising the actual
# risk: a real leftover .tmp-<pid> file after a failure that happens AFTER
# the temp file exists (write() raising, secure_chmod failing, os.replace
# failing). These tests call _atomic_write directly to close that gap.


def test_atomic_write_writes_and_replaces_target(tmp_path):
    target = tmp_path / "target.txt"

    _atomic_write(target, "w", _write_str("hello"), encoding="utf-8")

    assert target.read_text() == "hello"
    assert list(tmp_path.glob("*.tmp-*")) == []


def test_atomic_write_removes_temp_file_and_reraises_when_write_fails(tmp_path):
    target = tmp_path / "target.txt"

    def _boom(f):
        f.write("partial")
        raise ValueError("write failed mid-flight")

    with pytest.raises(ValueError, match="write failed mid-flight"):
        _atomic_write(target, "w", _boom, encoding="utf-8")

    assert not target.exists()
    assert list(tmp_path.glob("*.tmp-*")) == []


def test_atomic_write_leaves_preexisting_target_untouched_when_write_fails(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("original content")

    def _boom(f):
        f.write("should never land")
        raise ValueError("write failed mid-flight")

    with pytest.raises(ValueError):
        _atomic_write(target, "w", _boom, encoding="utf-8")

    assert target.read_text() == "original content"
    assert list(tmp_path.glob("*.tmp-*")) == []


def test_atomic_write_removes_temp_file_and_reraises_when_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "target.txt"

    def _raise_replace(*_args, **_kwargs):
        raise OSError("simulated os.replace failure")

    monkeypatch.setattr("ytm_player.services.auth.os.replace", _raise_replace)

    with pytest.raises(OSError, match="simulated os.replace failure"):
        _atomic_write(target, "w", _write_str("hello"), encoding="utf-8")

    assert not target.exists()
    assert list(tmp_path.glob("*.tmp-*")) == []


def test_atomic_write_removes_temp_file_and_reraises_when_chmod_fails(tmp_path, monkeypatch):
    target = tmp_path / "target.txt"

    def _raise_chmod(*_args, **_kwargs):
        raise OSError("simulated secure_chmod failure")

    monkeypatch.setattr("ytm_player.services.auth.secure_chmod", _raise_chmod)

    with pytest.raises(OSError, match="simulated secure_chmod failure"):
        _atomic_write(target, "w", _write_str("hello"), encoding="utf-8")

    assert not target.exists()
    assert list(tmp_path.glob("*.tmp-*")) == []
