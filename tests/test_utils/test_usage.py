"""UsageSampler turns CPU seconds and a clock into a percentage of one core."""

from __future__ import annotations

import pytest

from ytm_player.utils.usage import Usage, UsageSampler, format_usage


def _sampler(cpu: list[float], clock: list[float], rss: int | None = 200 * 1024 * 1024):
    cpu_iter, clock_iter = iter(cpu), iter(clock)
    return UsageSampler(
        cpu_seconds=lambda: next(cpu_iter), clock=lambda: next(clock_iter), rss_bytes=lambda: rss
    )


def test_cpu_is_the_share_of_one_core_since_the_last_sample():
    sampler = _sampler(cpu=[10.0, 10.1, 10.5], clock=[100.0, 102.0, 104.0])
    assert sampler.sample().cpu_percent == pytest.approx(5.0)
    assert sampler.sample().cpu_percent == pytest.approx(20.0)


def test_no_elapsed_time_reports_zero():
    sampler = _sampler(cpu=[1.0, 2.0], clock=[5.0, 5.0])
    assert sampler.sample().cpu_percent == 0.0


def test_format_shows_cpu_and_memory():
    assert format_usage(Usage(cpu_percent=3.4, rss_bytes=214 * 1024 * 1024)) == "CPU 3%  MEM 214M"


def test_format_drops_unknown_memory():
    assert format_usage(Usage(cpu_percent=12.6, rss_bytes=None)) == "CPU 13%"


def test_rss_reads_this_process_on_linux():
    import sys

    from ytm_player.utils.usage import _rss_bytes

    rss = _rss_bytes()
    if sys.platform.startswith("linux"):
        assert rss is not None and rss > 1024 * 1024
    else:
        assert rss is None
