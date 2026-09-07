"""Playback queue management."""

from __future__ import annotations

import logging
import random
import threading

from ytm_player.utils.compat import StrEnum, auto

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
        # One internal id per queue occurrence, parallel to ``_tracks``. An
        # id is minted when a track is inserted and dies with that entry, so
        # it identifies "this occurrence" even when the same track — or the
        # very same dict object — sits in the queue twice. Never stored in
        # the track dicts, so it can't leak into session files or API calls.
        self._entry_ids: list[int] = []
        self._next_entry_id: int = 0
        self._current_index: int = -1
        self._repeat: RepeatMode = RepeatMode.OFF
        self._shuffle: bool = False

        # When shuffle is enabled, _shuffle_order maps playback positions
        # to indices in _tracks. _original_order preserves the insertion order.
        self._shuffle_order: list[int] = []
        self._shuffle_position: int = -1

        # Stable ID of the collection (playlist/album/artist) the queue
        # was populated from, or None for ephemeral queues (search,
        # discovery roulette, single-track radio).  Used by the
        # per-collection shuffle memory feature — see ShufflePreferences.
        self._current_context_id: str | None = None

        self.radio_seeds: list[dict] | None = None

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
    def entries(self) -> tuple[tuple[int, dict], ...]:
        """``(entry_id, track)`` pairs in the current playback order.

        The entry id names one queue occurrence: it stays with the entry
        through moves and shuffle and disappears when that entry is
        removed. Two occurrences of the same track never share an id. Read
        both halves from this one call — reading ``tracks`` and the ids
        separately could interleave with a mutation on another thread.
        """
        with self._lock:
            if self._shuffle:
                return tuple((self._entry_ids[i], self._tracks[i]) for i in self._shuffle_order)
            return tuple(zip(self._entry_ids, self._tracks))

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

    @property
    def real_index(self) -> int:
        """Current index into _tracks regardless of shuffle mode."""
        with self._lock:
            return self._real_index()

    @property
    def current_context_id(self) -> str | None:
        """ID of the collection this queue was populated from, or None."""
        with self._lock:
            return self._current_context_id

    def set_context(self, context_id: str | None) -> None:
        """Set (or clear with None) the collection ID backing this queue."""
        with self._lock:
            self._current_context_id = context_id

    @property
    def remaining_tracks(self) -> int:
        """Number of tracks remaining after the current position (shuffle-aware)."""
        with self._lock:
            if self._shuffle:
                return len(self._shuffle_order) - self._shuffle_position - 1
            return len(self._tracks) - self._current_index - 1

    # -- Helpers ----------------------------------------------------------

    def _real_index(self) -> int:
        """Current index into _tracks (resolving shuffle indirection)."""
        if self._shuffle and 0 <= self._shuffle_position < len(self._shuffle_order):
            return self._shuffle_order[self._shuffle_position]
        return self._current_index

    def _new_entry_ids(self, count: int) -> list[int]:
        """Mint *count* fresh entry ids (caller must hold the lock)."""
        start = self._next_entry_id + 1
        self._next_entry_id += count
        return list(range(start, start + count))

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
        (entry_id,) = self._new_entry_ids(1)
        if position is None or position >= len(self._tracks):
            self._tracks.append(track)
            self._entry_ids.append(entry_id)
            new_idx = len(self._tracks) - 1
        else:
            position = max(0, position)
            self._tracks.insert(position, track)
            self._entry_ids.insert(position, entry_id)
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
                self._entry_ids.extend(self._new_entry_ids(1))
                new_idx = len(self._tracks) - 1
                insert_pos = self._shuffle_position + 1
                self._shuffle_order.insert(insert_pos, new_idx)
            else:
                insert_pos = self._current_index + 1 if self._current_index >= 0 else 0
                self._add_unlocked(track, position=insert_pos)

    def add_next_multiple(self, tracks: list[dict]) -> None:
        """Insert *tracks* immediately after the current track, preserving order.

        The N tracks become the next N to play, in the given order. Shuffle-aware:
        under shuffle they're spliced into the shuffle order right after the
        current position (so they play next, in order) while appended to
        ``_tracks``; otherwise they're spliced into ``_tracks`` after the current
        index. Duplicates are inserted as-is, mirroring :meth:`add_next`.
        """
        if not tracks:
            return
        with self._lock:
            entry_ids = self._new_entry_ids(len(tracks))
            if self._shuffle:
                start_idx = len(self._tracks)
                self._tracks.extend(tracks)
                self._entry_ids.extend(entry_ids)
                new_indices = range(start_idx, start_idx + len(tracks))
                insert_pos = self._shuffle_position + 1
                for offset, new_idx in enumerate(new_indices):
                    self._shuffle_order.insert(insert_pos + offset, new_idx)
            else:
                insert_pos = self._current_index + 1 if self._current_index >= 0 else 0
                self._tracks[insert_pos:insert_pos] = tracks
                self._entry_ids[insert_pos:insert_pos] = entry_ids

    def _add_multiple_unlocked(self, tracks: list[dict]) -> None:
        """Append *tracks* to the queue and update shuffle order.

        Caller must already hold ``self._lock``.  On a fresh (empty) queue with
        shuffle enabled this rebuilds the whole shuffle order rather than
        inserting piecemeal into an empty list.
        """
        was_empty = len(self._tracks) == 0
        start_idx = len(self._tracks)
        self._tracks.extend(tracks)
        self._entry_ids.extend(self._new_entry_ids(len(tracks)))
        new_indices = list(range(start_idx, start_idx + len(tracks)))

        if self._shuffle:
            if was_empty:
                # Fresh queue after clear() — do a full shuffle build
                # instead of piecemeal insertion into an empty list.
                self._rebuild_shuffle(keep_current=False)
            else:
                # Insert new indices at random future positions.
                insert_after = self._shuffle_position + 1 if self._shuffle_position >= 0 else 0
                random.shuffle(new_indices)
                for new_idx in new_indices:
                    pos = random.randint(insert_after, len(self._shuffle_order))
                    self._shuffle_order.insert(pos, new_idx)

    def add_multiple(self, tracks: list[dict]) -> None:
        """Append multiple tracks to the end of the queue."""
        if not tracks:
            return

        with self._lock:
            self._add_multiple_unlocked(tracks)

    def remove(self, index: int) -> None:
        """Remove the track at the given index (in visible/playback order)."""
        with self._lock:
            self._remove_unlocked(index)

    def remove_entry(self, entry_id: int) -> bool:
        """Remove the one occurrence *entry_id* names (see :attr:`entries`).

        Returns False and changes nothing when no entry carries that id any
        more — it was removed, or the queue was rebuilt, after the id was
        read. The caller decides what that means; this never falls back to
        another occurrence of the same track.
        """
        with self._lock:
            try:
                real_idx = self._entry_ids.index(entry_id)
                index = self._shuffle_order.index(real_idx) if self._shuffle else real_idx
            except ValueError:
                return False
            self._remove_unlocked(index)
            return True

    def _remove_unlocked(self, index: int) -> None:
        """Remove the entry at visible/playback *index* (caller must hold the lock)."""
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
            del self._entry_ids[real_idx]
            if index < self._shuffle_position:
                self._shuffle_position -= 1
            elif index == self._shuffle_position:
                # Current track removed; clamp position (mirrors the
                # non-shuffle branch: current() lands on the next track).
                if self._shuffle_position >= len(self._shuffle_order):
                    self._shuffle_position = len(self._shuffle_order) - 1
        else:
            del self._tracks[index]
            del self._entry_ids[index]
            if index < self._current_index:
                self._current_index -= 1
            elif index == self._current_index:
                # Current track removed; clamp index.
                if self._current_index >= len(self._tracks):
                    self._current_index = len(self._tracks) - 1

    def clear(self) -> None:
        """Remove all tracks from the queue.

        Resets shuffle state so that the next ``add_multiple`` + ``jump_to``
        sequence starts from a clean slate.  The user-visible shuffle
        *preference* (on/off) is preserved — the internal ordering is rebuilt
        automatically when new tracks are added.
        """
        with self._lock:
            self._tracks.clear()
            self._entry_ids.clear()
            self._current_index = -1
            self._shuffle_order.clear()
            self._shuffle_position = -1
            self.radio_seeds = None

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
                self._entry_ids.insert(to_idx, self._entry_ids.pop(from_idx))
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
            # Resolve the playing occurrence under the mode being left. Once
            # the flag flips, _real_index() reads the other mode's pointer,
            # and _current_index is stale after next/previous under shuffle.
            real = self._real_index()
            self._shuffle = not self._shuffle
            if self._shuffle:
                self._rebuild_shuffle(keep_current=True)
            else:
                # Exiting shuffle: the real index becomes the current position.
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
                # Share the append/shuffle path (we already hold the lock).
                self._add_multiple_unlocked(new_tracks)

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
                    # End of the shuffle order. Even under RepeatMode.ALL we
                    # can't predict the next reshuffle, so there's nothing to
                    # prefetch.
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
            if self._shuffle:
                if not 0 <= index < len(self._shuffle_order):
                    return None
                self._shuffle_position = index
                # Keep _current_index in sync as a safety fallback.
                self._current_index = self._shuffle_order[index]
            else:
                if not 0 <= index < len(self._tracks):
                    return None
                self._current_index = index
            real = self._real_index()
            if 0 <= real < len(self._tracks):
                return self._tracks[real]
            return None

    def jump_to_real(self, real_index: int) -> dict | None:
        """Jump to a track by its index in the internal track list.

        Unlike :meth:`jump_to` (which interprets *index* as a position in
        the current playback order — i.e. shuffle order when shuffled),
        this always resolves *real_index* as a position in ``_tracks``.
        """
        with self._lock:
            if not 0 <= real_index < len(self._tracks):
                return None
            if self._shuffle:
                try:
                    pos = self._shuffle_order.index(real_index)
                    self._shuffle_position = pos
                except ValueError:
                    # Track not in shuffle order — insert it right after the
                    # current position so playback can continue from here.
                    insert_pos = self._shuffle_position + 1
                    self._shuffle_order.insert(insert_pos, real_index)
                    self._shuffle_position = insert_pos
            else:
                self._current_index = real_index
            real = self._real_index()
            if 0 <= real < len(self._tracks):
                return self._tracks[real]
            return None
