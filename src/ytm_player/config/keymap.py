"""Keybinding system with multi-key sequences and modifier support."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Self

KEYMAP_FILE = Path.home() / ".config" / "ytm-player" / "keymap.toml"


class Action(str, Enum):
    # Playback
    PLAY_PAUSE = "play_pause"
    NEXT_TRACK = "next_track"
    PREVIOUS_TRACK = "previous_track"
    PLAY_RANDOM = "play_random"
    CYCLE_REPEAT = "cycle_repeat"
    TOGGLE_SHUFFLE = "toggle_shuffle"
    VOLUME_UP = "volume_up"
    VOLUME_DOWN = "volume_down"
    MUTE = "mute"
    SEEK_FORWARD = "seek_forward"
    SEEK_BACKWARD = "seek_backward"
    SEEK_START = "seek_start"

    # Navigation
    MOVE_DOWN = "move_down"
    MOVE_UP = "move_up"
    PAGE_DOWN = "page_down"
    PAGE_UP = "page_up"
    GO_TOP = "go_top"
    GO_BOTTOM = "go_bottom"
    SELECT = "select"
    FOCUS_NEXT = "focus_next"
    FOCUS_PREV = "focus_prev"
    GO_BACK = "go_back"
    CLOSE_POPUP = "close_popup"

    # Pages
    LIBRARY = "library"
    SEARCH = "search"
    BROWSE = "browse"
    LIKED_SONGS = "liked_songs"
    RECENTLY_PLAYED = "recently_played"
    LYRICS = "lyrics"
    CURRENT_CONTEXT = "current_context"
    JUMP_TO_CURRENT = "jump_to_current"
    QUEUE = "queue"
    HELP = "help"

    # Actions
    DELETE_ITEM = "delete_item"
    TRACK_ACTIONS = "track_actions"
    CONTEXT_ACTIONS = "context_actions"
    SELECTED_ACTIONS = "selected_actions"
    ADD_TO_QUEUE = "add_to_queue"
    ADD_TO_PLAYLIST = "add_to_playlist"
    FILTER = "filter"

    # Sorting
    SORT_TITLE = "sort_title"
    SORT_ARTIST = "sort_artist"
    SORT_ALBUM = "sort_album"
    SORT_DURATION = "sort_duration"
    SORT_DATE = "sort_date"
    REVERSE_SORT = "reverse_sort"

    # Search
    TOGGLE_SEARCH_MODE = "toggle_search_mode"


class MatchResult(Enum):
    NO_MATCH = "no_match"
    PENDING = "pending"
    EXACT = "exact"


DEFAULT_BINDINGS: dict[str, list[str]] = {
    # Playback
    "play_pause": ["space"],
    "next_track": ["n"],
    "previous_track": ["p"],
    "play_random": ["."],
    "cycle_repeat": ["C-r"],
    "toggle_shuffle": ["C-s"],
    "volume_up": ["+"],
    "volume_down": ["-"],
    "mute": ["_"],
    "seek_forward": [">"],
    "seek_backward": ["<"],
    "seek_start": ["^"],

    # Navigation
    "move_down": ["j", "down", "C-n"],
    "move_up": ["k", "up", "C-p"],
    "page_down": ["C-f", "page_down"],
    "page_up": ["C-b", "page_up"],
    "go_top": ["g g", "home"],
    "go_bottom": ["G", "end"],
    "select": ["enter"],
    "focus_next": ["tab"],
    "focus_prev": ["S-tab"],
    "go_back": ["backspace", "C-q"],
    "close_popup": ["escape"],

    # Pages
    "library": ["g l"],
    "search": ["g s"],
    "browse": ["g b"],
    "liked_songs": ["g y"],
    "recently_played": ["g r"],
    "lyrics": ["g L"],
    "current_context": ["g space"],
    "jump_to_current": ["g c"],
    "queue": ["z"],
    "help": ["?", "C-h"],

    # Actions
    "delete_item": ["delete", "d d"],
    "track_actions": ["a"],
    "context_actions": ["g A"],
    "selected_actions": ["g a", "C-space"],
    "add_to_queue": ["Z", "C-z"],
    "add_to_playlist": ["A"],
    "filter": ["/"],

    # Sorting
    "sort_title": ["s t"],
    "sort_artist": ["s a"],
    "sort_album": ["s A"],
    "sort_duration": ["s d"],
    "sort_date": ["s D"],
    "reverse_sort": ["s r"],

    # Search
    "toggle_search_mode": ["M-v"],
}


def parse_key_sequence(raw: str) -> tuple[str, ...]:
    return tuple(raw.strip().split())


@dataclass
class KeyMap:
    bindings: dict[tuple[str, ...], Action] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = KEYMAP_FILE) -> Self:
        keymap = cls()

        if path.exists():
            with open(path, "rb") as f:
                data = tomllib.load(f)
            keymap._load_from_dict(data)
        else:
            keymap._load_defaults()

        return keymap

    def _load_defaults(self) -> None:
        for action_name, key_strs in DEFAULT_BINDINGS.items():
            action = Action(action_name)
            for key_str in key_strs:
                seq = parse_key_sequence(key_str)
                self.bindings[seq] = action

    def _load_from_dict(self, data: dict) -> None:
        self._load_defaults()

        for section in data.values():
            if not isinstance(section, dict):
                continue
            for action_name, keys in section.items():
                try:
                    action = Action(action_name)
                except ValueError:
                    continue

                self._remove_action(action)

                if isinstance(keys, str):
                    keys = [keys]
                for key_str in keys:
                    seq = parse_key_sequence(key_str)
                    self.bindings[seq] = action

    def _remove_action(self, action: Action) -> None:
        to_remove = [k for k, v in self.bindings.items() if v == action]
        for key in to_remove:
            del self.bindings[key]

    def match(self, key_sequence: tuple[str, ...]) -> tuple[MatchResult, Action | None]:
        if key_sequence in self.bindings:
            return MatchResult.EXACT, self.bindings[key_sequence]

        for bound_seq in self.bindings:
            if len(bound_seq) > len(key_sequence) and bound_seq[:len(key_sequence)] == key_sequence:
                return MatchResult.PENDING, None

        return MatchResult.NO_MATCH, None

    def get_keys_for_action(self, action: Action) -> list[tuple[str, ...]]:
        return [seq for seq, act in self.bindings.items() if act == action]

    def format_key(self, seq: tuple[str, ...]) -> str:
        return " ".join(seq)


_keymap: KeyMap | None = None


def get_keymap() -> KeyMap:
    global _keymap
    if _keymap is None:
        _keymap = KeyMap.load()
    return _keymap
