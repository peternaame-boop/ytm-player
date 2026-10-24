"""A first resolve asks only the fast visionos client. A retry asks the full set."""

from __future__ import annotations

from ytm_player.services.stream import _choose_clients


def _params(clients: list[str]) -> dict:
    return {"extractor_args": {"youtube": {"player_client": clients}}}


def test_first_attempt_uses_the_fast_client_and_retries_the_full_set():
    params = _params(["default", "android"])
    _choose_clients(params, 0)
    assert params["extractor_args"]["youtube"]["player_client"] == ["visionos"]
    _choose_clients(params, 1)
    assert params["extractor_args"]["youtube"]["player_client"] == ["default", "android"]


def test_a_client_list_the_user_configured_is_left_alone():
    params = _params(["tv", "web"])
    _choose_clients(params, 0)
    assert params["extractor_args"]["youtube"]["player_client"] == ["tv", "web"]
