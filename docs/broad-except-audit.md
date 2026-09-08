# Broad-except audit — scoped notes

This is **not** the whole-app audit the project guidance refers to; that
document is not in the tree. It records only sites added since, with the
category the guidance asks for (KEEP / NARROW / PROMOTE).

| Site | Category | Why |
|---|---|---|
| `src/ytm_player/app/_app.py`, `YTMPlayerApp.on_unmount` → `_step` | KEEP | Independent best-effort shutdown. Each cleanup step (session save, IPC stop, PID removal, timer stop, final listen, player callbacks and shutdown, resolver cache, media integrations, Discord, history close, cache close) is attempted even when an earlier one raised; the failure is logged with the step name via `logger.exception` and a resource reference is dropped only when its step completed. Cancellation is a separate boundary: it is remembered, the remaining steps still run, and it is re-raised at the end, so a cancelled shutdown is never reported as an ordinary success. |
