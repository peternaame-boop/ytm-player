# Installation

This guide covers installing ytm-player on every supported platform.

## Step 1: Install mpv

mpv is required for audio playback. Install it with your system package manager:

| Platform | Command |
|----------|---------|
| Arch / CachyOS / Manjaro | `sudo pacman -S mpv` |
| Ubuntu / Debian | `sudo apt install mpv` |
| Fedora | `sudo dnf install mpv` |
| macOS (Homebrew) | `brew install mpv` |
| Windows (Scoop) | `scoop install mpv` (then see [Windows Setup](#windows-setup) for the libmpv DLL) |
| NixOS | Handled by the flake — see the NixOS section below |

## Step 2: Install ytm-player

### PyPI (Linux / macOS)

```bash
pip install ytm-player
```

### Arch Linux / CachyOS / EndeavourOS / Manjaro (AUR)

```bash
yay -S ytm-player-git
```

(Or any other AUR helper.) Package: [ytm-player-git](https://aur.archlinux.org/packages/ytm-player-git).

### Gentoo (GURU)

Enable the [GURU repository](https://wiki.gentoo.org/wiki/Project:GURU/Information_for_End_Users) then:

```bash
emerge --ask media-sound/ytm-player
```

### Windows

```powershell
pip install ytm-player
```

Launch with:

```powershell
py -m ytm_player
```

> `pip install` on Windows does not add the `ytm` command to PATH. Use `py -m ytm_player`, or install with [pipx](https://pipx.pypa.io/) which handles PATH automatically: `pipx install ytm-player`.

> **Important:** Windows requires extra mpv setup — see [Windows Setup](#windows-setup) below.

### NixOS (Flake)

ytm-player provides a `flake.nix` with two packages, a dev shell, and an overlay.

**Try it without installing:**

```bash
nix run github:peternaame-boop/ytm-player
```

**Add to your system flake:**

```nix
{
  inputs.ytm-player.url = "github:peternaame-boop/ytm-player";

  outputs = { nixpkgs, ytm-player, ... }: {
    nixosConfigurations.myhost = nixpkgs.lib.nixosSystem {
      modules = [
        ({ pkgs, ... }: {
          nixpkgs.overlays = [ ytm-player.overlays.default ];
          environment.systemPackages = [
            pkgs.ytm-player     # core (MPRIS + album art included)
            # ytm-player.packages.${pkgs.stdenv.hostPlatform.system}.ytm-player-full   # all features (Discord, Last.fm, Spotify import)
          ];
        })
      ];
    };
  };
}
```

**Or install imperatively:**

```bash
nix profile install github:peternaame-boop/ytm-player
nix profile install github:peternaame-boop/ytm-player#ytm-player-full
```

**Dev shell** (for contributors):

```bash
git clone https://github.com/peternaame-boop/ytm-player.git
cd ytm-player
nix develop
```

> **Note for pip-on-NixOS users:** if you install via `pip` instead of the flake, NixOS doesn't expose `libmpv.so` in standard library paths. Add to your shell config:
> ```fish
> # Fish
> set -gx LD_LIBRARY_PATH /run/current-system/sw/lib $LD_LIBRARY_PATH
> ```
> ```bash
> # Bash/Zsh
> export LD_LIBRARY_PATH="/run/current-system/sw/lib:$LD_LIBRARY_PATH"
> ```
> The flake handles this automatically.

### From source

```bash
git clone https://github.com/peternaame-boop/ytm-player.git
cd ytm-player
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Optional extras

> **Linux MPRIS / media keys ship by default.** The `dbus-fast` library MPRIS
> needs is a core dependency on Linux, so `playerctl`, hardware media keys, and
> desktop now-playing controls work out of the box — no extra to install. (The
> `[mpris]` extra still exists but is empty, kept only for backward compatibility.)

### pip

```bash
pip install "ytm-player[spotify]"          # Spotify playlist import
pip install "ytm-player[discord]"          # Discord Rich Presence
pip install "ytm-player[lastfm]"           # Last.fm scrobbling
pip install "ytm-player[transliteration]"  # Non-Latin lyric → ASCII
pip install "ytm-player[spotify,discord,lastfm,transliteration]"  # all
pip install -e ".[dev]"                    # Development tools (pytest, ruff)
```

### AUR

If you installed via AUR, install optional dependencies with pacman/yay — **not** pip (Arch enforces [PEP 668](https://peps.python.org/pep-0668/)):

```bash
yay -S python-pylast                       # Last.fm scrobbling
yay -S python-pypresence                   # Discord Rich Presence
yay -S python-spotipy python-thefuzz       # Spotify playlist import
yay -S python-anyascii                     # Lyrics transliteration
```

Spotify import without saved Spotify API credentials also needs the `spotifyscraper` Python package.

(`python-dbus-fast` is a hard dependency of the AUR package, so MPRIS / media
keys are pulled in automatically — no separate install needed.)

## Windows Setup

On Linux and macOS, `mpv` packages include the shared library that ytm-player needs. On Windows, `scoop install mpv` (and most other installers) only ship the **player executable** — the `libmpv-2.dll` library must be downloaded separately.

**Steps:**

1. Install mpv: `scoop install mpv` (or [download from mpv.io](https://mpv.io/installation/))
2. Install 7zip if you don't have it: `scoop install 7zip`
3. Download the latest **`mpv-dev-x86_64-*.7z`** from [shinchiro's mpv builds](https://github.com/shinchiro/mpv-winbuild-cmake/releases) (the file starting with `mpv-dev`, not just `mpv`)
4. Extract `libmpv-2.dll` into your mpv directory:

```powershell
7z e "$env:TEMP\mpv-dev-x86_64-*.7z" -o"$env:USERPROFILE\scoop\apps\mpv\current" libmpv-2.dll -y
```

If you installed mpv a different way, place `libmpv-2.dll` next to `mpv.exe` or anywhere on `%PATH%`.

ytm-player automatically searches common install locations (scoop's `mpv` and `mpv-git` apps, `Program Files\mpv`, `Program Files (x86)\mpv`) for the DLL.

## Authenticate

```bash
ytm setup                    # Browser cookies: [yt_dlp] cookies_file first if set, then cookie_browser if set, else auto-detect
ytm setup --browser firefox  # Target a specific browser
ytm setup --manual           # Skip detection, paste headers directly
```

Windows: replace `ytm` with `py -m ytm_player`.

The setup wizard has three modes — see the inline help for details (`ytm setup --help`).

Credentials are stored as `auth.json` in the config directory (`~/.config/ytm-player/`, respecting `$XDG_CONFIG_HOME`; `%APPDATA%\ytm-player\` on Windows), with `0o600` permissions on Linux/macOS.

> ⚠️ The `[yt_dlp].remote_components` setting allows fetching external JS components (npm/GitHub). Enable it only if you trust the source and network path.
