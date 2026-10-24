"""The API Cookie header keeps only sign-in cookies, so a busy browser profile stays under YouTube's header limit."""

from http.cookiejar import Cookie

from ytm_player.services.auth import _AUTH_COOKIE_NAMES, _auth_cookie_header


def _cookie(name: str, value: str) -> Cookie:
    return Cookie(
        0, name, value, None, False, ".youtube.com", True, True, "/", True,
        True, None, False, None, None, {},
    )  # fmt: skip


def test_drops_non_auth_cookies_from_a_large_profile():
    noise = [_cookie(f"ST-{i}", "x" * 900) for i in range(200)]
    auth = [_cookie("SAPISID", "abc"), _cookie("SID", "def"), _cookie("LOGIN_INFO", "ghi")]

    header = _auth_cookie_header(noise + auth)

    assert header == "SAPISID=abc; SID=def; LOGIN_INFO=ghi"
    assert len(header) < 8192


def test_keeps_one_value_per_name():
    header = _auth_cookie_header([_cookie("SAPISID", "old"), _cookie("SAPISID", "new")])

    assert header == "SAPISID=new"


def test_sapisid_is_in_the_kept_set():
    # ytmusicapi derives the Authorization header from SAPISID.
    assert "SAPISID" in _AUTH_COOKIE_NAMES
