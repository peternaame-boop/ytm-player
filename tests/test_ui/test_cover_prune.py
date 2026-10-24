"""The cover cache stays under [cache] covers_max_mb, oldest use first."""

from __future__ import annotations

import os

from ytm_player.ui.widgets import cover

MB = 1024 * 1024
NOW = 1_000_000.0


def _file(directory, name, mb, age_seconds):
    path = directory / name
    path.write_bytes(b"\0" * int(mb * MB))
    os.utime(path, (NOW - age_seconds, NOW - age_seconds))
    return path


def test_prune_removes_least_recently_used_until_under_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(cover, "THUMB_DIR", tmp_path)
    oldest = _file(tmp_path, "a.img", 1, 300)
    middle = _file(tmp_path, "b.img", 1, 200)
    newest = _file(tmp_path, "c.img", 1, 100)
    assert cover.prune_covers(2, now=NOW) == 1
    assert not oldest.exists()
    assert middle.exists() and newest.exists()


def test_prune_keeps_everything_under_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(cover, "THUMB_DIR", tmp_path)
    paths = [_file(tmp_path, f"{n}.img", 1, 100) for n in "ab"]
    assert cover.prune_covers(5, now=NOW) == 0
    assert all(p.exists() for p in paths)


def test_prune_removes_stale_temp_files_and_keeps_fresh_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(cover, "THUMB_DIR", tmp_path)
    stale = _file(tmp_path, "a.img.123.part", 0.01, 2 * 3600)
    fresh = _file(tmp_path, "b.img.456.part", 0.01, 5)
    cover.prune_covers(100, now=NOW)
    assert not stale.exists()
    assert fresh.exists()


def test_prune_tolerates_a_missing_cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cover, "THUMB_DIR", tmp_path / "absent")
    assert cover.prune_covers(1, now=NOW) == 0


def test_a_cache_hit_marks_the_cover_as_recently_used(tmp_path, monkeypatch):
    monkeypatch.setattr(cover, "THUMB_DIR", tmp_path)
    url = "https://example.invalid/cover.jpg"
    path = cover.cached_path(url)
    path.write_bytes(b"jpeg")
    os.utime(path, (NOW, NOW))
    assert cover.fetch_thumbnail(url) == path
    assert path.stat().st_mtime > NOW
