"""A malformed cookies file never puts a cookie value into the log.

``http.cookiejar`` reports a row it cannot parse as ``invalid Netscape
format cookies file '<path>': '<row>'``; the row holds the cookie's value.
With a malformed ``[yt_dlp] cookies_file`` yt-dlp passed that text to our
logger and into its ``DownloadError``, and both reached ytm.log (which
``ytm doctor`` prints). The session cookie jar load logged the same text
through ``exc_info``, and the download service logged the ``DownloadError``
and returned its text for the failure toast.

yt-dlp is real here: the failure happens while it loads the cookie jar,
before any request. The socket guard fails the test if one is attempted.
"""

from __future__ import annotations

import logging
import socket
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yt_dlp

from ytm_player.config.settings import YtDlpSettings
from ytm_player.services import download, stream
from ytm_player.services.auth import VouchedJar
from ytm_player.services.download import DownloadService
from ytm_player.services.stream import StreamResolver, _YtDlpLogger

_SECRET = "s3cr3t-cookie-value-9f2c"
# Seven fields, but the domain flag (FALSE) contradicts the leading dot:
# yt-dlp's own row check accepts it and http.cookiejar then rejects it.
_BAD_ROW = f".youtube.com\tFALSE\t/\tTRUE\t1893456000\tSAPISID\t{_SECRET}"
_BAD_JAR = f"# Netscape HTTP Cookie File\n{_BAD_ROW}\n"
_URL = "https://music.youtube.com/watch?v=abcdefghijk"

