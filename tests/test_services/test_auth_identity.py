"""Automatic session renewal is tied to the account recorded at setup.

`ytm setup` records the chosen account's channel ID in account.json, bound to
the exact auth.json it describes. Automatic renewal (try_auto_refresh, and the
non-interactive cookies-file path) may only replace the session with the SAME
channel ID — found in the saved browser slot or, after a browser re-order, in
exactly one other slot. Anything else refuses, so a pending write (like,
playlist edit, history sync) is never retried as another account.
"""

from __future__ import annotations

import hashlib
import json
from http.cookiejar import Cookie
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from ytmusicapi.exceptions import YTMusicServerError

from tests.conftest import make_ytmusic_service
from ytm_player.services.auth import AuthManager, _channel_id_from_account_menu

ME_ID = "UCa1b2c3d4e5f6g7h8i9j0k1"
OTHER_ID = "UCz9y8x7w6v5u4t3s2r1q0p9"
EXPIRED = YTMusicServerError("Server returned HTTP 401: Unauthorized.\n")
ME_INFO = {"accountName": "WootBoop", "channelHandle": "@wootboop", "accountPhotoUrl": "p"}
OTHER_INFO = {"accountName": "Other Person", "channelHandle": "@other", "accountPhotoUrl": "q"}


# ── Sanitized account/account_menu fixtures (structure of a real response) ──


def channel_link(browse_id: str, *, page_type: str = "MUSIC_PAGE_TYPE_USER_CHANNEL") -> dict:
    return {
        "compactLinkRenderer": {
            "icon": {"iconType": "ACCOUNT_BOX"},
            "title": {"runs": [{"text": "Your channel"}]},
            "navigationEndpoint": {
                "browseEndpoint": {
                    "browseId": browse_id,
                    "canonicalBaseUrl": "/@wootboop",
                    "browseEndpointContextSupportedConfigs": {
                        "browseEndpointContextMusicConfig": {"pageType": page_type}
                    },
                }
            },
        }
    }


def account_menu(*links: dict, header: bool = True) -> dict:
    items = [
        *links,
        {
            "compactLinkRenderer": {
                "icon": {"iconType": "MONETIZATION_ON"},
                "title": {"runs": [{"text": "Paid memberships"}]},
                "navigationEndpoint": {
                    "browseEndpoint": {"browseId": "FEmemberships_and_purchases"}
                },
            }
        },
        {
            "compactLinkRenderer": {
                "icon": {"iconType": "SWITCH_ACCOUNTS"},
                "title": {"runs": [{"text": "Switch account"}]},
                "navigationEndpoint": None,
            }
        },
        {
            "compactLinkRenderer": {
                "icon": {"iconType": "EXIT_TO_APP"},
                "title": {"runs": [{"text": "Sign out"}]},
                "navigationEndpoint": {"signOutEndpoint": {"hack": True}},
            }
        },
    ]
    menu_header = (
        {
            "activeAccountHeaderRenderer": {
                "accountName": {"runs": [{"text": "WootBoop"}]},
                "channelHandle": {"runs": [{"text": "@wootboop"}]},
                "accountPhoto": {"thumbnails": [{"url": "https://example.invalid/p"}]},
            }
        }
        if header
        else {}
    )
    return {
        "actions": [
            {
                "openPopupAction": {
                    "popup": {
                        "multiPageMenuRenderer": {
                            "header": menu_header,
                            "sections": [
                                {"multiPageMenuSectionRenderer": {"items": items}},
                                {"multiPageMenuSectionRenderer": {"items": []}},
                            ],
                        }
                    }
                }
            }
        ]
    }


ME = (ME_INFO, account_menu(channel_link(ME_ID)))
OTHER = (OTHER_INFO, account_menu(channel_link(OTHER_ID)))


