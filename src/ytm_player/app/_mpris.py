"""MPRIS / media-key callback mixin for YTMPlayerApp."""

from __future__ import annotations

from typing import Any

from ytm_player.app._base import YTMHostBase


class MPRISMixin(YTMHostBase):
    """Builds the callback dict expected by MPRISService / MacOS / Windows media keys."""

    def _build_mpris_callbacks(self) -> dict[str, Any]:
        """Build the callback dict expected by MPRISService.start()."""
        return {
            "play": self._mpris_play,
            "pause": self._mpris_pause,
            "play_pause": self._mpris_play_pause,
            "stop": self._mpris_stop,
            "next": self._mpris_next,
            "previous": self._mpris_previous,
            "seek": self._mpris_seek,
            "set_position": self._mpris_set_position,
            "quit": self._mpris_quit,
        }

    async def _mpris_play(self) -> None:
        if self.player and self.player.is_paused:
            await self.player.resume()

    async def _mpris_pause(self) -> None:
        if self.player:
            await self.player.pause()

    async def _mpris_play_pause(self) -> None:
        await self._toggle_play_pause()

    async def _mpris_stop(self) -> None:
        if self.player:
            await self.player.stop()

    async def _mpris_next(self) -> None:
        await self._play_next()

    async def _mpris_previous(self) -> None:
        await self._play_previous()

    async def _mpris_seek(self, offset_us: int) -> None:
        if self.player:
            await self.player.seek(offset_us / 1_000_000)

    async def _mpris_set_position(self, position_us: int) -> None:
        if self.player:
            await self.player.seek_absolute(position_us / 1_000_000)

    async def _mpris_quit(self) -> None:
        self.exit()
