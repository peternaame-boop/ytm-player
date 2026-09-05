"""Tests for ytm_player.services.auth."""

import json
from unittest.mock import MagicMock

from ytm_player.services.auth import AuthManager, _normalize_raw_headers


class TestAutoRefresh:
    def test_reuses_cookies_found_during_browser_detection(self, tmp_path, monkeypatch):
        manager = AuthManager(config_dir=tmp_path, auth_file=tmp_path / "auth.json")
        cookies = [MagicMock()]
        jar = [MagicMock(), MagicMock()]
        detect = MagicMock(return_value=("brave", cookies, jar))
        save = MagicMock(return_value=True)
        monkeypatch.setattr(manager, "_detect_browser", detect)
        monkeypatch.setattr(manager, "_save_youtube_cookies", save)

        assert manager.try_auto_refresh()

        detect.assert_called_once_with()
        save.assert_called_once_with(cookies, stream_jar=jar)

    def test_silent_refresh_refuses_without_recorded_identity(self, tmp_path, monkeypatch, capsys):
        """No account.json (a session set up before identities were recorded):
        renewal is refused before any account is probed — see
        test_auth_identity.py for the full matrix."""
        auth_file = tmp_path / "auth.json"
        auth_file.write_text(json.dumps({"x-goog-authuser": "2"}))
        probed: list[int] = []

        class Cookie:
            name = "__Secure-3PAPISID"
            value = "test"

        def fake_ytmusic(path):
            probed.append(int(json.loads(open(path).read())["x-goog-authuser"]))
            return MagicMock()

        monkeypatch.setattr("ytm_player.services.auth.YTMusic", fake_ytmusic)
        manager = AuthManager(config_dir=tmp_path, auth_file=auth_file)

        assert manager._save_youtube_cookies([Cookie()]) is False
        assert probed == []
        assert json.loads(auth_file.read_text())["x-goog-authuser"] == "2"
        assert capsys.readouterr().out == ""


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
            ":authority\nmusic.youtube.com\n:method\nPOST\ncookie\nabc=123\nuser-agent\nMozilla/5.0"
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


class TestChromeDecodedBlock:
    """Chrome annotates x-client-data with a multi-line decoded protobuf.

    The block spans an odd number of lines, so before it was stripped it
    shifted the alternating name/value pairing of every header after it and
    ``x-goog-authuser`` — required by ytmusicapi — was parsed as a value.
    """

    # Name, value, then the annotation Chrome appends underneath it.
    DECODED = (
        "x-client-data\n"
        "CIm2yQEIprbJAQipncoBCMiWywEI\n"
        "Decoded:\n"
        "message ClientVariations {\n"
        "  // Active Google-visible variation IDs on this client. These are reported for\n"
        "  // analysis, but do not directly affect any server-side behavior.\n"
        "  repeated int32 variation_id = [3300105, 3300134];\n"
        "  repeated int32 trigger_variation_id = [101003180];\n"
        "}"
    )

    def test_authuser_survives_decoded_block(self):
        raw = f"cookie\nabc=123\n{self.DECODED}\nx-goog-authuser\n0\nx-origin\nhttps://music.youtube.com"
        result = _normalize_raw_headers(raw)
        assert "x-goog-authuser: 0" in result
        assert "cookie: abc=123" in result
        assert "x-origin: https://music.youtube.com" in result

    def test_decoded_body_not_emitted_as_headers(self):
        raw = f"cookie\nabc=123\n{self.DECODED}\nx-goog-authuser\n0"
        result = _normalize_raw_headers(raw)
        assert "Decoded" not in result
        assert "ClientVariations" not in result
        assert "variation_id" not in result
        assert "x-client-data: CIm2yQEIprbJAQipncoBCMiWywEI" in result

    def test_decoded_block_in_standard_format(self):
        raw = (
            "cookie: abc=123\n"
            "x-client-data: CIm2yQEIprbJAQipncoBCMiWywEI\n"
            "Decoded:\n"
            "message ClientVariations {\n"
            "  repeated int32 variation_id = [3300105];\n"
            "}\n"
            "x-goog-authuser: 0"
        )
        result = _normalize_raw_headers(raw)
        assert "x-goog-authuser: 0" in result
        assert "ClientVariations" not in result

    def test_multiple_decoded_blocks(self):
        raw = f"cookie\nabc=123\n{self.DECODED}\n{self.DECODED}\nx-goog-authuser\n0"
        result = _normalize_raw_headers(raw)
        assert "x-goog-authuser: 0" in result
        assert "ClientVariations" not in result

    def test_unterminated_block_keeps_following_headers(self):
        """No closing brace: leave the input alone rather than truncate it."""
        raw = "cookie\nabc=123\nDecoded:\nmessage ClientVariations {\nx-goog-authuser\n0"
        result = _normalize_raw_headers(raw)
        assert "cookie: abc=123" in result
        assert "0" in result

    def test_lone_closing_brace_is_not_a_block(self):
        raw = "cookie\nabc=123\n}\nsomething\nx-goog-authuser\n0"
        result = _normalize_raw_headers(raw)
        assert "cookie: abc=123" in result
        assert "x-goog-authuser: 0" in result
