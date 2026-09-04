"""ContextPage expands get_artist's album/single previews into the full lists (#138)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from ytm_player.ui.pages.context import ContextPage


def _section(n: int, *, params: str | None = "p") -> dict:
    section = {"results": [{"title": f"a{i}", "browseId": f"MPREb_{i}"} for i in range(n)]}
    section["browseId"] = "MPAD_x"
    if params is not None:
        section["params"] = params
    return section


async def test_expands_albums_and_singles_from_see_all_pages():
    data = {"albums": _section(10), "singles": _section(10)}
    full = [{"title": f"full{i}", "browseId": f"MPREb_full{i}"} for i in range(27)]
    ytmusic = MagicMock()
    ytmusic.get_artist_albums = AsyncMock(return_value=full)

    await ContextPage._expand_artist_discography(ytmusic, data)

    assert data["albums"]["results"] == full
    assert data["singles"]["results"] == full
    assert ytmusic.get_artist_albums.await_count == 2
    ytmusic.get_artist_albums.assert_any_await("MPAD_x", "p")


async def test_section_without_params_is_left_alone():
    data = {"albums": _section(3, params=None)}
    ytmusic = MagicMock()
    ytmusic.get_artist_albums = AsyncMock(return_value=[{"title": "x"}])

    await ContextPage._expand_artist_discography(ytmusic, data)

    assert len(data["albums"]["results"]) == 3
    ytmusic.get_artist_albums.assert_not_awaited()


async def test_empty_full_fetch_keeps_preview():
    data = {"albums": _section(10)}
    ytmusic = MagicMock()
    ytmusic.get_artist_albums = AsyncMock(return_value=[])

    await ContextPage._expand_artist_discography(ytmusic, data)

    assert len(data["albums"]["results"]) == 10
