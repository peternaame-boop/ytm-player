"""T6 (``ytm import`` CLI flow): outside text is printed as typed.

``run_import`` printed playlist, track, artist and candidate names and
exception text through a Rich ``Console`` with markup on: ``[/x]`` aborted the
command with ``MarkupError``, ``[live]`` vanished, ``[link=...]`` became a
hyperlink and the app's own `` [video]`` suffix was swallowed. The app's own
styles (bold names, red error prefixes) must still apply.
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from rich.ansi import AnsiDecoder
from rich.color import Color
from rich.console import Console
from rich.text import Text

import ytm_player.config.settings as settings_module
import ytm_player.services.spotify_import as spotify_service

_URL = "https://open.spotify.com/playlist/abc123"

_NAMES = [
    pytest.param("Song [live]", id="lowercase-bracket-word"),
    pytest.param("Song [Live] [MV]", id="capitalised-bracket-words"),
    pytest.param("a [/x] b", id="malformed-closing-tag"),
    pytest.param("[link=https://example.invalid]x[/link]", id="link-tag"),
    pytest.param("tail\\", id="trailing-backslash"),
]

_ERROR_TEXT = "HTTP 404 [/x] [live] [link=https://example.invalid]x[/link] tail\\"


class _FakeYTMusic:
    def __init__(self, name: str, create_error: Exception | None = None) -> None:
        self.name = name
        self.create_error = create_error

    def search(self, query: str, filter: str | None = None, limit: int = 5) -> list[dict]:
        if query.endswith(" Missing"):
            return []
        return [
            {
                "videoId": "vid00000001",
                "title": self.name,
                "artists": [{"name": self.name}],
                "duration_seconds": 180,
                "resultType": "video",
            }
        ]

    def create_playlist(self, title, description, privacy_status="PRIVATE", video_ids=None):
        if self.create_error is not None:
            raise self.create_error
        return "PLnew"


def _recording_console(monkeypatch) -> Console:
    console = Console(
        file=io.StringIO(),
        force_terminal=True,
        color_system="standard",
        _environ={},
        width=400,
        legacy_windows=False,
    )
    monkeypatch.setattr(spotify_service, "Console", lambda: console)
    return console


def _patch_flow(monkeypatch, name: str, tracks: list[dict], ytmusic) -> None:
    monkeypatch.setattr(spotify_service, "_HAS_SPOTIFY_DEPS", True)
    monkeypatch.setattr(spotify_service, "extract_spotify_tracks", lambda url: (name, tracks))
    monkeypatch.setattr(spotify_service, "_fuzzy_score", lambda sp, candidate: 50)
    monkeypatch.setattr(spotify_service, "YTMusic", ytmusic)
    monkeypatch.setattr(
        settings_module,
        "get_settings",
        lambda: SimpleNamespace(general=SimpleNamespace(brand_account_id="")),
    )


def _script_prompts(monkeypatch, answers: list) -> None:
    pending = list(answers)

    def _prompt(text, **kwargs):
        if text == "Playlist name":
            return kwargs["default"]
        return pending.pop(0)

    monkeypatch.setattr(click, "prompt", _prompt)


def _lines(console: Console) -> tuple[str, list[Text]]:
    raw = console.file.getvalue()  # type: ignore[attr-defined]
    return raw, list(AnsiDecoder().decode(raw))


def _all_styled(line: Text, fragment: str, wanted) -> bool:
    """True when every character of ``fragment`` in ``line`` has a ``wanted`` style."""
    start = line.plain.index(fragment)
    return all(
        any(span.start <= i < span.end and wanted(span.style) for span in line.spans)
        for i in range(start, start + len(fragment))
    )


def _bold(style) -> bool:
    return bool(style.bold)


def _red(style) -> bool:
    return style.color is not None and style.color.number == Color.parse("red").number


def _assert_no_links(raw: str, lines: list[Text]) -> None:
    assert "\x1b]8;" not in raw
    assert all(span.style.link is None for line in lines for span in line.spans)


@pytest.mark.parametrize("name", _NAMES)
def test_import_flow_prints_names_as_typed(monkeypatch, name) -> None:
    console = _recording_console(monkeypatch)
    tracks = [{"name": name, "artist": name}, {"name": name, "artist": "Missing"}]
    _patch_flow(monkeypatch, name, tracks, lambda *a, **k: _FakeYTMusic(name))
    # Pick candidate 1; then for the unmatched track: search again, query, pick 1.
    _script_prompts(monkeypatch, [1, 1, "custom query", 1])

    spotify_service.run_import(_URL, Path("unused-auth.json"))

    raw, lines = _lines(console)
    plain = [line.plain.rstrip() for line in lines]

    fetched = f'Fetched "{name}" (2 tracks)'
    assert fetched in plain
    assert _all_styled(lines[plain.index(fetched)], f'"{name}"', _bold)

    for header in (f'── "{name}" by {name} ──', f'── "{name}" by Missing ──'):
        assert header in plain
        assert _all_styled(lines[plain.index(header)], f'"{name}"', _bold)

    candidate = f"  1. {name} — {name} (3:00) [video]"
    assert plain.count(candidate) == 2  # the first search and the custom search

    status = f'Creating playlist "{name}" on YouTube Music...'
    assert any(line.endswith(" " + status) for line in plain)

    assert any(line.startswith("✓ Playlist created with 2 tracks") for line in plain)
    _assert_no_links(raw, lines)


def _run_until_error(monkeypatch, where: str) -> tuple[str, list[Text]]:
    console = _recording_console(monkeypatch)
    error = RuntimeError(_ERROR_TEXT)

    def _raise(*args, **kwargs):
        raise error

    create_error = error if where == "create" else None

    def _client(*args, **kwargs) -> _FakeYTMusic:
        return _FakeYTMusic("Song", create_error=create_error)

    tracks = [{"name": "Song", "artist": "Band"}]
    _patch_flow(monkeypatch, "Source", tracks, _raise if where == "init" else _client)
    if where == "fetch":
        monkeypatch.setattr(spotify_service, "extract_spotify_tracks", _raise)
    _script_prompts(monkeypatch, [1])

    spotify_service.run_import(_URL, Path("unused-auth.json"))
    return _lines(console)


@pytest.mark.parametrize(
    ("where", "prefix"),
    [
        pytest.param("fetch", "Failed to fetch Spotify playlist:", id="fetch"),
        pytest.param("init", "Failed to initialize YouTube Music client:", id="client-init"),
        pytest.param("create", "Failed to create playlist:", id="create"),
    ],
)
def test_import_errors_print_exception_text_as_typed(monkeypatch, where, prefix) -> None:
    raw, lines = _run_until_error(monkeypatch, where)
    plain = [line.plain.rstrip() for line in lines]

    expected = f"{prefix} {_ERROR_TEXT}"
    assert expected in plain
    assert _all_styled(lines[plain.index(expected)], prefix, _red)
    _assert_no_links(raw, lines)
