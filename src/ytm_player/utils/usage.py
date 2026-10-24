"""CPU and memory use of the ytm process, for the top bar readout."""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Usage:
    cpu_percent: float  # of one core, so it can pass 100 on a multi-core machine
    rss_bytes: int | None  # None where the platform has no cheap way to read it


def _cpu_seconds() -> float:
    times = os.times()
    return times.user + times.system


def _rss_bytes() -> int | None:
    """Resident memory from /proc. Linux only."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        with open("/proc/self/statm", encoding="ascii") as statm:
            resident_pages = int(statm.read().split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return resident_pages * os.sysconf("SC_PAGE_SIZE")


class UsageSampler:
    """Reports CPU use since the previous sample, and current resident memory."""

    def __init__(
        self,
        cpu_seconds: Callable[[], float] = _cpu_seconds,
        clock: Callable[[], float] = time.monotonic,
        rss_bytes: Callable[[], int | None] = _rss_bytes,
    ) -> None:
        self._cpu_seconds = cpu_seconds
        self._clock = clock
        self._rss_bytes = rss_bytes
        self._last = (cpu_seconds(), clock())

    def sample(self) -> Usage:
        cpu, now = self._cpu_seconds(), self._clock()
        last_cpu, last_now = self._last
        self._last = (cpu, now)
        elapsed = now - last_now
        percent = max(0.0, (cpu - last_cpu) / elapsed * 100) if elapsed > 0 else 0.0
        return Usage(cpu_percent=percent, rss_bytes=self._rss_bytes())


def format_usage(usage: Usage) -> str:
    """'CPU 3%  MEM 214M', or CPU alone where memory is unknown."""
    text = f"CPU {usage.cpu_percent:.0f}%"
    if usage.rss_bytes is not None:
        text += f"  MEM {usage.rss_bytes / (1024 * 1024):.0f}M"
    return text
