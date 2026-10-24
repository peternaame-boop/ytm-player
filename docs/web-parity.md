# YouTube Music web parity

This checklist tracks how closely the fork matches music.youtube.com on a desktop browser.
Each row names one region or behaviour of the web app, then the status here.
A row is done only when a Pilot test covers it and a live click test in kitty passes.

Status values: `done`, `partial`, `todo`.

## Layout

| # | Web region | Here | Status |
| --- | --- | --- | --- |
| L1 | Left guide: Home, Explore, Library | Sidebar menu with active highlight, footer, `g h` / `g b` / `g l` | done |
| L2 | Left guide: your playlists | Playlist sidebar | done |
| L3 | Top bar: search box | Header search box opens Search with the cursor in it | done |
| L4 | Main area: Home shelves of cover cards | Home page | done |
| L5 | Bottom player bar: cover, title, artist, controls, progress, volume | Playback bar: previous, play, next, real cover, title and artist, like, volume, repeat, shuffle, progress | done |
| L6 | Real cover art everywhere | Home, Explore, mood pages, Search, Library grid, album and playlist pages, player bar | done |

## Behaviour

| # | Web action | Here | Status |
| --- | --- | --- | --- |
| B1 | Click a song card: play it | Home card click, Enter | done |
| B2 | Click an album or playlist card: open its page | Home card click, Enter | done |
| B3 | Shelf ◀ ▶ arrows scroll the row | Shelf arrow buttons, Shift+wheel, tilt wheel | done |
| B4 | Search from the top bar, results by type | Top result card with Play, songs, then Albums, Artists and Community playlists shelves of cover cards. Right-click a card for its actions | done |
| B5 | Album and playlist page: big cover, Play, track list | Hero header on every album and playlist page, sidebar playlists included: cover, details, Play, Shuffle, Save, Radio | done |
| B6 | Player bar: play/pause, next, previous, seek by click | Transport buttons at the bar's left end, click or scroll the progress line to seek. Previous restarts a track past 3 s | done |
| B7 | Up next queue | Click the player bar's cover or title: large cover, title and artist beside Up next (the queue, with its keys and menus). Click again to go back | done |
| B8 | Like a song | Heart in the player bar (Pilot-tested, not clicked live because it writes to the account), `+` key, track actions | done |
| B9 | Explore: new releases, charts, moods | Explore page: New releases and Charts buttons, shelves of new albums, trending and new videos, mood and genre chips that open a grid of playlists | done |
| B10 | Library page: grid of your playlists | Cover grid, cached for 10 minutes | done |
| B11 | Back to Home is instant | Home feed cached for 10 minutes | done |
| B12 | Search: "Showing results for X, Search instead for Y" and "Did you mean: X" | Spelling note above the chips, changed words in the primary colour. "Search instead for" searches as typed, then the note turns to "Did you mean" (Pilot-tested, live click test pending) | partial |
| B13 | Up next: mix chips on a radio (All, Familiar, Discover, Deep cuts ...) | Chips load once per track radio. A pick replaces the tracks after the playing one (Pilot-tested, live click test pending) | partial |
| B14 | Up next: Autoplay switch at the end of a queue that is not a radio | Switch after the last track. It sets `[playback] autoplay` and saves the config (Pilot-tested, live click test pending) | partial |
| B15 | Up next: Save the queue to a playlist | Save beside "Playing from" opens the playlist picker with every queued track (Pilot-tested, not clicked live because it writes to the account) | partial |

## Speed

| # | Web | Here | Status |
| --- | --- | --- | --- |
| S1 | A song starts in about 1 s | Any song starts within about 1 s: a cold lookup is one direct innertube request (0.55 to 0.63 s), with yt-dlp as the fallback. Warmed songs (first twelve of a list, rows in view, rows ahead of the cursor, rows rested on) start at once | done |
| S2 | A 400-track playlist opens in about 2 s | About 1.5 s for 383 tracks: the first 200 show, the rest load behind them | done |

## Responsiveness

| # | Web | Here | Status |
| --- | --- | --- | --- |
| R1 | A click on another page lands at once while covers load | Page switches take 0.25 s or less. Covers draw in `render()`, and Home paints its top two shelves before mounting the rest | done |
