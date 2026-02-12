"""Tests for new settings fields added in Phase 1 and Phase 2."""

from ytm_player.config.settings import (
    DiscordSettings,
    LastFMSettings,
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
        s.save(path)

        loaded = Settings.load(path)
        assert loaded.discord.enabled is True


class TestFilePermissions:
    def test_saved_file_permissions(self, tmp_config_dir):
        import os
        import stat

        path = tmp_config_dir / "config.toml"
        Settings().save(path)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600
