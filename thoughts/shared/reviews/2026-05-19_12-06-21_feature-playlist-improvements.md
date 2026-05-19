---
title: "Code Review — feature/playlist-improvements"
date: "2026-05-19T12:06:21+0200"
repository: ytm-player
branch: feature/playlist-improvements
commit: 68c013e
reviewer: Villoh
scope: feature/playlist-improvements vs master (first-parent)
status: needs_changes
verification: 15 findings · V=12 · W=2 · F=1 (dropped)
---

## Summary

Playlist improvements: create/edit playlist popup with privacy selector, remove track from playlist, duplicate-track confirmation flow, sidebar count sync, track table index renumbering after removal, and a `MutationResult("duplicate")` type. Overall the implementation is solid — consistent auth-error handling, well-structured message flow, and good test coverage for the core paths. Three areas need attention before merge.

---

## 🔴 Critical

### I2 — `update_item_count` ValueError silently strands sidebar count after every successful mutation [precedent-weighted] [cascade: stranded-state]

`update_item_count` casts the stored count with `int(current)`:

> `src/ytm_player/ui/sidebars/playlist_sidebar.py:232` — `target_item["count"] = max(0, int(current) + delta)`

When the YTMusic API returns a count as a non-numeric string, `int()` raises `ValueError`. Both call sites swallow it with a broad `except Exception`:

> `src/ytm_player/ui/popups/playlist_picker.py` (inside `_do_add` sidebar block) — `except Exception: logger.exception("Sidebar count update failed")`

> `src/ytm_player/app/_track_actions.py` (inside `_remove_track_from_playlist` UI block) — `except Exception: logger.exception("Failed to update UI after track removal")`

The server mutation has already succeeded at this point. The sidebar count is never corrected until the next full library reload. The only signal is a log line. This is the same class of silent-count-staleness that generated three follow-up commits on this branch (1e7ad8d → c6eb34f → c665711).

**Fix:** guard with `try: int(current) except (ValueError, TypeError): return` (or default to 0) before the arithmetic.

---

### I3 — `refresh_header` unmount window lets `update_track_count` write to a detached Label, delta silently discarded

`refresh_header` opens by removing all header children:

> `src/ytm_player/ui/pages/library.py` — `await header.remove_children()`

At this point `self._subtitle_label` still holds a reference to the now-unmounted `Label`. The guard in `update_track_count`:

> `src/ytm_player/ui/pages/library.py` — `if hasattr(self, "_subtitle_label") and self._subtitle_label:`

…always passes because `Label` objects are truthy regardless of mount state. Any concurrent worker (`_fetch_remaining`, `_do_add`, `_remove_track_from_playlist`) that fires between the `remove_children()` await and the later `self._subtitle_label = Label(...)` assign writes to the detached widget — visually a no-op. `refresh_header` then mounts the freshly-computed label, discarding whatever delta was applied.

**Fix:** set `self._subtitle_label = None` immediately after `remove_children()` so the truthiness guard correctly blocks concurrent writers during the remount window.

---

### Q6 — `int(current)` in `update_item_count` raises uncaught ValueError [precedent-weighted]

_(Root cause of I2 above; independently actionable.)_

> `src/ytm_player/ui/sidebars/playlist_sidebar.py:232` — `target_item["count"] = max(0, int(current) + delta)`

The YTMusic API can return `count` as a string. No try/except or isinstance guard exists. The exception propagates out of `update_item_count` to whatever broad handler catches it upstream.

---

## 🟡 Important

### Q3 — `_fetch_playlist_meta_for_edit` opens edit popup with silent stale defaults when playlist_id is falsy

> `src/ytm_player/app/_sidebar.py` — `if playlist_id and self.ytmusic:`

When `item.get("playlistId") or item.get("browseId", "")` is empty, the condition is false and the method falls through to `_open_edit_popup` with `current_description=""` and `current_privacy="PRIVATE"` — hardcoded defaults rather than fetched values. The user sees the edit popup with blank/wrong data and no notification.

**Fix:** return early with `self.notify("Couldn't load playlist metadata", severity="warning")` when `playlist_id` is falsy.