class TestChannelIdParser:
    def test_real_shape(self):
        assert _channel_id_from_account_menu(account_menu(channel_link(ME_ID))) == ME_ID

    @pytest.mark.parametrize(
        "response",
        [
            None,
            {},
            {"actions": "nope"},
            {"actions": []},
            {"actions": [{"openPopupAction": {"popup": {"multiPageMenuRenderer": "x"}}}]},
            MagicMock(),
        ],
    )
    def test_malformed_response_is_none(self, response):
        assert _channel_id_from_account_menu(response) is None

    def test_missing_active_account_header_is_none(self):
        assert (
            _channel_id_from_account_menu(account_menu(channel_link(ME_ID), header=False)) is None
        )

    def test_no_channel_link_is_none(self):
        assert _channel_id_from_account_menu(account_menu()) is None

    def test_two_channel_links_are_ambiguous(self):
        menu = account_menu(channel_link(ME_ID), channel_link(OTHER_ID))
        assert _channel_id_from_account_menu(menu) is None

    def test_wrong_page_type_is_none(self):
        menu = account_menu(channel_link(ME_ID, page_type="MUSIC_PAGE_TYPE_ARTIST"))
        assert _channel_id_from_account_menu(menu) is None

    @pytest.mark.parametrize("bad_id", ["FEmusic_history", "UCshort", "UC" + "x" * 23, ""])
    def test_non_channel_ids_are_none(self, bad_id):
        assert _channel_id_from_account_menu(account_menu(channel_link(bad_id))) is None

    def test_account_box_without_endpoint_is_none(self):
        link = channel_link(ME_ID)
        link["compactLinkRenderer"]["navigationEndpoint"] = None
        assert _channel_id_from_account_menu(account_menu(link)) is None


# ── Harness ─────────────────────────────────────────────────────────────────


def _cookie() -> Cookie:
    return Cookie(
        0,
        "__Secure-3PAPISID",
        "synthetic",
        None,
        False,
        ".youtube.com",
        True,
        True,
        "/",
        True,
        True,
        None,
        False,
        None,
        None,
        {},
    )


def _fake_ytmusic(slots: dict[int, Any], calls: dict[int, MagicMock] | None = None):
    """YTMusic stand-in: the probe result depends on the file's x-goog-authuser."""

    def factory(auth, user=None):
        headers = auth if isinstance(auth, dict) else json.loads(Path(auth).read_text("utf-8"))
        slot = int(headers["x-goog-authuser"])
        client = MagicMock(name=f"client-slot-{slot}")
        spec = slots.get(slot, EXPIRED)
        if isinstance(spec, Exception):
            client.get_account_info.side_effect = spec
            client.rate_song.side_effect = spec
        else:
            info, menu = spec
            client.get_account_info.return_value = info
            client._send_request.return_value = menu
            client.rate_song.return_value = {"status": "ok"}
        if calls is not None:
            calls[slot] = client
        return client

    return factory


def _record(auth: AuthManager, slot: str, channel_id: str | None, auth_bytes: bytes | None = None):
    payload = auth_bytes if auth_bytes is not None else auth.auth_file.read_bytes()
    auth._account_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "x-goog-authuser": slot,
                "channel_id": channel_id,
                "name": "WootBoop",
                "handle": "@wootboop",
                "auth_sha256": hashlib.sha256(payload).hexdigest(),
            }
        ),
        encoding="utf-8",
    )


def _auth(tmp_path: Path, slot: str = "2", *, record: str | None = ME_ID) -> AuthManager:
    auth_file = tmp_path / "auth.json"
    auth_file.write_bytes(json.dumps({"cookie": "SAPISID=x", "x-goog-authuser": slot}).encode())
    auth = AuthManager(
        config_dir=tmp_path, auth_file=auth_file, stream_cookies_file=tmp_path / "jar.txt"
    )
    if record is not None:
        _record(auth, slot, record)
    return auth


def _saved_slot(auth: AuthManager) -> str:
    return json.loads(auth.auth_file.read_text(encoding="utf-8"))["x-goog-authuser"]


class _Expired:
    """Client whose session has expired; a real class so bound methods carry __name__."""

    def rate_song(self, *args, **kwargs):
        raise EXPIRED


def _service_for(auth: AuthManager, channel_id: str | None):
    """A service whose current client (account *channel_id*) has an expired session."""
    expired = _Expired()
    service = make_ytmusic_service(_ytm=expired, _auth_manager=auth)
    if channel_id is not None:
        service._client_identities[expired] = channel_id
    return service


