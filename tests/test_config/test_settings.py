"""Tests for Settings TOML serialization, atomic writes, and load behavior."""

from __future__ import annotations

import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    # Python 3.10 backport via PyPI
    import tomli as tomllib  # pyright: ignore[reportMissingImports]

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


def test_ui_settings_show_selection_info_default():
    """show_selection_info defaults to True."""
    from ytm_player.config.settings import UISettings

    ui = UISettings()
    assert ui.show_selection_info is True


def test_settings_load_respects_show_selection_info_false(tmp_config_dir):
    """show_selection_info = false in config.toml is honoured."""
    config_file = tmp_config_dir / "config.toml"
    config_file.write_text("[ui]\nshow_selection_info = false\n", encoding="utf-8")
    s = Settings.load(config_file)
    assert s.ui.show_selection_info is False


def test_ui_settings_show_queue_source_default():
    """show_queue_source defaults to True."""
    from ytm_player.config.settings import UISettings

    ui = UISettings()
    assert ui.show_queue_source is True


def test_settings_load_respects_show_queue_source_false(tmp_config_dir):
    """show_queue_source = false in config.toml is honoured."""
    config_file = tmp_config_dir / "config.toml"
    config_file.write_text("[ui]\nshow_queue_source = false\n", encoding="utf-8")
    s = Settings.load(config_file)
    assert s.ui.show_queue_source is False


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
        path.write_text("[playback]\ndefault_volume = 42\n")

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


class TestAtomicWrites:
    """Settings.save must use atomic writes — no partial files left
    on the user's disk if the process is killed mid-write."""

    def test_save_does_not_leave_temp_file_on_success(self, tmp_path):
        config_path = tmp_path / "config.toml"
        s = Settings()
        s.save(config_path)

        # No leftover .tmp file.
        leftover = list(tmp_path.glob("*.tmp"))
        assert leftover == [], f"Atomic write left temp files: {leftover}"

        # The actual config file should exist and be valid TOML.
        assert config_path.exists()
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
        assert "general" in data

    def test_save_uses_replace_not_direct_write(self, tmp_path, monkeypatch):
        """The implementation should call os.replace (atomic) at some
        point during save, not just path.write_text."""
        import os

        config_path = tmp_path / "config.toml"
        replace_calls: list = []
        original_replace = os.replace

        def tracking_replace(src, dst):
            replace_calls.append((str(src), str(dst)))
            original_replace(src, dst)

        monkeypatch.setattr("os.replace", tracking_replace)

        s = Settings()
        s.save(config_path)

        # At least one os.replace into the final config_path.
        targets = [dst for _src, dst in replace_calls]
        assert str(config_path) in targets, (
            f"expected os.replace into {config_path}, got replace calls: {replace_calls}"
        )
