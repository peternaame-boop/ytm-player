"""Tests for new settings fields added in Phase 1 and Phase 2."""

import sys

import pytest

from ytm_player.config.settings import (
    DiscordSettings,
    LastFMSettings,
    LyricsSettings,
    PlaybackSettings,
    Settings,
)


class TestPhase1Fields:
    def test_gapless_default(self):
        assert PlaybackSettings().gapless is True

    def test_api_timeout_default(self):
        assert PlaybackSettings().api_timeout == 15

    def test_gapless_round_trip(self, tmp_config_dir):
        path = tmp_config_dir / "config.toml"
        s = Settings()
        s.playback.gapless = False
        s.playback.api_timeout = 30
        s.save(path)

        loaded = Settings.load(path)
        assert loaded.playback.gapless is False
        assert loaded.playback.api_timeout == 30


class TestPhase2Fields:
    def test_discord_defaults(self):
        d = DiscordSettings()
        assert d.enabled is False
        assert d.client_id == ""

    def test_lastfm_defaults(self):
        lf = LastFMSettings()
        assert lf.enabled is False
        assert lf.api_key == ""
        assert lf.api_secret == ""
        assert lf.session_key == ""
        assert lf.username == ""
        assert lf.password_hash == ""

    def test_lastfm_round_trip(self, tmp_config_dir):
        path = tmp_config_dir / "config.toml"
        s = Settings()
        s.lastfm.enabled = True
        s.lastfm.api_key = "test_key"
        s.lastfm.api_secret = "test_secret"
        s.lastfm.username = "myuser"
        s.save(path)

        loaded = Settings.load(path)
        assert loaded.lastfm.enabled is True
        assert loaded.lastfm.api_key == "test_key"
        assert loaded.lastfm.api_secret == "test_secret"
        assert loaded.lastfm.username == "myuser"

    def test_discord_round_trip(self, tmp_config_dir):
        path = tmp_config_dir / "config.toml"
        s = Settings()
        s.discord.enabled = True
        s.discord.client_id = "1234567890"
        s.save(path)

        loaded = Settings.load(path)
        assert loaded.discord.enabled is True
        assert loaded.discord.client_id == "1234567890"


class TestLyricsSettings:
    def test_lyrics_defaults(self):
        assert LyricsSettings().transliteration is False

    def test_lyrics_in_settings(self):
        s = Settings()
        assert s.lyrics.transliteration is False

    def test_lyrics_round_trip(self, tmp_config_dir):
        path = tmp_config_dir / "config.toml"
        s = Settings()
        s.lyrics.transliteration = True
        s.save(path)

        loaded = Settings.load(path)
        assert loaded.lyrics.transliteration is True

    def test_lyrics_from_partial_toml(self, tmp_config_dir):
        path = tmp_config_dir / "config.toml"
        path.write_text("[lyrics]\ntransliteration = true\n")
        loaded = Settings.load(path)
        assert loaded.lyrics.transliteration is True

    def test_missing_lyrics_section_uses_default(self, tmp_config_dir):
        path = tmp_config_dir / "config.toml"
        path.write_text("[playback]\ndefault_volume = 50\n")
        loaded = Settings.load(path)
        assert loaded.lyrics.transliteration is False


class TestFilePermissions:
    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows NTFS doesn't honor POSIX file mode bits",
    )
    def test_saved_file_permissions(self, tmp_config_dir):
        import os
        import stat

        path = tmp_config_dir / "config.toml"
        Settings().save(path)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600
