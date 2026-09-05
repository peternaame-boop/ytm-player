"""Tests for stream.py's stream-cookiejar existence check and the
StreamResolver concurrency fixes around resetting a live YoutubeDL instance
mid-resolve.

These guard against bugs confirmed in practice during development:
- A reset landing while a slow options-build was in flight being silently
  lost, permanently caching a stale YoutubeDL instance.
- Closing a live YoutubeDL instance out from under an in-flight resolve,
  freezing the UI thread.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ytm_player.config.settings import Settings
from ytm_player.services.stream import StreamResolver, _detect_stream_cookies


class TestDetectStreamCookies:
    def test_returns_path_when_file_exists(self, tmp_path, monkeypatch):
        cookiefile = tmp_path / "stream_cookies.txt"
        cookiefile.write_text("# cookies\n")
        monkeypatch.setattr("ytm_player.config.paths.STREAM_COOKIES_FILE", cookiefile)
        assert _detect_stream_cookies() == str(cookiefile)

    def test_returns_none_when_file_missing(self, tmp_path, monkeypatch):
        cookiefile = tmp_path / "stream_cookies.txt"
        monkeypatch.setattr("ytm_player.config.paths.STREAM_COOKIES_FILE", cookiefile)
        assert _detect_stream_cookies() is None


class TestResolveSyncShortCircuit:
    def test_keeps_retrying_for_ordinary_failures(self, monkeypatch):
        """Ordinary failures get the full retry budget — this must not regress."""
        resolver = StreamResolver()
        monkeypatch.setattr("ytm_player.services.stream.time.sleep", lambda _: None)

        call_count = 0

        def fake_try_resolve(url, video_id, attempt):
            nonlocal call_count
            call_count += 1
            return None

        resolver._try_resolve = fake_try_resolve

        result = resolver._resolve_sync("abc12345678")

        assert result is None
        assert call_count == 3


class TestResetYdlActiveResolvesGuard:
    """_reset_ydl() must not close a YoutubeDL instance a resolve is still
    using (confirmed in practice: closing mid-request froze the UI thread
    for however long the underlying socket/read timeout took)."""

    def test_does_not_close_while_resolve_active(self):
        resolver = StreamResolver()
        fake_ydl = MagicMock()
        resolver._ydl = fake_ydl
        resolver._active_resolves = 1

        resolver._reset_ydl()

        fake_ydl.close.assert_not_called()
        assert resolver._ydl is None  # still detached so the next build is fresh

    def test_closes_when_no_resolve_active(self):
        resolver = StreamResolver()
        fake_ydl = MagicMock()
        resolver._ydl = fake_ydl
        resolver._active_resolves = 0

        resolver._reset_ydl()

        fake_ydl.close.assert_called_once()
        assert resolver._ydl is None

    def test_bumps_generation_even_when_nothing_cached(self):
        """A reset landing while self._ydl is still None (nothing built
        yet) must not be silently lost — see _get_ydl's generation check."""
        resolver = StreamResolver()
        assert resolver._ydl is None
        generation_before = resolver._ydl_generation

        resolver._reset_ydl()

        assert resolver._ydl_generation == generation_before + 1

    def test_close_exception_is_swallowed(self):
        resolver = StreamResolver()
        fake_ydl = MagicMock()
        fake_ydl.close.side_effect = RuntimeError("boom")
        resolver._ydl = fake_ydl
        resolver._active_resolves = 0

        # Must not raise.
        resolver._reset_ydl()
        assert resolver._ydl is None


