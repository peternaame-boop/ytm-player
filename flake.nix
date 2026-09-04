{
  description = "ytm-player: A full-featured YouTube Music TUI client for the terminal";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        # Pinned to a stable middle of the supported range (3.10..3.14).
        # Bump along with nixpkgs releases.
        python = pkgs.python313;

        # spotifyscraper is not in nixpkgs — build from PyPI.
        spotifyscraper = python.pkgs.buildPythonPackage rec {
          pname = "spotifyscraper";
          version = "3.9.2";
          # PEP 517 build (hatchling). The sdist also ships a Makefile whose
          # `install` target runs `pip install --upgrade pip`; with
          # pyproject = false the Python hooks don't engage and stdenv falls
          # back to `make install`, which fails in the hermetic sandbox (no
          # pip, no network) — that was issue #93.
          pyproject = true;

          src = python.pkgs.fetchPypi {
            inherit pname version;
            hash = "sha256-6ozyx0VjVAbaqBnlUEZ5zUoRGPjS9U/qwnQH0DNPs60=";
          };

          build-system = with python.pkgs; [ hatchling ];

          # 3.x's only hard dependency; the CLI/browser/media/mcp extras are
          # optional and ytm-player uses none of them.
          dependencies = with python.pkgs; [ httpx ];

          # Tests require network access.
          doCheck = false;

          pythonImportsCheck = [ "spotify_scraper" ];

          meta = {
            description = "Scrape Spotify tracks, albums, playlists and artist data";
            homepage = "https://github.com/AliAkhtari9/SpotifyScraper";
            license = pkgs.lib.licenses.mit;
          };
        };

        ytm-player = python.pkgs.buildPythonApplication {
          pname = "ytm-player";
          version = (builtins.head (
            builtins.match ''.*__version__[[:space:]]*=[[:space:]]*"([^"]+)".*'' (
              builtins.readFile ./src/ytm_player/__init__.py
            )
          ));

          pyproject = true;
          src = ./.;

          build-system = [ python.pkgs.hatchling ];

          # Relax upper bounds that may conflict with nixpkgs versions.
          # nixpkgs may ship textual 9.x while pyproject.toml caps at <9.0.
          pythonRelaxDeps = [ "textual" ];

          # python-mpv on PyPI registers as "mpv" in dist-info.
          # Remove the PyPI name so pythonRuntimeDepsCheck doesn't fail.
          pythonRemoveDeps = [ "python-mpv" ];

          dependencies =
            (with python.pkgs; [
              textual
              ytmusicapi
              yt-dlp
              mpv            # provides python-mpv (dist-info Name: mpv)
              aiosqlite
              click
              pillow         # album art (moved from optional to core in v1.3.1)
              packaging
            ])
            # dbus-fast powers Linux MPRIS (playerctl / media keys / now-playing).
            # Core dep on Linux so MPRIS works out of the box; Linux-only because
            # it can't import on darwin (socket.CMSG_LEN), matching the pyproject
            # sys_platform marker.
            ++ pkgs.lib.optionals pkgs.stdenv.hostPlatform.isLinux [ python.pkgs.dbus-fast ];

          optional-dependencies = with python.pkgs; {
            mpris = [ ];  # dbus-fast moved to core deps (Linux-only); kept for compat
            images = [ ];  # Pillow moved to core deps; kept for compat
            discord = [ pypresence ];
            lastfm = [ pylast ];
            transliteration = [ anyascii ];
            spotify = [
              spotipy
              spotifyscraper
              thefuzz
            ];
          };

          # Wrap the ytm binary so mpv and yt-dlp are on PATH.
          # python-mpv's ctypes path to libmpv.so is already patched by nixpkgs,
          # but mpv the CLI tool is still needed for some operations, and yt-dlp
          # must be findable on PATH even though we also import it as a library.
          makeWrapperArgs = [
            "--prefix"
            "PATH"
            ":"
            (pkgs.lib.makeBinPath [
              pkgs.mpv
              python.pkgs.yt-dlp
            ])
          ];

          # Tests require network access and mpv runtime.
          doCheck = false;

          pythonImportsCheck = [
            "ytm_player"
            "ytm_player.cli"
            "ytm_player.services.update_check"
          ];

          meta = {
            description = "A full-featured YouTube Music TUI client for the terminal";
            homepage = "https://github.com/peternaame-boop/ytm-player";
            license = pkgs.lib.licenses.mit;
            maintainers = [ ];
            mainProgram = "ytm";
            platforms = pkgs.lib.platforms.linux ++ pkgs.lib.platforms.darwin;
          };
        };
      in
      {
        packages = {
          default = ytm-player;
          ytm-player = ytm-player;

          # Variant with all optional features enabled.
          ytm-player-full = ytm-player.overridePythonAttrs (old: {
            dependencies =
              old.dependencies
              ++ old.optional-dependencies.mpris
              ++ old.optional-dependencies.discord
              ++ old.optional-dependencies.lastfm
              ++ old.optional-dependencies.transliteration
              ++ old.optional-dependencies.spotify;
          });
        };

        devShells.default = pkgs.mkShell {
          inputsFrom = [ ytm-player ];

          packages =
            (with python.pkgs; [
              # Dev tools
              pytest
              pytest-asyncio
              pytest-cov
              ruff

              # Include all optional deps in the dev shell
              pillow
              pypresence
              pylast
              spotipy
              spotifyscraper
              thefuzz
              anyascii
            ])
            # dbus-fast is Linux-only (socket.CMSG_LEN); guard it so `nix develop`
            # still works on darwin.
            ++ pkgs.lib.optionals pkgs.stdenv.hostPlatform.isLinux [ python.pkgs.dbus-fast ]
            ++ [
              pkgs.mpv
            ];

          shellHook = ''
            echo "ytm-player dev shell"
            echo "  python: ${python.version}"
            echo "  run:    ytm"
            echo "  test:   pytest"
            echo "  lint:   ruff check src/ tests/"
          '';
        };
      }
    )
    // {
      # Overlay for use in NixOS configurations:
      #   nixpkgs.overlays = [ ytm-player.overlays.default ];
      #   environment.systemPackages = [ pkgs.ytm-player ];
      overlays.default = final: prev: {
        ytm-player = self.packages.${prev.stdenv.hostPlatform.system}.default;
      };
    };
}
