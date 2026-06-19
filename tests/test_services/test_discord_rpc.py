"""Tests for DiscordRPC (no external dependencies needed)."""

from ytm_player.services.discord_rpc import DEFAULT_DISCORD_CLIENT_ID, DiscordRPC


class TestDiscordRPCInit:
    def test_initial_state(self):
        rpc = DiscordRPC()
        assert rpc.is_connected is False
        assert rpc._rpc is None
        assert rpc._start_time == 0

    def test_default_client_id(self):
        """No client_id given → uses the bundled default."""
        assert DiscordRPC()._client_id == DEFAULT_DISCORD_CLIENT_ID

    def test_custom_client_id(self):
        """A user-supplied client_id is honoured (and trimmed)."""
        assert DiscordRPC(client_id="998877665544332211")._client_id == "998877665544332211"
        assert DiscordRPC(client_id="  998877665544332211  ")._client_id == "998877665544332211"

    def test_empty_client_id_falls_back(self):
        """Empty or whitespace-only client_id falls back to the default (#88)."""
        assert DiscordRPC(client_id="")._client_id == DEFAULT_DISCORD_CLIENT_ID
        assert DiscordRPC(client_id="   ")._client_id == DEFAULT_DISCORD_CLIENT_ID
