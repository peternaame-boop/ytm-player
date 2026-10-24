"""Full black theme with a dim chartreuse accent, thin scroll bars, and the taller player bar."""

from __future__ import annotations

from rich.color import Color
from textual.app import App, ComposeResult

from ytm_player.ui.playback_bar import PlaybackBar
from ytm_player.ui.theme import YTM_DARK
from ytm_player.ui.widgets.thin_scrollbar import ThinScrollBarRender


def test_the_theme_is_full_black_with_a_dim_green_accent_and_grey_chrome():
    assert YTM_DARK.background == "#000000" and YTM_DARK.surface == "#000000"
    assert YTM_DARK.primary == "#6aa81e"
    for name in ("border", "scrollbar", "block-cursor-background"):
        assert YTM_DARK.variables[name].startswith("#1") or YTM_DARK.variables[name].startswith(
            "#2"
        )


def test_horizontal_bars_draw_a_thin_line_and_vertical_bars_stay_one_cell():
    segments = ThinScrollBarRender.render_bar(
        size=20, virtual_size=60, window_size=20, position=0, vertical=False,
        back_color=Color.parse("#000000"), bar_color=Color.parse("#2e2e2e")).segments  # fmt: skip
    cells = [s.text for s in segments if s.text != "\n"]
    assert len(cells) == 20
    assert set(cells) == {"▃", " "}
    assert cells[:7] == ["▃"] * 7, "one even line, no stepped ends"


def test_vertical_bars_are_one_solid_cell():
    segments = ThinScrollBarRender.render_bar(
        size=10, virtual_size=40, window_size=20, position=0, vertical=True,
        back_color=Color.parse("#000000"), bar_color=Color.parse("#2e2e2e")).segments  # fmt: skip
    assert [s.text for s in segments][:5] == ["█"] * 5


class _Host(App):
    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        for name in ("playback-bar-bg", "progress-filled", "progress-empty", "active-tab",
                     "inactive-tab", "selected-item"):  # fmt: skip
            variables.setdefault(name, "#000000")
        return variables

    def compose(self) -> ComposeResult:
        yield PlaybackBar(id="playback-bar")


