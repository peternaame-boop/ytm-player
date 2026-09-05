"""Authentication refresh feedback tests."""

from unittest.mock import AsyncMock, MagicMock

from ytm_player.app._app import YTMPlayerApp


async def test_refresh_notice_is_rendered_before_auth_work_resumes():
    events = []
    app = MagicMock()
    app.notify.side_effect = lambda *_args, **_kwargs: events.append("notify")
    app.wait_for_refresh = AsyncMock(side_effect=lambda: events.append("render"))

    await YTMPlayerApp._notify_auth_refresh(app)

    assert events == ["notify", "render"]
    app.wait_for_refresh.assert_awaited_once_with()