---

### Q4 — Item schema inconsistency: freshly-created playlists lack `description`/`privacy` keys

> `src/ytm_player/app/_sidebar.py` — `panel.prepend_item({"playlistId": playlist_id, "title": name, "count": 0})`

`_create_sidebar_playlist` inserts a dict without `description` or `privacy`. `_apply_playlist_edit_to_ui` later writes those keys by assignment:

> `src/ytm_player/app/_sidebar.py` — `stored["description"] = description` / `stored["privacy"] = privacy`

Any code that reads these keys between playlist creation and first edit (e.g. a future `build_playlist_subtitle` call on the cached item) will see `KeyError` or a missing value. `_fetch_playlist_meta_for_edit` reads `data["description"]` from the API response, so it is not currently affected — but `_apply_playlist_edit_to_ui` directly reads `stored` without an API call.

**Fix:** include `"description": "", "privacy": "PRIVATE"` in the `prepend_item` dict.

---

### Q9 — `set_video_id` branch of `_matches` in `remove_track` is untested

> `src/ytm_player/ui/widgets/track_table.py` — `def _matches(t: dict) -> bool:`

All three tests in `test_track_table_remove.py` call `remove_track(video_id)` with no second argument, so the `if set_video_id:` path is never exercised. This is the precise logic path introduced to fix the duplicate-removal bug — the most important new code in the widget.

---

### Q17 — `_remove_track_from_playlist` has no unit test

No match for `_remove_track_from_playlist` in the entire `tests/` tree. The method orchestrates an API call, a table row removal, and a sidebar count update — the three state layers that have historically diverged on this branch.

---

### Q18 — Server/table desync is silent after `table.remove_track` raises

> `src/ytm_player/app/_track_actions.py` — `removed = table.remove_track(video_id, set_video_id=set_video_id)`

This sits inside a broad `except Exception` block. If `remove_track` raises after `remove_playlist_items` has already succeeded, the exception is logged and the user sees the "Track removed" toast — but the row is still visible in the table, and the sidebar count is not decremented. The user has no indication the local state is wrong.

**Fix:** notify the user ("Track removed on server; please reload the playlist") in the except block rather than silently swallowing.

---

### I1 — Delta-only count bump in `_do_add` can be overwritten by a concurrent `_fetch_remaining` worker

When `playlist_picker._do_add` has no `tracks` list, it takes the else-branch:

> `src/ytm_player/ui/popups/playlist_picker.py` — `library.update_track_count(+len(self.video_ids))`

This increments the displayed count without appending rows to the table. If `_fetch_remaining` — still running for a large playlist — completes afterward, it calls:

> `src/ytm_player/ui/pages/library.py` — `self.update_track_count()` (delta=0)

…which reads `len(table.tracks)` (unchanged — the rows were never appended) and overwrites the displayed count, silently reverting the optimistic increment.

---

## 🔵 Suggestions

### Q5 — `edit_playlist` success check is more permissive than `add_playlist_items`

> `src/ytm_player/services/ytmusic.py` — `succeeded = result == "STATUS_SUCCEEDED" if isinstance(result, str) else bool(result)`

`add_playlist_items` checks `"SUCCEEDED" not in result.get("status", "")` for dicts. `edit_playlist` uses `bool(result)`, which is `True` for any non-empty dict — including an error response. Aligning both to the same pattern reduces surprise.

---

### Q7 — `_filtered_map` rebuild uses `trk in self._tracks` (identity via dict equality)

> `src/ytm_player/ui/widgets/track_table.py` — `self._filtered_map = [i for i, trk in enumerate(self._all_tracks) if trk in self._tracks]`

Two tracks with identical content but different `setVideoId`s would be treated as the same entry. Low probability today, but now that duplicate tracks are supported (via `duplicates=True`), this edge becomes reachable.

---

### Q14 — Inconsistent widget query path in `playlist_picker._do_add`

> `src/ytm_player/ui/popups/playlist_picker.py` — `panel = self.app.query_one("#ps-playlists", LibraryPanel)`

