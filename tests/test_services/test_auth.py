"""Tests for ytm_player.services.auth._normalize_raw_headers."""

from ytm_player.services.auth import _normalize_raw_headers


class TestStandardFormat:
    """Standard 'Name: Value' per line (Firefox / older Chrome)."""

    def test_standard_headers_preserved(self):
        raw = "cookie: abc=123\nauthorization: Bearer xyz"
        result = _normalize_raw_headers(raw)
        assert "cookie: abc=123" in result
        assert "authorization: Bearer xyz" in result

    def test_pseudo_headers_stripped(self):
        raw = (
            ":authority: music.youtube.com\n"
            ":method: POST\n"
            ":path: /youtubei/v1/browse\n"
            ":scheme: https\n"
            "cookie: abc=123\n"
            "authorization: Bearer xyz"
        )
        result = _normalize_raw_headers(raw)
        assert ":authority" not in result
        assert ":method" not in result
        assert ":path" not in result
        assert ":scheme" not in result
        assert "cookie: abc=123" in result
        assert "authorization: Bearer xyz" in result


class TestAlternatingLines:
    """Chrome 'Copy request headers' alternating name/value lines."""

    def test_alternating_lines_paired(self):
        raw = "cookie\nabc=123\nauthorization\nBearer xyz"
        result = _normalize_raw_headers(raw)
        assert "cookie: abc=123" in result
        assert "authorization: Bearer xyz" in result

    def test_pseudo_headers_stripped_in_alternating(self):
        raw = (
            ":authority\nmusic.youtube.com\n"
            ":method\nPOST\n"
            "cookie\nabc=123\n"
            "user-agent\nMozilla/5.0"
        )
        result = _normalize_raw_headers(raw)
        assert ":authority" not in result
        assert ":method" not in result
        assert "cookie: abc=123" in result
        assert "user-agent: Mozilla/5.0" in result


class TestEscapeSeparated:
    """Terminal paste with ^[E separators (single line)."""

    def test_caret_escape_separated(self):
        raw = "cookie^[Eabc=123^[Eauthorization^[EBearer xyz"
        result = _normalize_raw_headers(raw)
        assert "cookie: abc=123" in result
        assert "authorization: Bearer xyz" in result

    def test_pseudo_headers_stripped_in_escape_format(self):
        raw = ":authority^[Emusic.youtube.com^[Ecookie^[Eabc=123"
        result = _normalize_raw_headers(raw)
        assert ":authority" not in result
        assert "cookie: abc=123" in result


class TestEdgeCases:
    def test_empty_input_returns_empty(self):
        assert _normalize_raw_headers("") == ""

    def test_single_standard_header(self):
        result = _normalize_raw_headers("cookie: session=abc")
        assert result == "cookie: session=abc"

    def test_whitespace_only_returns_empty(self):
        assert _normalize_raw_headers("   \n   \n  ") == ""
