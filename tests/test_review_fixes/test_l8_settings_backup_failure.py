"""C5: a corrupt config whose backup fails must not be overwritten with defaults.

``Path.rename`` raises on Windows whenever ``config.toml.bak`` already exists.
The error was swallowed and defaults were then written over the original, so
the user's settings were gone with no backup of them.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ytm_player.config.settings import Settings

_CORRUPT = b'[lastfm]\nsession_key = "users-real-key"\n[ui\ntheme = "nord"\n'
_OLD_BACKUP = b"# old backup from months ago\n"


def test_failed_backup_rename_leaves_the_original_untouched(tmp_path, monkeypatch, caplog):
    path = tmp_path / "config.toml"
    backup = tmp_path / "config.toml.bak"
    path.write_bytes(_CORRUPT)
    backup.write_bytes(_OLD_BACKUP)

    rename_calls: list[tuple[Path, Path]] = []

    def failing_rename(self, target):
        # Windows semantics, on every platform: the target exists.
        rename_calls.append((Path(self), Path(target)))
        raise FileExistsError(17, "Cannot create a file when that file already exists")

    monkeypatch.setattr(Path, "rename", failing_rename)

    with caplog.at_level(logging.WARNING, logger="ytm_player.config.settings"):
        loaded = Settings.load(path)

    assert rename_calls == [(path, backup)]
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