pytestmark = pytest.mark.filterwarnings("ignore:http.cookiejar bug")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("the test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


@pytest.fixture(autouse=True)
def _no_real_session_files(monkeypatch, tmp_path):
    # Same isolation as tests/test_services/test_stream.py.
    monkeypatch.setattr(stream, "_detect_stream_cookies", lambda: None)
    monkeypatch.setattr(
        stream,
        "_detect_session_record",
        lambda: (str(tmp_path / "auth.json"), str(tmp_path / "account.json")),
    )


def _settings(monkeypatch, **yt_dlp_settings) -> None:
    settings = SimpleNamespace(yt_dlp=YtDlpSettings(**yt_dlp_settings))
    monkeypatch.setattr(stream, "get_settings", lambda: settings)
    monkeypatch.setattr(download, "get_settings", lambda: settings)


@pytest.fixture
def bad_cookies_file(monkeypatch, tmp_path):
    """``[yt_dlp] cookies_file`` pointing at a file with the malformed row."""
    path = tmp_path / "cookies.txt"
    path.write_text(_BAD_JAR, encoding="utf-8")
    _settings(monkeypatch, cookies_file=str(path))
    return path


def _logged(caplog) -> list[str]:
    """Every captured record as the log file would hold it, traceback included."""
    formatter = logging.Formatter("%(levelname)s %(name)s %(message)s")
    return [formatter.format(record) for record in caplog.records]


def _resolve_once(caplog) -> list[str]:
    """One resolve attempt through the resolver's own YoutubeDL."""
    with caplog.at_level(logging.DEBUG, logger="ytm_player.services.stream"):
        assert StreamResolver()._try_resolve(_URL, "abcdefghijk", 0) is None
    return _logged(caplog)


# ── [yt_dlp] cookies_file: yt-dlp's own error report ────────────────────


def test_yt_dlp_error_report_for_a_bad_cookies_file_withholds_the_row(bad_cookies_file, caplog):
    lines = [line for line in _resolve_once(caplog) if " yt-dlp: " in line]

    # yt-dlp did report the failure through the logger it was given...
    reports = [line for line in lines if "invalid Netscape format cookies file" in line]
    assert reports, lines
    # ...and no line it sent holds the cookie's value.
    assert not [line for line in lines if _SECRET in line]
    assert all("details withheld" in line for line in reports)


def test_download_error_for_a_bad_cookies_file_withholds_the_row(bad_cookies_file, caplog):
    lines = [line for line in _resolve_once(caplog) if "yt-dlp download error" in line]

    assert len(lines) == 1, lines
    assert "video_id=abcdefghijk (attempt 1)" in lines[0]
    assert _SECRET not in lines[0]
    assert "invalid Netscape format cookies file (details withheld" in lines[0]


def test_nothing_logged_for_a_bad_cookies_file_holds_the_value(bad_cookies_file, caplog):
    assert not [line for line in _resolve_once(caplog) if _SECRET in line]


@pytest.mark.parametrize("level", ["debug", "info", "warning", "error"])
def test_every_yt_dlp_logger_level_withholds_the_row(level, caplog):
    message = f"ERROR: invalid Netscape format cookies file 'cookies.txt': {_BAD_ROW!r}"

    with caplog.at_level(logging.DEBUG, logger="ytm_player.services.stream"):
        getattr(_YtDlpLogger(), level)(message)

    (line,) = _logged(caplog)
    assert _SECRET not in line
    # What came before the cookie-file error is kept.
    assert "yt-dlp: ERROR: invalid Netscape format cookies file (details withheld" in line


@pytest.mark.parametrize("level", ["debug", "info", "warning", "error"])
def test_other_yt_dlp_messages_pass_through_unchanged(level, caplog):
    """Guard for the fix (passes on master too)."""
    message = "ERROR: [youtube] abcdefghijk: Video unavailable. This video is private"

    with caplog.at_level(logging.DEBUG, logger="ytm_player.services.stream"):
        getattr(_YtDlpLogger(), level)(message)

    (record,) = caplog.records
    assert record.getMessage() == f"yt-dlp: {message}"


# ── The sign-in hint on a DownloadError ─────────────────────────────────


def _resolver_raising(message: str) -> StreamResolver:
    resolver = StreamResolver()
    ydl = MagicMock()
    ydl.extract_info.side_effect = yt_dlp.utils.DownloadError(message)
    resolver._get_ydl = MagicMock(return_value=ydl)  # type: ignore[method-assign]
    return resolver


def test_sign_in_hint_still_follows_a_matching_download_error(monkeypatch, caplog):
    """Guard for the fix (passes on master too)."""
    monkeypatch.setattr(stream, "_session_cookiejar_signature", lambda: ("jar", 1, 2, 3))
    resolver = _resolver_raising("Sign in to confirm you are not a bot")

    with caplog.at_level(logging.WARNING, logger="ytm_player.services.stream"):
        assert resolver._try_resolve(_URL, "abcdefghijk", 0) is None

    (line,) = _logged(caplog)
    assert "Sign in to confirm you are not a bot" in line
    assert "stream cookiejar may be stale/revoked" in line


def test_sign_in_hint_is_decided_on_the_text_before_it_is_withheld(monkeypatch, caplog):
    monkeypatch.setattr(stream, "_session_cookiejar_signature", lambda: ("jar", 1, 2, 3))
    resolver = _resolver_raising(
        f"ERROR: invalid Netscape format cookies file 'cookies.txt': {_BAD_ROW!r} (sign in)"
    )

    with caplog.at_level(logging.WARNING, logger="ytm_player.services.stream"):
        assert resolver._try_resolve(_URL, "abcdefghijk", 0) is None

    (line,) = _logged(caplog)
    assert _SECRET not in line
    assert "stream cookiejar may be stale/revoked" in line


# ── The session cookie jar (use_session_cookies) ────────────────────────


def _vouched_session_jar(monkeypatch, payload: bytes) -> None:
    """Session cookies on, with *payload* as the vouched jar's bytes."""
    _settings(monkeypatch, use_session_cookies=True)
    signature = ("stream_cookies.txt", (1, 2, 3), (4, 5, 6), (7, 8, 9))
    monkeypatch.setattr(stream, "_session_cookiejar_signature", lambda: signature)
    monkeypatch.setattr(
        "ytm_player.services.auth.read_vouched_stream_jar",
        lambda _path, _session: VouchedJar(payload, signature),
    )


def test_a_bad_row_in_the_session_jar_is_named_but_not_logged(monkeypatch, caplog):
    _vouched_session_jar(monkeypatch, _BAD_JAR.encode("utf-8"))
    resolver = StreamResolver()

    with caplog.at_level(logging.DEBUG, logger="ytm_player.services.stream"):
        resolver._build_ydl(resolver._build_ydl_opts())

    lines = _logged(caplog)
    warnings = [line for line in lines if "Could not load stream cookiejar" in line]
    assert len(warnings) == 1, lines
    assert "stream_cookies.txt" in warnings[0]
    assert "LoadError" in warnings[0]
    assert not [line for line in lines if _SECRET in line]


def test_other_session_jar_failures_keep_their_traceback(monkeypatch, caplog):
    """Guard for the fix (passes on master too): only a LoadError's text is
    withheld."""
    _vouched_session_jar(monkeypatch, b"\xff\xfe not utf-8")
    resolver = StreamResolver()

    with caplog.at_level(logging.DEBUG, logger="ytm_player.services.stream"):
        resolver._build_ydl(resolver._build_ydl_opts())

    (warning,) = [line for line in _logged(caplog) if "Could not load stream cookiejar" in line]
    assert "Traceback (most recent call last)" in warning
    assert "UnicodeDecodeError" in warning


# ── The download service ────────────────────────────────────────────────


def _download_once(tmp_path, caplog):
    """One download attempt through the service's own YoutubeDL."""
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    with caplog.at_level(logging.DEBUG, logger="ytm_player"):
        result = DownloadService(download_dir=downloads)._download_sync("abcdefghijk")
    return result, [line for line in _logged(caplog) if "ytm_player.services.download" in line]


def test_download_with_a_bad_cookies_file_logs_no_cookie_value(bad_cookies_file, tmp_path, caplog):
    result, lines = _download_once(tmp_path, caplog)

    assert not result.success
    failures = [line for line in lines if "Download failed for abcdefghijk" in line]
    assert len(failures) == 1, lines
    assert not [line for line in lines if _SECRET in line]
    assert "invalid Netscape format cookies file (details withheld" in failures[0]


def test_download_with_a_bad_cookies_file_returns_no_cookie_value(
    bad_cookies_file, tmp_path, caplog
):
    """The returned error becomes the "Download failed: ..." toast."""
    result, _lines = _download_once(tmp_path, caplog)

    assert not result.success
    assert result.error is not None
    assert _SECRET not in result.error
    assert "invalid Netscape format cookies file (details withheld" in result.error


def test_other_download_errors_pass_through_unchanged(monkeypatch, tmp_path, caplog):
    """Guard for the fix (passes on master too)."""
    _settings(monkeypatch)
    message = "ERROR: [youtube] abcdefghijk: Video unavailable. This video is private"
    ydl = MagicMock()
    ydl.__enter__.return_value = ydl
    ydl.__exit__.return_value = False
    ydl.download.side_effect = yt_dlp.utils.DownloadError(message)
    monkeypatch.setattr(yt_dlp, "YoutubeDL", MagicMock(return_value=ydl))

    result, lines = _download_once(tmp_path, caplog)

    assert not result.success
    assert result.error == message
    (failure,) = lines
    assert failure.endswith(f"Download failed for abcdefghijk: {message}")