class TestGetYdlGenerationRace:
    """A reset landing while _build_ydl_opts() is still running (slow —
    it can trigger cookie extraction) must invalidate that build instead
    of caching stale settings permanently. See _get_ydl's docstring."""

    def test_reset_mid_build_forces_a_retry(self, monkeypatch):
        resolver = StreamResolver()
        build_calls = 0

        def fake_build_opts():
            nonlocal build_calls
            build_calls += 1
            if build_calls == 1:
                # Simulate a reset (e.g. clear_cache()) landing while this
                # "slow" build was in flight.
                resolver._reset_ydl()
            return {"quiet": True}

        resolver._build_ydl_opts = fake_build_opts

        fake_ydl_instance = MagicMock()
        with patch("yt_dlp.YoutubeDL", return_value=fake_ydl_instance) as mock_ydl_class:
            result = resolver._get_ydl()

        # First build's result was discarded; the retry's opts got used.
        assert build_calls == 2
        assert result is fake_ydl_instance
        mock_ydl_class.assert_called_once_with({"quiet": True})

    def test_no_race_builds_once(self):
        resolver = StreamResolver()
        resolver._build_ydl_opts = MagicMock(return_value={"quiet": True})

        with patch("yt_dlp.YoutubeDL", return_value=MagicMock()):
            resolver._get_ydl()
            resolver._get_ydl()  # second call hits the cached instance

        resolver._build_ydl_opts.assert_called_once()

    def test_another_build_winning_mid_flight_is_reused_not_rebuilt(self):
        """The OTHER half of the "brief double-checked-locking race" the
        docstring calls out as harmless: a second caller's unlocked
        _build_ydl_opts() finishes and assigns self._ydl WHILE this
        caller's own (unlocked) build is also in flight. This caller must
        return the winner's instance and never construct its own -- not
        just "eventually get an instance" (test_reset_mid_build_forces_a_retry
        covers the generation-mismatch path; this covers the plain
        already-assigned path, which is a different branch in _get_ydl)."""
        resolver = StreamResolver()
        winner_instance = MagicMock()

        def fake_build_opts():
            # Simulate a second thread's build finishing first and
            # winning the race while THIS build was still in flight.
            resolver._ydl = winner_instance
            return {"quiet": True}

        resolver._build_ydl_opts = fake_build_opts

        with patch("yt_dlp.YoutubeDL") as mock_ydl_class:
            result = resolver._get_ydl()

        assert result is winner_instance
        mock_ydl_class.assert_not_called()

    def test_real_get_ydl_increments_active_resolves_on_each_call(self):
        """_get_ydl() owns the _active_resolves increment now (moved out of
        _try_resolve() to close a TOCTOU gap) -- assert it against the REAL
        method, not a fake that manually increments on its caller's behalf
        (see TestActiveResolvesCounter, which necessarily fakes _get_ydl
        itself to isolate _try_resolve)."""
        resolver = StreamResolver()
        resolver._build_ydl_opts = MagicMock(return_value={"quiet": True})

        with patch("yt_dlp.YoutubeDL", return_value=MagicMock()):
            resolver._get_ydl()
            assert resolver._active_resolves == 1
            resolver._get_ydl()  # cached-instance fast path also increments
            assert resolver._active_resolves == 2


class TestActiveResolvesCounter:
    def test_try_resolve_increments_and_decrements_around_extract_info(self):
        resolver = StreamResolver()
        observed_during_call = []

        fake_ydl = MagicMock()

        def fake_extract_info(url, download=False):
            observed_during_call.append(resolver._active_resolves)
            return {
                "url": "https://example.com/stream",
                "acodec": "opus",
                "abr": 128,
                "duration": 200,
                "ext": "webm",
            }

        fake_ydl.extract_info = fake_extract_info

        def fake_get_ydl():
            # _get_ydl() now increments _active_resolves itself, atomically
            # with handing out the reference — see _get_ydl's docstring.
            resolver._active_resolves += 1
            return fake_ydl

        resolver._get_ydl = fake_get_ydl

        resolver._try_resolve("https://example.com", "abc12345678", 0)

        assert observed_during_call == [1]
        assert resolver._active_resolves == 0

    def test_active_resolves_decrements_even_on_exception(self):
        resolver = StreamResolver()
        fake_ydl = MagicMock()
        fake_ydl.extract_info.side_effect = RuntimeError("boom")

        def fake_get_ydl():
            resolver._active_resolves += 1
            return fake_ydl

        resolver._get_ydl = fake_get_ydl

        resolver._try_resolve("https://example.com", "abc12345678", 0)

        assert resolver._active_resolves == 0

    def test_get_ydl_raising_leaves_active_resolves_untouched(self):
        """If _get_ydl() itself raises (opts build or the YoutubeDL()
        constructor failing) -- as opposed to extract_info() raising --
        no increment ever happened, so _try_resolve()'s finally must not
        attempt a decrement either."""
        resolver = StreamResolver()
        resolver._get_ydl = MagicMock(side_effect=RuntimeError("boom"))

        result = resolver._try_resolve("https://example.com", "abc12345678", 0)

        assert result is None
        assert resolver._active_resolves == 0


