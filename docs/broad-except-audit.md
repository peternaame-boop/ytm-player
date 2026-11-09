# Broad-except audit — scoped notes

This is **not** the whole-app audit the project guidance refers to; that
document is not in the tree. It records only sites added since, with the
category the guidance asks for (KEEP / NARROW / PROMOTE).

| Site | Category | Why |
|---|---|---|
| `src/ytm_player/app/_app.py`, `YTMPlayerApp.on_unmount` → `_step` | KEEP | Independent best-effort shutdown. Each cleanup step (session save, IPC stop, PID removal, timer stop, final listen, player callbacks and shutdown, resolver cache, media integrations, Discord, history close, cache close) is attempted even when an earlier one raised; the failure is logged with the step name via `logger.exception` and a resource reference is dropped only when its step completed. Cancellation is a separate boundary: it is remembered, the remaining steps still run, and it is re-raised at the end, so a cancelled shutdown is never reported as an ordinary success. |
| `src/ytm_player/services/player.py`, `Player._finish_load` | KEEP | Moves the existing `Player.play` load-error boundary into the native worker. Logs the exception, reports one owned ERROR event, and never publishes a superseded load's state or failure. Cancellation remains a separate boundary in the async waiter. |
| `src/ytm_player/services/auth.py`, `_extract_browser_jar` → per-root extract | KEEP | Best-effort fallback across candidate Zen profile roots. One stale/empty root (e.g. leftover `~/.zen`) must not shadow the real profile, so its failure is logged at debug level and the next root is tried; callers keep their existing behaviour (`_detect_browser` skips the browser, `_extract_and_save` reports failure). If no root succeeds the last error propagates. |
