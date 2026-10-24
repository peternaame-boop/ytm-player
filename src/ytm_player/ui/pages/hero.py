"""The web app's page header for an album or playlist: a large cover, the title, details and actions.

The context page and the Library playlist view both draw it. Textual scopes a
widget's ``DEFAULT_CSS`` to that widget, so each page adds ``HERO_CSS`` to its own.
"""

from __future__ import annotations

from collections.abc import Iterable

from textual.containers import Horizontal, Vertical
from textual.widgets import Label, Static

from ytm_player.ui.widgets.cover import Cover

HERO_CSS = """
.context-hero {
    height: auto;
    padding: 1 2;
}
.context-hero > Cover {
    width: 30;
    height: 15;
    margin-right: 3;
}
.context-hero-text {
    width: 1fr;
    height: auto;
}
.context-hero .context-kind {
    color: $text-muted;
    margin-top: 2;
}
.context-hero .context-title {
    margin: 1 0;
    text-style: bold;
}
.context-hero .context-subtitle {
    color: $text-muted;
}
.context-hero .context-description {
    color: $text-muted;
    text-style: italic;
}
.context-actions {
    height: 3;
    margin-top: 1;
}
.context-actions > Static {
    width: auto;
    height: 3;
    padding: 1 2;
    margin-right: 1;
    border: none;
    background: $boost;
    content-align: center middle;
}
.context-actions > Static:hover {
    background: $primary 30%;
}
.context-actions > #play-all-btn {
    background: $foreground;
    color: $background;
    text-style: bold;
}
.context-actions > #play-all-btn:hover {
    background: $primary;
}
.context-actions > .locked {
    color: $primary;
    text-style: bold;
}
"""


def build_hero(
    kind: str,
    title: str,
    lines: Iterable[Label],
    actions: Iterable[Static],
    thumbnail_url: str,
) -> Horizontal:
    """The hero, ready to mount: cover on the left, text and action buttons on the right."""
    return Horizontal(
        Cover(thumbnail_url),
        Vertical(
            Label(kind, classes="context-kind"),
            Label(title, classes="context-title"),
            *lines,
            Horizontal(*actions, classes="context-actions"),
            classes="context-hero-text",
        ),
        classes="context-hero",
    )
