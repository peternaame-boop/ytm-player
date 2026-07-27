"""OAuth device-flow login popup.

Walks the user through Google's OAuth device flow: request a code, show the
verification URL and user code, poll until the user finishes the browser
step. Mirrors SpotifyImportPopup's credential-entry-then-worker-driven-
progress pattern, minus the multi-mode complexity Spotify import needs.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Static

from ytm_player.services.auth import AuthManager, OAuthSlowDownError
from ytm_player.ui.popups.base import BasePopup
from ytm_player.ui.theme import get_theme
from ytm_player.utils.formatting import copy_to_clipboard

logger = logging.getLogger(__name__)


class OAuthSetupPopup(BasePopup[bool]):
    """Modal walking the user through YouTube Music's OAuth device flow.

    Returns True on successful sign-in, False (the default cancel value)
    if dismissed without completing it.
    """

    _CANCEL_RESULT = False

    DEFAULT_CSS = """
    OAuthSetupPopup {
        height: 100%;
    }

    OAuthSetupPopup > Vertical {
        width: 64;
        height: auto;
    }

    OAuthSetupPopup #oauth-title {
        text-align: center;
        text-style: bold;
        width: 100%;
        margin-bottom: 1;
        color: $text;
    }

    OAuthSetupPopup #oauth-status {
        width: 100%;
        margin-bottom: 1;
    }

    OAuthSetupPopup Input {
        width: 100%;
        margin-bottom: 1;
    }

    OAuthSetupPopup #oauth-code {
        text-align: center;
        text-style: bold;
        width: 100%;
        margin-bottom: 1;
    }

    OAuthSetupPopup #oauth-button-row {
        height: auto;
        align: center middle;
        margin-top: 1;
    }

    OAuthSetupPopup Button {
        width: 1fr;
        margin: 0 1;
    }
    """

    def __init__(self, auth: AuthManager | None = None) -> None:
        super().__init__()
        self._auth = auth or AuthManager()
        self._phase: str = "creds"
        self._last_client_id: str = ""
        self._last_client_secret: str = ""

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Sign in with Google (OAuth)", id="oauth-title")
            yield Static(
                'Requires a free Google Cloud OAuth client ("TVs and Limited Input '
                'Devices" type). See docs/oauth-login.md for setup steps.',
                id="oauth-status",
            )
            yield Input(placeholder="Client ID", id="oauth-client-id")
            yield Input(placeholder="Client Secret", id="oauth-client-secret", password=True)
            yield Static("", id="oauth-code")
            with Horizontal(id="oauth-button-row"):
                yield Button("Cancel", variant="default", id="oauth-cancel-btn")
                yield Button("Continue", variant="primary", id="oauth-continue-btn")

    def on_mount(self) -> None:
        saved = self._auth.load_oauth_creds()
        if saved:
            self._start_device_flow(saved["client_id"], saved["client_secret"])
        else:
            self.query_one("#oauth-client-id", Input).focus()

    # ── Input handling ───────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self._phase == "creds":
            self._submit_creds()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "oauth-cancel-btn":
            self.action_cancel()
        elif event.button.id == "oauth-continue-btn" and self._phase == "creds":
            self._submit_creds()

    def _submit_creds(self) -> None:
        client_id = self.query_one("#oauth-client-id", Input).value.strip()
        client_secret = self.query_one("#oauth-client-secret", Input).value.strip()
        if not client_id or not client_secret:
            self.notify("Both Client ID and Client Secret are required", severity="warning")
            return
        self._start_device_flow(client_id, client_secret)

    # ── Device flow ──────────────────────────────────────────────────

    def _start_device_flow(self, client_id: str, client_secret: str) -> None:
        self._phase = "starting"
        self._last_client_id = client_id
        self._last_client_secret = client_secret
        self.query_one("#oauth-client-id", Input).display = False
        self.query_one("#oauth-client-secret", Input).display = False
        self.query_one("#oauth-continue-btn").display = False
        self.query_one("#oauth-status", Static).update("Requesting a login code from Google...")
        self.run_worker(
            self._do_device_flow(client_id, client_secret), name="oauth_login", exclusive=True
        )

    async def _do_device_flow(self, client_id: str, client_secret: str) -> None:
        status = self.query_one("#oauth-status", Static)
        code_display = self.query_one("#oauth-code", Static)
        error_color = get_theme().error

        try:
            credentials, code = await asyncio.to_thread(
                self._auth.oauth_start, client_id, client_secret
            )
        except Exception as exc:
            logger.exception("Failed to start OAuth device flow")
            status.update(f"[{error_color}]Could not reach Google:[/{error_color}] {exc}")
            self._show_retry()
            return

        # AuthCodeDict marks every field optional in its declared type (a
        # generic JSON response shape), but get_code() only returns
        # normally on success, where all of these are always present.
        code_data = cast("dict[str, Any]", code)
        verification_url = code_data["verification_url"]
        user_code = code_data["user_code"]
        login_url = f"{verification_url}?user_code={user_code}"

        if copy_to_clipboard(login_url):
            status.update(
                "Link copied to your clipboard. Open it, sign in, and approve access.\n"
                f"Or go to [bold]{verification_url}[/bold] and enter the code below."
            )
        else:
            status.update(
                f"Go to [bold]{verification_url}[/bold], sign in, and enter the code below."
            )
        code_display.update(f"[bold]{user_code}[/bold]")

        self._phase = "polling"
        interval = code_data.get("interval", 5)
        expires_in = code_data.get("expires_in", 1800)
        device_code = code_data["device_code"]
        elapsed = 0

        while elapsed < expires_in:
            sleep_for = min(interval, expires_in - elapsed)
            await asyncio.sleep(sleep_for)
            elapsed += sleep_for
            try:
                token = await asyncio.to_thread(self._auth.oauth_poll, credentials, device_code)
            except OAuthSlowDownError:
                # RFC 8628 §3.5: back off by increasing the interval, keep polling.
                interval += 5
                continue
            except Exception as exc:
                logger.exception("OAuth device flow failed")
                status.update(f"[{error_color}]Sign-in failed:[/{error_color}] {exc}")
                code_display.update("")
                self._show_retry()
                return

            if token is not None:
                if self._auth.save_oauth(client_id, client_secret, token):
                    self._phase = "success"
                    self.notify("Signed in to YouTube Music via OAuth", timeout=4)
                    self.dismiss(True)
                else:
                    status.update(
                        f"[{error_color}]Signed in, but saving credentials failed "
                        f"locally. Check the log for details.[/{error_color}]"
                    )
                    code_display.update("")
                    self._show_retry()
                return

        status.update(f"[{error_color}]Code expired.[/{error_color}] Try again.")
        code_display.update("")
        self._show_retry()

    def _show_retry(self) -> None:
        self._phase = "creds"
        id_input = self.query_one("#oauth-client-id", Input)
        secret_input = self.query_one("#oauth-client-secret", Input)
        id_input.value = self._last_client_id
        secret_input.value = self._last_client_secret
        id_input.display = True
        secret_input.display = True
        self.query_one("#oauth-continue-btn").display = True
        id_input.focus()

    # ── Cancel ───────────────────────────────────────────────────────

    def action_cancel(self) -> None:
        """Cancel any in-flight device-flow worker and dismiss the popup.

        Overrides BasePopup.action_cancel so both Escape and a backdrop
        click stop the polling loop instead of leaving it running.
        """
        for worker in self.workers:
            worker.cancel()
        self.dismiss(self._CANCEL_RESULT)