def _switch_session_to(auth: AuthManager, slot: str, channel_id: str) -> None:
    """What `ytm setup` in another process does: new auth.json + matching record."""
    auth.auth_file.write_bytes(
        json.dumps({"cookie": "SAPISID=new", "x-goog-authuser": slot}).encode()
    )
    _record(auth, slot, channel_id)


@pytest.fixture
def browser(monkeypatch):
    monkeypatch.setattr(
        AuthManager, "_detect_browser", staticmethod(lambda: ("brave", [_cookie()], [_cookie()]))
    )
    monkeypatch.setattr("ytm_player.services.auth.sapisid_from_cookie", lambda s: "sapisid")
    monkeypatch.setattr("ytm_player.services.auth.get_authorization", lambda s: "SAPISIDHASH x")


# ── Silent renewal ──────────────────────────────────────────────────────────


class TestSilentRenewal:
    def test_saved_slot_dead_other_account_valid_refuses(self, tmp_path, browser, monkeypatch):
        auth = _auth(tmp_path, "2")
        before = auth._account_file.read_bytes()
        monkeypatch.setattr("ytm_player.services.auth.YTMusic", _fake_ytmusic({0: OTHER}))

        assert (auth.try_auto_refresh(), _saved_slot(auth)) == (False, "2")
        assert auth._account_file.read_bytes() == before

    def test_same_slot_now_different_account_refuses(self, tmp_path, browser, monkeypatch):
        auth = _auth(tmp_path, "0")
        monkeypatch.setattr("ytm_player.services.auth.YTMusic", _fake_ytmusic({0: OTHER}))

        assert auth.try_auto_refresh() is False
        assert _saved_slot(auth) == "0"

    async def test_mutation_not_retried_against_another_account(
        self, tmp_path, browser, monkeypatch
    ):
        auth = _auth(tmp_path, "2")
        calls: dict[int, MagicMock] = {}
        monkeypatch.setattr("ytm_player.services.auth.YTMusic", _fake_ytmusic({0: OTHER}, calls))
        service = _service_for(auth, ME_ID)

        assert await service.rate_song("vid", "LIKE") == "auth_expired"
        assert not any(c.rate_song.called for c in calls.values())

    async def test_same_identity_refresh_recovers_and_retries(self, tmp_path, browser, monkeypatch):
        auth = _auth(tmp_path, "2")
        calls: dict[int, MagicMock] = {}
        monkeypatch.setattr("ytm_player.services.auth.YTMusic", _fake_ytmusic({2: ME}, calls))
        service = _service_for(auth, ME_ID)

        assert await service.rate_song("vid", "LIKE") == "success"
        assert _saved_slot(auth) == "2"
        assert calls[2].rate_song.called
        # The record was rewritten for the new auth.json bytes.
        recorded = auth._load_recorded_identity()
        assert recorded is not None and (recorded.slot, recorded.channel_id) == (2, ME_ID)

    def test_matching_name_and_handle_but_different_id_refuses(
        self, tmp_path, browser, monkeypatch
    ):
        auth = _auth(tmp_path, "2")
        lookalike = (ME_INFO, account_menu(channel_link(OTHER_ID)))
        monkeypatch.setattr("ytm_player.services.auth.YTMusic", _fake_ytmusic({2: lookalike}))

        assert auth.try_auto_refresh() is False
        assert _saved_slot(auth) == "2"

    def test_probe_without_channel_id_refuses(self, tmp_path, browser, monkeypatch):
        auth = _auth(tmp_path, "2")
        no_channel = (ME_INFO, account_menu())
        monkeypatch.setattr("ytm_player.services.auth.YTMusic", _fake_ytmusic({2: no_channel}))

        assert auth.try_auto_refresh() is False

    def test_record_without_channel_id_refuses_before_probing(self, tmp_path, browser, monkeypatch):
        auth = _auth(tmp_path, "2", record=None)
        _record(auth, "2", None)
        calls: dict[int, MagicMock] = {}
        monkeypatch.setattr("ytm_player.services.auth.YTMusic", _fake_ytmusic({2: ME}, calls))

        assert auth.try_auto_refresh() is False
        assert calls == {}

    def test_legacy_session_without_record_refuses_without_probing(
        self, tmp_path, browser, monkeypatch, caplog
    ):
        auth = _auth(tmp_path, "2", record=None)
        calls: dict[int, MagicMock] = {}
        monkeypatch.setattr("ytm_player.services.auth.YTMusic", _fake_ytmusic({2: ME}, calls))

        with caplog.at_level("WARNING", logger="ytm_player.services.auth"):
            assert auth.try_auto_refresh() is False
        assert calls == {}
        assert _saved_slot(auth) == "2"
        assert "Run `ytm setup` once" in caplog.text

    def test_reordered_slot_recovers_by_exact_identity(self, tmp_path, browser, monkeypatch):
        auth = _auth(tmp_path, "2")
        monkeypatch.setattr("ytm_player.services.auth.YTMusic", _fake_ytmusic({2: OTHER, 0: ME}))

        assert auth.try_auto_refresh() is True
        assert _saved_slot(auth) == "0"
        recorded = auth._load_recorded_identity()
        assert recorded is not None and (recorded.slot, recorded.channel_id) == (0, ME_ID)

    def test_ambiguous_identity_match_refuses(self, tmp_path, browser, monkeypatch):
        auth = _auth(tmp_path, "2")
        monkeypatch.setattr(
            "ytm_player.services.auth.YTMusic", _fake_ytmusic({2: OTHER, 0: ME, 1: ME})
        )

        assert auth.try_auto_refresh() is False
        assert _saved_slot(auth) == "2"

    def test_stale_record_hash_refuses_without_probing(self, tmp_path, browser, monkeypatch):
        auth = _auth(tmp_path, "2", record=None)
        _record(auth, "2", ME_ID, auth_bytes=b"some other auth.json content")
        calls: dict[int, MagicMock] = {}
        monkeypatch.setattr("ytm_player.services.auth.YTMusic", _fake_ytmusic({2: ME}, calls))

        assert auth.try_auto_refresh() is False
        assert calls == {}

    @pytest.mark.parametrize(
        "content",
        ["not json", "[]", json.dumps({"schema_version": 2}), json.dumps({"schema_version": 1})],
    )
    def test_malformed_record_refuses(self, tmp_path, browser, monkeypatch, content):
        auth = _auth(tmp_path, "2", record=None)
        auth._account_file.write_text(content, encoding="utf-8")
        monkeypatch.setattr("ytm_player.services.auth.YTMusic", _fake_ytmusic({2: ME}))

        assert auth.try_auto_refresh() is False


