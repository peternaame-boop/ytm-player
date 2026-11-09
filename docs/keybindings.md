# Keybindings

## Keyboard

| Key | Action |
|-----|--------|
| `space` | Play/Pause |
| `n` | Next track |
| `p` | Previous track |
| `l` | Toggle like on currently playing track |
| `.` | Play a random track from the queue |
| `+` / `-` | Volume up/down |
| `_` | Toggle mute |
| `>` / `<` | Seek forward / back by `[playback] seek_step` seconds |
| `^` | Seek to the start of the track |
| `j` / `k` | Move down/up (also `down` / `up`; `Ctrl+n` also moves down) |
| `Ctrl+f` / `Ctrl+b` | Scroll down / up one line, or 10 lines in the lyrics sidebar (also `page_down` / `page_up`) |
| `g g` / `G` | Go to the top / bottom of the list (also `home` / `end`) |
| `enter` | Select / play |
| `g l` | Go to Library |
| `g s` | Go to Search |
| `g b` | Go to Browse |
| `g y` | Go to Liked Songs |
| `g r` | Go to Recently Played |
| `z` | Go to Queue |
| `g space` | Go to the album of the playing track |
| `g c` | Move the cursor to the playing track in the current list |
| `g L` | Toggle lyrics sidebar |
| `Ctrl+e` | Toggle playlist sidebar |
| `Ctrl+a` | Toggle album art in playback bar |
| `Ctrl+p` | Change theme |
| `?` / `Ctrl+h` | Help (full keybinding reference inside the app). `Ctrl+h` needs a terminal that sends extended key input; elsewhere it arrives as `backspace` (Go back) |
| `Ctrl+w h` | Focus the Playlists sidebar (auto-shows it if hidden) |
| `Ctrl+w l` | Focus the main content, then the lyrics sidebar if it's open |
| `Ctrl+w w` | Cycle focus through the visible panes |
| `tab` / `Shift+tab` | Focus next / previous section — track table, result panel, Browse tab label, or any visible sidebar (then `j`/`k` move within it, `enter` activates). On Browse, `tab` highlights a tab label and `enter` opens it. |
| `J` / `K` | Queue: move the selected track down / up in queue order (count-aware, e.g. `15 J` moves down 15). Clears an active sort so the move is visible; a filter stays, and hidden rows still count as positions |
| `a` | Track actions menu |
| `X` / `Ctrl+x` | Play the highlighted track (or the playing one) next |
| `Z` / `Ctrl+z` | Add the highlighted track (or the playing one) to the queue |
| `delete` / `d d` | Queue: remove the selected track |
| `D` | Start a discovery mix (a random mix from one of several sources) |
| `v` | Mark / unmark the highlighted track (marks survive sorting and filtering; a marked track the filter hides still counts) |
| `V` | Start a range of marks that follows the cursor; `V` again ends it, keeping the marks |
| `escape` | Clear all marks |
| `A` | Add the marked tracks to a playlist, in the order shown (with no marks: the playing track) |
| `/` | Filter current list |
| `Ctrl+r` | Cycle repeat mode (off → all → one) |
| `Ctrl+s` | Toggle shuffle |
| `T` | Toggle lyrics transliteration (ASCII) |
| `s t` / `s a` / `s A` / `s d` | Sort by Title / Artist / Album / Duration |
| `s r` | Reverse current sort |
| `backspace` | Go back |
| `Shift+backspace` | Go forward (after a back) |
| `c` | Pick chart region (Browse → Charts only) |
| `q` / `Ctrl+q` | Quit |

## Mouse

| Action | Where | Effect |
|--------|-------|--------|
| Click | Progress bar | Seek to position |
| Scroll up/down | Progress bar | Scrub forward/backward (commits after 0.6s pause) |
| Scroll up/down | Volume display | Adjust volume by 5% |
| Click | Repeat button | Cycle repeat mode (off → all → one) |
| Click | Shuffle button | Toggle shuffle on/off |
| Click | Heart button | Toggle like on currently playing track |
| Click | Footer buttons | Navigate pages, play/pause, prev/next |
| Click | Header `← Back` / `Forward →` | Navigate history (auto-show/hide based on stack state) |
| Click | Playlist header `Shuffle lock: ON/off` | Toggle per-playlist forced-shuffle |
| Click | Charts shelf pills | Switch between chart shelves. Two rows: `Featured globally:` (events like Coachella, hidden on terminals < 80 cols) and the country charts (Top 100 Songs → Weekly Top Songs on Shorts → Trending 20 → rest). |
| Click | Liked Songs / Recently Played `[▶ Start Radio]` | Seed a radio from 5 random tracks in the collection |
| Right-click | Track row | Open context menu (play, queue, add to playlist, etc.). Works on every track-listing page including Queue / Liked Songs / Recently Played. |
| Click | Mark column (leftmost cell of a track row) | Mark / unmark that track. Never starts playback. |
| Ctrl+click | Track row | Mark / unmark that track and move the highlight there. Never starts playback. |
| Shift+click | Track row | Mark every row from the range's starting row to this one, keeping other marks. A normal click, Ctrl+click, or `v` sets the starting row. |

Whether Ctrl+click and Shift+click reach the app with their modifier depends on the terminal — some keep them for their own text selection or link handling. The keyboard alternative is `v` to mark the highlighted track and `V` to mark a range that follows the cursor.

## Custom keybindings

To rebind keys, edit `~/.config/ytm-player/keymap.toml`. The file maps action names to lists of keys. Example — change like-toggle from `l` to `Ctrl+l`:

```toml
[keys]
like_toggle = ["C-l"]
```

Modifiers are written `C-` (Ctrl), `S-` (Shift) and `M-` (Alt).

Multi-key sequences use space separation (e.g. `["g s"]` for "press g then s").

The in-app help (`?`) lists most actions with readable labels (`Like Toggle` for `like_toggle`); `keymap.toml` needs the snake_case name. `quit`, `toggle_sidebar`, `toggle_album_art` and `toggle_transliteration` are not listed there.
