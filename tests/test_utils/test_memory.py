"""limit_malloc_arenas caps glibc's arenas unless the user already chose a value."""

from __future__ import annotations

from unittest.mock import MagicMock

from ytm_player.utils import memory


def _fake_libc(monkeypatch, result=1):
    libc = MagicMock()
    libc.mallopt.return_value = result
    monkeypatch.setattr(memory.ctypes, "CDLL", lambda name: libc)
    monkeypatch.setattr(memory.sys, "platform", "linux")
    return libc


def test_sets_the_arena_cap_on_linux(monkeypatch):
    monkeypatch.delenv("MALLOC_ARENA_MAX", raising=False)
    libc = _fake_libc(monkeypatch)
    assert memory.limit_malloc_arenas() is True
    libc.mallopt.assert_called_once_with(-8, memory.MALLOC_ARENAS)


def test_an_environment_value_wins(monkeypatch):
    monkeypatch.setenv("MALLOC_ARENA_MAX", "4")
    libc = _fake_libc(monkeypatch)
    assert memory.limit_malloc_arenas() is False
    libc.mallopt.assert_not_called()


def test_other_platforms_are_left_alone(monkeypatch):
    monkeypatch.delenv("MALLOC_ARENA_MAX", raising=False)
    libc = _fake_libc(monkeypatch)
    monkeypatch.setattr(memory.sys, "platform", "darwin")
    assert memory.limit_malloc_arenas() is False
    libc.mallopt.assert_not_called()


def test_a_libc_without_mallopt_is_not_an_error(monkeypatch):
    monkeypatch.delenv("MALLOC_ARENA_MAX", raising=False)
    monkeypatch.setattr(memory.sys, "platform", "linux")

    def missing(name):
        raise OSError("no glibc")

    monkeypatch.setattr(memory.ctypes, "CDLL", missing)
    assert memory.limit_malloc_arenas() is False
