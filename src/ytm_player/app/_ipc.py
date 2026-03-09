"""IPC command handling mixin for YTMPlayerApp."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class IPCMixin:
    """Handles IPC commands from the CLI."""

    async def _handle_ipc_command(self, command: str, args: dict) -> dict:
        """Dispatch an IPC command from the CLI and return a response dict."""
        try:
            match command:
                case "play":
                    if not self.player:
                        return {"ok": False, "error": "player not ready"}
                    await self.player.resume()
                    return {"ok": True}

                case "pause":
                    if not self.player:
                        return {"ok": False, "error": "player not ready"}
                    await self.player.pause()
                    return {"ok": True}

                case "next":
                    if not self.player:
                        return {"ok": False, "error": "player not ready"}
                    await self._play_next()
                    return {"ok": True}

                case "prev":
                    if not self.player:
                        return {"ok": False, "error": "player not ready"}
                    await self._play_previous()
                    return {"ok": True}

                case "seek":
                    return await self._ipc_seek(args)

                case "now":
                    return self._ipc_now_playing()

                case "status":
                    return self._ipc_status()

                case "queue":
                    return self._ipc_queue_list()

                case "queue_add":
                    return await self._ipc_queue_add(args)

                case "queue_clear":
                    self.queue.clear()
                    return {"ok": True}

                case _:
                    return {"ok": False, "error": f"unknown command: {command}"}
        except Exception as exc:
            logger.exception("IPC command '%s' failed", command)
            return {"ok": False, "error": str(exc)}

    async def _ipc_seek(self, args: dict) -> dict:
        """Handle seek IPC command. Accepts relative (+10, -10) or absolute (1:30)."""
        if not self.player:
            return {"ok": False, "error": "player not ready"}

        offset_str = args.get("offset", "")
        if not offset_str:
            return {"ok": False, "error": "missing offset"}

        if offset_str.startswith("+") or offset_str.startswith("-"):
            try:
                seconds = float(offset_str)
            except ValueError:
                return {"ok": False, "error": f"invalid offset: {offset_str}"}
            await self.player.seek(seconds)
        elif ":" in offset_str:
            parts = offset_str.split(":")
            try:
                if len(parts) == 2:
                    total = int(parts[0]) * 60 + int(parts[1])
                elif len(parts) == 3:
                    total = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                else:
                    return {"ok": False, "error": f"invalid time format: {offset_str}"}
            except ValueError:
                return {"ok": False, "error": f"invalid time format: {offset_str}"}
            await self.player.seek_absolute(float(total))
        else:
            try:
                seconds = float(offset_str)
            except ValueError:
                return {"ok": False, "error": f"invalid offset: {offset_str}"}
            await self.player.seek_absolute(seconds)

        return {"ok": True}

    def _ipc_now_playing(self) -> dict:
        """Return current track info and position."""
        if not self.player or not self.player.current_track:
            return {"ok": True, "data": None}

        track = self.player.current_track
        return {
            "ok": True,
            "data": {
                "track": track,
                "position": self.player.position,
                "duration": self.player.duration,
                "is_playing": self.player.is_playing,
                "is_paused": self.player.is_paused,
            },
        }

    def _ipc_status(self) -> dict:
        """Return full player state."""
        playing = False
        paused = False
        volume = 0
        position = 0.0
        duration = 0.0
        track = None

        if self.player:
            playing = self.player.is_playing
            paused = self.player.is_paused
            volume = self.player.volume
            position = self.player.position
            duration = self.player.duration
            track = self.player.current_track

        return {
            "ok": True,
            "data": {
                "track": track,
                "is_playing": playing,
                "is_paused": paused,
                "volume": volume,
                "position": position,
                "duration": duration,
                "repeat": self.queue.repeat_mode.value,
                "shuffle": self.queue.shuffle_enabled,
                "queue_length": self.queue.length,
            },
        }

    def _ipc_queue_list(self) -> dict:
        """Return the current queue as a list of tracks."""
        return {
            "ok": True,
            "data": {
                "tracks": list(self.queue.tracks),
                "current_index": self.queue.current_index,
                "length": self.queue.length,
                "repeat": self.queue.repeat_mode.value,
                "shuffle": self.queue.shuffle_enabled,
            },
        }

    async def _ipc_queue_add(self, args: dict) -> dict:
        """Resolve a video_id via ytmusic and add to queue."""
        video_id = args.get("video_id", "")
        if not video_id:
            return {"ok": False, "error": "missing video_id"}

        if not self.ytmusic:
            return {"ok": False, "error": "ytmusic not initialized"}

        # Use get_watch_playlist -- it returns tracks in the flat format
        # that normalize_tracks() expects (unlike get_song() which returns
        # a nested videoDetails structure).
        try:
            watch_tracks = await self.ytmusic.get_watch_playlist(video_id)
        except Exception as exc:
            return {"ok": False, "error": f"failed to resolve track: {exc}"}

        if not watch_tracks:
            return {"ok": False, "error": f"track not found: {video_id}"}

        from ytm_player.utils.formatting import normalize_tracks

        normalized = normalize_tracks(watch_tracks[:1])
        if normalized:
            self.queue.add(normalized[0])
            return {"ok": True}
        return {"ok": False, "error": "failed to normalize track"}
