# Design note: the play-attempt module

Status: implemented for [#225](https://github.com/peternaame-boop/ytm-player/issues/225).
Terms (**playback request**, **play attempt**, **background tail**, **queue lifetime**, **stop**) are defined in [GLOSSARY.md](../../GLOSSARY.md).

## Problem

To decide "may this async continuation still act?", code in `app/` has to know nine pieces of state spread across the App:
`_play_request`, `_play_generation`, `_queue_stop_generation`, `_active_stop_generation`,
`_handled_end_attempt`, `_handled_error_attempt`, `_recovery_generation`, `_refill_owner`,
plus the ContextVar owner tuple in `app/_ownership.py`. `queue.generation` is a tenth input.

- `play_track` repeats `generation != self._play_generation or not owns_request(self)` 12 times.
- `_on_track_end` and `_on_player_error` repeat the same three-part staleness check.
- `playback_request` picks its policy from `fn.__name__`, so renaming a method silently changes its ownership rules.
- 12 `getattr(self, "_…", 0)` fallbacks exist only so partial test hosts work.
- `app/_base.py` has already drifted: its `play_track` stub lacks `recovery_of`.

Since July, four fixes have each added another token or check: c218204, 4ffcf75, 4839e0f, 8c78a6c.

## Decision

### What moves behind the seam

One app-owned plain Python object (no Textual, no host `getattr`) owns every ownership *decision*:
playback requests, play attempts, the single retry, background-tail and refill ownership, and the play_track duplicate-click debounce.

`QueueManager` keeps owning its own generation counter. The module reads it and never writes it.

### Scheduling stays synchronous

Ownership is captured when the caller *constructs* the coroutine, before `run_worker` runs it (today `schedule()` in `_ownership.py`). Moving it into the `async def` body would let a delayed worker claim ownership after a newer request.

The ContextVar stays, as an internal seam of the module. It carries:
- same-host inheritance (a nested helper runs under its caller's request),
- propagating an adopted queue lifetime back to the parent (`adopt_queue`),
- `unowned_scope()` around page mounting (`_navigation.py`), so a page's own plays are fresh requests.

### Policy is declared at the definition

Each decorated method declares one of four policies next to its definition. The three methods whose policy depends on their arguments supply a classifier: `play_track` (the track and the debounce choose ignore or foreground), `_fetch_and_play_radio(append=…)` and `_dispatch_entity_action(action_id)`.

| Policy | Behaviour |
|---|---|
| foreground | Reserves a new playback request, or inherits the caller's on the same host. |
| background | Runs as a background tail: owned by the queue lifetime and the stop count, not by a request. |
| bypass | Runs the method untouched and returns its result (non-play entity actions). |
| ignore | Returns an awaitable that does nothing and leaves the current request alone (duplicate click, `track is None`). |

### Who invalidates what

This table is the module's core contract. Each cell is pinned by an existing test or code path.

| Event | Playback request | Play attempt (EOF / error handling) | Background tail |
|---|---|---|---|
| Newer foreground request reserved | invalidated | kept: an unsuccessful newer request leaves the audible attempt eligible (`test_unsuccessful_request_keeps_audible_attempt_owned`) | kept (`test_tail_survives_ordinary_next_and_shuffle`) |
| Newer play attempt committed | n/a | invalidated | kept |
| Stop | invalidated | invalidated | invalidated |
| Queue clear/replace by someone else | invalidated | kept if the clear happens before EOF/error arrives (`test_queue_clear_preserves_audible_attempt_completion`) | invalidated (`test_tail_scheduled_before_replacement_never_attaches_to_new_queue`) |
| Queue replaced by the owning request (`adopt_queue`) | kept, moves to the new queue lifetime | n/a | n/a |
| Queue change *while* EOF awaits listen logging | n/a | EOF handler stops: it must not advance the changed queue (`_playback.py` re-check after `_log_listen_for`) | n/a |

Also behind the seam:
- EOF and error dedupe: each attempt's EOF and error are handled at most once.
- The single-retry budget: a request's first failed attempt gets one retry; the budget clears once audio actually advances.
- Refill ownership: one in-flight autoplay refill at a time per (queue lifetime, stop count).
- Prefetch window: a stream-prefetch batch ends on Stop or queue replacement; a newer committed attempt ends an exclusive batch and shrinks a refill batch by one per attempt.

### Debounce

The module owns `_last_play_video_id` / `_last_play_time` and all their resets.
- The duplicate check runs at scheduling, before a request is reserved. Nested same-host plays are exempt (Next/EOF may replay the same song).
- The timestamp is recorded only when `play_track` actually runs with a player, a resolver and a video ID.
- Stop, the recovery retry and qualifying failures reset it through one operation.
- The missing-video-ID path keeps `clear_debounce=False`, so a malformed track can't erase the previous click's guard.

### Player stays unchanged

`Player.play(..., attempt: int | None)` and the `attempt` echoed in EOF/ERROR payloads stay as they are. The module checks plain int attempts.

## Behaviour and tests

- Stop now clears a pending retry. Preserve the other playback behaviours covered by the baseline suite: the ownership, playback, player and queue test files (295 passed on 2026-11-21).
- Every behavioural assertion survives. Test setup that assigns private fields (e.g. `h._play_generation = 2; h._recovery_generation = 2`) is rewritten in the same change to drive the module; no compatibility fields kept only for tests.
- The tests that schedule through the real UI wrappers stay as integration cases. They cover the gap between scheduling a request and its worker starting.
- New direct tests on the module, one per row of the invalidation table, with no mixin host.

## Not in scope

- Player, `QueueManager` internals, the Now-playing fan-out, listen tracking (separate candidates in the 2026-11-21 architecture review).
