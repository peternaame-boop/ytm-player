"""Custom command palette providers for ytm-player."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.command import DiscoveryHit, Hit, Hits, Provider

if TYPE_CHECKING:
    from ytm_player.app._app import YTMPlayerApp


class YTMCommandProvider(Provider):
    """Custom command provider for ytm-player specific actions."""

    @property
    def _host(self) -> YTMPlayerApp:
        return self.app  # type: ignore[return-value]

    async def discover(self) -> Hits:
        """Yield discovery hits for ytm-player commands."""
        yield DiscoveryHit(
            "Theme: Set Current as Default",
            self._host.action_set_current_theme_as_default,
            help="Save the active theme to config.toml",
        )

    async def search(self, query: str) -> Hits:
        """Fuzzy search ytm-player commands."""
        matcher = self.matcher(query)
        commands = [
            (
                "Theme: Set Current as Default",
                self._host.action_set_current_theme_as_default,
                "Save the active theme to config.toml",
            ),
        ]
        for name, callback, help_text in commands:
            if (match := matcher.match(name)) > 0:
                yield Hit(
                    match,
                    matcher.highlight(name),
                    callback,
                    help=help_text,
                )
