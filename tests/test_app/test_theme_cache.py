"""Theme.toml caching tests for _app.get_css_variables.

The TOML file is read on every CSS variable resolution by Textual.
Without caching, this hits disk + TOML parser repeatedly during
re-renders.  Cache + mtime invalidation should make repeat calls free
and pick up file edits naturally.
"""

from __future__ import annotations

import os


def _write_theme(path, primary: str = "#ff0000", surface: str = "#1a1a1a") -> None:
    path.write_text(
        f'[colors]\nprimary = "{primary}"\nsurface = "{surface}"\n',
        encoding="utf-8",
    )


class TestThemeTomlCache:
    def test_cache_is_reused_when_file_unchanged(self, tmp_path, monkeypatch):
        """Repeated reads with no file change should re-use the cache."""
        from ytm_player.app import _app as app_module

        theme_file = tmp_path / "theme.toml"
        _write_theme(theme_file)
        monkeypatch.setattr(app_module, "THEME_FILE", theme_file, raising=False)
        # Ensure cache starts empty.
        if hasattr(app_module, "_theme_toml_cache"):
            app_module._theme_toml_cache = None
            app_module._theme_toml_mtime = None

        # First read populates the cache.
        result1 = app_module._read_theme_toml_cached()
        # Second read with no changes returns the same dict.
        result2 = app_module._read_theme_toml_cached()

        assert result1 == result2
        assert result1.get("primary") == "#ff0000"

    def test_cache_invalidated_on_mtime_change(self, tmp_path, monkeypatch):
        """When the file mtime changes, the cache should re-read."""
        from ytm_player.app import _app as app_module

        theme_file = tmp_path / "theme.toml"
        _write_theme(theme_file, primary="#ff0000")
        monkeypatch.setattr(app_module, "THEME_FILE", theme_file, raising=False)
        if hasattr(app_module, "_theme_toml_cache"):
            app_module._theme_toml_cache = None
            app_module._theme_toml_mtime = None

        first = app_module._read_theme_toml_cached()
        assert first.get("primary") == "#ff0000"

        _write_theme(theme_file, primary="#00ff00")
        # Force a distinct, newer mtime so the cache invalidates immediately —
        # no need to wait out the filesystem's mtime resolution (~1s on ext4).
        bumped = theme_file.stat().st_mtime + 10
        os.utime(theme_file, (bumped, bumped))

        second = app_module._read_theme_toml_cached()
        assert second.get("primary") == "#00ff00"

    def test_missing_file_returns_empty_dict(self, tmp_path, monkeypatch):
        from ytm_player.app import _app as app_module

        theme_file = tmp_path / "missing.toml"
        monkeypatch.setattr(app_module, "THEME_FILE", theme_file, raising=False)
        if hasattr(app_module, "_theme_toml_cache"):
            app_module._theme_toml_cache = None
            app_module._theme_toml_mtime = None

        result = app_module._read_theme_toml_cached()
        assert result == {}
