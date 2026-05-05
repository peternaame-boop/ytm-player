"""Tests for PlaylistPicker create-flow changes."""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock, patch

from ytm_player.ui.popups.playlist_picker import PlaylistPicker, _CreateNewItem


class TestOnCreateResult:
    def test_valid_result_creates_worker(self):
        picker = PlaylistPicker(video_ids=["vid1"])
        picker.run_worker = MagicMock()
        picker._create_and_add = MagicMock()

        picker._on_create_result(("My List", "A desc", "PUBLIC"))

        picker.run_worker.assert_called_once()
        args = picker.run_worker.call_args
        assert args[1]["name"] == "create_playlist"

    def test_none_result_does_nothing(self):
        picker = PlaylistPicker(video_ids=["vid1"])
        picker.run_worker = MagicMock()

        picker._on_create_result(None)

        picker.run_worker.assert_not_called()


class TestCreateNewItemSelected:
    def test_pushes_create_playlist_popup(self):
        picker = PlaylistPicker(video_ids=["vid1"])
        mock_app = MagicMock()
        with patch.object(
            type(picker), "app", new_callable=PropertyMock, return_value=mock_app
        ):
            event = MagicMock()
            event.item = _CreateNewItem()

            picker.on_list_view_selected(event)

            mock_app.push_screen.assert_called_once()
            call_args = mock_app.push_screen.call_args[0]
            from ytm_player.ui.popups.create_playlist_popup import CreatePlaylistPopup

            assert isinstance(call_args[0], CreatePlaylistPopup)
            assert call_args[1].__name__ == "_on_create_result"
            assert call_args[1].__self__ is picker


class TestCreateAndAddSignature:
    def test_default_description_and_privacy(self):
        """Verify the default values for description and privacy."""
        import inspect

        sig = inspect.signature(PlaylistPicker._create_and_add)
        assert sig.parameters["description"].default == ""
        assert sig.parameters["privacy"].default == "PRIVATE"
