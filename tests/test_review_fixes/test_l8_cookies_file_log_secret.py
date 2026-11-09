"""T5 (C1/S3): a malformed cookies file must not put a cookie value in the log.

http.cookiejar's LoadError quotes the whole offending line. That text went
into a WARNING, which `ytm doctor` prints for pasting into public issues.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from ytm_player.services.auth import AuthManager

_SECRET = "FAKEsecretSAPISIDvalue0123456789/AbCdEf"

_MALFORMED_LINES = {
    # An editor turned the TABs into spaces.
    "tabs_to_spaces": f".youtube.com TRUE / TRUE 1893456000 __Secure-3PAPISID {_SECRET}",
    # A field went missing (6 TAB-separated fields instead of 7).
    "truncated": f".youtube.com\tTRUE\t/\tTRUE\t__Secure-1PSID\t{_SECRET}",
}


@pytest.mark.parametrize("case", sorted(_MALFORMED_LINES))
# The stdlib parser reports the bad line through warnings.warn as well
# (a traceback without the line's text); not what this test is about.
@pytest.mark.filterwarnings("ignore:http.cookiejar bug")
def test_malformed_cookies_line_value_never_reaches_the_log(tmp_path: Path, caplog, case: str):
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text(
        "# Netscape HTTP Cookie File\n"
        ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tPREF\tharmless\n"
        f"{_MALFORMED_LINES[case]}\n",
        encoding="utf-8",
    )
    auth_file = tmp_path / "headers_auth.json"
    auth = AuthManager(auth_file=auth_file, stream_cookies_file=tmp_path / "stream_cookies.txt")

    with caplog.at_level(logging.DEBUG):
        assert auth._refresh_from_cookies_file(cookies_file) is False

    assert not auth_file.exists()
    # caplog.text is every record as a handler would write it, traceback included.
    assert _SECRET not in caplog.text
    for record in caplog.records:
        assert _SECRET not in record.getMessage()
        assert _SECRET not in repr(record.args)
        assert record.exc_info is None
    # The failure is still reported, with the file it is about.
    warnings = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and r.name == "ytm_player.services.auth"
    ]
    assert any("Failed to load cookies file" in m and str(cookies_file) in m for m in warnings)
