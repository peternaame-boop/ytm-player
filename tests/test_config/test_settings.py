"""Tests for Settings TOML serialization, atomic writes, and load behavior."""

from __future__ import annotations

import sys

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:
    # Python 3.10 backport via PyPI
    import tomli as tomllib  # pyright: ignore[reportMissingImports]

from ytm_player.config.settings import Settings, prefetch_ahead_count


class TestDefaultValues:
    def test_defaults(self):
        s = Settings()
        assert s.general.startup_page == "library"
        assert s.playback.audio_quality == "high"
        assert s.playback.default_volume == 80
        assert s.playback.autoplay is True
        assert s.search.default_mode == "music"
        assert s.yt_dlp.use_session_cookies is False
        assert s.yt_dlp.cookie_browser == ""
        assert s.cache.enabled is True
        assert s.cache.max_size_mb == 1024
        assert s.ui.theme == "ytm-dark"
        assert s.ui.album_art is True
        assert s.notifications.enabled is True
        assert s.mpris.enabled is True


def test_ui_settings_theme_default():
    """theme defaults to the bundled ytm-dark Textual theme."""
    from ytm_player.config.settings import UISettings

    ui = UISettings()
    assert ui.theme == "ytm-dark"


def test_settings_load_respects_ui_theme(tmp_config_dir):
    """theme in [ui] is honoured as the declarative default."""
    config_file = tmp_config_dir / "config.toml"
    config_file.write_text('[ui]\ntheme = "catppuccin-mocha"\n', encoding="utf-8")

    s = Settings.load(config_file)

    assert s.ui.theme == "catppuccin-mocha"


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


def test_settings_load_respects_cookie_browser(tmp_config_dir):
    """cookie_browser in [yt_dlp] is honoured as the selected cookie source."""
    config_file = tmp_config_dir / "config.toml"
    config_file.write_text('[yt_dlp]\ncookie_browser = "brave"\n', encoding="utf-8")

    s = Settings.load(config_file)

    assert s.yt_dlp.cookie_browser == "brave"


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
    @pytest.mark.parametrize(
        "value",
        [
            "{title}\n{artist}",
            "\r\n\t\b\f",
            "".join(chr(code) for code in range(32)) + "\x7f",
            '"quoted" \\ path \\n literal',
            "Björk 日本語 🎵 \u0085\u2028\u2029",
            '"\n[ui]\ntheme = "unexpected"\n#',
            "",
        ],
    )
    def test_string_values_round_trip_without_reset(self, tmp_path, value):
        path = tmp_path / "config.toml"
        original = Settings()
        original.notifications.format = value
        original.ui.theme = "nord"
        original.playback.default_volume = 37
        original.save(path)

        # Parse directly too: Settings.load recovers invalid TOML with defaults.
        with path.open("rb") as source:
            assert tomllib.load(source)["notifications"]["format"] == value
        loaded = Settings.load(path)
        assert loaded.notifications.format == value
        assert loaded.ui.theme == "nord"
        assert loaded.playback.default_volume == 37
        assert not path.with_suffix(".toml.bak").exists()

    def test_list_strings_use_the_same_escaping(self):
        from ytm_player.config.settings import _format_toml_value

        values = ["a\nb", "\x00\x7f", '"quoted"', "🎵", "C:\\path"]
        assert tomllib.loads(f"values = {_format_toml_value(values)}")["values"] == values

    def test_round_trip(self, tmp_config_dir):
        path = tmp_config_dir / "config.toml"

        original = Settings()
        original.playback.default_volume = 50
        original.general.startup_page = "search"
        original.cache.max_size_mb = 2048
        original.ui.theme = "textual-dark"
        original.save(path)

        loaded = Settings.load(path)
        assert loaded.playback.default_volume == 50
        assert loaded.general.startup_page == "search"
        assert loaded.cache.max_size_mb == 2048
        assert loaded.ui.theme == "textual-dark"
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

    def test_save_falls_back_to_direct_write_when_replace_is_denied(self, tmp_path, monkeypatch):
        """Windows may deny os.replace when config.toml is held by another process."""
        import os

        config_path = tmp_path / "config.toml"
        config_path.write_text("stale", encoding="utf-8")

        def denied_replace(_src, _dst):
            raise PermissionError("access denied")

        monkeypatch.setattr(os, "replace", denied_replace)

        s = Settings()
        s.ui.theme = "textual-dark"
        s.save(config_path)

        with open(config_path, "rb") as f:
            data = tomllib.load(f)
        assert data["ui"]["theme"] == "textual-dark"
        assert not config_path.with_suffix(config_path.suffix + ".tmp").exists()


class TestPrefetchNextSetting:
    """[cache] prefetch_next accepts bool or int (clamped to 0..MAX_PREFETCH_NEXT)."""

    def test_default_is_true_and_resolves_to_one(self):
        s = Settings()
        assert s.cache.prefetch_next is True
        assert prefetch_ahead_count(s.cache.prefetch_next) == 1

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (True, 1),
            (False, 0),
            (0, 0),
            (-3, 0),
            (1, 1),
            (3, 3),
            (5, 5),
            (99, 5),
            (object(), 1),
        ],
    )
    def test_prefetch_ahead_count_normalizes_values(self, raw, expected):
        assert prefetch_ahead_count(raw) == expected

    @pytest.mark.parametrize(
        ("toml_literal", "loaded_value", "ahead_count"),
        [
            ("true", True, 1),
            ("false", False, 0),
            ("0", 0, 0),
            ("-4", 0, 0),
            ("3", 3, 3),
            ("25", 5, 5),
        ],
    )
    def test_load_accepts_bool_and_clamps_int(
        self, tmp_config_dir, toml_literal, loaded_value, ahead_count
    ):
        path = tmp_config_dir / "config.toml"
        path.write_text(f"[cache]\nprefetch_next = {toml_literal}\n", encoding="utf-8")

        loaded = Settings.load(path)
        assert loaded.cache.prefetch_next == loaded_value
        assert type(loaded.cache.prefetch_next) is type(loaded_value)
        assert prefetch_ahead_count(loaded.cache.prefetch_next) == ahead_count

    @pytest.mark.parametrize("invalid_literal", ['"three"', "1.5", "[1, 2]"])
    def test_load_rejects_invalid_types_and_keeps_default(self, tmp_config_dir, invalid_literal):
        path = tmp_config_dir / "config.toml"
        path.write_text(f"[cache]\nprefetch_next = {invalid_literal}\n", encoding="utf-8")

        loaded = Settings.load(path)
        assert loaded.cache.prefetch_next is True

    @pytest.mark.parametrize("value", [True, False, 0, 4])
    def test_save_load_preserves_bool_vs_int_distinction(self, tmp_config_dir, value):
        path = tmp_config_dir / "config.toml"
        s = Settings()
        s.cache.prefetch_next = value
        s.save(path)

        loaded = Settings.load(path)
        assert loaded.cache.prefetch_next == value
        assert type(loaded.cache.prefetch_next) is type(value)
