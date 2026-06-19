"""Tests for CreatePlaylistPopup init and submit logic."""

from __future__ import annotations

from unittest.mock import MagicMock

from ytm_player.ui.popups.create_playlist_popup import PRIVACY_OPTIONS, CreatePlaylistPopup


class TestInit:
    def test_defaults(self):
        popup = CreatePlaylistPopup()
        assert popup._initial_name == ""
        assert popup._initial_description == ""
        assert popup._initial_privacy == "PRIVATE"
        assert popup._edit_mode is False

    def test_edit_mode(self):
        popup = CreatePlaylistPopup(edit_mode=True)
        assert popup._edit_mode is True

    def test_prefills(self):
        popup = CreatePlaylistPopup(
            initial_name="My List",
            initial_description="A description",
            initial_privacy="PUBLIC",
        )
        assert popup._initial_name == "My List"
        assert popup._initial_description == "A description"
        assert popup._initial_privacy == "PUBLIC"

    def test_privacy_options_constant(self):
        assert PRIVACY_OPTIONS == [
            ("Private", "PRIVATE"),
            ("Public", "PUBLIC"),
            ("Unlisted", "UNLISTED"),
        ]


class TestSubmit:
    def test_submit_dismisses_with_tuple_when_name_present(self):
        popup = CreatePlaylistPopup()
        dismissed = []

        def fake_dismiss(value):
            dismissed.append(value)

        popup.dismiss = fake_dismiss

        popup.query_one = MagicMock(
            side_effect=lambda selector, _type=None: {
                "#input-name": MagicMock(value="  My Playlist  "),
                "#input-description": MagicMock(value="  Desc  "),
                "#select-privacy": MagicMock(value="PUBLIC"),
            }[selector]
        )

        popup._submit()
        assert dismissed == [("My Playlist", "Desc", "PUBLIC")]

    def test_submit_warns_and_does_not_dismiss_when_name_empty(self):
        popup = CreatePlaylistPopup()
        dismissed = []

        def fake_dismiss(value):
            dismissed.append(value)

        popup.dismiss = fake_dismiss
        popup.notify = MagicMock()

        name_input = MagicMock(value="   ")
        popup.query_one = MagicMock(
            side_effect=lambda selector, _type=None: {
                "#input-name": name_input,
                "#input-description": MagicMock(value=""),
                "#select-privacy": MagicMock(value="PRIVATE"),
            }[selector]
        )

        popup._submit()
        assert dismissed == []
        popup.notify.assert_called_once()
        assert popup.notify.call_args.kwargs.get("severity") == "warning"
        name_input.focus.assert_called_once()

    def test_submit_strips_whitespace(self):
        popup = CreatePlaylistPopup()
        dismissed = []

        def fake_dismiss(value):
            dismissed.append(value)

        popup.dismiss = fake_dismiss

        popup.query_one = MagicMock(
            side_effect=lambda selector, _type=None: {
                "#input-name": MagicMock(value="\t Playlist \n"),
                "#input-description": MagicMock(value="\n Description \t"),
                "#select-privacy": MagicMock(value="UNLISTED"),
            }[selector]
        )

        popup._submit()
        assert dismissed == [("Playlist", "Description", "UNLISTED")]


class TestButtonPressed:
    def test_cancel_button_dismisses_none(self):
        popup = CreatePlaylistPopup()
        dismissed = []

        def fake_dismiss(value):
            dismissed.append(value)

        popup.dismiss = fake_dismiss

        class FakeButton:
            id = "btn-cancel"

        class FakeEvent:
            button = FakeButton()

        popup.on_button_pressed(FakeEvent())
        assert dismissed == [None]

    def test_create_button_calls_submit(self):
        popup = CreatePlaylistPopup()
        submitted = []

        def fake_submit():
            submitted.append(True)

        popup._submit = fake_submit

        class FakeButton:
            id = "btn-create"

        class FakeEvent:
            button = FakeButton()

        popup.on_button_pressed(FakeEvent())
        assert submitted == [True]
