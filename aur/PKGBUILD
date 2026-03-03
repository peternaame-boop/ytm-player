# Maintainer: Peter <your@email.com>
pkgname=ytm-player-git
pkgver=1.2.9.r0.gb59cc5c
pkgrel=1
pkgdesc="A full-featured YouTube Music TUI client with vim-style navigation"
arch=('any')
url="https://github.com/peternaame-boop/ytm-player"
license=('MIT')
depends=(
    'mpv'
    'python'
    'python-aiosqlite'
    'python-click'
    'python-mpv'
    'python-textual>=7.0'
    'python-ytmusicapi'
    'yt-dlp'
)
makedepends=('git' 'python-build' 'python-installer' 'python-hatchling')
optdepends=(
    'python-dbus-next: MPRIS media key support'
    'python-pillow: album art rendering'
    'python-pylast: Last.fm scrobbling'
    'python-pypresence: Discord Rich Presence'
    'python-spotipy: Spotify playlist import (AUR)'
    'python-thefuzz: Spotify import fuzzy matching (AUR)'
)
provides=('ytm-player')
conflicts=('ytm-player')
source=("git+${url}.git")
sha256sums=('SKIP')

pkgver() {
    cd ytm-player
    git describe --long --tags 2>/dev/null | sed 's/^v//;s/\([^-]*-g\)/r\1/;s/-/./g' ||
    printf "r%s.%s" "$(git rev-list --count HEAD)" "$(git rev-parse --short HEAD)"
}

build() {
    cd ytm-player
    python -m build --wheel --no-isolation
}

package() {
    cd ytm-player
    python -m installer --destdir="$pkgdir" dist/*.whl
    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
