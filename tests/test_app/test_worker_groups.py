"""Every exclusive worker names a group of its own.

Textual's ``exclusive=True`` cancels the other workers in the same *group*
on the same node, and a worker started without ``group=`` lands in
``"default"``. An exclusive worker with no group therefore cancels every
unrelated default-group worker on its node: a Discovery Mix cancelled the
track that was starting, Start Radio cut a playlist's tail fetch short,
typing during a search cancelled the search. The static check fails on any
such site; the mounted tests pin the Textual semantics the fix relies on.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.worker import WorkerState

import ytm_player

SRC = Path(ytm_player.__file__).parent


def _string_constants(tree: ast.Module) -> dict[str, str | None]:
    """Names bound to a string literal at module or class level (None when bound twice)."""
    found: dict[str, str | None] = {}

    def visit(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                visit(node.body)
                continue
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            else:
                continue
            if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    name = target.id
                    found[name] = (
                        value.value if found.get(name, value.value) == value.value else None
                    )

    visit(tree.body)
    return found


def _group_of(value: ast.expr, constants: dict[str, str | None]) -> str | None:
    """The group a ``group=`` keyword names: a literal, or a constant of the same module."""
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    if isinstance(value, ast.Attribute):
        return constants.get(value.attr)
    if isinstance(value, ast.Name):
        return constants.get(value.id)
    return None


def _exclusive_workers() -> list[tuple[str, str | None]]:
    """Every ``run_worker(..., exclusive=True)`` call in the package, with its group."""
    sites: list[tuple[str, str | None]] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = _string_constants(tree)
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "run_worker"
            ):
                continue
            keywords = {k.arg: k.value for k in node.keywords if k.arg}
            exclusive = keywords.get("exclusive")
            if not (isinstance(exclusive, ast.Constant) and exclusive.value is True):
                continue
            group = keywords.get("group")
            site = f"{path.relative_to(SRC.parent)}:{node.lineno}"
            sites.append((site, None if group is None else _group_of(group, constants)))
    return sites


def test_every_exclusive_worker_names_a_group_of_its_own() -> None:
    offenders = [site for site, group in _exclusive_workers() if not group or group == "default"]
    assert offenders == []


def test_the_scan_sees_the_exclusive_sites() -> None:
    # A scan that found nothing would pass the check above for the wrong reason.
    sites = {site.split(":")[0] for site, _group in _exclusive_workers()}
    assert {"ytm_player/app/_keys.py", "ytm_player/ui/pages/browse.py"} <= sites
    assert len(_exclusive_workers()) >= 25


# ── Textual semantics ──────────────────────────────────────────────────


class _Harness(App[None]):
    def compose(self) -> ComposeResult:
        with Vertical(id="page"):
            yield Widget(id="section")


async def _job(started: asyncio.Event) -> None:
    started.set()
    await asyncio.Event().wait()


async def test_an_exclusive_worker_in_its_own_group_leaves_default_group_work_running() -> None:
    app = _Harness()
    async with app.run_test() as pilot:
        started = asyncio.Event()
        job = app.run_worker(_job(started), name="fetch-remaining-playlist")
        await asyncio.wait_for(started.wait(), 2)

        app.run_worker(asyncio.sleep(0), group="discovery-mix", exclusive=True)
        await pilot.pause()
        await pilot.pause()

        assert job.state == WorkerState.RUNNING
        job.cancel()


async def test_an_exclusive_worker_without_a_group_cancels_default_group_work() -> None:
    """The shape every site the static check covers used to have."""
    app = _Harness()
    async with app.run_test() as pilot:
        started = asyncio.Event()
        job = app.run_worker(_job(started), name="fetch-remaining-playlist")
        await asyncio.wait_for(started.wait(), 2)

        app.run_worker(asyncio.sleep(0), name="discovery-mix", exclusive=True)
        await pilot.pause()
        await pilot.pause()

        assert job.state == WorkerState.CANCELLED


async def test_cancel_group_targets_the_node_that_started_the_worker() -> None:
    """BrowsePage runs the Charts pill fetch itself, so it cancels it on itself."""
    app = _Harness()
    async with app.run_test() as pilot:
        page = app.query_one("#page")
        section = app.query_one("#section")
        started = asyncio.Event()
        worker = page.run_worker(_job(started), group="charts-shelf", exclusive=True)
        await asyncio.wait_for(started.wait(), 2)

        assert app.workers.cancel_group(section, "charts-shelf") == []
        assert worker.state == WorkerState.RUNNING
        assert app.workers.cancel_group(page, "charts-shelf") == [worker]
        await pilot.pause()
        assert worker.state == WorkerState.CANCELLED
