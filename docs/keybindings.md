# Keybindings

## Keyboard

| Key | Action |
|-----|--------|
| `space` | Play/Pause |
| `n` | Next track |
| `p` | Previous track |
| `l` | Toggle like on currently playing track |
| `+` / `-` | Volume up/down |
| `j` / `k` | Move down/up |
| `enter` | Select / play |
| `g l` | Go to Library |
| `g s` | Go to Search |
| `g b` | Go to Browse |
| `g y` | Go to Liked Songs |
| `g r` | Go to Recently Played |
| `z` | Go to Queue |
| `g L` | Toggle lyrics sidebar |
| `Ctrl+e` | Toggle playlist sidebar |
| `Ctrl+a` | Toggle album art in playback bar |
| `Ctrl+p` | Change theme |
| `?` | Help (full keybinding reference inside the app) |
| `Ctrl+w h` | Focus the Playlists sidebar (auto-shows it if hidden) |
| `Ctrl+w l` | Focus the main content, then the lyrics sidebar if it's open |
| `Ctrl+w w` | Cycle focus through the visible panes |
| `tab` | Focus next element on the current page (page-specific) |
| `a` | Track actions menu |
| `/` | Filter current list |
| `Ctrl+r` | Cycle repeat mode (off → all → one) |
| `Ctrl+s` | Toggle shuffle |
| `T` | Toggle lyrics transliteration (ASCII) |
| `s t` / `s a` / `s A` / `s d` | Sort by Title / Artist / Album / Duration |
| `s r` | Reverse current sort |
| `backspace` | Go back |
| `Shift+backspace` | Go forward (after a back) |
| `c` | Pick chart region (Browse → Charts only) |
| `q` | Quit |

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

## Custom keybindings

To rebind keys, edit `~/.config/ytm-player/keymap.toml`. The file maps action names to lists of keys. Example — change like-toggle from `l` to `Ctrl+l`:

```toml
[keys]
like_toggle = ["ctrl+l"]
```

Multi-key sequences use space separation (e.g. `["g s"]` for "press g then s").

A complete list of action names is shown in the in-app help (`?`).
