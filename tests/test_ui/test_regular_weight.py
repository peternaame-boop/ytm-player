"""Every character renders in the regular face: no bold, no italic."""

from __future__ import annotations

from rich.segment import Segment
from rich.style import Style
from textual.app import App, ComposeResult
from textual.color import Color
from textual.geometry import Region
from textual.widgets import Static

from ytm_player.ui.regular_weight import RegularWeight, RegularWeightMixin


def test_bold_and_italic_become_regular_and_colour_stays():
    segments = [
        Segment("a", Style(bold=True, color="red")),
        Segment("b", Style(italic=True, dim=True)),
        Segment("c", Style(underline=True)),
        Segment("d"),
    ]
    out = RegularWeight().apply(segments, Color(0, 0, 0))
    assert [(s.style.bold, s.style.italic) for s in out[:2]] == [(False, False), (False, False)]
    assert out[0].style.color.name == "red"
    assert out[1].style.dim is True
    assert out[2] is segments[2] and out[3] is segments[3]


def test_control_codes_survive():
    control = [(1,)]
    out = RegularWeight().apply([Segment("x", Style(bold=True), control)], Color(0, 0, 0))
    assert out[0].control == control


class _Host(RegularWeightMixin, App):
    CSS = "#css-bold { text-style: bold italic; }"

    def compose(self) -> ComposeResult:
        yield Static("[b]markup bold[/b]", id="markup-bold")
        yield Static("css bold", id="css-bold")


async def test_rendered_lines_carry_no_bold_or_italic():
    app = _Host()
    async with app.run_test(size=(40, 4)) as pilot:
        await pilot.pause()
        for selector in ("#markup-bold", "#css-bold"):
            widget = app.query_one(selector)
            (strip,) = widget.render_lines(Region(0, 0, widget.size.width, 1))
            styled = [seg.style for seg in strip if seg.text.strip() and seg.style]
            assert styled, selector
            assert not any(st.bold or st.italic for st in styled), selector


def test_uppercase_keeps_every_column():
    from ytm_player.ui.regular_weight import UppercaseText

    segments = [
        Segment("Gone Forever", Style(color="green")),
        Segment("Straße ﬁx", None),
        Segment("", None),
        Segment("x", None, [(1,)]),
    ]
    out = UppercaseText().apply(segments, Color(0, 0, 0))
    assert out[0].text == "GONE FOREVER" and out[0].style.color.name == "green"
    assert out[1].text == "STRAßE ﬁX" and len(out[1].text) == len(segments[1].text)
    assert out[2] is segments[2] and out[3] is segments[3]


async def test_uppercase_setting_turns_capitals_on_and_off(monkeypatch):
    from ytm_player.config.settings import get_settings

    for on, expected in ((True, "CSS BOLD"), (False, "css bold")):
        monkeypatch.setattr(get_settings().ui, "uppercase", on)
        app = _Host()
        async with app.run_test(size=(40, 4)) as pilot:
            await pilot.pause()
            widget = app.query_one("#css-bold")
            (strip,) = widget.render_lines(Region(0, 0, widget.size.width, 1))
            assert strip.text.strip() == expected
