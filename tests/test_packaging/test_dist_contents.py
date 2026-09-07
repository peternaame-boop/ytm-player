"""Packaging safeguards.

hatchling selects archive members from the working tree: it honours
``.gitignore`` but reads neither ``.git/info/exclude`` nor the git index, so an
untracked file would be packaged. The sdist is therefore an explicit allowlist
and ``*.local.md`` is ignored, which keeps the known private-file locations out
of a local build. These tests check that configuration and report any other
untracked inclusion from the file selection alone: this checkout is never
archived, and the only archives built here come from a synthetic tree.
"""

from __future__ import annotations

import os
import subprocess
import tarfile
import zipfile
from pathlib import Path, PurePath

import pytest

hatchling_build = pytest.importorskip("hatchling.build")
from hatchling.builders.plugin.interface import IncludedFile  # noqa: E402
from hatchling.builders.sdist import SdistBuilder  # noqa: E402
from hatchling.builders.wheel import WheelBuilder  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

# Without these the sdist could not build, install or run its own tests.
REQUIRED_SDIST_FILES = (
    "src/ytm_player/__init__.py",
    "tests/conftest.py",
    "docs/installation.md",
    ".github/scripts/extract_changelog.py",
    "scripts/regenerate_srcinfo.py",
    "aur/PKGBUILD",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
)

# Files hatchling adds to every sdist on its own when they exist at the root.
AUTOMATIC_SDIST_FILES = ("pyproject.toml", ".gitignore", "README.md", "LICENSE")

# Names modelled on real local notes and scratch files; the contents are synthetic.
PRIVATE_FILES = (
    "REVIEW-TASKS.local.md",
    "HANDOFF.local.md",
    "CODE-REVIEW-2026-01-01.md",
    ".env",
    "probes/repro_private.py",
    "docs/private-notes.local.md",
    "src/ytm_player/notes.local.md",
    "tests/fixtures.local.md",
    "reasoning/notes.md",
    "headers.txt",
)


def _posix(relative_path: str) -> str:
    """Native separators become ``/``; hatchling's empty sentinel for external files stays."""
    return PurePath(relative_path).as_posix() if relative_path else ""


def _selected(builder_cls: type, root: Path) -> set[str]:
    """POSIX-form relative paths *builder_cls* would package from *root*.

    Mirrors the start of ``BuilderInterface.build``: the builder's default build
    data is applied so that automatic inclusions (``pyproject.toml``, the readme
    and license files, ``.gitignore``, a root ``hatch_build.py``) are enumerated
    as well, but no build hook runs and nothing is archived. Configuration and
    project metadata are read as for any build; the selected payloads are not.
    Generated archive metadata (``PKG-INFO``, the wheel ``.dist-info``) is not a
    project file and never appears here.
    """
    builder = builder_cls(str(root))
    build_data = builder.get_default_build_data()
    builder.set_build_data_defaults(build_data)
    with builder.config.set_build_data(build_data):
        return {_posix(file.relative_path) for file in builder.recurse_included_files()}


def _unexpected(root: Path, tracked: set[str]) -> list[str]:
    """Paths the sdist or wheel would package from *root* that are not in *tracked*."""
    selected = _selected(SdistBuilder, root) | _selected(WheelBuilder, root)
    return sorted(selected - tracked)


def _git_tracked(root: Path) -> set[str]:
    def git(*args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(root), *args], capture_output=True, check=True
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            pytest.skip(f"not a git checkout: {exc}")
        return result.stdout.decode("utf-8")

    toplevel = Path(git("rev-parse", "--show-toplevel").strip())
    if toplevel.resolve() != root.resolve():
        pytest.skip(f"{root} is not the root of a git checkout (found {toplevel})")
    return {name for name in git("ls-files", "-z").split("\0") if name}


