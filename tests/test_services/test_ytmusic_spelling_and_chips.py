"""The search spelling note and the Up next mix chips, read from raw responses.

The shapes are trimmed copies of live music.youtube.com responses (2026-10-11).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import make_ytmusic_service
from ytm_player.services.ytmusic import parse_queue_chips, parse_search_correction

SHOWING = {"contents": {"tabbedSearchResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
    "sectionListRenderer": {"contents": [{"itemSectionRenderer": {"contents": [{
        "showingResultsForRenderer": {
            "showingResultsFor": {"runs": [{"text": "Showing results for"}]},
            "correctedQuery": {"runs": [{"text": "beyonce", "italics": True}, {"text": " halo"}]},
            "correctedQueryEndpoint": {"searchEndpoint": {"query": "beyonce halo"}},
            "searchInsteadFor": {"runs": [{"text": "Search instead for"}]},
            "originalQuery": {"runs": [{"text": "beyonse halo"}]},
            "originalQueryEndpoint": {"searchEndpoint": {"query": "beyonse halo",
                                                         "params": "QgIIAQ%3D%3D"}},
        }}]}}]}}}}]}}}  # fmt: skip

DID_YOU_MEAN = {"contents": [{"itemSectionRenderer": {"contents": [{"didYouMeanRenderer": {
    "didYouMean": {"runs": [{"text": "Did you mean:"}]},
    "correctedQuery": {"runs": [{"text": "asdkj", "italics": True}, {"text": " "},
                                {"text": "quoi", "italics": True}]},
    "correctedQueryEndpoint": {"searchEndpoint": {"query": "asdkj quoi"}},
}}]}}]}  # fmt: skip


def _chip(label: str, playlist_id: str, selected: bool = False) -> dict:
    return {"chipCloudChipRenderer": {
        "text": {"runs": [{"text": label}]}, "isSelected": selected,
        "navigationEndpoint": {"queueUpdateCommand": {"fetchContentsCommand": {
            "watchEndpoint": {"playlistId": playlist_id, "params": f"p-{label}"}}}}}}  # fmt: skip


def _next(queue: dict) -> dict:
    return {"contents": {"singleColumnMusicWatchNextResultsRenderer": {"tabbedRenderer": {
        "watchNextTabbedResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
            "musicQueueRenderer": queue}}}]}}}}}  # fmt: skip


RADIO = _next({
    "header": {"musicQueueHeaderRenderer": {"subtitle": {"runs": [{"text": "Halo Mix"}]}}},
    "subHeaderChipCloud": {"chipCloudRenderer": {"chips": [
        _chip("All", "RDAMVMv1", selected=True), _chip("Familiar", "RDATiYvv1"),
        _chip("Deep cuts", "RDATpXvv1")]}},
    "content": {"playlistPanelRenderer": {"contents": []}},
})  # fmt: skip


class TestParseSearchCorrection:
    def test_a_corrected_search_shows_both_queries_and_marks_the_changed_word(self):
        note = parse_search_correction(SHOWING, "beyonse halo")
        assert note == {"kind": "showing", "corrected": "beyonce halo",
                        "original": "beyonse halo",
                        "runs": [("beyonce", True), (" halo", False)]}  # fmt: skip

    def test_a_suggestion_is_did_you_mean_with_the_typed_query_as_original(self):
        note = parse_search_correction(DID_YOU_MEAN, "asdkjh qweoiu")
        assert note is not None
        assert note["kind"] == "did_you_mean"
        assert note["corrected"] == "asdkj quoi"
        assert note["original"] == "asdkjh qweoiu"

    @pytest.mark.parametrize("response", [{}, {"contents": []}, None, []])
    def test_a_search_without_a_note_returns_none(self, response):
        assert parse_search_correction(response, "halo") is None


class TestParseQueueChips:
    def test_a_radio_lists_its_chips_in_order_with_the_selected_one(self):
        chips = parse_queue_chips(RADIO)
        assert [c["label"] for c in chips] == ["All", "Familiar", "Deep cuts"]
        assert chips[0] == {"label": "All", "playlist_id": "RDAMVMv1", "params": "p-All",
                            "selected": True}  # fmt: skip
        assert not chips[2]["selected"]

    def test_an_album_queue_has_no_chips(self):
        album = _next({"content": {"playlistPanelRenderer": {"contents": []}}})
        assert parse_queue_chips(album) == []


@pytest.fixture
def svc():
    return make_ytmusic_service()


class TestServiceCalls:
    async def test_search_passes_ignore_spelling_only_when_it_is_on(self, svc):
        with patch.object(svc, "_call", new_callable=AsyncMock, return_value=[]) as call:
            await svc.search("beyonse halo", filter="songs", limit=5)
            await svc.search("beyonse halo", filter="songs", limit=5, ignore_spelling=True)
        assert "ignore_spelling" not in call.await_args_list[0].kwargs
        assert call.await_args_list[1].kwargs["ignore_spelling"] is True

    async def test_correction_after_search_instead_sends_the_no_correction_params(self, svc):
        with patch.object(svc, "_call", new_callable=AsyncMock, return_value=SHOWING) as call:
            plain = await svc.get_search_correction("beyonse halo")
            await svc.get_search_correction("beyonse halo", ignore_spelling=True)
        assert plain is not None and plain["corrected"] == "beyonce halo"
        assert call.await_args_list[0].args[1:] == ("search", {"query": "beyonse halo"})
        body = call.await_args_list[1].args[2]
        assert body["query"] == "beyonse halo" and body["params"]

    async def test_correction_failure_is_none(self, svc):
        with patch.object(svc, "_call", new_callable=AsyncMock, side_effect=RuntimeError):
            assert await svc.get_search_correction("halo") is None

    async def test_chips_come_from_the_radio_of_the_video(self, svc):
        with patch.object(svc, "_call", new_callable=AsyncMock, return_value=RADIO) as call:
            chips = await svc.get_queue_chips("v1")
        assert [c["label"] for c in chips] == ["All", "Familiar", "Deep cuts"]
        body = call.await_args.args[2]
        assert body["videoId"] == "v1" and body["playlistId"] == "RDAMVMv1"

    async def test_chip_tracks_send_the_chip_playlist_and_params(self, svc):
        song = {"playlistPanelVideoRenderer": {"videoId": "v9"}}
        response = _next({"content": {"playlistPanelRenderer": {"contents": [song]}}})
        parsed = [{"videoId": "v9", "title": "Bye Bye"}]
        with (
            patch.object(svc, "_call", new_callable=AsyncMock, return_value=response) as call,
            patch("ytmusicapi.parsers.watch.parse_watch_playlist", return_value=parsed) as parse,
        ):
            tracks = await svc.get_chip_tracks("v1", "RDATpXvv1", "p-Deep cuts")
        assert tracks == parsed
        parse.assert_called_once_with([song])
        body = call.await_args.args[2]
        assert (body["videoId"], body["playlistId"], body["params"]) == (
            "v1",
            "RDATpXvv1",
            "p-Deep cuts",
        )

    async def test_chip_tracks_failure_is_empty(self, svc):
        with patch.object(svc, "_call", new_callable=AsyncMock, side_effect=RuntimeError):
            assert await svc.get_chip_tracks("v1", "RDATpXvv1", "p") == []