All other call sites scope through `query_one("#playlist-sidebar", PlaylistSidebar)` first. Functionally equivalent while IDs are unique, but the inconsistency will cause a subtle bug if the widget hierarchy is ever reorganised.

---

### Q10 — `refresh_header` duplicates the header-construction sequence from `_load_library_content`

> `src/ytm_player/ui/pages/library.py` — `async def refresh_header(`

Both methods mount title label, radio button, spacer, lock button, description label, and subtitle label. Future header changes require two edits.

---

## 💭 Discussion

### Q13 — `str(Select.BLANK)` is unreachable but unguarded

> `src/ytm_player/ui/popups/create_playlist_popup.py` — `privacy = str(self.query_one("#select-privacy", Select).value)`

`allow_blank=False` prevents blank selection in normal operation. A minor defensive assertion (`assert value is not Select.BLANK`) would make the assumption explicit and catch regressions if the widget config is changed.

---

## Predicate-set coherence

The VL-prefix triple-check `pid in (playlist_id, raw_id, f"VL{raw_id}")` appears twice in `_sidebar.py`. Both instances are consistent. Since `strip_vl_prefix` already exists, replacing with `strip_vl_prefix(pid) == raw_id` would be simpler and eliminate the redundant third member.

---

## Impact

`formatting.py` (26 consumers), `track_table.py` (11 consumers), `ytmusic.py` (9 consumers): changes to these hub files affect the entire app. The changes to `formatting.py` (`normalize_tracks` setVideoId passthrough, `strip_vl_prefix`, `build_playlist_subtitle`) are additive and low-risk. `track_table.py`'s `remove_track` change adds a new optional parameter and is backward-compatible. `ytmusic.py`'s `add_playlist_items` signature change (adds `duplicates=False` default) is backward-compatible.

---

## Precedents

| Hash    | Subject                                        | 30d follow-ups                | Note                                                                                                                 |
| ------- | ---------------------------------------------- | ----------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| 1e7ad8d | sync sidebar count when tracks added           | 3 (c6eb34f, 1472882, c665711) | Incomplete first attempt — only handled `_do_add`, missed `_create_and_add`; count arithmetic then had a str/int bug |
| c6eb34f | also sync sidebar count on creation            | 1 (1472882)                   | 15-min follow-up for missed path                                                                                     |
| c665711 | correct track count updates, normalize sidebar | 0                             | Cast added (`int(current)`) but without ValueError guard                                                             |
| 3d04e30 | add remove track from playlist                 | 1 (1472882)                   | Large feature; follow-up cleaned up count method                                                                     |

**Composite lessons:** Count sync across sidebar / library header / track table has produced ≥4 follow-up commits in this branch. The `int(current)` cast was added in c665711 to fix the str+int TypeError but introduced the uncaught ValueError path that I2/Q6 flag. Any future work touching `update_item_count` or `update_track_count` should treat these as high-regression-risk call sites.

---

## Reconciliation Notes

- **Q1 Falsified**: `_apply_playlist_edit_to_ui` contains `await library.refresh_header(...)` — `async` is required. Dropped.
- **Q6 bumped 🟡→🔴** [precedent-weighted]: same symbol (`update_item_count`) had 3 follow-up fixes within 30 days in this branch.
- **I2 bumped 🟡→🔴** [precedent-weighted]: aggregates Q6 with the two broad-catch call sites; same staleness pattern that drove c665711.
- **I3 bumped to 🔴**: two-file emergent; `_subtitle_label` truthiness gap + `refresh_header` remount window combine into a concrete delta-discard path.
- **Q11 demoted 🟡→🔵** (Weakened): race is real but narrow; requires concurrent worker activity timed to the await yield in `refresh_header`.
- **Q13 demoted 🔵→💭** (Weakened): `allow_blank=False` makes the path unreachable in normal operation.
- **InScopeFiles pre-filter**: `InScopeFiles == ChangedFiles` (no back-merge sidecars). 0 findings dropped by pre-filter.
- **Verification**: 1 finding Falsified (Q1), 2 Weakened (Q11, Q13), 12 Verified.