class TestBuildYdlOptsCookieInjection:
    """_build_ydl_opts()'s cookie-injection logic had no direct test
    before this file; every existing test in this file targets
    _detect_stream_cookies or _get_ydl/_reset_ydl in
    isolation, none of them assert what actually ends up in the opts
    dict."""

    def test_injects_cookiefile_when_cache_available(self, monkeypatch):
        settings = Settings()
        monkeypatch.setattr("ytm_player.services.stream.get_settings", lambda: settings)
        with patch(
            "ytm_player.services.stream._detect_stream_cookies",
            return_value="/fake/path/stream_cookies.txt",
        ) as mock_detect:
            opts = StreamResolver()._build_ydl_opts()
        mock_detect.assert_called_once()
        assert opts["cookiefile"] == "/fake/path/stream_cookies.txt"
        assert "cookiesfrombrowser" not in opts

    def test_no_cookiejar_file_injects_nothing(self, monkeypatch):
        settings = Settings()
        monkeypatch.setattr("ytm_player.services.stream.get_settings", lambda: settings)
        with patch(
            "ytm_player.services.stream._detect_stream_cookies",
            return_value=None,
        ) as mock_detect:
            opts = StreamResolver()._build_ydl_opts()
        mock_detect.assert_called_once()
        assert "cookiefile" not in opts
        assert "cookiesfrombrowser" not in opts

    def test_skips_detection_when_cookies_file_already_configured(self, monkeypatch):
        settings = Settings()
        settings.yt_dlp.cookies_file = "/home/user/manual_cookies.txt"
        monkeypatch.setattr("ytm_player.services.stream.get_settings", lambda: settings)
        with patch("ytm_player.services.stream._detect_stream_cookies") as mock_detect:
            opts = StreamResolver()._build_ydl_opts()
        mock_detect.assert_not_called()
        from ytm_player.services.yt_dlp_options import normalize_cookiefile

        assert opts["cookiefile"] == normalize_cookiefile("/home/user/manual_cookies.txt")


class TestClientAndFormatSelection:
    """Regression guard for the client list and format selector.

    The client/format selection regressed silently once before (commit
    8450505 -> 3efbd66); these tests exist so a future refactor can't do
    the same."""

    def test_player_client_is_default_and_android(self, monkeypatch):
        settings = Settings()
        monkeypatch.setattr("ytm_player.services.stream.get_settings", lambda: settings)
        with patch("ytm_player.services.stream._detect_stream_cookies", return_value=None):
            opts = StreamResolver()._build_ydl_opts()
        assert opts["extractor_args"]["youtube"]["player_client"] == ["default", "android"]

    def test_every_tier_prefers_bestaudio_with_a_combined_fallback(self, monkeypatch):
        """bestaudio first (real audio-only formats on authenticated
        resolves), then a combined audio+video format so clients that expose
        no audio-only stream still resolve to something playable."""
        from ytm_player.services.stream import QUALITY_FORMATS

        assert QUALITY_FORMATS == {
            "high": "bestaudio/best[vcodec!=none][acodec!=none]",
            "medium": "bestaudio[abr<=128]/bestaudio/best[vcodec!=none][acodec!=none]",
            "low": "bestaudio[abr<=64]/bestaudio/best[vcodec!=none][acodec!=none]",
        }

        settings = Settings()
        monkeypatch.setattr("ytm_player.services.stream.get_settings", lambda: settings)
        for tier, selector in QUALITY_FORMATS.items():
            with patch("ytm_player.services.stream._detect_stream_cookies", return_value=None):
                opts = StreamResolver(tier)._build_ydl_opts()
            assert opts["format"] == selector

    def test_formats_are_valid_yt_dlp_selector_syntax(self):
        """Parsing through yt-dlp's own selector grammar catches composition
        mistakes (unbalanced brackets, bad operators) at negligible cost —
        no network I/O, pure syntax check."""
        import yt_dlp

        from ytm_player.services.stream import QUALITY_FORMATS

        ydl = yt_dlp.YoutubeDL({"quiet": True})
        for selector in QUALITY_FORMATS.values():
            ydl.build_format_selector(selector)  # raises SyntaxError if malformed
