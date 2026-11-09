"""C5: a corrupt config must be backed up before defaults replace it, or left alone.

``Path.rename`` raises on Windows whenever ``config.toml.bak`` already exists.
The error was swallowed and defaults were then written over the original, so
the user's settings were gone with no backup of them. The backup now replaces
an older ``.bak``; if even that fails, the corrupt file is not rewritten.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ytm_player.config.settings import Settings

_CORRUPT = b'[lastfm]\nsession_key = "users-real-key"\n[ui\ntheme = "nord"\n'
_OLD_BACKUP = b"# old backup from months ago\n"


def _windows_rename(self, target):
    # Windows semantics, on every platform: rename refuses an existing target.
    raise FileExistsError(17, "Cannot create a file when that file already exists")


def test_older_backup_is_replaced_and_defaults_are_written(tmp_path, monkeypatch, caplog):
    path = tmp_path / "config.toml"
    backup = tmp_path / "config.toml.bak"
    path.write_bytes(_CORRUPT)
    backup.write_bytes(_OLD_BACKUP)
    monkeypatch.setattr(Path, "rename", _windows_rename)

    with caplog.at_level(logging.WARNING, logger="ytm_player.config.settings"):
        loaded = Settings.load(path)

    # The newest damaged config is the backup now; the older one is gone.
    assert backup.read_bytes() == _CORRUPT
    assert any("Backed up corrupted config" in r.getMessage() for r in caplog.records)
    # Defaults were written and load cleanly, so a later save() in the same
    # run has nothing unbacked-up left to overwrite.
    assert loaded == Settings()
    assert path.read_bytes() != _CORRUPT
    assert Settings.load(path) == Settings()
    assert backup.read_bytes() == _CORRUPT


def test_failed_backup_leaves_the_original_untouched(tmp_path, monkeypatch, caplog):
    path = tmp_path / "config.toml"
    backup = tmp_path / "config.toml.bak"
    path.write_bytes(_CORRUPT)
    backup.write_bytes(_OLD_BACKUP)

    replace_calls: list[tuple[Path, Path]] = []

    def failing_replace(self, target):
        replace_calls.append((Path(self), Path(target)))
        raise PermissionError(13, "Permission denied")

    # No way to move the file aside at all (a locked file, a read-only directory).
    monkeypatch.setattr(Path, "rename", failing_replace)
    monkeypatch.setattr(Path, "replace", failing_replace)

    with caplog.at_level(logging.WARNING, logger="ytm_player.config.settings"):
        loaded = Settings.load(path)

    assert replace_calls == [(path, backup)]
    # The only copy of the user's settings is still there, byte for byte.
    assert path.read_bytes() == _CORRUPT
    assert backup.read_bytes() == _OLD_BACKUP
    # This run uses in-memory defaults.
    assert loaded == Settings()
    assert loaded.lastfm.session_key == ""
    # The failed backup is reported instead of swallowed.
    assert any(
        "Could not back up" in r.getMessage() and str(path) in r.getMessage()
        for r in caplog.records
    )


def test_successful_backup_still_recreates_defaults(tmp_path):
    """Guard for the fix itself: the normal recovery path is unchanged."""
    path = tmp_path / "config.toml"
    backup = tmp_path / "config.toml.bak"
    path.write_bytes(_CORRUPT)

    loaded = Settings.load(path)

    assert backup.read_bytes() == _CORRUPT
    assert loaded == Settings()
    assert Settings.load(path) == Settings()
    assert path.read_bytes() != _CORRUPT
