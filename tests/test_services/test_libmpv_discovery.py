"""Tests for Homebrew libmpv discovery (#90, #101, #104)."""

import sys

from ytm_player.services import player
from ytm_player.utils.doctor import _libmpv_status


class TestFindBrewLibmpv:
    def test_returns_none_when_no_brew_dirs(self, monkeypatch, tmp_path):
        monkeypatch.setattr(player, "_BREW_LIB_DIRS", (tmp_path / "missing",))
        assert player._find_brew_libmpv() is None

    def test_finds_linux_so(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        lib = tmp_path / "libmpv.so.2"
        lib.touch()
        monkeypatch.setattr(player, "_BREW_LIB_DIRS", (tmp_path,))
        assert player._find_brew_libmpv() == str(lib)

    def test_finds_macos_dylib(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "darwin")
        lib = tmp_path / "libmpv.2.dylib"
        lib.touch()
        monkeypatch.setattr(player, "_BREW_LIB_DIRS", (tmp_path,))
        assert player._find_brew_libmpv() == str(lib)

    def test_prefers_unversioned_name(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        (tmp_path / "libmpv.so.2").touch()
        plain = tmp_path / "libmpv.so"
        plain.touch()
        monkeypatch.setattr(player, "_BREW_LIB_DIRS", (tmp_path,))
        assert player._find_brew_libmpv() == str(plain)

    def test_ignores_unrelated_files(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        (tmp_path / "libfoo.so").touch()
        monkeypatch.setattr(player, "_BREW_LIB_DIRS", (tmp_path,))
        assert player._find_brew_libmpv() is None


class TestDoctorLibmpvStatus:
    def test_reports_status_line(self):
        line = _libmpv_status()
        assert line.startswith("libmpv: ")
        # In the test environment mpv is either genuinely loadable (OK) or
        # stubbed (NOT LOADABLE) — both are valid doctor outputs.
        assert ("OK" in line) or ("NOT LOADABLE" in line)