# ── A pending call is bound to the account of the client it failed on ───────


class TestPendingCallAcrossAccountSwitch:
    async def test_setup_switching_accounts_while_a_call_is_pending_does_not_replay_it(
        self, tmp_path, browser, monkeypatch
    ):
        auth = _auth(tmp_path, "0")  # session + record: account A
        service = _service_for(auth, ME_ID)  # the client the like is sent from
        # `ytm setup` for account B lands while A's call is still pending.
        _switch_session_to(auth, "1", OTHER_ID)
        calls: dict[int, MagicMock] = {}
        monkeypatch.setattr("ytm_player.services.auth.YTMusic", _fake_ytmusic({1: OTHER}, calls))

        assert await service.rate_song("vid", "LIKE") == "auth_expired"
        assert not any(c.rate_song.called for c in calls.values())
        assert _saved_slot(auth) == "1"  # B's setup is left alone

    async def test_switch_between_renewal_and_client_rebuild_is_not_replayed(
        self, tmp_path, browser, monkeypatch
    ):
        auth = _auth(tmp_path, "0")
        service = _service_for(auth, ME_ID)
        calls: dict[int, MagicMock] = {}
        monkeypatch.setattr(
            "ytm_player.services.auth.YTMusic", _fake_ytmusic({0: ME, 1: OTHER}, calls)
        )
        real_refresh = auth.try_auto_refresh

        def _refresh_then_setup_switches(expected_channel_id=None):
            assert real_refresh(expected_channel_id) is True  # A renews fine...
            _switch_session_to(auth, "1", OTHER_ID)  # ...then setup switches to B
            return True

        monkeypatch.setattr(auth, "try_auto_refresh", _refresh_then_setup_switches)

        assert await service.rate_song("vid", "LIKE") == "auth_expired"
        assert not any(c.rate_song.called for c in calls.values())

    async def test_current_client_of_another_account_is_not_used_for_the_retry(
        self, tmp_path, browser, monkeypatch
    ):
        """Another caller already rebuilt the client — for account B."""
        auth = _auth(tmp_path, "0")
        expired_a = _Expired()
        client_b = MagicMock(name="client-b")
        service = make_ytmusic_service(_ytm=client_b, _auth_manager=auth)
        service._client_identities[expired_a] = ME_ID
        service._client_identities[client_b] = OTHER_ID
        refresh = MagicMock(return_value=True)
        monkeypatch.setattr(auth, "try_auto_refresh", refresh)

        with pytest.raises(YTMusicServerError):
            await service._call(expired_a.rate_song, "vid", "LIKE")
        client_b.rate_song.assert_not_called()
        refresh.assert_not_called()

    async def test_client_without_recorded_identity_is_not_renewed(
        self, tmp_path, browser, monkeypatch
    ):
        auth = _auth(tmp_path, "0")
        service = _service_for(auth, None)
        refresh = MagicMock(return_value=True)
        monkeypatch.setattr(auth, "try_auto_refresh", refresh)

        assert await service.rate_song("vid", "LIKE") == "auth_expired"
        refresh.assert_not_called()

    def test_bound_client_carries_the_identity_of_the_bytes_it_was_built_from(
        self, tmp_path, browser, monkeypatch
    ):
        auth = _auth(tmp_path, "0")
        monkeypatch.setattr("ytm_player.services.auth.YTMusic", _fake_ytmusic({0: ME}))

        client, channel_id = auth.create_bound_client()

        assert channel_id == ME_ID
        assert client.get_account_info() == ME_INFO

    def test_rollback_during_construction_cannot_tag_b_client_with_a_identity(
        self, tmp_path, browser, monkeypatch
    ):
        """A -> B -> A while the client is being built: `ytm setup` for B
        lands, then a failed setup rolls the files back to A. The client's
        credentials and the returned identity must both come from the same
        snapshot, whatever the files do in between."""
        auth = _auth(tmp_path, "0")  # A
        a_auth = auth.auth_file.read_bytes()
        a_record = auth._account_file.read_bytes()
        built_from: list[dict] = []

        def _factory(headers, user=None):
            built_from.append(headers)
            _switch_session_to(auth, "1", OTHER_ID)  # B lands mid-construction...
            auth.auth_file.write_bytes(a_auth)  # ...and is rolled back to A
            auth._account_file.write_bytes(a_record)
            return MagicMock(name="client")

        monkeypatch.setattr("ytm_player.services.auth.YTMusic", _factory)

        _client, channel_id = auth.create_bound_client()

        assert built_from == [json.loads(a_auth)]  # built from A's snapshot, not the path
        assert channel_id == ME_ID

    def test_bound_client_without_record_has_no_identity(self, tmp_path, browser, monkeypatch):
        auth = _auth(tmp_path, "0", record=None)
        monkeypatch.setattr("ytm_player.services.auth.YTMusic", _fake_ytmusic({0: ME}))

        _client, channel_id = auth.create_bound_client()

        assert channel_id is None


