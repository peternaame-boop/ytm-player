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
        python = pkgs.python312;

        # spotifyscraper is not in nixpkgs — build from PyPI.
        spotifyscraper = python.pkgs.buildPythonPackage rec {
          pname = "spotifyscraper";
          version = "2.1.5";
          pyproject = false; # uses setup.py

          src = python.pkgs.fetchPypi {
            inherit pname version;
            hash = "sha256-QoapRb+L265P3zUX4IHJxf9k4dlB6451p5aI6nZvOto=";
          };

          build-system = [ python.pkgs.setuptools ];

          dependencies = with python.pkgs; [
            requests
            beautifulsoup4
            lxml
            click
            rich
            tqdm
            fake-useragent
          ];

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
          # nixpkgs may ship textual 8.x while pyproject.toml says <8.0.
          pythonRelaxDeps = [ "textual" ];

          dependencies = with python.pkgs; [
            textual
            ytmusicapi
            yt-dlp
            mpv
            aiosqlite
            click
          ];

          optional-dependencies = with python.pkgs; {
            mpris = [ dbus-next ];
            images = [ pillow ];
            discord = [ pypresence ];
            lastfm = [ pylast ];
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
              ++ old.optional-dependencies.images
              ++ old.optional-dependencies.discord
              ++ old.optional-dependencies.lastfm
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
              dbus-next
              pillow
              pypresence
              pylast
              spotipy
              spotifyscraper
              thefuzz
            ])
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
