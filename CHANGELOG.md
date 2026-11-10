# Changelog

All notable changes to ytm-player are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/).

---

### v2.2.0 (2026-11-09)

**New features**

- **Choose the browser used for cookie extraction**: `[yt_dlp] cookie_browser` selects the browser used by setup and automatic session renewal, with configured cookies files tried first. `ytm setup --browser` overrides the sources for one setup run without changing the setting. Thanks to @londospark ([#193](https://github.com/peternaame-boop/ytm-player/issues/193), [#194](https://github.com/peternaame-boop/ytm-player/pull/194)).
- **Jump to a queue position from the CLI**: `ytm play-index <n>` selects and plays the track at zero-based position `<n>` in the current queue, including shuffle order. Invalid positions return an error. Thanks to @Caetanoag for the report and the fix ([#197](https://github.com/peternaame-boop/ytm-player/issues/197), [#198](https://github.com/peternaame-boop/ytm-player/pull/198)).
- **Autoplay picks are marked in the queue**: tracks autoplay adds are marked ↻ on the Queue page and counted in its footer. Removing one keeps autoplay from adding it again until the queue is cleared or the app exits. Thanks to @VedantAnand17 for the request and the change ([#144](https://github.com/peternaame-boop/ytm-player/issues/144), [#199](https://github.com/peternaame-boop/ytm-player/pull/199)).

**Changes**

- **Python 3.15**: CI tests on Python 3.10 and 3.15, and the package lists 3.15 as a supported version.

**Fixes**

- **MPRIS and macOS Now Playing kept reporting Playing after the queue ran out**: when the last track finished, nothing updated the status, so `playerctl status` still said `Playing` with nothing playing. It now reports `Stopped`. Thanks to @tmbrid for the report and @TheeCoderAhmed for the fix ([#176](https://github.com/peternaame-boop/ytm-player/issues/176), [#207](https://github.com/peternaame-boop/ytm-player/pull/207)).
- **Duration column cut off again once a track list needs scrolling**: the table came out wider than its viewport, scrolled sideways, and pushed the rightmost column off the right edge, the same symptom the v1.9.2 fix addressed. The Title column fit now runs once more after the layout settles, also when rows are appended to a table already on screen. Thanks to @postoso for the fix ([#175](https://github.com/peternaame-boop/ytm-player/pull/175)).
- **Choosing an action with Enter no longer replaces the queue**: picking Play Next, Add to Queue or Go to Artist from the track actions menu with the keyboard also played the row the menu was opened on, replacing the queue with the page's list. The keyboard now does exactly what a mouse click does. Thanks to @tmbrid for the report ([#177](https://github.com/peternaame-boop/ytm-player/issues/177)).
- **Tracks skipped with `HTTP error 403 Forbidden` on yt-dlp 2026.10.21 or newer**: that release made `tv_samsung` one of yt-dlp's default YouTube clients, and on a free account its stream for a track with a pre-roll ad stays locked until the ad would have finished. Playback now waits until the stream is available, which delays the start of that track by up to the length of the ad. A prefetched next track usually has no wait left. Thanks to @tmbrid for the report, @ardzz for the fix and @slastra for the diagnosis ([#195](https://github.com/peternaame-boop/ytm-player/issues/195), [#190](https://github.com/peternaame-boop/ytm-player/pull/190)).
- **Crash at startup on Termux (Android)**: Termux builds Python without `os.link`, so claiming the instance lock raised `AttributeError` and ytm exited with a traceback. The lock now falls back to the plain exclusive create it already used on filesystems without hard links. Thanks to @giquo for the report and the fix ([#204](https://github.com/peternaame-boop/ytm-player/issues/204)).
- **Spotify import no longer crashes when `thefuzz` is missing**: without the optional `thefuzz` package, the Import button still opened its popup, and matching the first track crashed with `NameError: name 'fuzz' is not defined`. The button now shows an error naming the package and how to install it. Thanks to @Bimasakti1024 for the report and @Jah-yee for the fix ([#180](https://github.com/peternaame-boop/ytm-player/issues/180), [#187](https://github.com/peternaame-boop/ytm-player/pull/187)).
- **Crash when two page switches overlap**: a page switch started while another was still running, for example by a footer click, could crash with `DuplicateIds` or leave two pages on screen at once. Page switches now run one at a time, and if more are requested during a switch, the last one is carried out when it finishes. Thanks to @pay-87 for the report ([#145](https://github.com/peternaame-boop/ytm-player/issues/145)).
- **`g L` and other key sequences with a capital letter did nothing in kitty**: with recent Textual versions, terminals that speak the kitty keyboard protocol also send a key event for pressing Shift on its own, and ytm counted it as part of the sequence. The same broke `s A`, `s D` and a count before a capital, such as `15 J`. Modifier presses on their own are now skipped. Thanks to @tmbrid for the report ([#178](https://github.com/peternaame-boop/ytm-player/issues/178)).
- **macOS Now Playing stays active**: the Cocoa main run loop is serviced alongside Textual's asyncio loop, so published track metadata appears reliably in Control Center. Thanks to @alvaro0x404 ([#131](https://github.com/peternaame-boop/ytm-player/pull/131)).
- **macOS media keys stopped working after a while**: macOS disables the media-key event tap when a callback is slow or during an input flood, and nothing re-enabled it, so the keys silently went to Apple Music for the rest of the session. The tap is re-enabled as soon as macOS reports it disabled. Thanks to @wgordon17 ([#141](https://github.com/peternaame-boop/ytm-player/pull/141)).
- **Phantom bouncing "Python" Dock icon on macOS**: creating the mpv handle promotes the process to a foreground app and nothing ever finishes that launch, so a generic Python tile bounced in the Dock indefinitely. The process is demoted to an accessory app (no Dock tile, still a Now Playing source) once mpv is up. Thanks to @aksholokhov ([#128](https://github.com/peternaame-boop/ytm-player/pull/128)).
- **`ytm doctor` now shows the traceback under a logged error**: the Recent ERROR/WARNING section dropped the traceback and exception message logged under an entry such as `Failed to initialize services`. Each entry now keeps up to the last 40 lines logged under it, and the section shows the last 20 entries. Thanks to @dhivyanj for the report ([#181](https://github.com/peternaame-boop/ytm-player/issues/181)).
- **`ytm next` and `ytm prev` reported "TUI is not responding" while the next track loaded**: the TUI replied only after the new track had started, which can take more than the CLI's 5-second wait while a stream waits out a pre-roll ad. The track still started. The TUI now replies as soon as it accepts the command ([#201](https://github.com/peternaame-boop/ytm-player/issues/201)).
- **Autoplay refill skipped a replaced queue**: when the queue was replaced, or playback stopped and started again, while a background refill was still fetching suggestions, the new queue got no refill at that track change. The new queue now starts its own refill. The next track is also prefetched after a refill appends suggestions, reducing the wait when skipping to it.
- **Sign-in failed or the home feed broke with a large browser cookie jar**: the auth header carried hundreds of multi-KB `ST-*` tracking cookies, pushing requests over the server's size limit, so `ytm setup` found no valid account or the home feed failed with `413 Request Entity Too Large` on a continuation request. The `ST-*` cookies are now left out of the header for browser and cookies-file setup and for automatic renewal. Thanks to @dayaj1222 for the fix and @slastra for the Firefox diagnosis ([#182](https://github.com/peternaame-boop/ytm-player/pull/182)).
- **Zen setup stopped at a leftover profile folder**: a stale folder such as an old `~/.zen` hid the real profile, so `ytm setup` found no Zen cookies. Setup now uses the first Zen profile folder with a signed-in YouTube session, and also looks in the Flatpak `config/zen` and Windows `%LOCALAPPDATA%\zen` folders. Thanks to @dayaj1222 for the fix ([#182](https://github.com/peternaame-boop/ytm-player/pull/182)).
- **`ytm setup` found no cookies in Brave, Chrome or Chromium on Hyprland and other Linux desktops yt-dlp does not recognise**: on those desktops, a profile whose cookie key is kept in the GNOME keyring decrypted to nothing and setup reported "No .youtube.com cookies found". When a Chromium-based browser yields no signed-in YouTube session on Linux, setup and automatic renewal now retry each keyring yt-dlp supports. `secretstorage` is a new Linux dependency, since yt-dlp needs it to read the GNOME keyring. Thanks to @londospark for the report and the fix ([#191](https://github.com/peternaame-boop/ytm-player/issues/191), [#192](https://github.com/peternaame-boop/ytm-player/pull/192)).
- **Google accounts in browser slots 5–9 were not found**: `ytm setup` and automatic renewal's fallback scan now check slots 0–9 instead of 0–4. `ytm setup --slots N` checks the first N slots (up to 30) for an account further down the browser's list. For an account recorded past slot 9, renewal checks all 30 slots if its saved slot no longer matches. Thanks to @wgordon17 for the fix ([#179](https://github.com/peternaame-boop/ytm-player/pull/179)).
- **Clicking a track after a right-click menu did nothing**: once a track, artist or album menu opened with a right-click closed, by picking an action, pressing Escape or clicking outside it, the track list ignored the next click that should start a track. The list no longer ignores that click. Thanks to @lhenriquez02 for the fix ([#186](https://github.com/peternaame-boop/ytm-player/pull/186)).
- **Bracketed text in names disappeared**: track, artist, album and playlist names, lyrics, search results and error messages were read as terminal markup, so `Song [Live]` showed as `Song`, a name containing `[/x]` raised an error, and a crafted name could carry a clickable action. They now show exactly as typed in the track list, pages, sidebars, popups, notifications, the Spotify import popup and `ytm import`.
- **Seeking with nothing loaded could close the app**: pressing a seek key while nothing was playing, or while a track was still loading, raised an error in the key handler. The seek is now ignored.
- **A superscript digit before a key could close the app**: characters such as `²` were accepted as a count prefix and then failed to parse. Only `0`-`9` count as a prefix now.
- **Skipping to a track that was being prepared could close the app**: when the background prefetch of the next track was replaced while a play request was waiting on it, the play was cancelled along with it. The play now resolves the track itself.
- **Rows on the Queue page stopped playing after Play all or a discovery mix**: the Queue page opened by those actions stayed tied to the request that opened it, so selecting a row after the next track change did nothing.
- **Large playlists and artist song lists stopped at the first batch**: pressing Next while the first track was still loading dropped the fetch of the remaining tracks, leaving the queue cut short.
- **A right-click that opened no menu swallowed the next click**: right-clicking the header or empty space of a track list, or an artist or album cell with nothing to open, made the list ignore the next click.
- **Closing Spotify import cancelled unrelated background work**: cancelling the import popup cancelled every background task in the app, not only the import.
- **Spotify import stopped at playlists of exactly 100 tracks**: such a playlist was assumed to be cut short and the import asked for Spotify API credentials in a form with no visible fields. The playlist is now imported as it is.
- **`ytm cache status` and `ytm cache clear` ignored `[cache] location`**: both used only the default cache directory, and `clear` deleted the cache index even while the TUI was using it. `status` now reports the configured directory. `clear` refuses to run while the TUI is running, removes the cached files the index lists and empties the index in place, leaving unknown files in a custom location alone. A damaged index is still removed, so the app can start again.
- **A malformed cookies file put cookie values in the log**: when a `cookies_file` line could not be parsed, the warnings from setup, session renewal, streaming and downloads quoted the whole line, cookie value included, and `ytm doctor` then printed them. The failed-download notification showed it too. None of them includes the line now.
- **Startup failed when the MPRIS name could not be registered**: a session bus that accepted the connection but refused the name or the interfaces stopped the app during startup. It now starts without MPRIS and logs the reason.
- **A corrupt config file could be overwritten without a backup**: when a corrupt `config.toml` could not be moved to its backup, which on Windows happened whenever an older backup existed, defaults were written over it anyway. The backup now replaces an older one, and if it still fails the file is not overwritten at startup.

---

### v2.1.0 (2026-09-08)

**Upgrade notes**

- **Existing sign-ins keep working**: when an older session expires, run `ytm setup` once to enable account-checked automatic renewal.
- **Authenticated streaming is optional**: `[yt_dlp] use_session_cookies` remains off by default. Enabling it requires a working JavaScript solver and runtime; anonymous streaming is unaffected. See [configuration](https://github.com/peternaame-boop/ytm-player/blob/v2.1.0/docs/configuration.md#yt_dlp).
- **Downloads share the audio cache limit**: older cache entries may be evicted, oversized downloads are reported as not retained, and interrupted downloads restart from the beginning.

**New features**

- **Multi-select tracks**: click a track, then Shift-click another to select the range; Ctrl-click toggles individual tracks. Keyboard alternatives are `v` to mark and `V` to extend a range. Press `A` to add the selection to a playlist. Marks survive sorting, filtering and background refreshes, including duplicate song occurrences. Mouse modifiers depend on terminal support. See [keybindings](https://github.com/peternaame-boop/ytm-player/blob/v2.1.0/docs/keybindings.md) ([#159](https://github.com/peternaame-boop/ytm-player/issues/159)).
- **Browse Playlists and Subscriptions**: discover playlists from your home feed and open your subscribed artists. Five tabs fit alongside the sidebar in an 80-column terminal. Thanks to @ThePhatLeee ([#125](https://github.com/peternaame-boop/ytm-player/pull/125)).
- **Combined listening history**: Recently Played now opens on All, showing local history first, followed by additional account history, not one combined timeline. Separate Local and YT Music tabs remain available; the account tab no longer excludes songs played in this app. Thanks to @Villoh ([#115](https://github.com/peternaame-boop/ytm-player/issues/115)).

**Changes**

- **Sidebar playlist double-click**: double-clicking a sidebar playlist starts playback without leaving its Library view.
- **URL redaction in `ytm doctor`**: `ytm doctor` redacts HTTP(S)/TCP URLs, including embedded stream IP/signature parameters. Review diagnostics before sharing; arbitrary secrets are not guaranteed to be removed.
- **yt-dlp logging, resolver resets and cookie extraction**: yt-dlp warnings reach the app log, resolver resets handle concurrent work safely, and `pycryptodomex` supports faster browser-cookie extraction. Thanks to @wgordon17 ([#136](https://github.com/peternaame-boop/ytm-player/pull/136)).

**Fixes**

- **Playlist shuffle**: playlist shuffle now includes the first track played and respects saved shuffle preferences. Selecting a specific song still plays that song. For playlists over 300 tracks, the first song is chosen from the first 300; the remaining tracks shuffle in as they load.
- **Failed streams**: failed streams retry once with a fresh resolver after network or VPN changes. Exhausted recovery reports the failure rather than silently stopping or looping in repeat-one ([#135](https://github.com/peternaame-boop/ytm-player/issues/135)).
- **Stale playback requests**: Stop and newer playback requests supersede stale loads, radio requests and delayed queue updates. Old playback events no longer advance a newer selection.
- **Queue actions on duplicate songs**: queue actions target the selected occurrence, including duplicate songs. Removing the playing row lets that song finish before continuing with its surviving successor.
- **Queue rows after shuffle and background changes**: shuffle toggles and background queue changes keep the displayed rows synchronised. Turning shuffle off preserves the current track; reordering clears temporary sorting while retaining filters.
- **Saved resume point**: opening and quitting without playback preserves the saved resume point. Failed startup, failed loads and superseded seeks no longer overwrite it.
- **Playlist change results**: playlist changes report rejected or unconfirmed results accurately. After a timeout, check the playlist before retrying, as the change may already have succeeded.
- **Playlist picker**: the playlist picker prevents duplicate submissions, retains filter focus, handles duplicate confirmation consistently, and displays newly created playlists even when adding tracks is declined.
- **Marks and cursor in Recently Played**: Recently Played preserves marks and cursor position across repeated account-history rows and background updates.
- **Browse and Charts loads**: Browse and Charts recover from failed or interrupted loads. Country changes show matching data, and locale values select the correct country.
- **Truncated libraries and artist lists**: large playlist libraries and full artist album/single lists are no longer truncated ([#134](https://github.com/peternaame-boop/ytm-player/issues/134), [#138](https://github.com/peternaame-boop/ytm-player/issues/138)).
- **Background work cancelled by other actions**: radio, discovery, search and background playlist loading no longer cancel unrelated work. Sidebar double-clicks no longer trigger redundant playlist loads.
- **Automatic renewal**: automatic renewal stays with the recorded account, works for sorted playlists, and backs off for 60 seconds after failure.
- **Concurrent sign-in updates**: sign-in updates protect newer setup sessions from concurrent renewal and bind stream cookies to the corresponding sign-in.
- **Manual setup**: manual setup accepts Chrome’s pasted headers more reliably, preserves the existing session until replacement, and reports when a running app needs restarting. Zen Browser detection is supported ([#119](https://github.com/peternaame-boop/ytm-player/issues/119)).
- **Startup with unreachable or damaged data**: unreachable YouTube Music, an unreadable history database, or malformed session/update-cache files no longer prevent the supported startup paths from continuing.
- **Shutdown cleanup and settings round-trip**: shutdown attempts remaining cleanup even when a service fails. Settings containing multiline or control characters round-trip correctly.
- **Downloads**: downloads are indexed correctly, publish only completed files, and report directory failures without closing the app.
- **Custom themes**: custom themes correctly control selection highlights and album-art placeholders; switching themes preserves terminal colours and other base settings ([#122](https://github.com/peternaame-boop/ytm-player/issues/122), [#158](https://github.com/peternaame-boop/ytm-player/pull/158)).
- **Seek keys, radio artwork, titles and artist rows**: fixed punctuation seek keys, missing radio artwork, markup-like titles and duplicate artist-album rows ([#143](https://github.com/peternaame-boop/ytm-player/issues/143)).
- **403 failures and Spotify import**: updated yt-dlp requirements address widespread 403 failures; Spotify scraper compatibility and full-playlist fetching are restored (#140, [#142](https://github.com/peternaame-boop/ytm-player/issues/142)).

**Internal**

- **Packaging**: packaging excludes known local-note files and checks for unintended untracked inclusions in locally built distributions.

---

### v2.0.0 (2026-07-04)

**New features**

- **Recently Played, YT Music account history tab**: the Recently Played page now has two tabs: Local (the existing SQLite play history recorded inside this app) and YT Music (your account-wide play history fetched from the server via ytmusicapi `get_history()`). Switch tabs by clicking the labels or with the keyboard (`Tab` / `Shift+Tab` to focus a tab label, `Enter` to activate), consistent with the Browse page. Each tab is cached so switching back doesn't refetch; re-clicking the active tab does a hard refresh (Local re-reads SQLite, YT Music refetches the server). Both tabs are capped at 100 rows to keep the TUI responsive. The two views are disjoint: Local shows what you played in this app (a just-played track appears at the top immediately), YT Music shows only plays made elsewhere (phone, browser). Plays this app syncs to your account are filtered out of the server feed, and a track you play locally drops off the YT Music tab live. `[▶ Start Radio]` and filtering work on both. No new keybinding: both views live under `g r`. Thanks to @Villoh ([#116](https://github.com/peternaame-boop/ytm-player/pull/116)).
- **Plays sync to your YouTube Music history**: tracks played in the TUI are now reported back to your YT Music account (via `add_history_item()`), so they show up in your history and feed recommendations like any other client. Fired best-effort in the background so it never blocks playback. Opt-out via `playback.sync_history_to_ytmusic = false`. Thanks to @Villoh ([#116](https://github.com/peternaame-boop/ytm-player/pull/116)).
- **Configurable listen threshold**: `playback.history_min_listen_seconds` (default 5) sets how long a track must play before it counts as a play instead of a skip, for both local history and the YT Music account sync. Clamped to 0–3600; `0` still requires at least one full second of playback. Thanks to @Villoh ([#116](https://github.com/peternaame-boop/ytm-player/pull/116)).
- **Play Next, everywhere**: press `X` (or `Ctrl+X`) on any page to slot the focused track in right after the current one. Albums and playlists get a "Play Next" entry in their context menus too, inserting the whole set in order. It is shuffle-aware, so they still play next even with shuffle on.

**Changes**

- **Only one TUI instance at a time**: launching `ytm` while another instance is running now exits with "ytm is already running (PID …)" instead of letting two instances fight over the same session and IPC socket.
- **Add to Queue works on every page**: `Z` / `Ctrl+Z` silently did nothing on five of the seven pages; it now works everywhere through the same track resolution the actions menu uses, and queue toasts name the track ("Added to queue: …", "Playing next: …").
- **Retired keybindings that promised nothing**: `g A` / `g a` ("context/selected actions") were bound, routed, and listed in Help, but no page ever implemented them (the `a` actions menu already does that job). They are removed, along with the dead Escape close-popup action. The Help page now matches reality.
- **Every colour now follows your theme**: the last hard-coded widget colours (the search-mode `▶` indicator and the Spotify-import status texts and result symbols) now use the theme's `primary` / `success` / `warning` / `error` variables, so custom themes recolour them too.
- **`toggle_search_mode` ships unbound**: the `M-v` default was unreliable because most terminals intercept Alt+letter for their own menus; bind your own key in `keymap.toml` if you want one.

**Fixes**

- **Video rows stop crediting "1.2M views" as the artist**: YT Music returns the view count as a pseudo-artist on video results; it's now stripped during normalisation, so history and video rows show the real artist and artist-based actions (go to artist, radio) built from those rows target the right one. Thanks to @Villoh ([#116](https://github.com/peternaame-boop/ytm-player/pull/116)).
- **MPRIS survives broken dbus-fast builds**: a dbus-fast that raises `TypeError` at import (dbus-fast 4.x on Python 3.14) now disables MPRIS with a notice instead of crashing at startup. Thanks to @dsafxP ([#113](https://github.com/peternaame-boop/ytm-player/issues/113)).
- **Discord presence can no longer wedge on one bad call**: a single failed pypresence call (closed pipe, timeout) silently disabled Rich Presence for the rest of the session, leaving the last song stuck on Discord until app exit. RPC calls are now serialised, reconnect on demand, and retry once after a failure. Thanks to @SUPER-MAGIX for the report ([#117](https://github.com/peternaame-boop/ytm-player/issues/117)).
- **Queue: `d`/`J`/`K` after sort or filter**: deleting or reordering on a sorted/filtered Queue page acted on the wrong underlying track; the row→track mapping is now correct.
- **Playing a selected track works the same on every page**: Search and Browse now rebuild the queue the way Library and Context always did, fixing stale Queue-page contents and duplicate plays from double-fired events.
- **Removing the playing track under shuffle**: the queue now advances to the next shuffled track, matching non-shuffle behaviour, instead of landing somewhere arbitrary.
- **`playerctl` volume and position**: MPRIS `Volume` is wired in both directions and `Seeked` is emitted on jumps, so desktop applets can set the volume and track the playhead.
- **Rapid track-switch race**: starting a track while another was still resolving could double-play or land on the wrong track; play requests are now serialised and the newest wins.
- **IPC hardening**: CLI↔TUI messages are newline-framed, and the Windows TCP fallback now requires an auth token instead of trusting anything on localhost.
- **Config resilience**: a corrupt `keymap.toml` is backed up and replaced with defaults instead of aborting startup, settings values are type-validated at load, and `cache_dir` accepts `~` paths.
- **Navigation state stays fresh**: pages no longer restore stale cursor/content state, and deleting a playlist purges its cached pages instead of leaving ghost entries reachable via Back.
- **Popup backdrop clicks**: clicking outside any popup now dismisses it consistently, across all popups.
- **Database errors degrade gracefully**: SQLite errors at history/session write sites are caught alongside OS errors instead of crashing the operation.
- **Player teardown races**: transport actions during mpv shutdown no longer raise, and a failed stream start no longer disturbs end-of-track handling for the track after it.
- **Media-key presses can no longer vanish**: Windows and macOS key callbacks were scheduled without holding a reference, so the garbage collector could occasionally reclaim one mid-flight and the press did nothing. A shared dispatcher now keeps every in-flight callback alive.
- **Radio respects shuffle from a cold start**: starting radio into an empty queue with shuffle on skipped the shuffle-order build, so tracks played in linear order until shuffle was toggled off and on.
- **Fast track-switching can't wedge stream resolution**: cancelling a track that was still resolving could leave the next request for the same track waiting forever, or turn a successful resolve into an error. Both cancellation paths now settle cleanly.
- **Playlist-creation failures say why**: creating a playlist (sidebar, picker, or Spotify import) now reports whether it failed from an expired session, a network problem, or a server error, instead of a generic "Failed to create playlist".
- **A corrupt saved volume can't derail startup**: a garbage `volume` in session.json falls back to the default and out-of-range values clamp to 0–100, instead of aborting the session restore midway.
- **Liked Songs distinguishes "empty" from "failed"**: a fetch failure now shows a check-the-log message instead of the misleading "No liked songs found."
- **Browse outages say so**: when For You or Charts can't be fetched (network, expired session, server error), the sections now show the retryable error copy instead of pretending the account has no recommendations or the region has no charts.
- **Two swallowed-input quirks**: a right-click that didn't open a popup no longer eats your next Enter/click, and picking a search suggestion identical to the current input no longer eats the next keystroke.
- **`ytm doctor` stops counting bystanders**: any process whose command line contained a `/ytm…` path (an editor open on the repo, say) counted as a running instance; only the real `ytm` / `python -m ytm_player` entry points match now, including console-script launches (where the interpreter sits in argv[0] and the `ytm` script path in argv[1]).
- **A crashed session can't lock you out**: if the OS handed a crashed instance's PID to an unrelated process, `ytm` refused to launch until `ytm.pid` was deleted by hand; the single-instance guard now checks that the recorded process is actually ytm-player and cleans the stale file itself.
- **Parser drift isn't blamed on your connection**: when YouTube changes a response shape mid-operation, the error now reads as a server problem instead of "check your internet connection".
- **No phantom crash file from the yt-dlp pre-warm**: a failure in the background yt-dlp import was written out as a crash file; it now logs a warning, and the only real cost is a slower first play.

**Internal**

- **Shared filter mixin and popup base**: the five per-page track-filter stacks are unified into one shared mixin, all popups sit on a shared base shell, and the ytmusicapi sort-parameter patch window no longer leaks into unrelated concurrent API calls.
- **Test coverage**: first dedicated test coverage for the Spotify import service and the mpv player wrapper.
- **Services dedup sweep**: the Python 3.10 StrEnum shim has one home, the three platform media-key services share one thread→loop dispatcher, stream-URL expiry follows a single policy, the lyrics and update-check fetchers use `requests`, and duplicated CLI/IPC boilerplate, path normalisers, listen-logging, and go-to-artist/album id extraction each collapsed into single implementations. Page-load failures now log their real tracebacks.
- **Packaging hardening**: the AUR package now declares `python-packaging`, requires `dbus-fast` 5.x (4.x is the [#113](https://github.com/peternaame-boop/ytm-player/issues/113) crash combo), and caps textual below 9.0; a mistagged release can no longer upload the wrong version to PyPI (the tag is checked against `__version__` before anything builds); CI now also runs on pull requests targeting `dev`.

---

### v1.9.5 (2026-06-28)

**New features**

- **Unified Tab / Shift+Tab section navigation**: `Tab` and `Shift+Tab` now move focus between sections (track tables, result panels, Browse tab labels, and any visible sidebar) consistently on every page, with `j`/`k`/arrows moving within the focused section and `Enter` activating it. Previously only the Search page worked this way; other pages ignored `Tab` or repurposed it. On Browse, `Tab` highlights a tab label and `Enter` opens it.
- **Vim-style pane focus navigation**: keyboard users can now move focus between the Playlists sidebar, main content, and visible lyrics pane with `Ctrl+w h`, `Ctrl+w l`, and `Ctrl+w w`. The Playlists shortcut auto-shows the sidebar if it is hidden. Thanks to @860windtree (#107, [#108](https://github.com/peternaame-boop/ytm-player/pull/108)).

**Changes**

- **Queue track reorder moved off `Tab`**: reordering the selected track now uses `Shift+J` / `Shift+K` (lowercase `j`/`k` move the cursor, as on every other page). It honours a count prefix, so `15 J` moves the selected track down 15 positions in one step. This frees `Tab` / `Shift+Tab` on the Queue page for section navigation.
- **`playerctl` / media keys / now-playing work out of the box on Linux**: the `dbus-fast` library that MPRIS needs now ships by default on Linux instead of behind an optional `[mpris]` extra, so a standard `pip install ytm-player` (or AUR / Nix install) exposes `playerctl`, hardware media keys, and desktop now-playing controls with no extra steps. `ytm doctor` reports MPRIS status and a one-time startup notice flags a broken or incomplete install, so the previous silent no-op can't recur. Thanks to @pironha2 for the report ([#110](https://github.com/peternaame-boop/ytm-player/issues/110)).
- **Crash files self-identify, and `ytm doctor` flags stale crashes**: every crash log now records the app version, time, Python, and platform it was written under, and `ytm doctor` warns when the most recent crash predates the installed version (or predates version stamping). A stale, already-fixed crash from an older build no longer reads as a live bug.

---

### v1.9.4 (2026-06-19)

A broad release: community playlist and context-menu features, a batch of crash fixes across macOS, Windows, and Nix, and Discord Rich Presence working again.

**New features**

- **Create playlist with metadata**: the "New Playlist" flow now asks for name, description, and privacy (Private / Public / Unlisted) in a single modal, instead of defaulting to private with no description. New `CreatePlaylistPopup` replaces the minimal `InputPopup`. Thanks to @Villoh ([#79](https://github.com/peternaame-boop/ytm-player/pull/79)).
- **Edit playlist metadata**: right-click any user playlist in the sidebar and choose "Edit Playlist" to rename it, update its description, or change privacy. The sidebar and the open library header update in place without reloading tracks. Thanks to @Villoh ([#79](https://github.com/peternaame-boop/ytm-player/pull/79)).
- **Enriched library playlist header**: description, privacy status, and year now appear in the header alongside owner and track count. Thanks to @Villoh ([#79](https://github.com/peternaame-boop/ytm-player/pull/79)).
- **Sidebar count sync on add**: adding tracks via the "Add to Playlist" picker bumps the target playlist's track count in the sidebar immediately, and duplicate adds prompt before re-adding. Thanks to @Villoh ([#79](https://github.com/peternaame-boop/ytm-player/pull/79)).
- **Auto-navigate on delete**: deleting the currently open playlist from the sidebar returns you to the plain library view instead of leaving a ghost page. Thanks to @Villoh ([#79](https://github.com/peternaame-boop/ytm-player/pull/79)).
- **Remove a track from a playlist**: the track action menu now offers "Remove from Playlist" when viewing one of your playlists, removing the track in place. Thanks to @Villoh ([#79](https://github.com/peternaame-boop/ytm-player/pull/79)).
- **Entity action consolidation**: context menu actions (Play All, Shuffle Play, Add to Queue, Start Radio, Go to Artist, Subscribe) now work consistently for albums, playlists, and artists across all dispatch sites: sidebar, search results, track table column right-click, context page, library page, and browse page. "Shuffle Play" pre-shuffles for a one-time random order without enabling ongoing shuffle. "Play All" / "Shuffle Play" from sidebar and search start playback immediately and jump to the queue. Thanks to @wgordon17 ([#81](https://github.com/peternaame-boop/ytm-player/pull/81)).
- **Column-aware context menus**: right-clicking the Artist column opens artist actions (Go to Artist, Play Top Songs, Start Radio, Subscribe); the Album column opens album actions (Play All, Shuffle Play, Add to Queue, Go to Artist). Multi-artist tracks show a picker first. Thanks to @wgordon17 ([#81](https://github.com/peternaame-boop/ytm-player/pull/81)).
- **Corporate SSL proxy support**: set `ca_bundle` under `[yt_dlp]` to a custom CA certificate bundle so stream resolution works behind SSL-inspecting proxies (Zscaler, Netskope, etc.). A warning is logged if the path doesn't exist. Thanks to @glywil ([#98](https://github.com/peternaame-boop/ytm-player/pull/98)).
- **Richer Discord Rich Presence**: the now-playing status shows the track's album art (falling back to the app icon), displays as "Listening to YouTube Music", and includes elapsed time. Thanks to @Wiibleyde ([#103](https://github.com/peternaame-boop/ytm-player/pull/103)).

**Changes**

- **Faster Nix installs**: the nixpkgs pin now points at a cached channel revision, so flake users download prebuilt dependencies instead of compiling them (notably Deno, pulled in by yt-dlp) from source.
- **Install and troubleshooting docs**: install and troubleshooting docs now reference `dbus-fast` instead of the obsolete `dbus-next` (the code migrated in v1.9.3). Thanks to @aaguilar-hub ([#99](https://github.com/peternaame-boop/ytm-player/issues/99)).

**Fixes**

- **Discord Rich Presence connects again**: the bundled application ID had been rejected by Discord, so presence never appeared. Registered a fresh app and made the ID configurable via `[discord] client_id` for anyone who wants to use their own. Thanks to @Villoh for the report ([#88](https://github.com/peternaame-boop/ytm-player/issues/88)).
- **Crash setting ANSI themes**: selecting `ansi-dark` / `ansi-light` (added in Textual 8.2.5) crashed the app because their colour tokens like `ansi_cyan` aren't parseable by Rich. They're now translated to the bare ANSI names. Thanks to @dmnmsc ([#89](https://github.com/peternaame-boop/ytm-player/issues/89)).
- **mpv not found with Homebrew installs**: on macOS and Linuxbrew, libmpv lives outside the default library search path, so a non-brew Python (uv tool, pipx, distro) couldn't load it even though `mpv` was on PATH. ytm now searches the Homebrew prefixes, and `ytm doctor` reports libmpv loadability on its own line ([#90](https://github.com/peternaame-boop/ytm-player/issues/90), [#101](https://github.com/peternaame-boop/ytm-player/issues/101), [#104](https://github.com/peternaame-boop/ytm-player/issues/104)).
- **Crash on Windows with the `mpris` extra**: `dbus-fast` is Linux-only and raised an uncaught error at import; the extra is now marked Linux-only and the import is platform-gated, so `uv sync --all-extras` elsewhere degrades gracefully instead of crashing at startup. Thanks to @Villoh ([#106](https://github.com/peternaame-boop/ytm-player/issues/106)).
- **Spotify import builds on Nix again**: the `spotifyscraper` derivation no longer fails on a sandboxed `pip install` during the build, so flake builds with the `spotify` extra work again. Thanks to @peternaame-boop for both causing and fixing it ([#93](https://github.com/peternaame-boop/ytm-player/issues/93)).
- **Nix flake: missing `packaging` dependency**: the update checker crashed on flake builds because `packaging` wasn't declared. Now included, with an import check so it can't regress. Thanks to @szx19970521 ([#95](https://github.com/peternaame-boop/ytm-player/issues/95), [#105](https://github.com/peternaame-boop/ytm-player/pull/105)).

---

### v1.9.3 (2026-05-04)

A small follow-up release: crash fixes caught by manual smoke after v1.9.2, CLI/utility fixes, plus community PRs.

**New features**

- **Configurable default theme**: set `theme` under `[ui]` in `config.toml` to control which Textual theme loads on startup (default `ytm-dark`). Changing theme via `Ctrl+P` → `Theme` updates the current session only; the startup default is no longer overwritten by session state. To persist the active theme as the new default, use `Ctrl+P` → `Theme: Set Current as Default`. Thanks to @Villoh ([#85](https://github.com/peternaame-boop/ytm-player/pull/85)).
- **`Theme: Set Current as Default` command palette action**: saves the currently active theme to `config.toml` so it becomes the startup default. Includes rollback on save failure (e.g. read-only filesystem) with an error toast.
- **Discovery round-robin**: `D` now cycles deterministically through Charts → Trending → For You → Liked → Artist → Recently Played (was random source selection). Charts sub-rotates through its shelves between presses, and the Discovery label shows the active source (e.g. `Discovery (US Daily Top 100)`). Mood source dropped (the Moods & Genres tab was removed in v1.9.1 due to upstream crashes). Thanks to @wgordon17 ([#75](https://github.com/peternaame-boop/ytm-player/pull/75)).
- **Radio queues prepend their seed tracks**: radio playback now starts with the seed before suggestions, matching YouTube Music's native behaviour. Append-mode background refill is unchanged. Thanks to @wgordon17 ([#75](https://github.com/peternaame-boop/ytm-player/pull/75)).
- **Persistent queue source header**: Queue page shows `Generated from: …` beneath Now Playing for radio and discovery queues, with up to three seed titles inline and a tooltip for the full list. Toggle via `[ui] show_queue_source` (default on). Thanks to @wgordon17 ([#75](https://github.com/peternaame-boop/ytm-player/pull/75)).

**Changes**

- **Theme persistence model**: `config.toml` is now the authoritative source for the startup theme; `session.json` stores runtime state only and no longer restores the theme on launch. This separates user-authored configuration from app-managed session state.

**Fixes**

- **Crash on Go to Artist / Album / Playlist**: `TrackTable` and `_ArtistAlbumList` set up their columns in `on_mount`, but `context._build_artist`'s nested-mount chain calls `load_tracks` / `load_albums` synchronously before `on_mount` fires, so `add_row` ran with 0 columns and raised `ValueError: More values provided than there are columns`. Both widgets now eager-init columns in `__init__`, eliminating the mount-order race.
- **Update check version comparison**: `_is_newer` now uses `packaging.version.Version` for proper PEP 440 comparison. The hand-rolled tuple parser dropped non-numeric chunks and got post-releases (e.g. `1.6.0.post1`) wrong.
- **`ytm config` with multi-arg `$EDITOR`**: `EDITOR="code -w"` and similar now work; previously the whole string was passed as a single argv entry, so subprocess looked for an executable literally named `code -w` and failed. Malformed quoting (e.g. unbalanced quotes) now exits cleanly via the existing error path instead of crashing.
- **Search race conditions**: fixed four interacting bugs that caused searches to require a second Enter, the suggestion overlay to re-appear on top of incoming results, and selecting a suggestion to cancel the in-flight search worker. Thanks to @wgordon17 ([#84](https://github.com/peternaame-boop/ytm-player/pull/84)).
- **Settings save Windows fallback**: `Settings.save()` now falls back to a direct `path.write_text()` when `os.replace()` raises `PermissionError` (e.g. when `config.toml` is held open by an external editor on Windows).

**Internal**

- **Command palette provider architecture**: app-specific commands moved from `get_system_commands()` to a dedicated `YTMCommandProvider` registered in `App.COMMANDS`. Isolates command definitions in `src/ytm_player/app/_commands.py` and keeps `_app.py` focused on app logic.
- **Pre-commit hooks + pyright in CI + `dbus-fast` migration**: new `.pre-commit-config.yaml` runs ruff-format / ruff / pyright on commit and pytest on push (`pre-commit install` to activate). New CI job runs pyright at standard strictness. The Linux MPRIS service migrates from the unmaintained `dbus-next` to the maintained `dbus-fast` fork (identical API). Thanks to @wgordon17 ([#83](https://github.com/peternaame-boop/ytm-player/pull/83)).

---

### v1.9.2 (2026-05-01)

A focused fix release. The Charts page region selector was effectively non-functional: three structural bugs in how YouTube's chart response was read made a global event playlist appear regardless of region. Also includes a TrackTable migration that retires three hand-rolled `DataTable` pages and two related bug fixes. Thanks to @dmnmsc for the Charts bug report ([#73](https://github.com/peternaame-boop/ytm-player/issues/73)).

**New features**

- **TrackTable migration on Queue, Liked Songs, Recently Played**: three pages migrated from raw `DataTable` to `TrackTable`. Gains right-click context menus, play indicators, column resize, filtering, and sorting, on three pages that previously rolled those manually. Also retires the `on_mouse_down` right-click workaround added to QueuePage in v1.9.0 (TrackTable already wires this up). Thanks to @wgordon17 ([#74](https://github.com/peternaame-boop/ytm-player/pull/74)).
- **`[▶ Start Radio]` button**: added to Liked Songs and Recently Played page headers. It seeds a radio from 5 random tracks in the collection. Thanks to @wgordon17 ([#74](https://github.com/peternaame-boop/ytm-player/pull/74)).
- **Shuffle-lock integration**: on Liked Songs and Recently Played, selecting a track applies the per-collection shuffle preference. Thanks to @wgordon17 ([#74](https://github.com/peternaame-boop/ytm-player/pull/74)).

**Changes**

- **Region picker expanded 17 → 68 entries**: Global (`ZZ`) is the new default. The list now mirrors YouTube's full advertised set (62 codes from `countries.options`) plus six historically-supported codes outside that list (Hong Kong, Malaysia, Singapore, Taiwan, Thailand, Vietnam). Settings default flips `region = "GB"` → `region = "ZZ"`.
- **`_clean_shelf_title` no longer strips brand prefixes**: Coachella keeps its `"Coachella 2026:"` prefix on the pill so users can tell an event from a country chart at a glance.
- **Discovery mix source order**: Discovery mix now cycles sources in fixed order (Charts → Trending → For You → Your Liked Songs → Artist → Recently Played) instead of random selection, which guarantees variety across consecutive presses of `D`.
- **Radio notifications**: radio notifications now list all seed track names as a bulleted list instead of showing only the first seed or a generic label.
- **Playlist radio notification**: playlist radio notification now includes the playlist name (e.g. "Playing: Radio from My Playlist").
- **Mood source removed from discovery mix**: upstream removed Moods & Genres tab; the source was failing silently.
- **Sidebar gains a bottom separator under the Playlists panel**: the existing top separator (a `Rule` widget between the pinned-nav block and the LibraryPanel) is now mirrored below the LibraryPanel, so the `Playlists` panel sits between two matching `$border`-coloured horizontal rules instead of just one.

**Fixes**

- **Events visually separated from country charts**: YouTube injects a global event playlist (currently `"Coachella 2026: Daily Top 100 Songs"`, same playlist ID worldwide) at position 0 of every country's response. Previously the Charts page default-loaded that slot, so picking a country still showed the global Coachella playlist. The Charts page now renders two stacked pill rows: a `Featured globally:` strip for any shelves whose title carries a brand prefix (`": "` separator) and a country-charts row for the actual regional shelves. Country charts sort by priority (`Top 100 Songs` → `Weekly Top Songs on Shorts` → `Trending 20` → rest) and the default-loaded pill is the first country chart, never an event. The event row hides automatically on narrow terminals (< 80 cols) to reclaim a row of vertical space.
- **Charts now read `daily + weekly + videos` from the API**: previously only `daily` was consulted, which silently dropped Spain (returns its data under `videos`) and missed the Top 100 Songs / Top 100 Music Videos shelves under `weekly` for premium-supported regions. All three keys are now concatenated, then split into events vs charts.
- **Locale-style configs auto-normalise**: new `services/regions.normalise_region()` helper strips locale tails, so `"ES-ES"`, `"en-GB"`, `"es_ES"` now resolve to bare two-letter codes (`ES`, `EN`, `ES`) before hitting the API. YouTube's chart endpoint silently falls back to Global for any locale-shaped input; this prevents existing configs from being broken by that quirk.
- **Radio track durations no longer show `--:--`**: ytmusicapi's `get_watch_playlist` returns duration under a `length` key (e.g. `"3:07"`), not `duration`. `extract_duration()` now checks `duration_seconds` → `duration` → `length` in priority order. Thanks to @wgordon17 ([#74](https://github.com/peternaame-boop/ytm-player/pull/74)).
- **Play history no longer stores duration as 0**: `log_play` was reading raw `track.get("duration_seconds", 0)`, but normalised tracks store the value under `duration`. Switched to `extract_duration()` so the value is always correct regardless of source. Thanks to @wgordon17 ([#74](https://github.com/peternaame-boop/ytm-player/pull/74)).
- **TrackTable Duration column no longer cut off on first paint**: the row-label column (which carries the `▶` playing indicator) reserves ~3 cells of width that the original column-fit pass didn't account for, so the rightmost column ("Duratio…") got pushed past the visible viewport on initial render. `_fill_title_column` now runs after `load_tracks` and `append_tracks` so the Title column shrinks to compensate as soon as rows exist.

**Internal**

- **Shared chart utilities**: `clean_shelf_title` and `get_chart_shelf_tracks` added as shared utilities for reuse in discovery mix.

---

### v1.9.1 (2026-04-30)

A community-PR-driven feature release: PRs from @wgordon17 plus user-reported UX work, an AUR auto-publish pipeline, and a per-collection shuffle memory system.

> Supersedes the same-day v1.9.0 tag, which shipped two small regressions: the browser forward-stack got clobbered when clicking the current page's footer entry a second time, and the playback-bar Shuffle-lock dimming didn't refresh when starting a radio from the sidebar. Both are fixed in this release; install v1.9.1 directly.

**New features**

- **"Start Radio" from playlist**: context-menu entry in the sidebar plus a `[▶ Start Radio]` header button on the Library and Context pages. Backed by ytmusicapi's `RDAMPL` watch-playlist convention. New service method `YTMusicService.get_playlist_radio(playlist_id)`. Thanks to @wgordon17 ([#70](https://github.com/peternaame-boop/ytm-player/pull/70)).
- **Proactive radio refill**: when ≤3 tracks remain in queue with autoplay on and repeat off, the app silently fetches more tracks in the background, seeded from up to 5 already-played items in the current queue. New methods `_maybe_extend_queue` / `_extend_queue` on PlaybackMixin. Thanks to @wgordon17 ([#71](https://github.com/peternaame-boop/ytm-player/pull/71)).
- **Multi-seed `get_radio`**: service method now accepts a list of seeds, fetches in parallel via `asyncio.gather`, dedups by `videoId`, shuffles, and trims to limit. Backward-compatible at the signature level (single string still accepted). Thanks to @wgordon17 ([#71](https://github.com/peternaame-boop/ytm-player/pull/71)).
- **Single-seed shuffle guard**: `get_radio` only shuffles the result pool when `len(video_ids) > 1`. Single-seed callers (the existing "Start Radio on track X" flow) keep their seed-first ordering.
- **Discovery roulette**: random mix from one of seven sources (trending, mood, charts, home, liked songs, library artist, recent history) with last-source-exclusion rotation to avoid repeats. New service method `get_discovery_mix() -> tuple[list[dict], str]`. Bound to the `D` keybinding (added by the maintainer alongside the merge; the PR description had mentioned the binding but it didn't ship). Discovery is also reachable via the new "♫ Discovery Mix" item in the playlist sidebar's pinned-nav block. Help page lists the new action. Thanks to @wgordon17 ([#71](https://github.com/peternaame-boop/ytm-player/pull/71)).
- **Shuffle lock (per-playlist)**: explicit toggle in the Library page playlist header (`Shuffle lock: ON` / `Shuffle lock: off`). When ON: opening that playlist forces shuffle, and the playback-bar shuffle button is dimmed and rejects clicks (with a toast pointing the user back to the lock toggle). When OFF: normal global shuffle behaviour. Persists per-playlist-ID to `~/.config/ytm-player/shuffle_prefs.json` (LRU-capped at 1000 entries; thread-safe atomic JSON writes). Replaces the originally-planned implicit "remember last shuffle state" behaviour, which was un-discoverable and didn't actually persist via the playback-bar click path. New `QueueManager.current_context_id` + `set_context()` underpin the lookup. Session restore preserves `queue_context_id` so the lock identity survives a restart.
- **Configurable home shelf count**: set `home_shelves` under `[ui]` in `config.toml` to control how many recommendation shelves are fetched on the Browse > For You tab (default 3, range 1–25). Shelves now scroll as a single section instead of independently, with subtle separators (`$border`) and theme-primary section titles for readability. Thanks to @wgordon17 ([#69](https://github.com/peternaame-boop/ytm-player/pull/69)).
- **Sidebar overflow config (`[ui] sidebar_overflow`)**: `"truncate"` (default) guarantees exactly 1 row per playlist with a `…` ellipsis on overflow, or `"wrap"` to let names span multiple lines naturally. Implementation via a `truncate-items` CSS class on `LibraryPanel` plus a `_render_text` helper that picks the right strategy per mode.
- **Selection info bar**: a 1-row strip mounted between the page body and the playback bar, displays the full name of the currently-focused item (sidebar playlist or TrackTable row). Toggleable via `[ui] show_selection_info: bool = True`. Replaces the old marquee/bouncing-text animation in playlist sidebars. The bar is gated on widget focus so it shows only what the user is actively navigating, not whatever auto-highlighted in a freshly-mounted panel.
- **Now-playing row indicator**: every TrackTable now uses Textual's row-label feature to show a `▶` glyph in a dedicated 1-char column to the left of the index, marking the playing row independently of cursor state. Bold (no colour) on data cells of the playing row provides a secondary at-a-glance signal. The glyph survives navigation away and back via `cursor_foreground_priority="renderable"` and an `on_mount` lookup of the app's current playing track. This pattern follows the canonical approach used by spotify-tui, ncmpcpp, and cmus.
- **Charts country support**: `get_charts(country=)` now defaults to `"GB"` instead of YouTube's empty-data `"ZZ"` placeholder. New `[ui] region` config field (ISO 3166-1 alpha-2) lets users pin Charts to their preferred country without code changes. The Charts page now resolves the top daily chart playlist into a track table (ytmusicapi's `get_charts` no longer returns a flat songs list: daily/weekly/genres/artists chart playlists is the new shape).
- **Country picker (`c`)**: press `c` on Browse → Charts to open a filterable region modal. Type to filter on ISO code or country name; Enter to select; Esc to cancel. Selection persists to `[ui] region` in `config.toml` and triggers a chart refetch. New `Action.PICK_COUNTRY` enum entry, default binding `c`.
- **Daily-shelf pills**: clickable pill row above the track table shows all available daily chart shelves for the selected country (typically `Daily Top 100` / `Trending 20` / `Daily Top Videos` / `Daily Top Songs (Shorts)`; labels stripped of brand prefixes like `Coachella 2026:` and country suffixes). Click a pill to swap the table to that shelf's playlist. `HorizontalScroll` container keeps pills usable on narrow terminals.
- **Browser-style back / forward**: `← Back` and `Forward →` buttons in the header bar (next to `☰ Playlists`). Auto-hide on root pages and at the front of history. Keys: `Backspace` (back), `Shift+Backspace` (forward). Forward stack invalidates on any non-back/forward navigation, matching every browser/file-explorer.

**Changes**

- **Moods & Genres sub-tab removed entirely**: clicking a mood was producing a silent process-exit traceable past every Python-level handler in the audit. The remaining suspects are sub-Python (libmpv segfault, finaliser raise, etc.) and require runtime data that is not available. Pulled the tab rather than ship a feature that crashes the app on click. Tab order is now (For You, Charts, New Releases). The unused `YTMusicService.get_mood_categories` / `get_mood_playlists` wrappers were dropped at the same time. Discovery Mix calls the underlying ytmusicapi client directly, so the wrappers had no remaining callers.
- **Track actions popup conditionally swaps "Add to Queue" ↔ "Remove from Queue"**: when the track being right-clicked is currently in the playback queue, the menu shows "Remove from Queue" (action_id `remove_from_queue`) instead of "Add to Queue". Same menu slot, different label and action based on queue state. Detection happens at popup-open time by scanning `queue.tracks` for the track's `video_id`.
- **`faulthandler.enable(all_threads=True)`**: fatal-signal capture wired in `cli.py` after `setup_logging`. SIGSEGV/SIGBUS/SIGFPE/SIGILL/SIGABRT now leave a Python traceback for every thread in `~/.config/ytm-player/crashes/faulthandler.log` before the kernel delivers the signal. Critical for catching libmpv/python-mpv C-side crashes that bypass `sys.excepthook` entirely.
- **asyncio loop exception handler**: `App.on_mount` installs `_asyncio_exception_handler` via `loop.set_exception_handler`. Funnels "Task exception was never retrieved" warnings and any orphan-task errors into `crashes/ytm-crash-*.log` instead of the invisible-stderr default that Textual's alt-screen swallows.
- **`sys.unraisablehook`**: `install_excepthooks` now also captures `__del__` / weakref-callback / generator-close errors, labels them `Unraisable in <obj>`, and writes a crash file before chaining to `sys.__unraisablehook__`. Fixes a class of silent finaliser failures that were previously lost.
- **mpv `log_handler` + `loglevel="warn"`**: Player constructs `mpv.MPV(log_handler=...)` so libmpv's internal warnings/errors hit the Python logger as `mpv[<area>]: <message>`. `ytm doctor` greps these out of `ytm.log` so libmpv issues are surface-level visible.
- **`ytm doctor` v2, eight sections**: version, paths, process status, recent ERROR/WARNING (filtered, last 20), recent mpv warnings, latest faulthandler trace, latest crash file, active-hooks summary. All output passes through a redaction layer that scrubs `Authorization`, `Cookie`, `Bearer`, `token`, `x-goog-pageid`, `SAPISID` so issue pastes never leak auth.
- **App-level `_handle_exception` override**: the YTMPlayerApp overrides Textual's `App._handle_exception` (documented "Always results in the app exiting") to write a crash file via `write_crash_file()`, surface a toast, and not call `super()`. Keeps the TUI alive on otherwise-fatal worker / render / event errors so the user doesn't lose their queue position to a transient failure.
- **Region list trimmed to 17**: empirically-verified working countries (Australia, Brazil, Canada, France, Germany, Hong Kong, Japan, Malaysia, Mexico, Singapore, South Korea, Taiwan, Thailand, UAE, UK, US, Vietnam). YouTube's `countries.options` advertises 62 but most return no daily-chart data even with auth; trimmed to the subset that actually displays tracks.
- **Empty-region UX**: a region picked with no chart data now shows a message that no chart data is available for that country, that YouTube Music coverage varies by region, and that `c` picks a different one, instead of the previous catch-all `"Failed to load charts."`.
- **Marquee removed from sidebars**: `_BouncingLabel` (the bouncing-text animation that fired on highlighted playlist names that overflowed) deleted entirely. Replaced with static truncate-with-ellipsis. Long names show `Some long playlist nam…`; the full name surfaces in the new selection info bar when the item is highlighted.
- **Bottom-stack layout reflow**: `SelectionInfoBar`, `PlaybackBar`, and `FooterBar` are now wrapped in a single `Vertical(id="bottom-stack")` docked to the bottom. Order top-to-bottom: info bar (1 row) → playback bar (4 rows) → footer (1 row). Replaces the previous three-sibling-dock-bottom approach which produced inconsistent stacking.
- **`truncate()` uses Unicode `…`**: instead of three ASCII dots. Frees 2 chars of visible content space; matches every modern UI's truncation idiom.

**Fixes**

- **macOS built-in keyboard media keys (prev/next)**: built-in MacBook keyboards send key codes 19/20 (FAST/REWIND) instead of 17/18 (NEXT/PREVIOUS) that external keyboards use. The Quartz event tap now maps both sets. Verified against Apple's IOKit constants and Rogue Amoeba's canonical reference. Thanks to @wgordon17 ([#67](https://github.com/peternaame-boop/ytm-player/pull/67)).
- **Browse tab bar visibility**: was rendering at `height: 1` with `border-bottom: solid` on the active tab clipping its label. Now uses `height: 4`, `border-bottom: tall`, `width: auto` for content-sized tabs, hover state, `$surface` background, `$border` separator, plus 1-row top padding for breathing room from the page edge. Thanks to @wgordon17 ([#68](https://github.com/peternaame-boop/ytm-player/pull/68)).
- **yt-dlp android client fallback**: madeForKids content (e.g., children's music tracks) was failing with "Video unavailable" on the default web client. yt-dlp now also tries the android client which falls through to a non-PoT legacy format that succeeds. Empirically validated: Baby Shark goes from broken to playing format-18 m4a audio. Normal songs unaffected. The line carries a `# WHY:` comment explaining the rationale so a future audit doesn't strip it as cargo-cult config. Thanks to @wgordon17 ([#72](https://github.com/peternaame-boop/ytm-player/pull/72)).
- **`KeyError` from ytmusicapi parsers no longer counted as "unexpected"**: added `KeyError` to `_EXPECTED_API_EXCEPTIONS` so `_call` treats parser drift as a recoverable API-side failure instead of letting it propagate as a "programming error." This was masking real upstream issues and surfacing crash files for routine YouTube response-shape changes.
- **Sidebar playlist track count now updates immediately after add**: was reading the cached `count` field from the library payload and never bumping it after `add_playlist_items` succeeded, so the `(N tracks)` suffix stayed stale until the next library reload. New `LibraryPanel.update_item_count(playlist_id, delta)` primitive optimistically updates the cached count and rebuilds the affected row's label text. Wired into both `_do_add` and `_do_create_and_add` in playlist_picker. Tolerates VL-prefix mismatches in either direction. Leaves `count = None` (unknown) untouched rather than fabricating.
- **Queue right-click now opens the actions popup**: previously did nothing because the Queue page uses a raw `DataTable` (not `TrackTable`), so the `TrackRightClicked` handler never fired. Added `on_mouse_down` on `QueuePage` that forwards button-3 clicks to `_open_actions_for_track` using the cursor row.
- **Forward stack no longer clobbered by same-page footer clicks (post-v1.9.0)**: clicking the current page's footer entry a second time intentionally pops the previous page off the back stack, but the navigation path was treating it as a fresh forward navigation and clearing the forward stack along with it. Added a `same_page_back` flag so the forward stack survives the round-trip.
- **Sidebar Start Radio refreshes the Shuffle-lock visual state (post-v1.9.0)**: starting a radio from the sidebar correctly applied the Shuffle-lock force-shuffle behaviour, but the playback-bar shuffle button's dim/non-dim state wasn't repainted to match. Added an explicit `refresh_shuffle_lock_state()` call on the playback bar from the sidebar Start-Radio path.
- **`write_crash_file` self-bootstraps**: falls back to `paths.CRASH_DIR` when `install_excepthooks` was never called, and logs `OSError` instead of silently returning `None`. Fixes the "crashes dir empty after a real crash" diagnostic black hole.
- **OLAK5 playlist fallback**: Trending shelves use OLAK5-prefixed auto-generated playlist IDs that ytmusicapi's `get_playlist` chokes on (`tracks[0]['album']` is `None` → TypeError). `ChartsSection._load_active_daily` detects the prefix and uses `get_watch_playlist` instead, which calls a different endpoint and parses correctly. Service-layer `YTMusicService.get_watch_playlist` extended to accept a `playlist_id`-only call shape (was video-only before).
- **Click bubbling fix**: `Action.PICK_COUNTRY` was missing from `app/_keys.py:_handle_action`'s page-delegation allowlist, so `c` silently no-op'd when first shipped. Added the entry; same `_app-header` ID-based HeaderBar lookup as the rest of the codebase.

**Internal**

- **CONTRIBUTING.md "Theming & UI" section**: documents the rule that all widget colours flow through `theme.py` variables (`$primary`, `$text`, `$surface`, `$border`, `$accent`, `$secondary`, `$text-muted`) and that hardcoded hex colours in widget CSS are forbidden because they break custom `theme.toml` files. Closes a contributor-docs gap surfaced during the [#69](https://github.com/peternaame-boop/ytm-player/pull/69) review.
- **`type: ignore[attr-defined]` removed**: in the `_start_playlist_radio` call sites from [#70](https://github.com/peternaame-boop/ytm-player/pull/70) in `context.py` and `library.py`. Replaced with the project-standard `cast("YTMHostBase", self.app)._start_playlist_radio(...)` pattern. Added stub method to `_base.py:YTMHostBase` (CLEANUP-1).
- **New stubs and config fields**: `_fetch_and_play_radio`, `_start_discovery_mix`, `_start_playlist_radio`, `shuffle_prefs`, `home_shelves`, `region`, `show_selection_info`, `sidebar_overflow` all added as TYPE_CHECKING stubs / config fields with the project's existing patterns.
- **`_BouncingLabel` removal**: `_BouncingLabel` and timing constants (`_BOUNCE_INTERVAL`, `_BOUNCE_PAUSE`) removed.
- **AUR auto-publish workflow**: new `.github/workflows/aur-publish.yml` triggered after the `Publish` workflow succeeds. Clones the AUR `ytm-player-git` repository via SSH key (stored in `secrets.AUR_SSH_PRIVATE_KEY`), copies `aur/PKGBUILD`, regenerates `.SRCINFO` via the new `scripts/regenerate_srcinfo.py` helper, commits, and pushes. Gated on `event.workflow_run.event == 'push'` so TestPyPI dry-runs don't trigger a real AUR push.
- **`scripts/regenerate_srcinfo.py`**: pure-Python `.SRCINFO` generator that parses `aur/PKGBUILD` (handles scalar fields, array fields, multi-line arrays, single/double quotes, shell-variable expansion via `_expand_vars`, and emits the canonical key-value format consumed by AUR). Runs on Ubuntu CI runners without requiring Arch Linux base-devel.
- **`RELEASING.md`**: repo-root release procedure. Documents the tag-driven flow (bump `__version__` → tag `vX.Y.Z` → push), what each automated workflow does (`publish.yml`, `aur-publish.yml`), the manual fallback for the AUR push if the action fails, the one-time setup for AUR SSH keys + GitHub secrets + PyPI trusted publishing, the dry-run path via TestPyPI, and the distribution-channel matrix (PyPI/AUR/GitHub Release automated; NixOS flake manual; Gentoo GURU community-maintained by @dsafxP).
- **AUR repo secrets**: `AUR_SSH_PRIVATE_KEY` and `AUR_KNOWN_HOSTS` repo secrets configured (one-time setup).
- **Radio, discovery and packaging tests**: unit tests cover playlist radio, including VL-prefix stripping, normal flow, exceptions, empty results and non-dict responses; multi-seed radio; single-seed deterministic and multi-seed shuffled behaviour; discovery mixes; and `.SRCINFO` parser primitives, repo-PKGBUILD round-trip and emit format.

---

### v1.8.0 (2026-04-28)

A reliability and quality release driven by an audit. Hardens error handling across the service/UI cascade so silent-failure UX is replaced with actionable feedback, and fixes several latent runtime bugs.

**New features**

- **First-run discoverability toast**: on first launch, a 1.5s-delayed toast reads "Press ? for help · vim-style keys" (8s timeout). State persists in `session.json` so the hint shows once. Legacy session files without the field upgrade cleanly.
- **Per-cause mutation-failure toasts**: like/playlist-add operations now distinguish auth-required, auth-expired, network-down, and server-error failures with specific messages (e.g. a "Sign in again" message pointing to `ytm setup` vs "Check your connection") instead of a single generic "Couldn't update". The classifier inspects ytmusicapi's exception types and parses HTTP status from `YTMusicServerError` messages.

**Fixes**

- **Page error-fallback states**: Recently Played and Context (album/artist/playlist) pages used to show "Loading…" forever on API/disk failure. Now they replace the loading indicator with a clear error message pointing at `~/.config/ytm-player/logs/ytm.log`.
- **Album art crashed on Pillow ≥ 10**: `Image.LANCZOS` was removed in Pillow 10. Switched to `Image.Resampling.LANCZOS`. Pyproject pins `Pillow>=10`, so this had been shipping broken for every modern install.
- **Spotify single/multi import would have ImportError**: the popup imported `_get_video_id` from `services.spotify_import`, but that symbol does not exist (the function is `get_video_id` in `utils/formatting.py`). Renamed all call sites.
- **`gg` / `G` in browse and playlist sidebar would crash**: code called `ListView.action_first()` / `action_last()`, and neither method exists on Textual's ListView. Replaced with the standard cursor-index assignment.
- **New Releases tab in Browse silently empty**: `YTMusicService.get_new_releases` called `client.get_new_releases` which doesn't exist on `YTMusic`. The wrapping broad-except swallowed the AttributeError. Switched to `get_explore()['new_releases']` per the actual ytmusicapi surface.
- **Session-save errors disappeared into the void**: `_save_session_state` now narrows its catch to `(OSError, TypeError)` and surfaces a toast on failure instead of silently dropping the user's volume / queue / playback position.
- **Mutation methods now return `MutationResult`**: `rate_song`, `add_playlist_items`, `remove_playlist_items`, plus `add_to_library`, `remove_album_from_library`, `unsubscribe_artist`, and `delete_playlist`. A Literal: success / auth_required / auth_expired / network / server_error. Previously returned `None` (or `bool`) whether the server accepted or not, so the UI showed "Liked!" or "Added!" toasts even when the API failed silently. Worst case was the Spotify import "Created with N tracks" toast firing when every batch failed. All UI cascade sites now show a per-cause toast suffix via `mutation_failure_suffix`.
- **Spotify multi-import partial-failure track count was off by up to 99**: when a 350-track import had only the last batch fail, the toast reported "~300/350 added" because the formula assumed every successful batch was a full 100. Now tracks `added_total` cumulatively by summing `len(batch)` on success, so the count is exact.
- **Read-side `logger.debug` → `logger.exception` in `ytmusic.py`**: Search, library-list, get_album/artist/playlist/song/lyrics/history etc. previously caught broad exceptions and logged at debug level, so post-mortem of "library page came up empty" required `--debug`. Now they land in the log file at default level.
- **`YTMusicService._call` outer catch narrowed**: programming-error exceptions (TypeError, AttributeError) now propagate instead of being swallowed and mistakenly counted toward the consecutive-failure threshold.
- **Thread-safety on lazy `YTMusicService.client` init**: concurrent first-access from `asyncio.to_thread` workers is now guarded by a `threading.Lock` with double-checked locking.
- **Credential file writes use `O_NOFOLLOW`**: `auth.json` (`services/auth.py`) and `spotify.json` (`services/spotify_import.py`) now refuse to follow a symlink at the target path, defence-in-depth matching the existing pattern in `utils/logging.py`.

**Internal**

- **Integration tests**: new integration tests in `tests/test_integration/` covering the search→queue→play flow, track-change fan-out, cache-bypass behaviour, session round-trip, search cancellation, and the mutation cascade. The coverage floor was raised.
- **Comprehensive broad-except audit**: at `docs/broad-except-audit.md`, categorises the `except Exception:` sites in the codebase as KEEP / NARROW / PROMOTE with a cross-cutting cascade map.
- **Pyright clean-up**: the remaining errors are entirely in `services/mpris.py` (D-Bus magic, exempted in the ruff rules) and `services/macos_eventtap.py` (macOS-only AppKit/Quartz). Fixed real bugs along the way: `playback_bar._FooterButton.__init__` typed `kwargs` as `object` (rejecting all forwarded params), `_RepeatButton.repeat_mode` typed as `str` while assigned an `int`-typed enum value, `track_table._filter_timer` typed as `object | None` so `.stop()` didn't type-check, and several Optional-access defensive gaps.
- **Codebase-wide `self.app.X` typing**: UI widgets/pages now cast `self.app` to `YTMHostBase` at access points so Pyright can see the host's services.
- **README polish**: badges, tagline, Contributors section.

---

### v1.7.2 (2026-04-27)

A combined release covering broader Python compatibility, a monthly Python release watcher, a full README restructure into a landing page + dedicated docs, and the 3.10 backport shims required to support Ubuntu 22.04.

**Changes**

- **README split into a landing page and dedicated docs**: README has been split into a landing page plus dedicated docs (`docs/installation.md`, `docs/configuration.md`, `docs/keybindings.md`, `docs/cli-reference.md`, `docs/spotify-import.md`, `docs/troubleshooting.md`, `docs/architecture.md`). The README is now purely an index: every topic lives in exactly one file with full detail.
- **Python floor lowered from 3.12 to 3.10**: Ubuntu 22.04 LTS users can now `pip install ytm-player` against the system `python3` without installing a newer Python first. Verified locally on Python 3.10 and via the new CI matrix `[3.10, 3.14]`.
- **Python 3.10 lifecycle**: CPython 3.10 reaches end-of-life October 2026. Ubuntu 22.04 keeps shipping 3.10 until April 2027 (standard support) or 2032 (Pro), so 22.04 users stay covered well past CPython's EOL.
- **Classifiers**: classifiers updated: now lists Python 3.10, 3.11, 3.12, 3.13, 3.14.
- **`flake.nix` Python pin**: `flake.nix` Python pin bumped from 3.12 to 3.13 (a stable middle of the supported range).
- **Docs corrections**: sweep findings absorbed into the new docs: `l` keybinding documented (`docs/keybindings.md`), `[playback] resume_on_launch` documented (`docs/configuration.md`), corrected `lyrics_current = "#ff4e45"` in the theme.toml example (was stale `#2ecc71`), `app/_base.py` added to the architecture file tree, full CLI subcommand reference now lists every `ytm` command (was missing `ytm dislike`, `ytm now`, `ytm doctor`, `ytm config`, etc.).

**Fixes**

- **Theme cache on Python 3.10**: theme cache (`_read_theme_toml_cached`) was silently returning `{}` on Python 3.10 because its function-local `import tomllib` was caught by a broad except clause. The bug was masked on 3.12 (where tomllib is stdlib) but would have shipped a non-functional theme cache to 3.10 users. Caught during the 3.10 verification gate; fixed by moving the import to module-level with a `sys.version_info` shim.

**Internal**

- **Python release watcher**: new monthly workflow `check-python-versions.yml` opens a maintenance issue when CPython releases a new stable major.minor version newer than the CI matrix ceiling. Idempotent: won't reopen if an issue is already open. Defensive regex guard rejects RC/beta strings to avoid bogus issues.
- **CI matrix**: CI matrix shifted from `[3.12, 3.13]` to `[3.10, 3.14]`, testing the supported floor + the latest stable. Same 6 jobs as before (3 OSes × 2 Pythons), better-targeted coverage.
- **Lint job and watcher Python version**: lint job + Python release watcher updated to use Python 3.14 (was 3.12), aligning auxiliary tooling with the test matrix ceiling.
- **Type-check and lint target**: Pyright + ruff configured to type-check and lint against `py310` so accidentally-introduced 3.11+ syntax fails locally and in CI.
- **CONTRIBUTING.md**: `CONTRIBUTING.md` gained a "Python version compatibility" section explaining the `sys.version_info` shim pattern and the `YTMHostBase` mixin attribute typing pattern for new contributors.
- **AUR PKGBUILD**: AUR PKGBUILD maintainer email replaced (was a placeholder).
- **Hero screenshot**: replaced hero screenshot (v4 → v5).
- **PyPI release workflow**: new `publish.yml` workflow automates the PyPI release. Pushing a `vX.Y.Z` tag now builds wheel + sdist, smoke-tests the wheel by installing it into a fresh venv and running `ytm --version`, uploads to PyPI via OIDC trusted publishing (no API tokens stored anywhere), and creates the matching GitHub Release with the CHANGELOG section attached. A manual `workflow_dispatch` with `target=testpypi` is wired for paranoid dry-runs against test.pypi.org. AUR is still updated by hand afterward.
- **Dependabot major bumps**: Dependabot now opens major-version bumps in their own grouped PR (previously suppressed by `update-types: [minor, patch]`). Both `pip` and `github-actions` ecosystems split into `*-minor-patch` (auto-merge candidates) and `*-major` (review carefully), so security-relevant majors no longer require manual intervention to surface.
- **Stale comments**: stale comments cleaned up: `pyproject.toml` Pyright comment now reads as past tense; `services/player.py` Windows note no longer claims a 3.12+ requirement that was never accurate (ucrtbase has been the default since 3.5).
- **Python 3.10 backport shims**: to support Python 3.10 (where several stdlib symbols don't exist), backport shims were added using `sys.version_info >= (3, 11)` checks (which type-checkers narrow correctly).
- **`tomllib` shim**: `tomllib` (3.11+) falls back to `tomli` (PyPI) on 3.10. Files: `config/keymap.py`, `config/settings.py`, `ui/theme.py`, `app/_app.py`, `tests/test_config/test_settings.py`.
- **`typing.Self` shim**: `typing.Self` (3.11+) falls back to `typing_extensions.Self` on 3.10. Files: `config/keymap.py`, `config/settings.py`, `ui/theme.py`.
- **`enum.StrEnum` shim**: `enum.StrEnum` (3.11+) falls back to a `(str, Enum)` polyfill that mirrors stdlib's `auto()` lowercase-name behaviour. Files: `services/queue.py`, `services/player.py`.
- **Conditional dependencies**: `tomli` and `typing_extensions` added as conditional dependencies (`python_version < "3.11"` markers) so 3.11+ users don't pull them.

---

### v1.7.0 (2026-04-27)

A polish release focused on resume-on-launch, lyric metadata cleanup, theming correctness, and a typing overhaul that silences Pyright noise across the mixin-based App.

**New features**

- **Heart toggle on the playback bar**: visible `❤` indicator between the track info and volume, filled in the theme accent when the current track is liked, muted when not. Press `l` (or click) to toggle. Backed by `ytmusicapi.rate_song(LIKE/INDIFFERENT)` so the change syncs to your YouTube Music account in real time. Toast confirms ("Added to Liked songs" / "Removed from Liked songs"); pressing `l` while not signed in surfaces a "Sign in to like songs" warning instead of feeling like a dead key. Thanks to @valkyrieglasc ([#62](https://github.com/peternaame-boop/ytm-player/issues/62)).
- **Last-playing track is remembered across launches**: the track + queue + position are saved on every exit (not just unclean ones). Relaunch ytm-player and the playback bar shows the same track you were on, ready to go. Default-on; opt out with `[playback] resume_on_launch = false` in `config.toml`. Two safety guards: tracks paused for under 1 second are no longer saved (avoids a startup-crash overwriting a perfectly good prior resume), and the pending slot survives if you start playing a different track first.
- **Artist context page now fetches ALL top songs**: `ytmusicapi.get_artist()` returns only ~5 by default with the full list at a separate browseId. ytm-player fetches the first 300 in the background after the page renders, then chains `get_playlist_remaining` for anything beyond. No more silent truncation at 100. Thanks to @dmnmsc ([#55](https://github.com/peternaame-boop/ytm-player/issues/55)).
- **Lyric title sanitisation**: strips a wide set of YouTube-style noise patterns (`(Official Music Video)`, `[Audio]`, `(HD)`, `(feat. Bob)` / `(ft. Bob)` / `(featuring Bob)`, `(Remix)` / `(Extended Remix)` / `(Radio Remix)`, `(Remastered)` / `(Remastered 2009)`, `(Deluxe)` / `(Deluxe Edition)`, `(Live)` / `(Live at Wembley)`, `(Acoustic)` / `(Acoustic Version)`, etc.) and the `Artist - ` prefix before LRCLIB lookup. Handles nested parens correctly (`(feat. Bob (Junior))` → strips cleanly). Improves match rate for tracks played from YouTube proper (where titles are noisy) without affecting clean YouTube Music tracks. Thanks to @valkyrieglasc ([#62](https://github.com/peternaame-boop/ytm-player/issues/62)).

**Changes**

- **Search input auto-focuses on fresh entry**: pressing `g s` from a fresh state focuses the search bar so you can type immediately. Returning to the page with a cached query leaves focus on the results table so you can keep browsing without re-typing.
- **Search Escape now does the right thing**: when the input is focused or the predictive-suggestions dropdown is showing, Escape hides the dropdown and moves focus to the songs results table (or blurs entirely if no results yet). Typing a new query also clears the previous results so a subsequent Escape doesn't strand you on stale rows.
- **Notifications match the active theme**: toast border colours now use `$primary` / `$warning` / `$error` from the theme instead of Textual's hardcoded green default. Notifications no longer stick out on a custom theme.
- **Notifications shift left when the lyrics sidebar is open**: toast rack offsets so notifications don't cover the lyrics.
- **Lyric line colours derive from theme tokens**: current line uses the theme accent (was hardcoded green); upcoming lines are normal foreground (clearly distinct from the dimmed played lines instead of all looking grey-on-grey).
- **Theme accent for Now Playing and active states**: Now Playing header in the queue + repeat/shuffle "active" state in the playback bar use the theme accent (`$primary`) instead of the hardcoded `$success` (green).
- **Allow textual 8.x**: `pyproject.toml` upper bound bumped from `<8.0` to `<9.0` ([#63](https://github.com/peternaame-boop/ytm-player/pull/63)). All tests pass on textual 8.2.4.

**Fixes**

- **Search "Searching..." indicator stuck after cancel**: Search "Searching..." indicator no longer sticks forever when the worker is cancelled. `asyncio.CancelledError` inherits from `BaseException` in Python 3.8+, so the existing `except Exception:` block didn't catch it; the loading-text-clear was outside the try/finally and never ran on cancel. Now an explicit handler clears the indicator before re-raising.
- **Queue footer**: Queue footer no longer duplicates the repeat/shuffle state from the playback bar. Footer now just shows `Tracks: N`. Thanks to @valkyrieglasc ([#62](https://github.com/peternaame-boop/ytm-player/issues/62)).
- **Sidebar play-from-double-click**: sidebar play-from-double-click no longer passes a possibly-`None` track to `play_track`, surfaced when the new typing infrastructure (below) tightened the signature. The `None` case now early-returns gracefully.
- **Lyric-current default colour**: lyric-current default colour is now a single `DEFAULT_LYRIC_CURRENT = "#ff4e45"` constant in `theme.py`, referenced by the dataclass default and both fallback paths in `_app.py`. Previously the three sites disagreed (green vs red), which would have surfaced for users who wrote stripped-down custom themes that defined neither `accent` nor `primary`.

**Internal**

- **Mixin attribute typing**: new `src/ytm_player/app/_base.py` declares `YTMHostBase`, a `TYPE_CHECKING`-only stub class that mirrors `YTMPlayerApp`'s full attribute and cross-mixin method surface. All eight mixins now extend it. At runtime `YTMHostBase = object` (zero behaviour change); under Pyright/Pylance the editor sees a fully typed `App[None]` subclass and stops emitting "Cannot access attribute X for class FooMixin" noise.
- **`PageWidget` Protocol**: a new `PageWidget` Protocol replaces bare `Widget` returns where pages are looked up. `_get_current_page()` now returns `PageWidget | None` so `handle_action` and `get_nav_state` calls type-check correctly without `cast()` at every site.
- **Pyright now finds the project venv**: added `[tool.pyright]` to `pyproject.toml` so editor IDEs (VS Code / Pylance / basedpyright) resolve `textual`, `pytest`, `ytmusicapi` etc. without flooding the Problems panel with false-positive "Import could not be resolved" errors.
- **New test coverage**: lyric title sanitiser (original noise patterns, the new feat/ft/featuring/remix/remastered/deluxe/live/acoustic patterns, nested parens, and negative passthroughs: `Remix Culture`, `Live and Let Die`, `Acoustic Sessions Vol 1` stay untouched); resume-on-launch flow (restore, position-guard boundaries, pending-resume match/non-match); `_toggle_like_current` (LIKE↔INDIFFERENT, DISLIKE→LIKE, no-op-with-notify when not signed in, no-op when no current track).
- **Test `ResourceWarning`s**: cleaned up several pre-existing test `ResourceWarning`s (unclosed file handles in `test_auth_multi_account.py`).

---

### v1.6.0 (2026-04-17)

A polish release focused on diagnostics, stability, security, and performance. No headline user-facing features, but a lot of friction removed from "what do I do when something breaks?"

**New features**

- **`ytm doctor` command**: prints a one-paste diagnostic report (version, Python, mpv, OS, recent log lines, most recent crash trace). Drop the output into a bug report and it has everything needed to triage.
- **File-based logging**: logs now go to `~/.config/ytm-player/logs/ytm.log` (rotated, 5×1MB by default). Previously, log output disappeared into Textual's alt-screen and was unrecoverable. New `[logging]` config section exposes level + rotation knobs. New `--debug` CLI flag enables verbose tracing.
- **Crash file capture**: unhandled exceptions from any thread now write a full traceback to `~/.config/ytm-player/crashes/` so you have something to attach to issues even when the app is dead.
- **Local audio cache now serves replays**: previously the `CacheManager` indexed downloaded tracks but `play_track()` never asked it. Cached files (downloads + replayed tracks under your `[cache] max_size_gb`) now bypass yt-dlp entirely for instant playback with no network round-trip.
- **Update notifications on startup**: a background worker checks PyPI once per 24 hours and surfaces a one-time toast when a newer version is available. Silent on network failure, cache lives at `~/.config/ytm-player/update_check.json`. Opt out by setting `check_for_updates = false` in `[general]`.
- **Session.json schema versioning**: `schema_version` field lets future format changes detect-and-discard incompatible state instead of silently misbehaving.

**Changes**

- **Theme switches feel instant**: `theme.toml` is now mtime-cached in `get_css_variables` instead of being parsed from disk on every CSS variable lookup. Edits to `theme.toml` still pick up automatically (mtime invalidates the cache).
- **Queue page play-indicator update is O(1) instead of O(n)**: switching tracks no longer rewrites every row in queues with hundreds of tracks; only the changed cells are touched.
- **Album art rendering moved off the event loop**: `_image_to_half_blocks` (PIL resize + per-pixel iteration) now runs in `asyncio.to_thread`, so loading a thumbnail never blocks input handling.
- **Library page background workers cancel on page removal**: the "fetch remaining tracks" worker for large libraries no longer keeps running after you've navigated away.

**Fixes**

- **IPC socket permissions**: IPC socket is now created owner-only via `umask(0o077)` around `bind()`, in addition to the existing `chmod 0600`.
- **Player no longer races on track changes**: `_current_track` writes during `play()` and `stop()` are now under `_skip_lock`, preventing torn reads from MPRIS, Discord, Last.fm, and the end-of-track callback during rapid skips (C1).
- **mpv crash recovery no longer silently breaks subsequent playback**: when `_play_sync` catches `mpv.ShutdownError` and `_try_recover` succeeds, `_current_track` and the `TRACK_CHANGE` event payload now stay consistent. (Two issues here: an initial fix went too far and cleared `_current_track` mid-`play()`, breaking MPRIS/Discord/Last.fm and stopping auto-advance after recovery; caught in final review and reverted.)
- **`ytmusic.get_playlist(order=...)` is now safe under concurrent calls**: the function monkey-patches ytmusicapi's internal `_send_request` to inject the `order` parameter, but two concurrent calls would corrupt the patch state. Now serialised with an `asyncio.Lock` (C3).
- **`config.toml` and session.json writes are now atomic**: uses `os.replace` after writing to a `.tmp` sibling, so power loss / `kill -9` mid-write can no longer leave you with a half-written file that crashes startup.
- **Session.json corruption no longer crashes startup**: bad JSON or schema mismatch falls back to defaults silently. First launch (file absent) stays silent; format mismatch on existing files logs a warning.
- **Play failures no longer block retry within 1 second**: the double-click debounce stamp now clears on stream-resolution failure or `player.play()` exception, so clicking the same track again after a failure isn't silently swallowed.
- **Update check with a fast system clock**: update check no longer pins users to a stale "latest" if the system clock ever ran fast. Negative cache age now triggers a re-fetch.
- **Bare `except:` swallows promoted to `logger.exception`**: in high-impact spots (player callbacks, ytmusic API failures, MPRIS/Discord/Last.fm errors, history logging), failures that previously vanished now show up in `~/.config/ytm-player/logs/ytm.log` with a stack trace.

**Internal**

- **Multi-OS CI**: workflow now runs on Ubuntu / macOS / Windows × Python 3.12 / 3.13 (was Ubuntu / 3.12 only). Pip cache enabled. `dev` branch now triggers CI too.
- **Dependabot**: Dependabot configured for weekly grouped pip + GitHub Actions updates.
- **Issue templates (bug + feature)**: bug template requires `ytm doctor` output; feature template asks contributors to bundle related ideas.
- **Community files**: PR template, CODEOWNERS, SECURITY.md (private vulnerability reporting), CONTRIBUTING.md (dev setup, ruff order, logging conventions, RTL guard, PR norms).
- **Git attributes and ignores**: `.gitattributes` enforces LF line endings; `.gitignore` expanded for IDE/OS junk, `result` (Nix), `*.log`, etc.
- **Repo metadata**: description, homepage, topics (`tui`, `textual`, `music-player`, `youtube-music`, `terminal`, `mpv`, `python`, `vim-keybindings`, `cli`), GitHub Discussions enabled.
- **New test coverage**: new coverage areas: logging + excepthook setup, doctor diagnostics, cache contract, session restore resilience, schema versioning, update check + clock skew, IPC dispatch + seek parsing, key normalisation, navigation back-stack invariants, playback debounce + cache-hit path, atomic writes, settings load.
- **Help-page coverage enforcement test**: fails CI if any `Action` enum member is missing from `ACTION_DESCRIPTIONS` or `ACTION_CATEGORIES` in `ui/pages/help.py`. Catches the "added a new keybind, forgot to document it" drift forever.

---

### v1.5.9 (2026-04-16)

**Fixes**

- **`gc` (jump to current track) crashing on Queue and Liked Songs**: `scroll_to_cursor()` doesn't exist on Textual's `DataTable`. Removed the bogus calls; `move_cursor()` already scrolls by default. Thanks to @dmnmsc ([#52](https://github.com/peternaame-boop/ytm-player/issues/52)).
- **Queue "Now Playing" header not updating on track change**: `call_from_thread()` raises `RuntimeError` when called from the same thread as the app, and the bare `except` was silently swallowing it. Player events are already on the main thread, so the update method is now called directly. Thanks to @dmnmsc ([#56](https://github.com/peternaame-boop/ytm-player/issues/56)).

---

### v1.5.8 (2026-04-16)

**New features**

- **Track filter (`/`) extended to Queue and Liked Songs pages**: search by title or artist, debounced, queue/reorder/delete operations correctly map filtered indices to real positions. Thanks to @dmnmsc ([#48](https://github.com/peternaame-boop/ytm-player/issues/48)).
- **Liked Songs loading status**: footer now shows "loading more…" while background fetch runs for libraries beyond 300 tracks. Thanks to @dmnmsc ([#51](https://github.com/peternaame-boop/ytm-player/issues/51)).

**Fixes**

- **RTL text bleed across visual boundaries**: RTL track titles were appearing duplicated at row edges and bleeding into the playback bar's volume/repeat/shuffle area. All user text fragments are now wrapped with Unicode FSI/PDI isolation marks. Includes a regression test that mechanically prevents the bug from reappearing.
- **App crash on artist→album→album navigation**: `DuplicateIds` race when navigating between two ContextPages with the same widget ID. Each ContextPage instance now uses a unique sequence-based ID. Thanks to @dmnmsc ([#47](https://github.com/peternaame-boop/ytm-player/issues/47)).
- **`g c` (jump to current track) doing nothing**: action was wired to the keymap but no page implemented it. Now works on Library, Context, Browse, Search, Queue, Liked Songs, and Recently Played. Thanks to @dmnmsc ([#49](https://github.com/peternaame-boop/ytm-player/issues/49)).
- **`g space` (current context) crashing**: was navigating to ContextPage without required `context_type`/`context_id`. Now extracts album info from the currently playing track, or shows a notification if unavailable. Thanks to @dmnmsc ([#50](https://github.com/peternaame-boop/ytm-player/issues/50)).

---

### v1.5.7 (2026-04-15)

**New features**

- **Track filter on Library and Context pages**: press `/` to filter tracks by title, artist, or album in real-time. Enter keeps filtered view, Escape clears it. Queue integration preserved. Thanks to @dmnmsc ([#43](https://github.com/peternaame-boop/ytm-player/issues/43)) and @valkyrieglasc ([#46](https://github.com/peternaame-boop/ytm-player/issues/46)).
- **Optimistic sidebar updates**: creating or deleting a playlist updates the sidebar instantly without an API round-trip or delay. Thanks to @Villoh ([#41](https://github.com/peternaame-boop/ytm-player/pull/41)).

**Fixes**

- **App crash when opening album from artist page**: `DuplicateIds` caused by uncancelled background workers on page navigation. Workers now cancelled on page removal. Thanks to @dmnmsc ([#44](https://github.com/peternaame-boop/ytm-player/issues/44)).
- **Like/unlike toggle always showing "Like"**: `likeStatus` was stripped during track normalisation and not updated after rating. Now preserved and updated in real-time. Thanks to @dmnmsc ([#45](https://github.com/peternaame-boop/ytm-player/issues/45)).
- **`album_art` and `progress_style` config options**: `album_art = false` and `progress_style = "line"` config options are no longer ignored. Thanks to @valkyrieglasc ([#42](https://github.com/peternaame-boop/ytm-player/issues/42)).
- **`theme.toml` base colour overrides not applying**: `theme.toml` base colour overrides (background, primary, etc.) were not applying after Textual theme integration. User customisations now override the active theme ([#42](https://github.com/peternaame-boop/ytm-player/issues/42)).

---

### v1.5.6 (2026-04-10)

**New features**

- **Bouncing playlist names**: long playlist titles in the sidebar now bounce (scroll back and forth) when highlighted, so you can read the full name. Thanks to @dmnmsc ([#32](https://github.com/peternaame-boop/ytm-player/issues/32)).

**Fixes**

- **"Add to Queue" / "Play Next" doing nothing**: popup dismissal was triggering a spurious track selection that cleared the queue immediately after adding. Now suppressed with a refocus guard. Thanks to @dmnmsc ([#30](https://github.com/peternaame-boop/ytm-player/issues/30)).
- **Sidebar "Add to Queue" only showing a notification**: sidebar "Add to Queue" was only showing a notification without actually adding tracks. It now fetches the playlist and queues all tracks ([#30](https://github.com/peternaame-boop/ytm-player/issues/30)).
- **Theme colours not updating in header bar when switching themes**: migrated toggle labels from Rich Text to CSS classes. Thanks to @Villoh ([#37](https://github.com/peternaame-boop/ytm-player/issues/37)).
- **Progress bar colours not updating on theme switch**: colours now read at render time instead of construction time. Thanks to @Villoh ([#39](https://github.com/peternaame-boop/ytm-player/issues/39)).
- **Hover backgrounds making text invisible**: all hover states now use `$accent 30%` instead of `$border`.
- **500-track limit on session queue restore**: removed the 500-track limit on session queue restore. Full queue now persists regardless of size. Thanks to @dmnmsc ([#31](https://github.com/peternaame-boop/ytm-player/issues/31)).

---

### v1.5.5 (2026-04-09)

**New features**

- **Textual native theme support**: all 18 built-in themes (nord, dracula, gruvbox, catppuccin, etc.) work via `Ctrl+P` → "Change theme". Theme selection persists across sessions. Custom ytm-dark theme registered as default. Thanks to @dsafxP ([#23](https://github.com/peternaame-boop/ytm-player/issues/23)).
- **CLI like/dislike/unlike commands**: `ytm like`, `ytm dislike`, `ytm unlike` to rate the current track via IPC. Thanks to @moschi ([#26](https://github.com/peternaame-boop/ytm-player/pull/26)).
- **Brand Account support**: set `brand_account_id` in `config.toml` under `[general]` to use a YouTube Brand Account. Thanks to @nitsujri ([#25](https://github.com/peternaame-boop/ytm-player/issues/25)).
- **Toggle album art**: `Ctrl+A` hides/shows album art in the playback bar. Thanks to @valkyrieglasc ([#28](https://github.com/peternaame-boop/ytm-player/issues/28)).

**Fixes**

- **Large playlists (1500+) failing to load**: progressive loading fetches first 300 tracks immediately, then loads remaining in background with extended timeout. Applies to playlists, liked songs, and sidebar play-all. Thanks to @Jxshua17, @dmnmsc and @nitsujri ([#24](https://github.com/peternaame-boop/ytm-player/issues/24)) and @valkyrieglasc ([#27](https://github.com/peternaame-boop/ytm-player/issues/27)).

---

### v1.5.2 (2026-03-17)

**Fixes**

- **RTL lyrics displaying in wrong word order**: disabled manual RTL reordering which was reversing text on both BiDi and non-BiDi terminals. Added `bidi_mode` config option (`auto`/`reorder`/`passthrough`) for users who need explicit control.
- **Lyrics sidebar ignoring custom theme colours**: lyrics sidebar was ignoring custom `lyrics_played`/`lyrics_current`/`lyrics_upcoming` theme colours. CSS was wired to wrong variables (`$success`, `$text-muted`, `$text` instead of `$lyrics-*`).
- **Hard-coded colours in album art placeholder and context page cursor**: album art placeholder and context page cursor were using hard-coded colours instead of theme. All UI colours now flow through `ThemeColors` for full theme customisation support.

---

### v1.5.1 (2026-03-12)

**New features**

- **Multi-account auth support**: `ytm setup` now handles Google accounts logged into multiple YouTube Music profiles, probing all `x-goog-authuser` indices automatically. Thanks to @glywil ([#15](https://github.com/peternaame-boop/ytm-player/pull/15)).
- **Gentoo packaging**: available in the GURU overlay via `emerge media-sound/ytm-player`. Thanks to @dsafxP ([#21](https://github.com/peternaame-boop/ytm-player/pull/21)).

**Fixes**

- **`nix build` failing**: added missing `pillow` to core Nix deps, resolved `python-mpv` vs `mpv` dist-info name mismatch with `pythonRemoveDeps`, added `transliteration` optional dep. Thanks to @muhmud ([#18](https://github.com/peternaame-boop/ytm-player/issues/18)).
- **Browse page showing "Unknown" artist in notifications**: raw API items now normalised before playback. Thanks to @Gimar250 ([#19](https://github.com/peternaame-boop/ytm-player/issues/19), [#20](https://github.com/peternaame-boop/ytm-player/pull/20)).
- **`d d` / `delete` keybind not removing tracks on the Queue page**: was matching `TRACK_ACTIONS` instead of `DELETE_ITEM`. Thanks to @CarterSnich ([#22](https://github.com/peternaame-boop/ytm-player/issues/22)).

---

### v1.5.0 (2026-03-09)

**New features**

- **Lyrics transliteration**: toggle ASCII transliteration of non-Latin lyrics with `T` (Shift+T), useful for Japanese, Korean, Arabic, Cyrillic, etc. Requires optional `anyascii` package. Thanks to @Kineforce ([#14](https://github.com/peternaame-boop/ytm-player/issues/14)).
- **Add to Library button**: albums and playlists that aren't in your library now show a clickable `[+ Add to Library]` button on their context page.
- **Delete/remove playlist confirmation**: deleting a playlist now asks for confirmation first; also supports removing non-owned playlists from your library.
- **Page state preservation**: Search, Browse, Liked Songs, and Recently Played pages now remember their state (query, results, cursor position, active tab) when navigating away and back.

**Changes**

- **Search mode toggle is now clickable**: click the `Music`/`All` label to toggle (was keyboard-only before).
- **Transliteration toggle state**: transliteration toggle state now persists across restarts via session.json.
- **Sidebar refresh**: sidebar refreshes after adding or removing playlists from library.

**Fixes**

- **RTL text word order**: restored BiDi reordering for RTL track titles, artists, and lyrics (UAX #9 algorithm).
- **Right-click targeting wrong track**: right-click now opens actions for the row under the cursor, not the previously highlighted row. Thanks to @glywil ([#16](https://github.com/peternaame-boop/ytm-player/pull/16)).
- **Artist search results**: artist names now appear instead of "Unknown".
- **Radio tracks crashing playback**: radio API responses are now normalised before adding to queue.
- **Browse page items not opening**: capitalised `resultType` values and missing routing for radio/mix entries.
- **Session restore crash**: session restore no longer crashes when saved tracks become unavailable (deleted/region-locked videos).
- **Actions popup crash**: actions popup no longer crashes when album field is a plain string instead of dict. Thanks to @glywil ([#16](https://github.com/peternaame-boop/ytm-player/pull/16)).
- **Double-click playing a track twice**: fixed with a 1-second debounce.
- **Back navigation**: back navigation no longer ping-pongs between two pages.
- **Lyrics sidebar performance**: batch-mount widgets instead of mounting individually.
- **Transliteration toggle highlight flash**: forces immediate lyrics re-sync after toggle.

**Internal**

- **`app.py` split into mixins**: decomposed `app.py` into a package with 8 focused mixins: playback, navigation, keys, session, sidebar, track actions, MPRIS, IPC. Zero behavioural changes; all tests pass unchanged.

---

### v1.4.0 (2026-03-07)

**New features**

- **Native macOS media key and Now Playing support**: hardware media keys (play/pause, next, previous) now work via Quartz event taps, and track metadata appears in macOS Control Center. Thanks to @Thayrov ([#12](https://github.com/peternaame-boop/ytm-player/pull/12)).

**Changes**

- **Optional features for AUR users**: documented how to install optional features for AUR users, as pip doesn't work on Arch due to PEP 668 ([#13](https://github.com/peternaame-boop/ytm-player/issues/13)).

---

### v1.3.6 (2026-03-05)

**Changes**

- **Service init error messages**: improved error messages for service init failures.

**Fixes**

- **mpv crash inside Textual TUI on Windows**: locale was being set via the legacy `msvcrt.dll` CRT, but Python 3.12+ uses `ucrtbase.dll`, so the `setlocale(LC_NUMERIC, "C")` call had no effect and mpv refused to initialise (access violation on null handle).
- **mpv DLL not found on Windows when installed via scoop/chocolatey**: auto-locates `libmpv-2.dll` in common install directories.

---

### v1.3.4 (2026-03-05)

**New features**

- **TCP localhost IPC for Windows**: added TCP localhost IPC for Windows (Unix sockets unavailable), with proper stale port cleanup.
- **Clipboard support for Windows and macOS**: added clipboard support for Windows (`Set-Clipboard`) and macOS (`pbcopy`).

**Changes**

- **Windows config and cache locations**: config now stored in `%APPDATA%\ytm-player`, cache in `%LOCALAPPDATA%\ytm-player`.
- **Corrupted config files**: corrupted config files are backed up to `.toml.bak` before recreating defaults.

**Fixes**

- **Crash on Windows caused by config file encoding**: fixed crash on Windows caused by config file encoding (em-dash written as cp1252 instead of UTF-8).
- **PID liveness check on Windows**: fixed PID liveness check on Windows using `OpenProcess` API.
- **Crash log path, libc detection and `ytm config` on Windows**: fixed crash log path, libc detection (`msvcrt`), and `ytm config` command for Windows.
- **File I/O encoding**: added `encoding="utf-8"` to all file I/O (Windows defaults to cp1252).

---

### v1.3.3 (2026-03-05)

**Fixes**

- **Media key listener on macOS**: disabled media key listener on macOS. pynput can't intercept keys, causing previous track to open iTunes.
- **Noisy warnings on macOS startup**: suppressed noisy warnings on macOS startup ("dbus-next not installed", "process not trusted").

---

### v1.3.1 (2026-03-05)

**New features**

- **Cross-platform media key support**: play/pause, next, and previous media keys now work on macOS and Windows via `pynput` (Linux already supported via MPRIS).

**Changes**

- **Pillow (album art) is now a default dependency**: no longer requires `pip install ytm-player[images]`.

---

### v1.3.0 (2026-03-05)

**New features**

- **`ytm setup --manual`**: skip browser detection, paste request headers directly. Thanks to @uhs-robert ([#10](https://github.com/peternaame-boop/ytm-player/issues/10)).
- **`ytm setup --browser <name>`**: extract cookies from a specific browser (chrome, firefox, brave, etc.).
- **NixOS packaging**: `flake.nix` with `ytm-player` and `ytm-player-full` packages, dev shell, and overlay.
- **Free-tier support**: tracks without a video ID (Premium-only) are now filtered from playlists/albums/search with an "unavailable tracks hidden" notice, instead of silently failing on click.

**Changes**

- **Auth validation rewrite**: rewrote auth validation to use `get_account_info()` instead of monkey-patching. More reliable across platforms and ytmusicapi versions.
- **Unplayable tracks**: unplayable tracks (no video ID) now auto-skip to the next track instead of stopping playback dead.

**Fixes**

- **Theme variables `$surface` and `$text` now properly defined**: fixes unstyled popups, sidebars, and scrollbars. Thanks to @ahloiscreamo ([#6](https://github.com/peternaame-boop/ytm-player/issues/6)).
- **MPRIS crash (`SignatureBodyMismatchError`)**: fixed MPRIS crash (`SignatureBodyMismatchError`) when track metadata contains None values. Thanks to @markvincze ([#9](https://github.com/peternaame-boop/ytm-player/issues/9)).
- **Large playlists only loading 200-300 songs**: now fetches all tracks via ytmusicapi pagination. Thanks to @bananarne ([#5](https://github.com/peternaame-boop/ytm-player/issues/5)).
- **Search results missing `video_id`**: songs from search couldn't play. Thanks to @firedev ([#4](https://github.com/peternaame-boop/ytm-player/pull/4)).
- **Browse/charts page normalisation**: fixed the same missing normalisation bug on the browse/charts page.
- **macOS `Player` init crash**: hardcoded `libc.so.6` replaced with platform-aware detection. Thanks to @hanandewa5 ([#2](https://github.com/peternaame-boop/ytm-player/pull/2)).
- **Auth validation crashing with raw tracebacks on network errors**: now shows friendly message with recovery suggestion. Thanks to @CarterSnich ([#7](https://github.com/peternaame-boop/ytm-player/issues/7)) and @Tohbuu ([#11](https://github.com/peternaame-boop/ytm-player/issues/11)).

---

### v1.2.11 (2026-03-03)

**New features**

- **yt-dlp configuration support**: `cookies.txt` auth, `remote_components`, `js_runtimes` via `[yt_dlp]` config section. Thanks to @gitiy1 ([#1](https://github.com/peternaame-boop/ytm-player/pull/1)).

---

### v1.2.10 (2026-03-03)

**Fixes**

- **RTL text in track table columns**: added BiDi isolation (LRI/PDI) so RTL album/artist names don't bleed into adjacent columns.

---

### v1.2.9 (2026-03-02)

**Changes**

- **Published to PyPI**: install with `pip install ytm-player` or `pipx install ytm-player`.

**Fixes**

- **Track auto-advance stopping after song ends**: three root causes: mpv end-file reason read from wrong event object, event loop reference permanently lost under thread race condition, and `CancelledError` not caught in track-end handler.
- **RTL text display**: removed manual word-reordering that double-reversed text on terminals with native BiDi support; added Unicode directional isolation to prevent RTL titles from displacing playback bar controls.
- **Shuffle state and queue index**: fixed shuffle state corrupting queue after clear, and `jump_to()` desyncing the current index when shuffle is on.
- **Column resize**: fixed column resize triggering sort, and Title column not staying at user-set width.

---

### v1.2.4 (2026-02-17)

**Fixes**

- **Intermittent playback stopping mid-queue**: consecutive stream failures (stale yt-dlp session, network hiccup) now reset the stream resolver automatically, preventing the queue index from advancing past all remaining tracks.
- **Playlists appearing empty after prolonged use**: YTMusic API client now auto-reinitialises after 3 consecutive failures (handles expired sessions/cookies).
- **Misleading "Queue is empty" message**: when queue has tracks but playback index reached the end, it now says "End of queue".

---

### v1.2.3 (2026-02-17)

**Fixes**

- **MPRIS silently disabled on Python 3.14**: `from __future__ import annotations` caused dbus-next to reject `-> None` return types, disabling media keys and desktop player widgets.
- **RTL lyrics line-wrap reading bottom-to-top**: long lines are now pre-wrapped in logical order before reordering, so sentence start is on top.

---

### v1.2.2 (2026-02-15)

**New features**

- **Right-click on playback bar**: right-click on playback bar (album art or track info) now opens the track actions popup, matching right-click behaviour on track tables.

**Fixes**

- **Play/pause doing nothing after session restore**: player had no stream loaded so toggling pause was a no-op; now starts playback from the restored queue position.
- **MPRIS play/pause after session restore**: MPRIS play/pause was also a no-op after session restore (same root cause).
- **RTL lyrics displaying in wrong order**: segment-level reordering now renders bidirectional text correctly.
- **Lyrics sidebar crash from dict-style access on LyricLine objects**: switched to attribute access.
- **Lyrics sidebar reload**: lyrics sidebar no longer reloads unnecessarily when reopened for the same track.

---

### v1.2.1 (2026-02-14)

**New features**

- **Synced (timestamped) lyrics**: lyrics highlight and auto-scroll with the song in real time.
- **Click-to-seek on lyrics**: click any synced lyric line to jump to that part of the song.
- **LRCLIB.net fallback**: when YouTube Music doesn't provide synced lyrics, fetches them from LRCLIB.net (no API key needed).
- **Lyrics auto-centre**: current lyric line stays centred in the viewport as the song plays.

**Changes**

- **Lyrics sidebar on launch**: lyrics sidebar always starts closed on launch regardless of previous session state.

**Fixes**

- **Crash on song change with both sidebars open**: Textual's `LoadingIndicator` timer raced with widget pruning during track transitions.
- **Crash from unhandled exceptions in player event callbacks**: sync callbacks dispatched via `call_soon_threadsafe` now wrapped in error handlers.
- **Crashes during app transitions**: wrapped `notify()` and `_prefetch_next_track()` in `_on_track_change` with try/except to prevent crashes during app transitions.
- **Synced lyrics not being requested**: `timestamps=True` now passed to ytmusicapi with automatic fallback to plain text.

---

### v1.2.0 (2026-02-14)

**New features**

- **Persistent playlist sidebar (left)**: visible across all views, toggleable per-view with state memory (`Ctrl+e`).
- **Persistent lyrics sidebar (right)**: synced lyrics with auto-scroll, replaces the old full-page Lyrics view (`l` to toggle).
- **Header bar**: header bar with toggle buttons for both sidebars.
- **Pinned navigation items**: pinned navigation items (Liked Songs, Recently Played) in the playlist sidebar.
- **Per-view sidebar state**: sidebar visibility is remembered per page and restored on navigation.

**Changes**

- **Lyrics sidebar performance**: lyrics sidebar registers player events lazily and skips updates when hidden for performance.
- **Lyrics page removed**: replaced entirely by the lyrics sidebar.
- **Lyrics button removed from footer bar**: use header bar toggle or `l` key instead.

---

### v1.1.3 (2026-02-14)

**New features**

- **Click column headers to sort**: click any column header (Title, Artist, Album, Duration, #) to sort; click again to reverse.
- **Drag-to-resize columns**: drag column header borders to adjust widths; Title column auto-fills remaining space.
- **Playlist sort order**: requests "recently added" order from YouTube Music API when loading playlists.
- **`#` column**: `#` column preserves original playlist position and can be clicked to reset sort order.

**Changes**

- **Textual dependency pin**: pinned Textual dependency to `>=7.0,<8.0` to protect against internal API breakage.

**Fixes**

- **Click-to-sort not working**: fixed click-to-sort not working (ColumnKey.value vs str(ColumnKey) mismatch).
- **Horizontal scroll position**: horizontal scroll position no longer resets when sorting.
- **Session restore with shuffle**: queue is now populated before enabling shuffle so the saved index points at the correct track.
- **`jump_to_real()` fallback**: fixed `jump_to_real()` fallback when track not in shuffle order (was a silent no-op, now inserts into shuffle order).
- **Crash on Python 3.14**: fixed crash on Python 3.14 from dbus-next annotation parsing (MPRIS gracefully disables).

---

### v1.1.2 (2026-02-14)

**New features**

- **Shuffle-aware playlist playback**: double-clicking a playlist with shuffle on now starts from a random track instead of always the first.
- **Table sorting**: sort any track list by Title (`s t`), Artist (`s a`), Album (`s A`), Duration (`s d`), or reverse (`s r`).
- **Session resume**: on startup, restores last queue position and shows the track in the footer (without auto-playing).
- **Quit action (`q` / `Ctrl+Q`)**: clean exit that clears resume state; unclean exits (terminal close/kill) preserve it.

**Fixes**

- **Queue position desync with shuffle**: fixed queue position desync when selecting tracks with shuffle enabled (all pages: Library, Context, Liked Songs, Recently Played).
- **Search mode toggle showing empty box**: fixed search mode toggle showing empty box due to Rich markup interpretation (`[Music]` → `Music`).

---

### v1.1.1 (2026-02-13)

**New features**

- **Lyrics button in footer bar**: always-visible Lyrics button in footer bar (dimmed when no track playing, active during playback).
- **Footer page click**: clicking the active footer page navigates back to the previous page.
- **Library playlist memory**: Library remembers selected playlist when navigating away and back.
- **Click outside popups to dismiss**: actions menu and Spotify import close when clicking the background.

**Fixes**

- **Right-click on track table**: fixed right-click on track table triggering playback instead of only opening context menu.
- **Auto-advance bug**: songs after the 2nd track would not play due to stale `_end_file_skip` counter.
- **Thread-safe skip counter**: check+increment now atomic under lock.
- **Duplicate end-file events**: fixed duplicate end-file events causing track skipping (debounce guard).
- **Stale `_current_track` state**: fixed `player.play()` failure leaving stale `_current_track` state.
- **Stream resolution exceptions**: fixed unhandled exceptions in stream resolution crashing the playback chain.
- **`player.play()` exceptions**: fixed `player.play()` exceptions silently stopping all playback.
- **Browse page crash**: fixed Browse page crash from unawaited async mount operations.
- **API error tracebacks**: fixed API error tracebacks polluting TUI with red stderr overlay.
- **Skip counter after mpv crash**: reset skip counter on mpv crash recovery.
- **Terminal image protocol detection**: fixed terminal image protocol detection (`TERM_FEATURES` returning wrong protocol).

**Internal**

- **Encapsulation break**: fixed encapsulation break (cache private method called from app).

---

### v1.1.0 (2026-02-12)

**New features**

- **Liked Songs page (`g y`)**: browse and play your liked music.
- **Recently Played page (`g r`)**: local history from SQLite.
- **Download for offline**: right-click any track → "Download for Offline".
- **Discord Rich Presence**: show what you're listening to (optional, `pip install -e ".[discord]"`).
- **Last.fm scrobbling**: automatic scrobbling + Now Playing (optional, `pip install -e ".[lastfm]"`).
- **Gapless playback**: gapless playback enabled by default.
- **Queue persistence**: queue persistence across restarts (saved in session.json).
- **Track change notifications**: track change notifications wired to `[notifications]` config section.
- **New config sections**: `[discord]`, `[lastfm]`, `[playback].gapless`, `[playback].api_timeout`.
- **Configurable column widths**: configurable column widths via `[ui]` settings (`col_index`, `col_title`, `col_artist`, `col_album`, `col_duration`).
- **Pinned sidebar entries**: Liked Songs and Recently Played pinned in library sidebar.

**Changes**

- **IPC socket security**: IPC socket security hardening (permissions, command whitelist, input validation).
- **File permissions**: file permissions hardened to 0o600 across all config/state files.
- **Queue manager thread safety**: thread safety for queue manager (prevents race conditions).
- **mpv crash recovery**: mpv crash detection and automatic recovery.
- **Auth validation**: auth validation distinguishes network errors from invalid credentials.
- **Disk-full handling**: disk-full (OSError) handling in cache and history managers.
- **API timeouts**: API timeout handling (15s default, prevents TUI hangs on slow networks).
- **Cache eviction**: batch DELETE for cache eviction (replaces per-row deletes).
- **Cache-hit commits**: deferred cache-hit commits (every 10 hits instead of every hit).
- **yt-dlp instance reuse**: reuse yt-dlp instance across stream resolves (was creating new per call).
- **Spotify import matching**: concurrent Spotify import matching with ThreadPoolExecutor.
- **Stream URL expiry**: stream URL expiry checks before playback.

**Internal**

- **CI pipeline**: GitHub Actions CI pipeline (ruff lint + pytest with coverage).
- **Tests**: tests covering queue, IPC, stream resolver, cache, history, auth, downloads, Discord RPC, Last.fm, and settings.

---

### v1.0.0 (2026-02-07)

Initial release.

**New features**

- **TUI**: full TUI with 7 pages (Library, Search, Browse, Context, Lyrics, Queue, Help).
- **Keybindings**: vim-style keybindings with multi-key sequences and count prefixes.
- **Audio playback**: audio playback via mpv with shuffle, repeat, queue management.
- **Search**: predictive search with music-first mode.
- **Spotify import**: Spotify playlist import (API + scraper).
- **History**: play and search history in SQLite.
- **Audio cache**: audio cache with LRU eviction (1GB default).
- **Album art**: album art with coloured half-block rendering.
- **MPRIS**: MPRIS D-Bus integration for media key support.
- **IPC**: Unix socket IPC for CLI↔TUI control.
- **CLI**: CLI subcommands for headless usage.
- **Configuration**: TOML configuration for settings, keybindings, and theme.