# ── Setup paths record the identity ─────────────────────────────────────────


class TestSetupRecordsIdentity:
    def test_interactive_setup_records_identity(self, tmp_path, browser, monkeypatch):
        auth = _auth(tmp_path, "0", record=None)
        monkeypatch.setattr("ytm_player.services.auth.YTMusic", _fake_ytmusic({1: ME}))

        assert auth._save_youtube_cookies([_cookie()], interactive=True) is True
        assert _saved_slot(auth) == "1"
        recorded = auth._load_recorded_identity()
        assert recorded is not None and (recorded.slot, recorded.channel_id) == (1, ME_ID)
        assert not auth._account_file.read_text().startswith("\n")
        assert json.loads(auth._account_file.read_text())["name"] == "WootBoop"

    def test_interactive_setup_without_channel_id_disables_renewal(
        self, tmp_path, browser, monkeypatch, capsys
    ):
        auth = _auth(tmp_path, "0", record=None)
        no_channel = (ME_INFO, account_menu())
        monkeypatch.setattr("ytm_player.services.auth.YTMusic", _fake_ytmusic({0: no_channel}))

        assert auth._save_youtube_cookies([_cookie()], interactive=True) is True
        assert "automatic session renewal is not available" in capsys.readouterr().out
        assert auth.try_auto_refresh() is False

    def test_account_file_write_failure_keeps_session_but_disables_renewal(
        self, tmp_path, browser, monkeypatch, caplog
    ):
        auth = _auth(tmp_path, "0")
        monkeypatch.setattr("ytm_player.services.auth.YTMusic", _fake_ytmusic({0: ME}))

        with (
            patch("ytm_player.services.auth._atomic_write", side_effect=OSError("disk full")),
            caplog.at_level("ERROR", logger="ytm_player.services.auth"),
        ):
            assert auth._save_youtube_cookies([_cookie()], interactive=True) is True
        assert _saved_slot(auth) == "0"
        assert not auth._account_file.exists()
        assert "automatic session renewal is disabled" in caplog.text
        assert auth.try_auto_refresh() is False

    def test_auth_write_failure_removes_old_record(self, tmp_path, browser, monkeypatch):
        auth = _auth(tmp_path, "2")
        monkeypatch.setattr("ytm_player.services.auth.YTMusic", _fake_ytmusic({2: ME}))
        real_open = auth._auth_file.__class__  # noqa: F841 (documentation)

        def _fail_auth_open(path, *args, **kwargs):
            if str(path) == str(auth._auth_file):
                raise OSError("read-only")
            return real_os_open(path, *args, **kwargs)

        import os

        real_os_open = os.open
        with patch("ytm_player.services.auth.os.open", side_effect=_fail_auth_open):
            assert auth.try_auto_refresh() is False
        assert not auth._account_file.exists()

    def test_manual_setup_replaces_previous_record(self, tmp_path, browser, monkeypatch):
        auth = _auth(tmp_path, "0", record=OTHER_ID)
        responses = iter(["Host: music.youtube.com", "Cookie: SAPISID=abc123", ""])
        monkeypatch.setattr("builtins.input", lambda: next(responses))

        def _fake_setup(filepath, headers_raw):
            Path(filepath).write_text(
                '{"cookie": "SAPISID=abc123", "x-goog-authuser": "0"}', encoding="utf-8"
            )

        monkeypatch.setattr("ytm_player.services.auth.YTMusic", _fake_ytmusic({0: ME}))
        with patch("ytmusicapi.setup", side_effect=_fake_setup):
            assert auth.setup_interactive(manual=True) is True

        recorded = auth._load_recorded_identity()
        assert recorded is not None and recorded.channel_id == ME_ID

    def test_manual_setup_probe_failure_leaves_no_record(
        self, tmp_path, browser, monkeypatch, capsys
    ):
        auth = _auth(tmp_path, "0", record=OTHER_ID)
        responses = iter(["Host: music.youtube.com", "Cookie: SAPISID=abc123", ""])
        monkeypatch.setattr("builtins.input", lambda: next(responses))

        def _fake_setup(filepath, headers_raw):
            Path(filepath).write_text('{"cookie": "SAPISID=abc123"}', encoding="utf-8")

        monkeypatch.setattr("ytm_player.services.auth.YTMusic", _fake_ytmusic({}))
        with patch("ytmusicapi.setup", side_effect=_fake_setup):
            assert auth.setup_interactive(manual=True) is True

        assert not auth._account_file.exists()
        assert "automatic session renewal is not available" in capsys.readouterr().out
        assert auth.try_auto_refresh() is False

    def test_cookies_file_refresh_restores_record_on_validate_failure(
        self, tmp_path, browser, monkeypatch
    ):
        auth = _auth(tmp_path, "0")
        old_auth = auth.auth_file.read_bytes()
        old_record = auth._account_file.read_bytes()
        cookies_file = tmp_path / "cookies.txt"
        cookies_file.write_text(
            "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t2147483647\tSAPISID\tabc\n"
        )
        monkeypatch.setattr("ytm_player.services.auth.YTMusic", _fake_ytmusic({0: ME}))
        monkeypatch.setattr(auth, "validate", lambda: False)

        assert auth._refresh_from_cookies_file(cookies_file, interactive=True) is False
        assert auth.auth_file.read_bytes() == old_auth
        assert auth._account_file.read_bytes() == old_record
