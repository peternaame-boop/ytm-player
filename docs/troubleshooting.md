# Troubleshooting

## Playback fails with "HTTP error 403 Forbidden"

Every track fails and `~/.config/ytm-player/logs/ytm.log` shows
`mpv[ffmpeg]: https: HTTP error 403 Forbidden`.

YouTube stopped serving streams to yt-dlp's former default client on
2026-08-17. yt-dlp 2026.08.19 fixed this, and ytm-player now requires it.
If you still see 403s, the yt-dlp ytm-player actually uses is stale. For
pipx/uv/pip installs that is the copy inside ytm-player's own environment —
updating a system-wide `yt-dlp` does not touch it. Upgrade ytm-player itself,
which pulls the required yt-dlp:

- pipx: `pipx upgrade ytm-player` (or `pipx uninstall ytm-player && pipx install ytm-player`)
- uv: `uv tool upgrade ytm-player`
- pip: `pip install -U ytm-player`
- AUR: `yay -Syu` (yt-dlp is a system package there, so a system update is the fix)
- Nix, declarative: `nix flake update` on your input, then rebuild
- Nix, `nix profile`: `nix profile list`, then `nix profile upgrade <name>`

Then restart `ytm` — it keeps its yt-dlp instance and cached stream URLs for
the whole process lifetime. Also restart after changing networks or
disconnecting a VPN (stream URLs are bound to the IP that requested them).

## "mpv not found" or playback doesn't start

Ensure mpv is installed and in your `$PATH`:

```bash
mpv --version
```

If installed but not found, check that the `libmpv` shared library is available:

```bash
# Arch
pacman -Qs mpv

# Ubuntu/Debian — you may need the dev package
sudo apt install libmpv-dev
```

For Windows-specific libmpv setup, see [docs/installation.md#windows-setup](installation.md#windows-setup).

## Authentication fails

- Make sure you're signed in to YouTube Music (free or Premium) in your browser.
- Try a different browser: `ytm setup` auto-detects Chrome, Firefox, Zen, Brave, Edge, Chromium, Vivaldi, Opera, Helium.
- If auto-detection fails, use the manual paste method: `ytm setup --manual`.
- Re-run `ytm setup` to re-authenticate.
- **"Your YouTube Music session expired" even though the browser is signed in**: automatic renewal only replaces a session with the *same* account, using the channel ID `ytm setup` records in `account.json`. Sessions set up before that file existed keep working until they expire, but can't be renewed automatically; run `ytm setup` once and renewal works from then on. Renewal is also refused when the browser no longer has that account signed in, or when the account has no YouTube channel.
- For multi-account or Brand Account setups: `ytm setup` will detect multiple Google accounts and prompt you to pick. Brand Accounts can also be configured via `[general] brand_account_id` in `config.toml`.

## No sound / wrong audio device

mpv uses your system's default audio output. To change it, create `~/.config/mpv/mpv.conf`:

```
audio-device=pulse/your-device-name
```

List available devices with `mpv --audio-device=help`.

## macOS media keys open Apple Music instead of ytm-player

- ytm-player registers with macOS Now Playing while running, so media keys should target it.
- Start playback in `ytm` first; macOS routes media keys to the active Now Playing app.
- Grant Accessibility and Input Monitoring permission to your terminal app (Terminal, Ghostty, iTerm) in System Settings → Privacy & Security.
- If Apple Music still steals keys, fully quit Music.app and press play/pause once in ytm.

## MPRIS / media keys not working (Linux)

MPRIS (`playerctl`, hardware media keys, desktop now-playing) ships by default on
Linux. If it isn't working, check these in order:

1. **Are you in a desktop session?** MPRIS needs a running D-Bus *session* bus —
   it won't work over plain SSH, in a bare container, or on a headless box.
   Verify one exists:
   ```bash
   dbus-send --session --print-reply --dest=org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus.ListNames
   ```
2. **Run `ytm doctor`.** The `MPRIS / media keys` line reports whether `dbus-fast`
   is present and the bus name ytm registers under.
3. **If `ytm doctor` reports `dbus-fast` missing,** your install is incomplete
   (it's a core dependency on Linux, so this is unusual — typically a partial or
   stale install). Reinstall:
   ```bash
   pip install --force-reinstall ytm-player
   # pipx:
   pipx reinstall ytm-player
   # fallback — pull just the library:
   pipx inject ytm-player dbus-fast
   ```

## Cache taking too much space

```bash
ytm cache status   # Check cache size
ytm cache clear    # Wipe all cached audio
```

Or reduce the limit in `config.toml`:

```toml
[cache]
max_size_mb = 512
```

## Logs and diagnostics

ytm-player writes a rotating log file to:

- Linux/macOS: `~/.config/ytm-player/logs/ytm.log`
- Windows: `%APPDATA%\ytm-player\logs\ytm.log`

Crash tracebacks for any unhandled exception (main thread or background thread) are saved to the `crashes/` directory next to the log file. The same directory holds `faulthandler.log` (created on every TUI startup) which captures Python tracebacks for fatal signals (SIGSEGV / SIGBUS / SIGFPE / SIGILL / SIGABRT) — important for catching libmpv C-side crashes that bypass the normal exception machinery.

For verbose logs, launch with `--debug`:

```bash
ytm --debug
```

When reporting a bug, please run:

```bash
ytm doctor
```

and paste the output into your GitHub issue. It includes eight sections: version + platform info, config/log/crash paths, running-process status, recent ERROR/WARNING log lines, recent mpv warnings, the most recent faulthandler trace, the most recent crash file, and the active-hooks summary. Auth-sensitive substrings (Authorization / Cookie / Bearer / token / SAPISID) are scrubbed automatically before output.
