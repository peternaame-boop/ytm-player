"""Tests for DiscordRPC (no external dependencies needed)."""

from ytm_player.services.discord_rpc import DiscordRPC


class TestDiscordRPCInit:
    def test_initial_state(self):
        rpc = DiscordRPC()
        assert rpc.is_connected is False
        assert rpc._rpc is None
        assert rpc._start_time == 0
