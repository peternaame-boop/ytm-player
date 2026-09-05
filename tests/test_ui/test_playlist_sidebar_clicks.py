"""Tests for LibraryPanel click message handling."""

from __future__ import annotations

from unittest.mock import MagicMock

from ytm_player.ui.sidebars import playlist_sidebar
from ytm_player.ui.sidebars.playlist_sidebar import LibraryPanel


def _make_panel(monkeypatch) -> LibraryPanel:
    panel = LibraryPanel("Library", instant_select=True)
    panel._filtered_items = [{"playlistId": "PL1", "title": "A"}]
    monkeypatch.setattr(panel, "_find_clicked_item_index", MagicMock(return_value=0))
    monkeypatch.setattr(panel, "post_message", MagicMock())
    return panel


def _make_click():
    return MagicMock(button=1)


def _make_selected():
    return MagicMock(list_view=MagicMock(index=0))


def _posted_message_types(panel: LibraryPanel) -> list[type]:
    return [type(call.args[0]) for call in panel.post_message.call_args_list]


def test_single_click_posts_one_selected(monkeypatch):
    panel = _make_panel(monkeypatch)
    monkeypatch.setattr(playlist_sidebar.time, "monotonic", MagicMock(return_value=1.0))

    panel.on_click(_make_click())
    panel.on_list_view_selected(_make_selected())

    assert _posted_message_types(panel) == [LibraryPanel.ItemSelected]


def test_double_click_posts_selected_then_double_clicked(monkeypatch):
    panel = _make_panel(monkeypatch)
    monkeypatch.setattr(playlist_sidebar.time, "monotonic", MagicMock(side_effect=[1.0, 1.2]))
    first_click = _make_click()
    second_click = _make_click()

    panel.on_click(first_click)
    panel.on_list_view_selected(_make_selected())
    panel.on_click(second_click)
    panel.on_list_view_selected(_make_selected())

    assert _posted_message_types(panel) == [
        LibraryPanel.ItemSelected,
        LibraryPanel.ItemDoubleClicked,
    ]
    second_click.stop.assert_called_once_with()


def test_single_click_after_double_click_posts_selected(monkeypatch):
    panel = _make_panel(monkeypatch)
    monkeypatch.setattr(
        playlist_sidebar.time,
        "monotonic",
        MagicMock(side_effect=[1.0, 1.2, 2.0]),
    )

    panel.on_click(_make_click())
    panel.on_list_view_selected(_make_selected())
    panel.on_click(_make_click())
    panel.on_list_view_selected(_make_selected())
    panel.on_click(_make_click())
    panel.on_list_view_selected(_make_selected())

    assert _posted_message_types(panel) == [
        LibraryPanel.ItemSelected,
        LibraryPanel.ItemDoubleClicked,
        LibraryPanel.ItemSelected,
    ]
