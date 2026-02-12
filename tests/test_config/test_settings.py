"""Tests for ytm_player.config.settings."""


from ytm_player.config.settings import Settings


class TestDefaultValues:
    def test_defaults(self):
        s = Settings()
        assert s.general.startup_page == "library"
        assert s.playback.audio_quality == "high"
        assert s.playback.default_volume == 80
        assert s.playback.autoplay is True
        assert s.search.default_mode == "music"
        assert s.cache.enabled is True
        assert s.cache.max_size_mb == 1024
        assert s.ui.album_art is True
        assert s.notifications.enabled is True
        assert s.mpris.enabled is True


class TestSaveLoadRoundTrip:
    def test_round_trip(self, tmp_config_dir):
        path = tmp_config_dir / "config.toml"

        original = Settings()
        original.playback.default_volume = 50
        original.general.startup_page = "search"
        original.cache.max_size_mb = 2048
        original.save(path)

        loaded = Settings.load(path)
        assert loaded.playback.default_volume == 50
        assert loaded.general.startup_page == "search"
        assert loaded.cache.max_size_mb == 2048
        # Other defaults preserved.
        assert loaded.playback.audio_quality == "high"
        assert loaded.ui.album_art is True

    def test_save_creates_file(self, tmp_config_dir):
        path = tmp_config_dir / "new_config.toml"
        assert not path.exists()
        Settings().save(path)
        assert path.exists()


class TestPartialToml:
    def test_partial_preserves_defaults(self, tmp_config_dir):
        path = tmp_config_dir / "config.toml"
        path.write_text('[playback]\ndefault_volume = 42\n')

        loaded = Settings.load(path)
        assert loaded.playback.default_volume == 42
        # All other fields should be defaults.
        assert loaded.playback.audio_quality == "high"
        assert loaded.general.startup_page == "library"

    def test_missing_file_creates_default(self, tmp_config_dir):
        path = tmp_config_dir / "nonexistent.toml"
        loaded = Settings.load(path)
        assert loaded.playback.default_volume == 80
        assert path.exists()  # Default file should be created.