async def test_the_player_bar_lays_out_like_the_web_player():
    app = _Host()
    async with app.run_test(size=(140, 20)) as pilot:
        await pilot.pause()
        bar = app.query_one(PlaybackBar)
        assert bar.outer_size.height == 7
        seek = app.query_one("#pb-progress")
        assert seek.region.y == bar.region.y and seek.region.width == bar.region.width
        groups = [app.query_one(s).region for s in ("#pb-left", "#pb-center", "#pb-right")]
        assert [g.x for g in groups] == sorted(g.x for g in groups)
        middles = {g.y + g.height // 2 for g in groups}
        assert len(middles) == 1, "the three groups share one middle row"


async def test_the_time_shows_beside_the_transport_buttons():
    app = _Host()
    async with app.run_test(size=(140, 20)) as pilot:
        app.query_one(PlaybackBar).update_position(75, 225)
        await pilot.pause()
        assert str(app.query_one("#pb-time").render()) == "1:15 / 3:45"


async def test_bar_icons_are_one_cell_greyscale_and_the_theme_colour_when_on():
    from rich.cells import cell_len

    from ytm_player.services.queue import RepeatMode
    from ytm_player.ui.theme import get_theme

    app = _Host()
    async with app.run_test(size=(140, 20)) as pilot:
        bar = app.query_one(PlaybackBar)
        shuffle, repeat = app.query_one("#pb-shuffle"), app.query_one("#pb-repeat")
        off = [shuffle.render(), repeat.render()]
        assert all(cell_len(str(t)) == 1 for t in off)
        assert all(get_theme().primary not in str(t.style) for t in off)
        bar.update_shuffle(True)
        bar.update_repeat(RepeatMode.ALL)
        await pilot.pause()
        on = [shuffle.render(), repeat.render()]
        assert all(get_theme().primary in str(t.style) for t in on)


async def test_title_like_and_controls_share_the_play_buttons_middle_row():
    app = _Host()
    async with app.run_test(size=(140, 20)) as pilot:
        bar = app.query_one(PlaybackBar)
        bar.update_track({"title": "MOTTO", "artist": "NF", "album": "HOPE", "video_id": "v"})
        await pilot.pause()
        play = app.query_one("#pb-play").region
        middle = play.y + play.height // 2
        title_row = app.query_one("#pb-track-info").region.y
        assert title_row == middle
        for selector in ("#pb-time", "#pb-heart", "#pb-volume", "#pb-repeat", "#pb-shuffle"):
            region = app.query_one(selector).region
            assert region.y + region.height // 2 == middle, selector
        art = app.query_one("#pb-art").region
        assert app.query_one("#pb-track-info").region.x - art.right <= 1


async def test_the_footer_has_no_second_set_of_transport_buttons():
    from ytm_player.ui.playback_bar import FooterBar, _FooterButton

    class _FooterHost(App):
        def compose(self) -> ComposeResult:
            yield FooterBar()

    app = _FooterHost()
    async with app.run_test(size=(140, 5)) as pilot:
        await pilot.pause()
        actions = [b._action for b in app.query(_FooterButton)]
        assert not {"prev", "play_pause", "next"} & set(actions)


async def test_the_footer_does_not_cover_the_player_bar():
    """Regression: bar and footer both docked to the bottom, so the footer hid the bar's last row."""
    from textual.containers import Vertical

    from ytm_player.ui.playback_bar import FooterBar

    class _StackHost(_Host):
        CSS = "#bottom-stack { dock: bottom; height: auto; }"

        def compose(self) -> ComposeResult:
            with Vertical(id="bottom-stack"):
                yield PlaybackBar(id="playback-bar")
                yield FooterBar(id="app-footer")

    app = _StackHost()
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        bar, footer = app.query_one("#playback-bar").region, app.query_one("#app-footer").region
        assert bar.bottom <= footer.y


async def test_the_footer_opens_recently_played_and_highlights_it():
    from unittest.mock import AsyncMock

    from ytm_player.ui.playback_bar import FooterBar

    class _FooterHost(App):
        def compose(self) -> ComposeResult:
            yield FooterBar(id="app-footer")

    app = _FooterHost()
    app.navigate_to = AsyncMock()
    async with app.run_test(size=(140, 5)) as pilot:
        await pilot.click("#footer-recently_played")
        await pilot.pause()
        app.navigate_to.assert_awaited_once_with("recently_played")
        app.query_one(FooterBar).set_active_page("recently_played")
        assert app.query_one("#footer-recently_played").is_active


async def test_the_selected_item_shows_on_the_artist_line_when_it_fits():
    app = _Host()
    async with app.run_test(size=(160, 20)) as pilot:
        bar = app.query_one(PlaybackBar)
        bar.update_track({"title": "Die With A Smile", "artist": "Lady Gaga", "video_id": "v"})
        bar.set_selection("Cruel Summer — Taylor Swift")
        await pilot.pause()
        line2 = str(app.query_one("#pb-track-info").render()).split("\n")[1]
        assert "Lady Gaga" in line2 and "Cruel Summer — Taylor Swift" in line2


async def test_the_selection_is_left_out_when_there_is_no_room_or_it_is_playing():
    app = _Host()
    async with app.run_test(size=(70, 20)) as pilot:
        bar = app.query_one(PlaybackBar)
        bar.update_track({"title": "Die With A Smile", "artist": "Lady Gaga, Bruno Mars",
                          "album": "MAYHEM", "video_id": "v"})  # fmt: skip
        bar.set_selection("Cruel Summer — Taylor Swift")
        await pilot.pause()
        assert "Cruel" not in str(app.query_one("#pb-track-info").render())
    app = _Host()
    async with app.run_test(size=(160, 20)) as pilot:
        bar = app.query_one(PlaybackBar)
        bar.update_track({"title": "Die With A Smile", "artist": "Lady Gaga", "video_id": "v"})
        bar.set_selection("Die With A Smile — Lady Gaga")
        await pilot.pause()
        assert "›" not in str(app.query_one("#pb-track-info").render())


async def test_track_tables_have_no_grey_background_or_header():
    from ytm_player.app._app import YTMPlayerApp
    from ytm_player.ui.widgets.track_table import TrackTable

    class _TableHost(App):
        CSS = YTMPlayerApp.CSS

        def on_mount(self) -> None:
            self.register_theme(YTM_DARK)
            self.theme = YTM_DARK.name

        def get_css_variables(self) -> dict[str, str]:
            variables = super().get_css_variables()
            variables.setdefault("selected-item", "#1c1c1c")
            return variables

        def compose(self) -> ComposeResult:
            yield TrackTable(id="t")

    app = _TableHost()
    async with app.run_test(size=(80, 10)) as pilot:
        await pilot.pause()
        table = app.query_one(TrackTable)
        assert table.styles.background.hex.upper() == "#000000"
        header = table.get_component_styles("datatable--header")
        assert header.background.hex.upper() == "#000000"


async def test_controls_sit_midway_between_the_seek_line_and_the_bottom_edge():
    app = _Host()
    async with app.run_test(size=(140, 20)) as pilot:
        await pilot.pause()
        bar = app.query_one(PlaybackBar).region
        seek = app.query_one("#pb-progress").region
        play = app.query_one("#pb-play").region
        above = play.y - seek.bottom
        below = bar.bottom - play.bottom
        assert above == below == 1


async def test_the_playlist_sidebar_paints_black_instead_of_letting_grey_through():
    """The sidebar and its panels set no background, so the app body's grey
    showed through. Each of them must paint the background colour itself."""
    from ytm_player.app._app import YTMPlayerApp
    from ytm_player.ui.sidebars.playlist_sidebar import LibraryPanel, PlaylistSidebar

    app = YTMPlayerApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(1.0)
        sidebar = app.query_one(PlaylistSidebar)
        # A transparent colour shows the app body's grey underneath, so each
        # surface must read opaque black rather than carry a zero alpha.
        assert sidebar.styles.background.rgb == (0, 0, 0)
        assert sidebar.styles.background.a == 1
        for panel in list(app.query(LibraryPanel)):
            assert panel.styles.background.rgb == (0, 0, 0)
            assert panel.styles.background.a == 1
        lv = app.query_one("LibraryPanel ListView")
        assert lv.styles.background.rgb == (0, 0, 0)
        assert lv.styles.background.a == 1