def _synthetic_project(tmp_path: Path) -> tuple[Path, tuple[str, ...], tuple[str, ...]]:
    """A tree that uses this checkout's packaging config, with synthetic private files.

    Returns the root, the stub files inside the allowlist and the stubs outside it.
    """
    project = tmp_path / "project"
    allowlisted = (
        "src/ytm_player/__init__.py",
        "tests/conftest.py",
        "docs/index.md",
        "scripts/tool.py",
        ".github/scripts/tool.py",
        "aur/PKGBUILD",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
    )
    outside_allowlist = ("CLAUDE.md", ".github/workflows/ci.yml")
    contents = {rel: "" for rel in allowlisted + outside_allowlist}
    contents["src/ytm_player/__init__.py"] = '__version__ = "0.0.0"\n'
    contents["README.md"] = "# stub\n"
    contents["LICENSE"] = "stub\n"
    for name in ("pyproject.toml", ".gitignore"):
        contents[name] = (REPO_ROOT / name).read_text(encoding="utf-8")
    for rel in PRIVATE_FILES:
        contents[rel] = "synthetic private content\n"
    for rel, text in contents.items():
        target = project / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return project, allowlisted, outside_allowlist


def test_checkout_selection_contains_only_tracked_files():
    unexpected = _unexpected(REPO_ROOT, _git_tracked(REPO_ROOT))
    assert not unexpected, (
        "files git does not track would be packaged: " + ", ".join(unexpected) + ". "
        "Review them; stage intended project files explicitly, or exclude private files."
    )


def test_sdist_selection_covers_required_files():
    selected = _selected(SdistBuilder, REPO_ROOT)
    missing = sorted(set(REQUIRED_SDIST_FILES + AUTOMATIC_SDIST_FILES) - selected)
    assert not missing, f"required files are not selected for the sdist: {missing}"


def test_selection_paths_use_posix_separators(monkeypatch):
    native = os.path.join("src", "pkg", "mod.py")
    files = [
        IncludedFile(str(REPO_ROOT / native), native, native),
        IncludedFile(str(REPO_ROOT.parent / "external.txt"), "", "external.txt"),
    ]
    literal = "odd\\name.py"  # on POSIX a backslash is part of the name, not a separator
    if os.name != "nt":
        files.append(IncludedFile(str(REPO_ROOT / literal), literal, literal))
    monkeypatch.setattr(SdistBuilder, "recurse_included_files", lambda self: iter(files))

    selected = _selected(SdistBuilder, REPO_ROOT)

    assert "src/pkg/mod.py" in selected
    assert "" in selected
    if os.name != "nt":
        assert literal in selected


def test_automatic_inclusions_are_reported(tmp_path):
    project, allowlisted, _ = _synthetic_project(tmp_path)
    (project / "hatch_build.py").write_text("# synthetic, never executed\n", encoding="utf-8")
    tracked = {*allowlisted, *AUTOMATIC_SDIST_FILES}

    selected = _selected(SdistBuilder, project)

    assert set(AUTOMATIC_SDIST_FILES) <= selected
    assert "PKG-INFO" not in selected
    assert _unexpected(project, tracked) == ["hatch_build.py"]


def test_synthetic_build_excludes_private_files(tmp_path, monkeypatch):
    """Build archives from a synthetic tree that uses this checkout's packaging config."""
    project, allowlisted, outside_allowlist = _synthetic_project(tmp_path)
    out = tmp_path / "dist"
    out.mkdir()
    monkeypatch.chdir(project)
    sdist_name = hatchling_build.build_sdist(str(out))
    wheel_name = hatchling_build.build_wheel(str(out))
    with tarfile.open(out / sdist_name) as tar:
        sdist = {
            member.name.split("/", 1)[1]
            for member in tar.getmembers()
            if member.isfile() and "/" in member.name
        }
    with zipfile.ZipFile(out / wheel_name) as archive:
        wheel = set(archive.namelist())

    assert not set(PRIVATE_FILES) & sdist
    assert not {name for name in sdist | wheel if name.endswith(".local.md")}
    assert not set(outside_allowlist) & sdist
    assert {*AUTOMATIC_SDIST_FILES, *allowlisted} <= sdist
    assert all(name.startswith(("ytm_player/", "ytm_player-")) for name in wheel)
    assert "ytm_player/__init__.py" in wheel
