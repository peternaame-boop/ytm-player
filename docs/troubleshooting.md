# Troubleshooting

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
- Try a different browser: `ytm setup` auto-detects Chrome, Firefox, Brave, Edge, Chromium, Vivaldi, Opera, Helium.
- If auto-detection fails, use the manual paste method: `ytm setup --manual`.
- Re-run `ytm setup` to re-authenticate.
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

Install the optional MPRIS dependency:

```bash
pip install -e ".[mpris]"
# or, on Arch:
sudo pacman -S python-dbus-fast
```

Requires D-Bus (standard on most Linux desktops). Verify with:

```bash
dbus-send --session --print-reply --dest=org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus.ListNames
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
