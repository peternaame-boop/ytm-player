"""Playback queue management."""

from __future__ import annotations

import logging
import random
import threading
from enum import StrEnum, auto

logger = logging.getLogger(__name__)


class RepeatMode(StrEnum):
    """Repeat mode for the playback queue."""

    OFF = auto()
    ALL = auto()
    ONE = auto()


class QueueManager:
    """Manages an ordered playback queue with shuffle and repeat.

    Tracks are stored as dicts matching the standardized track format:
        {
            "video_id": str,
            "title": str,
            "artist": str,
            "artists": list[dict],
            "album": str | None,
            "album_id": str | None,
            "duration": int | None,
            "thumbnail_url": str | None,
            "is_video": bool,
        }
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tracks: list[dict] = []
        self._current_index: int = -1
        self._repeat: RepeatMode = RepeatMode.OFF
        self._shuffle: bool = False

        # When shuffle is enabled, _shuffle_order maps playback positions
        # to indices in _tracks. _original_order preserves the insertion order.
        self._shuffle_order: list[int] = []
        self._shuffle_position: int = -1

    # -- Properties -------------------------------------------------------

    @property
    def current_index(self) -> int:
        """Index of the currently playing track in the visible order."""
        with self._lock:
            if self._shuffle:
                return self._shuffle_position
            return self._current_index

    @property
    def current_track(self) -> dict | None:
        return self.current()

    @property
    def tracks(self) -> tuple[dict, ...]:
        """Tracks in the current playback order."""
        with self._lock:
            if self._shuffle:
                return tuple(self._tracks[i] for i in self._shuffle_order)
            return tuple(self._tracks)

    @property
    def is_empty(self) -> bool:
        with self._lock:
            return len(self._tracks) == 0

    @property
    def length(self) -> int:
        with self._lock:
            return len(self._tracks)

    @property
    def repeat_mode(self) -> RepeatMode:
        return self._repeat

    @property
    def shuffle_enabled(self) -> bool:
        return self._shuffle

    # -- Helpers ----------------------------------------------------------

    def _real_index(self) -> int:
        """Current index into _tracks (resolving shuffle indirection)."""
        if self._shuffle and 0 <= self._shuffle_position < len(self._shuffle_order):
            return self._shuffle_order[self._shuffle_position]
        return self._current_index

    def _rebuild_shuffle(self, keep_current: bool = True) -> None:
        """Rebuild the shuffle order, optionally keeping the current track first."""
        indices = list(range(len(self._tracks)))
        current_real = self._real_index()

        if keep_current and 0 <= current_real < len(self._tracks):
            indices.remove(current_real)
            random.shuffle(indices)
            self._shuffle_order = [current_real, *indices]
            self._shuffle_position = 0
        else:
            random.shuffle(indices)
            self._shuffle_order = indices
            self._shuffle_position = -1

    # -- Queue manipulation -----------------------------------------------

    def add(self, track: dict, position: int | None = None) -> None:
        """Add a track to the queue. None = append to end."""
        with self._lock:
            self._add_unlocked(track, position)

    def _add_unlocked(self, track: dict, position: int | None = None) -> None:
        """Add a track without acquiring the lock (caller must hold it)."""
        if position is None or position >= len(self._tracks):
            self._tracks.append(track)
            new_idx = len(self._tracks) - 1
        else:
            position = max(0, position)
            self._tracks.insert(position, track)
            new_idx = position
            # Adjust current index if we inserted before it.
            if not self._shuffle and position <= self._current_index:
                self._current_index += 1

        if self._shuffle:
            # Insert the new track at a random future position in shuffle order.
            insert_at = self._shuffle_position + 1 if self._shuffle_position >= 0 else 0
            # Shift existing shuffle indices that are >= new_idx.
            self._shuffle_order = [(i + 1 if i >= new_idx else i) for i in self._shuffle_order]
            future_pos = (
                random.randint(insert_at + 1, len(self._shuffle_order))
                if insert_at < len(self._shuffle_order)
                else len(self._shuffle_order)
            )
            self._shuffle_order.insert(future_pos, new_idx)

    def add_next(self, track: dict) -> None:
        """Insert a track immediately after the currently playing track."""
        with self._lock:
            if self._shuffle:
                # Insert into _tracks and put it next in shuffle order.
                self._tracks.append(track)
                new_idx = len(self._tracks) - 1
                insert_pos = self._shuffle_position + 1
                self._shuffle_order.insert(insert_pos, new_idx)
            else:
                insert_pos = self._current_index + 1 if self._current_index >= 0 else 0
                self._add_unlocked(track, position=insert_pos)

    def add_multiple(self, tracks: list[dict]) -> None:
        """Append multiple tracks to the end of the queue."""
        if not tracks:
            return

        with self._lock:
            start_idx = len(self._tracks)
            self._tracks.extend(tracks)
            new_indices = list(range(start_idx, start_idx + len(tracks)))

            if self._shuffle:
                # Insert all new indices at random future positions in shuffle order.
                insert_after = self._shuffle_position + 1 if self._shuffle_position >= 0 else 0
                # Collect future positions and shuffle new indices into them.
                random.shuffle(new_indices)
                for new_idx in new_indices:
                    pos = random.randint(insert_after, len(self._shuffle_order))
                    self._shuffle_order.insert(pos, new_idx)

    def remove(self, index: int) -> None:
        """Remove the track at the given index (in visible/playback order)."""
        with self._lock:
            if not 0 <= index < len(self._tracks):
                return

            if self._shuffle:
                if index >= len(self._shuffle_order):
                    return
                real_idx = self._shuffle_order[index]
                del self._shuffle_order[index]
                # Shift indices that pointed beyond the removed track.
                self._shuffle_order = [(i - 1 if i > real_idx else i) for i in self._shuffle_order]
                del self._tracks[real_idx]
                if index <= self._shuffle_position and self._shuffle_position > 0:
                    self._shuffle_position -= 1
            else:
                del self._tracks[index]
                if index < self._current_index:
                    self._current_index -= 1
                elif index == self._current_index:
                    # Current track removed; clamp index.
                    if self._current_index >= len(self._tracks):
                        self._current_index = len(self._tracks) - 1

    def clear(self) -> None:
        """Remove all tracks from the queue."""
        with self._lock:
            self._tracks.clear()
            self._current_index = -1
            self._shuffle_order.clear()
            self._shuffle_position = -1

    def move(self, from_idx: int, to_idx: int) -> None:
        """Move a track from one position to another in the visible order."""
        with self._lock:
            if from_idx == to_idx:
                return
            if not (0 <= from_idx < len(self._tracks)):
                return
            to_idx = max(0, min(to_idx, len(self._tracks) - 1))

            if self._shuffle:
                # Move within shuffle order.
                if from_idx >= len(self._shuffle_order) or to_idx >= len(self._shuffle_order):
                    return
                item = self._shuffle_order.pop(from_idx)
                self._shuffle_order.insert(to_idx, item)
                # Update shuffle_position if it was affected.
                if from_idx == self._shuffle_position:
                    self._shuffle_position = to_idx
                elif from_idx < self._shuffle_position <= to_idx:
                    self._shuffle_position -= 1
                elif to_idx <= self._shuffle_position < from_idx:
                    self._shuffle_position += 1
            else:
                track = self._tracks.pop(from_idx)
                self._tracks.insert(to_idx, track)
                # Update current_index if it was affected.
                if from_idx == self._current_index:
                    self._current_index = to_idx
                elif from_idx < self._current_index <= to_idx:
                    self._current_index -= 1
                elif to_idx <= self._current_index < from_idx:
                    self._current_index += 1

    # -- Playback navigation ----------------------------------------------

    def current(self) -> dict | None:
        """Return the currently selected track, or None."""
        with self._lock:
            real = self._real_index()
            if 0 <= real < len(self._tracks):
                return self._tracks[real]
            return None

    def next_track(self) -> dict | None:
        """Advance to the next track and return it.

        Respects repeat and shuffle modes.
        Returns None if there is no next track to play.
        """
        with self._lock:
            if len(self._tracks) == 0:
                return None

            if self._repeat == RepeatMode.ONE:
                real = self._real_index()
                if 0 <= real < len(self._tracks):
                    return self._tracks[real]
                return None

            if self._shuffle:
                next_pos = self._shuffle_position + 1
                if next_pos >= len(self._shuffle_order):
                    if self._repeat == RepeatMode.ALL:
                        self._rebuild_shuffle(keep_current=False)
                        self._shuffle_position = 0
                    else:
                        return None
                else:
                    self._shuffle_position = next_pos
            else:
                next_idx = self._current_index + 1
                if next_idx >= len(self._tracks):
                    if self._repeat == RepeatMode.ALL:
                        self._current_index = 0
                    else:
                        return None
                else:
                    self._current_index = next_idx

            real = self._real_index()
            if 0 <= real < len(self._tracks):
                return self._tracks[real]
            return None

    def previous_track(self) -> dict | None:
        """Go back to the previous track and return it.

        Respects repeat and shuffle modes.
        Returns None if there is no previous track.
        """
        with self._lock:
            if len(self._tracks) == 0:
                return None

            if self._repeat == RepeatMode.ONE:
                real = self._real_index()
                if 0 <= real < len(self._tracks):
                    return self._tracks[real]
                return None

            if self._shuffle:
                prev_pos = self._shuffle_position - 1
                if prev_pos < 0:
                    if self._repeat == RepeatMode.ALL:
                        self._shuffle_position = len(self._shuffle_order) - 1
                    else:
                        return None
                else:
                    self._shuffle_position = prev_pos
            else:
                prev_idx = self._current_index - 1
                if prev_idx < 0:
                    if self._repeat == RepeatMode.ALL:
                        self._current_index = len(self._tracks) - 1
                    else:
                        return None
                else:
                    self._current_index = prev_idx

            real = self._real_index()
            if 0 <= real < len(self._tracks):
                return self._tracks[real]
            return None

    # -- Repeat / Shuffle -------------------------------------------------

    def set_repeat(self, mode: RepeatMode) -> None:
        with self._lock:
            self._repeat = mode

    def cycle_repeat(self) -> RepeatMode:
        """Cycle through repeat modes: OFF -> ALL -> ONE -> OFF."""
        with self._lock:
            cycle = {
                RepeatMode.OFF: RepeatMode.ALL,
                RepeatMode.ALL: RepeatMode.ONE,
                RepeatMode.ONE: RepeatMode.OFF,
            }
            self._repeat = cycle[self._repeat]
            return self._repeat

    def toggle_shuffle(self) -> None:
        """Toggle shuffle mode on or off."""
        with self._lock:
            self._shuffle = not self._shuffle
            if self._shuffle:
                self._rebuild_shuffle(keep_current=True)
            else:
                # Exiting shuffle: restore the real index as the current position.
                real = self._real_index()
                self._current_index = real if 0 <= real < len(self._tracks) else -1
                self._shuffle_order.clear()
                self._shuffle_position = -1

    # -- Random / Radio ---------------------------------------------------

    def play_random(self) -> dict | None:
        """Pick and jump to a random track from the queue."""
        with self._lock:
            if len(self._tracks) == 0:
                return None
            idx = random.randrange(len(self._tracks))
            if self._shuffle:
                # Find or set position in shuffle order.
                try:
                    self._shuffle_position = self._shuffle_order.index(idx)
                except ValueError:
                    self._shuffle_position = 0
            else:
                self._current_index = idx
            real = self._real_index()
            if 0 <= real < len(self._tracks):
                return self._tracks[real]
            return None

    def set_radio_tracks(self, tracks: list[dict]) -> None:
        """Append radio/autoplay suggestion tracks to the queue.

        Skips tracks already in the queue (by video_id).
        """
        with self._lock:
            existing_ids = {t.get("video_id") for t in self._tracks}
            new_tracks = [t for t in tracks if t.get("video_id") not in existing_ids]
            if new_tracks:
                logger.debug("Adding %d radio tracks to queue", len(new_tracks))
                # Use internal unlocked path since we already hold the lock.
                start_idx = len(self._tracks)
                self._tracks.extend(new_tracks)
                new_indices = list(range(start_idx, start_idx + len(new_tracks)))
                if self._shuffle:
                    insert_after = self._shuffle_position + 1 if self._shuffle_position >= 0 else 0
                    random.shuffle(new_indices)
                    for new_idx in new_indices:
                        pos = random.randint(insert_after, len(self._shuffle_order))
                        self._shuffle_order.insert(pos, new_idx)

    def peek_next(self) -> dict | None:
        """Return the next track WITHOUT advancing the position.

        Useful for prefetching stream URLs ahead of time.
        """
        with self._lock:
            if len(self._tracks) == 0:
                return None

            if self._repeat == RepeatMode.ONE:
                real = self._real_index()
                if 0 <= real < len(self._tracks):
                    return self._tracks[real]
                return None

            if self._shuffle:
                next_pos = self._shuffle_position + 1
                if next_pos >= len(self._shuffle_order):
                    if self._repeat == RepeatMode.ALL:
                        # Would wrap around, but we can't predict the reshuffle.
                        return None
                    return None
                real_idx = self._shuffle_order[next_pos]
                if 0 <= real_idx < len(self._tracks):
                    return self._tracks[real_idx]
                return None
            else:
                next_idx = self._current_index + 1
                if next_idx >= len(self._tracks):
                    if self._repeat == RepeatMode.ALL:
                        return self._tracks[0] if self._tracks else None
                    return None
                return self._tracks[next_idx]

    # -- Utility ----------------------------------------------------------

    def jump_to(self, index: int) -> dict | None:
        """Jump to a specific index in the visible order and return the track."""
        with self._lock:
            if not 0 <= index < len(self._tracks):
                return None
            if self._shuffle:
                self._shuffle_position = index
            else:
                self._current_index = index
            real = self._real_index()
            if 0 <= real < len(self._tracks):
                return self._tracks[real]
            return None
