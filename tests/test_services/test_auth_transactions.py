"""The sign-in files are updated as one guarded transaction.

auth.json is replaced atomically, account.json is written for exactly those
bytes, the stream cookiejar is published first and vouched for by hash, and
an automatic renewal only replaces the session it started from. Readers
never wait for the lock and never build a client from a half-replaced pair;
a crash between the writes leaves a whole, usable, unbound session rather
than an invented identity.

Cross-process behaviour lives in test_auth_lock_process.py; the resolver's
side of jar vouching in test_stream_resolver_internals.py.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import sys
import threading
from http.cookiejar import Cookie
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from ytmusicapi.exceptions import YTMusicServerError

from tests.conftest import make_ytmusic_service
from tests.test_services.test_auth_identity import account_menu, channel_link
from ytm_player.services import auth as auth_module
from ytm_player.services.auth import (
    AuthManager,
    SessionBusyError,
    SessionError,
    SessionLockTimeoutError,
    SessionUnreadableError,
    _atomic_write,
    _ProbedAccount,
    _SessionLock,
    _SessionOwner,
    read_vouched_stream_jar,
)

A_ID = "UC" + "a" * 22
B_ID = "UC" + "b" * 22
A = _ProbedAccount(slot=0, name="Synthetic A", handle="", channel_id=A_ID)
B = _ProbedAccount(slot=1, name="Synthetic B", handle="", channel_id=B_ID)
HEADERS_A = {"cookie": "SAPISID=a; __Secure-3PAPISID=a", "x-goog-authuser": "0"}
HEADERS_B = {"cookie": "SAPISID=b; __Secure-3PAPISID=b", "x-goog-authuser": "1"}
EXPIRED = YTMusicServerError("Server returned HTTP 401: Unauthorized.\n")


def _payload(headers: dict) -> bytes:
    return json.dumps(headers, ensure_ascii=True, indent=4, sort_keys=True).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cookie(value: str = "a", name: str = "SAPISID") -> Cookie:
    return Cookie(
        0,
        name,
        value,
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


def _mgr(tmp_path: Path) -> AuthManager:
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    return AuthManager(
        config_dir=cfg,
        auth_file=cfg / "auth.json",
        cookies_file=None,
        stream_cookies_file=cfg / "stream_cookies.txt",
        account_file=cfg / "account.json",
    )


def _commit(mgr: AuthManager, headers: dict, account: _ProbedAccount, jar=None, **kw) -> bool:
    if jar is None:
        jar = [_cookie(account.name[-1].lower())]
    return mgr._commit_session(_payload(headers), account, jar, **kw)


def _record(mgr: AuthManager) -> dict:
    return json.loads(mgr._account_file.read_text(encoding="utf-8"))


def _session(mgr: AuthManager) -> tuple[str, str]:
    return str(mgr.auth_file), str(mgr._account_file)


def _vouched(mgr: AuthManager) -> bytes | None:
    return read_vouched_stream_jar(str(mgr._stream_cookies_file), _session(mgr)).payload


def _temps(mgr: AuthManager) -> list[Path]:
    return sorted(mgr._config_dir.glob("*.tmp-*"))


def _files(mgr: AuthManager) -> dict[str, bytes]:
    """Every file in the config dir except the lock file (created on demand)."""
    return {p.name: p.read_bytes() for p in mgr._config_dir.iterdir() if p != mgr._lock_path}


class _ExpiredClient:
    """A client whose session has expired; a real class so bound methods carry
    __self__ (YTMusicService looks the failed client up by it)."""

    def get_account_info(self):
        raise EXPIRED

    def rate_song(self, *args, **kwargs):
        raise EXPIRED


def _fake_ytmusic(slots: dict[int, _ProbedAccount]):
    """YTMusic stand-in for the real probe: the account depends on the
    x-goog-authuser of the file (or dict) the client is built from."""

    def factory(auth, user=None):
        headers = auth if isinstance(auth, dict) else json.loads(Path(auth).read_text("utf-8"))
        slot = int(headers["x-goog-authuser"])
        account = slots.get(slot)
        if account is None:
            return _ExpiredClient()
        client = MagicMock(name=f"client-slot-{slot}")
        client.get_account_info.return_value = {
            "accountName": account.name,
            "channelHandle": account.handle,
        }
        client._send_request.return_value = account_menu(channel_link(account.channel_id or ""))
        return client

    return factory


def _paste(monkeypatch, headers: dict) -> None:
    """Drive _setup_manual with a paste that ytmusicapi turns into *headers*."""
    lines = iter(["cookie: whatever", ""])
    monkeypatch.setattr("builtins.input", lambda *a: next(lines))
    import ytmusicapi

    monkeypatch.setattr(ytmusicapi, "setup", lambda **kw: json.dumps(headers))


@contextlib.contextmanager
def _interrupted_after(writes: int):
    """Within the block, the (*writes* + 1)-th _atomic_write raises, i.e. the
    commit crashes after that many files were published. Yields the list of
    paths written so far. Scoped to its own MonkeyPatch so lifting the fault
    never lifts a test's other patches (browser/network isolation included)."""
    real = auth_module._atomic_write
    written: list[Path] = []

    def flaky(path, *args, **kwargs):
        if len(written) >= writes:
            raise OSError(errno.EIO, "interrupted")
        written.append(path)
        return real(path, *args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(auth_module, "_atomic_write", flaky)
        yield written


@pytest.fixture(autouse=True)
def _no_real_browser_or_network(monkeypatch):
    """Nothing in this module may reach a browser profile or the network:
    browser discovery finds nothing, extraction and client construction
    fail loudly. Tests that need them install their own fakes on top."""
    monkeypatch.setattr(AuthManager, "_detect_browser", staticmethod(lambda: None))

    def _no_extraction(browser):
        raise RuntimeError("browser isolation: extraction attempted in a test")

    def _no_network(*args, **kwargs):
        raise RuntimeError("network isolation: YTMusic constructed in a test")

    monkeypatch.setattr("ytm_player.services.auth._extract_browser_jar", _no_extraction)
    monkeypatch.setattr("ytm_player.services.auth.YTMusic", _no_network)


@pytest.fixture
def browser(monkeypatch):
    monkeypatch.setattr(
        AuthManager,
        "_detect_browser",
        staticmethod(lambda: ("brave", [_cookie("a")], [_cookie("a")])),
    )
    monkeypatch.setattr("ytm_player.services.auth.sapisid_from_cookie", lambda s: "sapisid")
    monkeypatch.setattr("ytm_player.services.auth.get_authorization", lambda s: "SAPISIDHASH x")


# ── Setup wins, and binds its identity to the bytes it probed ───────────────


class TestSetupBindsItsOwnBytes:
    def test_manual_setup_binds_the_probed_bytes_even_if_another_setup_lands_mid_probe(
        self, tmp_path, monkeypatch
    ):
        """F01: while A's paste is being probed, a setup for B lands. The
        record must describe A's bytes, and the client built afterwards must
        carry A's headers with A's identity — never B's headers tagged A."""
        mgr = _mgr(tmp_path)
        probed: list[bytes] = []

        def probe(payload, slot):
            probed.append(payload)
            assert _commit(mgr, HEADERS_B, B)  # `ytm setup` for B lands now
            return A

        monkeypatch.setattr(mgr, "_probe_payload", probe)
        _paste(monkeypatch, HEADERS_A)

        assert mgr._setup_manual() is True

        with patch("ytm_player.services.auth.YTMusic", side_effect=lambda h, **kw: h):
            client, identity = mgr.create_bound_client()
        assert (client, identity) == (HEADERS_A, A_ID)
        assert mgr.auth_file.read_bytes() == probed[0]  # committed == probed bytes
        assert _record(mgr)["auth_sha256"] == _sha(mgr.auth_file.read_bytes())
        assert _record(mgr)["verified"] is True

    def test_rejected_paste_leaves_the_working_session_and_its_record(self, tmp_path, monkeypatch):
        """A5: a paste ytmusicapi rejects changes nothing on disk."""
        mgr = _mgr(tmp_path)
        assert _commit(mgr, HEADERS_A, A)
        before = (mgr.auth_file.read_bytes(), mgr._account_file.read_bytes())
        lines = iter(["this is not a header block", ""])
        monkeypatch.setattr("builtins.input", lambda *a: next(lines))
        import ytmusicapi

        monkeypatch.setattr(
            ytmusicapi, "setup", lambda **kw: (_ for _ in ()).throw(Exception("bad headers"))
        )

        assert mgr._setup_manual() is False

        assert (mgr.auth_file.read_bytes(), mgr._account_file.read_bytes()) == before
        recorded = mgr._load_recorded_identity()
        assert recorded is not None and recorded.channel_id == A_ID


# ── Renewal only replaces the session it started from ───────────────────────


class TestRenewalOwnership:
    def test_setup_landing_during_the_probe_defeats_the_renewal(
        self, tmp_path, browser, monkeypatch, caplog
    ):
        """F02 (setup during refresh): B's setup lands while A's renewal is
        probing; the renewal refuses and B stays."""
        mgr = _mgr(tmp_path)
        assert _commit(mgr, HEADERS_A, A)

        def probe(payload, slot):
            assert _commit(mgr, HEADERS_B, B)
            return A

        monkeypatch.setattr(mgr, "_probe_payload", probe)

        with caplog.at_level("WARNING", logger="ytm_player.services.auth"):
            assert mgr.try_auto_refresh(A_ID) is False

        assert json.loads(mgr.auth_file.read_bytes()) == HEADERS_B
        assert _record(mgr)["channel_id"] == B_ID
        assert "changed while it was being renewed" in caplog.text

    def test_browser_fallback_refuses_after_setup_landed_during_the_cookies_file_source(
        self, tmp_path, browser, monkeypatch
    ):
        """F02 (fallback): the owner snapshot is taken once per attempt and
        carried into the browser fallback, which therefore refuses too."""
        cfg = tmp_path / "config"
        cfg.mkdir()
        mgr = AuthManager(
            config_dir=cfg,
            auth_file=cfg / "auth.json",
            cookies_file=str(tmp_path / "cookies.txt"),
            stream_cookies_file=cfg / "stream_cookies.txt",
        )
        assert _commit(mgr, HEADERS_A, A)
        reads = MagicMock(wraps=mgr._read_session_pair)
        monkeypatch.setattr(mgr, "_read_session_pair", reads)

        def cookies_source(*args, **kwargs):
            assert _commit(mgr, HEADERS_B, B)  # B lands while this source runs
            return False  # ...and the source itself fails → browser fallback

        monkeypatch.setattr(mgr, "_extract_and_save_from_cookies_file", cookies_source)
        monkeypatch.setattr(mgr, "_probe_payload", lambda payload, slot: A)

        assert mgr.try_auto_refresh(A_ID) is False

        assert json.loads(mgr.auth_file.read_bytes()) == HEADERS_B
        assert _record(mgr)["channel_id"] == B_ID
        assert reads.call_count == 1

    def test_setup_with_byte_identical_headers_still_defeats_the_renewal(
        self, tmp_path, browser, monkeypatch
    ):
        mgr = _mgr(tmp_path)
        assert _commit(mgr, HEADERS_A, A)
        setup_revision: list[str] = []

        def probe(payload, slot):
            # A setup committing exactly the bytes this renewal will commit.
            assert mgr._commit_session(payload, A, [_cookie("a")])
            setup_revision.append(_record(mgr)["revision"])
            return A

        monkeypatch.setattr(mgr, "_probe_payload", probe)

        assert mgr.try_auto_refresh(A_ID) is False
        assert _record(mgr)["revision"] == setup_revision[0]

    def test_renewal_is_refused_while_another_writer_holds_the_files(
        self, tmp_path, browser, monkeypatch, caplog
    ):
        mgr = _mgr(tmp_path)
        assert _commit(mgr, HEADERS_A, A)
        monkeypatch.setattr(
            mgr, "_read_session_pair", MagicMock(side_effect=SessionBusyError("mid-replacement"))
        )
        probe = MagicMock()
        monkeypatch.setattr(mgr, "_probe_payload", probe)

        with caplog.at_level("WARNING", logger="ytm_player.services.auth"):
            assert mgr.try_auto_refresh(A_ID) is False
        probe.assert_not_called()
        assert "another ytm process is updating the sign-in" in caplog.text


# ── Atomic writes and symlinks ──────────────────────────────────────────────


class TestAtomicWrite:
    def test_symlink_at_auth_json_is_refused_and_preserved(self, tmp_path, monkeypatch):
        """A4: a link planted at auth.json is neither written through nor
        replaced; the paste fails and the link is still a link."""
        mgr = _mgr(tmp_path)
        victim = tmp_path / "victim.txt"
        victim.write_text("PRECIOUS", encoding="utf-8")
        try:
            mgr.auth_file.symlink_to(victim)
        except OSError as exc:  # pragma: no cover - Windows without the privilege
            pytest.skip(f"cannot create a symlink here: {exc}")
        monkeypatch.setattr(mgr, "_probe_payload", lambda payload, slot: A)
        _paste(monkeypatch, HEADERS_A)

        assert mgr._setup_manual() is False

        assert mgr.auth_file.is_symlink()
        assert victim.read_text(encoding="utf-8") == "PRECIOUS"
        assert _temps(mgr) == []
        with pytest.raises(OSError) as excinfo:
            _atomic_write(mgr.auth_file, "wb", lambda f: f.write(b"x"))
        assert excinfo.value.errno == errno.ELOOP

    def test_link_planted_in_the_check_replace_gap_is_replaced_but_its_target_untouched(
        self, tmp_path, monkeypatch
    ):
        """The documented outcome for the one gap left: os.replace never
        writes through a link, so the target survives; the link entry
        itself becomes the file."""
        target = tmp_path / "auth.json"
        victim = tmp_path / "victim.txt"
        victim.write_text("PRECIOUS", encoding="utf-8")
        real_replace = os.replace

        def plant_then_replace(src, dst):
            try:
                Path(dst).symlink_to(victim)  # planted after the lstat check
            except OSError as exc:  # pragma: no cover
                pytest.skip(f"cannot create a symlink here: {exc}")
            return real_replace(src, dst)

        monkeypatch.setattr("ytm_player.services.auth.os.replace", plant_then_replace)

        _atomic_write(target, "wb", lambda f: f.write(b"new"))

        assert victim.read_text(encoding="utf-8") == "PRECIOUS"
        assert not target.is_symlink()
        assert target.read_bytes() == b"new"

    def test_temp_file_is_created_exclusively_and_a_foreign_file_is_never_removed(
        self, tmp_path, monkeypatch
    ):
        target = tmp_path / "auth.json"
        monkeypatch.setattr("ytm_player.services.auth.os.urandom", lambda n: b"\0" * n)
        foreign = tmp_path / f"auth.json.tmp-{os.getpid()}-00000000"
        foreign.write_text("theirs", encoding="utf-8")

        with pytest.raises(FileExistsError):
            _atomic_write(target, "wb", lambda f: f.write(b"new"))

        assert foreign.read_text(encoding="utf-8") == "theirs"
        assert not target.exists()

    def test_partial_auth_write_leaves_the_previous_session_whole(self, tmp_path, monkeypatch):
        """A3: a write that fails part-way (disk full) leaves auth.json's
        previous bytes, the record that describes them, and no temp file."""
        mgr = _mgr(tmp_path)
        assert _commit(mgr, HEADERS_A, A)
        before = (mgr.auth_file.read_bytes(), mgr._account_file.read_bytes())
        real = auth_module._atomic_write

        def flaky(path, mode, write, encoding=None):
            if path == mgr.auth_file:

                def partial(f):
                    f.write(b'{"co')
                    raise OSError(errno.ENOSPC, "No space left on device")

                return real(path, mode, partial, encoding)
            return real(path, mode, write, encoding)

        monkeypatch.setattr(auth_module, "_atomic_write", flaky)

        assert _commit(mgr, HEADERS_B, B) is False

        assert (mgr.auth_file.read_bytes(), mgr._account_file.read_bytes()) == before
        assert mgr.is_authenticated() is True
        recorded = mgr._load_recorded_identity()
        assert recorded is not None and recorded.channel_id == A_ID
        assert _temps(mgr) == []


# ── Partial publication: every crash state is whole and fail-closed ─────────


class TestPartialPublication:
    def test_crash_after_the_jar_keeps_the_identity_and_refuses_the_jar(
        self, tmp_path, monkeypatch, caplog
    ):
        mgr = _mgr(tmp_path)
        assert _commit(mgr, HEADERS_A, A)
        a_auth = mgr.auth_file.read_bytes()

        with _interrupted_after(1):  # strict record → writes: jar, auth, record
            assert _commit(mgr, HEADERS_B, B) is False

        assert mgr.auth_file.read_bytes() == a_auth
        recorded = mgr._load_recorded_identity()
        assert recorded is not None and recorded.channel_id == A_ID
        with caplog.at_level("WARNING", logger="ytm_player.services.auth"):
            assert _vouched(mgr) is None
        assert "does not match the saved sign-in" in caplog.text
        assert _temps(mgr) == []

    def test_crash_after_auth_yields_an_unbound_session_not_a_busy_one(
        self, tmp_path, monkeypatch, caplog
    ):
        mgr = _mgr(tmp_path)
        assert _commit(mgr, HEADERS_A, A)

        with _interrupted_after(2):
            assert _commit(mgr, HEADERS_B, B) is False

        assert json.loads(mgr.auth_file.read_bytes()) == HEADERS_B
        with caplog.at_level("WARNING", logger="ytm_player.services.auth"):
            assert mgr._load_recorded_identity() is None  # permanent path, no SessionBusyError
        assert "does not describe the current auth.json" in caplog.text
        probe = MagicMock()
        monkeypatch.setattr(mgr, "_probe_payload", probe)
        assert mgr.try_auto_refresh() is False
        probe.assert_not_called()
        assert _vouched(mgr) is None
        assert _temps(mgr) == []
        json.loads(mgr._account_file.read_text(encoding="utf-8"))  # whole

    def test_commit_without_a_jar_removes_the_previous_one(self, tmp_path):
        mgr = _mgr(tmp_path)
        assert _commit(mgr, HEADERS_A, A)
        assert mgr._stream_cookies_file.exists()

        assert mgr._commit_session(_payload(HEADERS_B), B, None) is True

        assert not mgr._stream_cookies_file.exists()
        assert _record(mgr)["stream_sha256"] is None
        assert _vouched(mgr) is None

    def test_crash_leftovers_are_never_cleaned_up(self, tmp_path):
        mgr = _mgr(tmp_path)
        stale = mgr._config_dir / "auth.json.tmp-1234-deadbeef"
        stale.write_text("not ours to remove", encoding="utf-8")

        assert _commit(mgr, HEADERS_A, A) is True

        assert stale.read_text(encoding="utf-8") == "not ours to remove"


# ── Leaving legacy acceptance before publishing anything ────────────────────


class TestLegacyExit:
    def _legacy(self, tmp_path, form: str) -> AuthManager:
        mgr = _mgr(tmp_path)
        mgr.auth_file.write_bytes(_payload(HEADERS_A))
        mgr._stream_cookies_file.write_bytes(b"# old jar\n")
        if form == "record_without_key":
            mgr._account_file.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "x-goog-authuser": "0",
                        "channel_id": A_ID,
                        "name": "Synthetic A",
                        "handle": None,
                        "auth_sha256": _sha(_payload(HEADERS_A)),
                    }
                ),
                encoding="utf-8",
            )
        assert _vouched(mgr) == b"# old jar\n"  # today's trust, before any commit
        return mgr

    @pytest.mark.parametrize("form", ["no_record", "record_without_key"])
    def test_interrupted_first_commit_never_accepts_the_new_jar(
        self, tmp_path, monkeypatch, caplog, form
    ):
        mgr = self._legacy(tmp_path, form)

        with _interrupted_after(2):  # writes: transition record, jar, (auth ✗)
            assert _commit(mgr, HEADERS_B, B, [_cookie("b")]) is False

        assert json.loads(mgr.auth_file.read_bytes()) == HEADERS_A
        record = _record(mgr)
        assert record["stream_sha256"] == _sha(b"# old jar\n")
        assert record["channel_id"] == (A_ID if form == "record_without_key" else None)
        assert mgr._stream_cookies_file.read_bytes() != b"# old jar\n"
        with caplog.at_level("WARNING", logger="ytm_player.services.auth"):
            assert _vouched(mgr) is None
        assert "does not match the saved sign-in" in caplog.text
        assert (mgr._load_recorded_identity() is not None) == (form == "record_without_key")

    @pytest.mark.parametrize("form", ["no_record", "record_without_key"])
    def test_failure_at_the_transition_changes_nothing(self, tmp_path, monkeypatch, form):
        mgr = self._legacy(tmp_path, form)
        before = _files(mgr)

        with _interrupted_after(0):
            assert _commit(mgr, HEADERS_B, B, [_cookie("b")]) is False

        assert _files(mgr) == before
        assert _vouched(mgr) == b"# old jar\n"

    @pytest.mark.parametrize("form", ["no_record", "record_without_key"])
    def test_successful_first_commit_records_the_jar_and_a_revision(self, tmp_path, form):
        mgr = self._legacy(tmp_path, form)

        assert _commit(mgr, HEADERS_B, B, [_cookie("b")]) is True

        record = _record(mgr)
        assert record["stream_sha256"] == _sha(mgr._stream_cookies_file.read_bytes())
        assert len(record["revision"]) == 32
        assert _vouched(mgr) == mgr._stream_cookies_file.read_bytes()

    def test_legacy_install_without_a_jar_records_null(self, tmp_path, monkeypatch):
        mgr = _mgr(tmp_path)
        mgr.auth_file.write_bytes(_payload(HEADERS_A))

        with _interrupted_after(1):
            assert _commit(mgr, HEADERS_B, B, [_cookie("b")]) is False

        assert _record(mgr)["stream_sha256"] is None
        assert _record(mgr)["auth_sha256"] == _sha(_payload(HEADERS_A))
        assert _vouched(mgr) is None

    @pytest.mark.parametrize(
        "content",
        [b"{broken", json.dumps({"schema_version": 2, "channel_id": A_ID}).encode()],
        ids=["malformed", "unknown_schema"],
    )
    def test_invalid_record_is_replaced_by_one_that_vouches_for_nothing(
        self, tmp_path, caplog, content
    ):
        """Only genuine legacy shapes inherit today's jar trust. A record that
        exists but does not parse (or is of another schema) refused the jar
        before; after a commit that fails right after the transition it must
        still refuse it — the provisional record vouches for no jar."""
        mgr = _mgr(tmp_path)
        mgr.auth_file.write_bytes(_payload(HEADERS_A))
        mgr._stream_cookies_file.write_bytes(b"# unvouched jar\n")
        mgr._account_file.write_bytes(content)
        assert _vouched(mgr) is None

        with caplog.at_level("WARNING", logger="ytm_player.services.auth"):
            with _interrupted_after(1):  # transition record published, jar ✗
                assert _commit(mgr, HEADERS_B, B, [_cookie("b")]) is False

        record = _record(mgr)
        assert (record["stream_sha256"], record["channel_id"], record["verified"]) == (
            None,
            None,
            False,
        )
        assert record["auth_sha256"] == _sha(_payload(HEADERS_A))
        assert _vouched(mgr) is None
        assert mgr._load_recorded_identity() is None
        assert "was malformed; replaced it with a record that vouches for no" in caplog.text

    def test_unreadable_record_fails_the_commit(self, tmp_path, monkeypatch, caplog):
        mgr = _mgr(tmp_path)
        assert _commit(mgr, HEADERS_A, A)
        before = _files(mgr)
        monkeypatch.setattr(
            mgr, "_read_record_raw", MagicMock(side_effect=PermissionError("unreadable"))
        )

        with caplog.at_level("ERROR", logger="ytm_player.services.auth"):
            assert _commit(mgr, HEADERS_B, B) is False

        assert _files(mgr) == before
        assert "while reading the current sign-in" in caplog.text

    def test_strict_record_skips_the_transition(self, tmp_path, monkeypatch):
        mgr = _mgr(tmp_path)
        assert _commit(mgr, HEADERS_A, A)

        with _interrupted_after(99) as written:
            assert _commit(mgr, HEADERS_B, B) is True

        assert written.count(mgr._account_file) == 1  # step 3 only


# ── Fresh install: strict metadata exists before anything is published ──────


class TestFreshInstall:
    # _atomic_write calls on a fresh install: provisional record, jar, auth, record.
    @pytest.mark.parametrize("published", [1, 2, 3])
    def test_candidate_jar_is_refused_until_the_commit_completes(
        self, tmp_path, monkeypatch, caplog, published
    ):
        mgr = _mgr(tmp_path)
        assert not any(mgr._config_dir.iterdir())

        with _interrupted_after(published):
            assert _commit(mgr, HEADERS_A, A) is False

        fresh = _mgr(tmp_path)
        record = _record(fresh)
        assert (record["channel_id"], record["verified"]) == (None, False)
        assert (record["auth_sha256"], record["stream_sha256"]) == (None, None)
        assert "revision" not in record
        with caplog.at_level("WARNING", logger="ytm_player.services.auth"):
            assert _vouched(fresh) is None
            with patch("ytm_player.services.auth.YTMusic", side_effect=lambda h, **kw: h) as ctor:
                client, identity = fresh.create_bound_client()
        assert identity is None
        if published >= 3:  # auth.json is there: unbound, never busy
            assert client == HEADERS_A
            assert "does not describe the current auth.json" in caplog.text
        else:  # no auth.json yet: today's path-based fallback
            assert ctor.call_args.args[0] == str(fresh.auth_file)
        assert fresh.try_auto_refresh() is False

        assert _commit(fresh, HEADERS_A, A) is True
        assert _vouched(fresh) == fresh._stream_cookies_file.read_bytes()
        recorded = fresh._load_recorded_identity()
        assert recorded is not None and recorded.channel_id == A_ID


# ── Recovery through the real service ───────────────────────────────────────


class TestRecoveryThroughTheService:
    async def test_crash_after_auth_replacement_leaves_a_working_unbound_session(
        self, tmp_path, monkeypatch, caplog
    ):
        mgr = _mgr(tmp_path)
        assert _commit(mgr, HEADERS_A, A)
        with _interrupted_after(2):
            assert _commit(mgr, HEADERS_B, B) is False

        fresh = _mgr(tmp_path)  # restart
        refresh = MagicMock(return_value=True)
        monkeypatch.setattr(fresh, "try_auto_refresh", refresh)
        service = make_ytmusic_service(_ytm=None, _auth_manager=fresh)
        monkeypatch.setattr("ytm_player.services.auth.YTMusic", _fake_ytmusic({}))

        with caplog.at_level("WARNING"):
            assert await service.rate_song("vid", "LIKE") == "auth_expired"

        assert service._client_identities[service.client] is None
        refresh.assert_not_called()
        assert "does not describe the current auth.json" in caplog.text
        assert "no recorded account identity" in caplog.text

        # `ytm setup` is the recovery: the next client carries the identity.
        monkeypatch.setattr(fresh, "_probe_payload", lambda payload, slot: B)
        _paste(monkeypatch, HEADERS_B)
        assert fresh._setup_manual() is True
        service._ytm = None
        assert service._client_identities[service.client] == B_ID

    async def test_crash_after_the_jar_keeps_renewal_working(self, tmp_path, browser, monkeypatch):
        mgr = _mgr(tmp_path)
        assert _commit(mgr, HEADERS_A, A)
        with _interrupted_after(1):
            assert _commit(mgr, HEADERS_B, B) is False

        fresh = _mgr(tmp_path)
        assert _vouched(fresh) is None
        monkeypatch.setattr("ytm_player.services.auth.YTMusic", _fake_ytmusic({0: A}))

        assert fresh.try_auto_refresh(A_ID) is True

        assert _vouched(fresh) == fresh._stream_cookies_file.read_bytes()
        recorded = fresh._load_recorded_identity()
        assert recorded is not None and recorded.channel_id == A_ID


# ── Readers: consistent snapshot, never waiting, never an OSError ────────────


class TestReaders:
    def test_session_exceptions_are_not_oserrors(self):
        for exc in (SessionBusyError("x"), SessionLockTimeoutError("x")):
            assert isinstance(exc, SessionError)
            assert isinstance(exc, RuntimeError)
            assert not isinstance(exc, OSError)

    def test_missing_auth_file_keeps_the_path_based_fallback(self, tmp_path):
        mgr = _mgr(tmp_path)
        with patch("ytm_player.services.auth.YTMusic", side_effect=lambda h, **kw: h) as ctor:
            client, identity = mgr.create_bound_client()
        assert (client, identity) == (str(mgr.auth_file), None)
        ctor.assert_called_once()

    @pytest.mark.parametrize("lock_state", ["free", "held"])
    def test_unreadable_record_refuses_and_is_never_absent(self, tmp_path, monkeypatch, lock_state):
        """A record that exists but cannot be read is not "no record": the
        public method raises SessionUnreadableError (never an OSError, never
        an unbound client) whether or not a writer holds the lock."""
        mgr = _mgr(tmp_path)
        assert _commit(mgr, HEADERS_A, A)
        monkeypatch.setattr(
            mgr, "_read_record_raw", MagicMock(side_effect=PermissionError("unreadable"))
        )
        holder = _SessionLock(_mgr(tmp_path)._lock_path)
        held, release = threading.Event(), threading.Event()

        def hold():
            holder.acquire(timeout=5)
            held.set()
            release.wait(5)
            holder.release()

        thread = threading.Thread(target=hold) if lock_state == "held" else None
        if thread is not None:
            thread.start()
            assert held.wait(5)
        try:
            with patch("ytm_player.services.auth.YTMusic") as ctor:
                with pytest.raises(SessionUnreadableError) as excinfo:
                    mgr.create_bound_client()
            ctor.assert_not_called()
            assert not isinstance(excinfo.value, OSError)
            assert mgr._load_recorded_identity() is None
            assert mgr.try_auto_refresh() is False
        finally:
            release.set()
            if thread is not None:
                thread.join(5)

    async def test_unreadable_record_reaches_the_services_safe_default(self, tmp_path, monkeypatch):
        mgr = _mgr(tmp_path)
        assert _commit(mgr, HEADERS_A, A)
        monkeypatch.setattr(
            mgr, "_read_record_raw", MagicMock(side_effect=PermissionError("unreadable"))
        )
        service = make_ytmusic_service(_ytm=None, _auth_manager=mgr)

        assert await service.get_home() is None
        assert service._ytm is None

    def test_malformed_record_is_an_unbound_session_with_a_warning(self, tmp_path, caplog):
        mgr = _mgr(tmp_path)
        assert _commit(mgr, HEADERS_A, A)
        mgr._account_file.write_bytes(b"{broken")

        with caplog.at_level("WARNING", logger="ytm_player.services.auth"):
            with patch("ytm_player.services.auth.YTMusic", side_effect=lambda h, **kw: h):
                client, identity = mgr.create_bound_client()
        assert (client, identity) == (HEADERS_A, None)
        assert "account.json is malformed; ignoring it" in caplog.text

    def test_contention_raises_session_busy_and_builds_no_client(self, tmp_path, monkeypatch):
        """Another thread (a second manager for the same files) holds the lock
        while the pair is inconsistent: the public method raises SessionBusyError,
        and no client is constructed — not even an unbound one."""
        mgr = _mgr(tmp_path)
        assert _commit(mgr, HEADERS_A, A)
        mgr.auth_file.write_bytes(_payload(HEADERS_B))  # mid-commit shape
        holder = _mgr(tmp_path)
        lock = _SessionLock(holder._lock_path)
        held, release = threading.Event(), threading.Event()

        def hold():
            lock.acquire(timeout=5)
            held.set()
            release.wait(5)
            lock.release()

        thread = threading.Thread(target=hold)
        thread.start()
        try:
            assert held.wait(5)
            with patch("ytm_player.services.auth.YTMusic") as ctor:
                with pytest.raises(SessionBusyError):
                    mgr.create_bound_client()
            ctor.assert_not_called()
        finally:
            release.set()
            thread.join(5)

        # Lock free again: the same pair is a permanent leftover → unbound.
        with patch("ytm_player.services.auth.YTMusic", side_effect=lambda h, **kw: h):
            client, identity = mgr.create_bound_client()
        assert (client, identity) == (HEADERS_B, None)

    async def test_session_busy_propagates_to_the_services_safe_default(
        self, tmp_path, monkeypatch
    ):
        mgr = _mgr(tmp_path)
        assert _commit(mgr, HEADERS_A, A)
        monkeypatch.setattr(mgr, "_read_session_pair", MagicMock(side_effect=SessionBusyError("x")))
        refresh = MagicMock()
        monkeypatch.setattr(mgr, "try_auto_refresh", refresh)
        service = make_ytmusic_service(_ytm=None, _auth_manager=mgr)

        assert await service.get_home() is None
        refresh.assert_not_called()
        assert service._ytm is None  # the next call retries the read

    def test_owner_snapshot_carries_hash_and_revision(self, tmp_path):
        mgr = _mgr(tmp_path)
        assert _commit(mgr, HEADERS_A, A)
        pair = mgr._read_session_pair()
        assert pair.owner == _SessionOwner(
            _sha(mgr.auth_file.read_bytes()), _record(mgr)["revision"]
        )


# ── The in-process lock: per path, shared by managers, not re-entrant ───────


class TestInProcessLock:
    def test_second_manager_waits_for_the_first_and_nesting_raises(self, tmp_path):
        mgr1, mgr2 = _mgr(tmp_path), _mgr(tmp_path)
        order: list[str] = []
        a_holds, a_release = threading.Event(), threading.Event()

        def thread_a():
            lock = _SessionLock(mgr1._lock_path)
            lock.acquire(timeout=5)
            a_holds.set()
            with pytest.raises(RuntimeError, match="not re-entrant"):
                _SessionLock(mgr2._lock_path).acquire(timeout=5)  # same thread, other manager
            a_release.wait(5)
            order.append("A-release")
            lock.release()

        def thread_b():
            lock = _SessionLock(mgr2._lock_path)
            lock.acquire(timeout=5)
            order.append("B-acquired")
            lock.release()

        ta = threading.Thread(target=thread_a)
        ta.start()
        assert a_holds.wait(5)
        tb = threading.Thread(target=thread_b)
        tb.start()
        a_release.set()
        ta.join(5)
        tb.join(5)

        assert order == ["A-release", "B-acquired"]
        assert mgr1._lock_path.exists()  # never unlinked

    def test_timeout_covers_the_thread_level_lock(self, tmp_path):
        mgr1, mgr2 = _mgr(tmp_path), _mgr(tmp_path)
        held, release = threading.Event(), threading.Event()

        def hold():
            lock = _SessionLock(mgr1._lock_path)
            lock.acquire(timeout=5)
            held.set()
            release.wait(5)
            lock.release()

        thread = threading.Thread(target=hold)
        thread.start()
        try:
            assert held.wait(5)
            with pytest.raises(SessionLockTimeoutError):
                _SessionLock(mgr2._lock_path).acquire(timeout=0.2)
        finally:
            release.set()
            thread.join(5)

    def test_symlink_at_the_lock_path_is_refused(self, tmp_path, caplog):
        mgr = _mgr(tmp_path)
        victim = tmp_path / "victim.txt"
        victim.write_text("x", encoding="utf-8")
        try:
            mgr._lock_path.symlink_to(victim)
        except OSError as exc:  # pragma: no cover
            pytest.skip(f"cannot create a symlink here: {exc}")

        with caplog.at_level("WARNING", logger="ytm_player.services.auth"):
            assert _commit(mgr, HEADERS_A, A) is False
        assert "Cannot lock the sign-in files" in caplog.text
        assert not mgr.auth_file.exists()


# ── read_vouched_stream_jar ─────────────────────────────────────────────────


class TestReadVouchedStreamJar:
    def _files(self, tmp_path, *, jar=b"# jar\n", auth=b'{"cookie": "SAPISID=a"}', record="strict"):
        jar_path, auth_path, record_path = (
            tmp_path / "stream_cookies.txt",
            tmp_path / "auth.json",
            tmp_path / "account.json",
        )
        if jar is not None:
            jar_path.write_bytes(jar)
        if auth is not None:
            auth_path.write_bytes(auth)
        if record == "strict":
            record_path.write_text(
                json.dumps(
                    {"schema_version": 1, "auth_sha256": _sha(auth), "stream_sha256": _sha(jar)}
                )
            )
        elif isinstance(record, dict):
            record_path.write_text(json.dumps(record))
        elif record == "malformed":
            record_path.write_text("{not json")
        return str(jar_path), (str(auth_path), str(record_path))

    def test_vouched_jar_is_returned_with_the_snapshots_signature(self, tmp_path):
        jar_path, session = self._files(tmp_path)
        result = read_vouched_stream_jar(jar_path, session)
        assert result.payload == b"# jar\n"
        stats = tuple(
            (st.st_ino, st.st_mtime_ns, st.st_size)
            for st in (os.stat(jar_path), os.stat(session[0]), os.stat(session[1]))
        )
        assert result.signature == (jar_path, *stats)

    def test_legacy_forms_are_accepted(self, tmp_path):
        jar_path, session = self._files(tmp_path, record=None)
        assert read_vouched_stream_jar(jar_path, session).payload == b"# jar\n"
        jar_path, session = self._files(
            tmp_path, record={"schema_version": 1, "auth_sha256": _sha(b'{"cookie": "SAPISID=a"}')}
        )
        assert read_vouched_stream_jar(jar_path, session).payload == b"# jar\n"

    @pytest.mark.parametrize(
        "case, reason",
        [
            ("no_auth", "auth.json is missing"),
            ("other_auth", "does not describe the current auth.json"),
            ("null", "has no stream cookie file"),
            ("mismatch", "does not match the saved sign-in"),
            ("malformed", "account.json is malformed"),
            ("no_session", "no sign-in files to check it against"),
        ],
    )
    def test_refusals_name_their_reason(self, tmp_path, caplog, case, reason):
        auth = b'{"cookie": "SAPISID=a"}'
        if case == "no_auth":
            jar_path, session = self._files(tmp_path, auth=None, record=None)
        elif case == "other_auth":
            jar_path, session = self._files(
                tmp_path,
                record={"schema_version": 1, "auth_sha256": _sha(b"other"), "stream_sha256": None},
            )
        elif case == "null":
            jar_path, session = self._files(
                tmp_path,
                record={"schema_version": 1, "auth_sha256": _sha(auth), "stream_sha256": None},
            )
        elif case == "mismatch":
            jar_path, session = self._files(
                tmp_path,
                record={"schema_version": 1, "auth_sha256": _sha(auth), "stream_sha256": "00"},
            )
        elif case == "malformed":
            jar_path, session = self._files(tmp_path, record="malformed")
        else:
            jar_path, session = self._files(tmp_path)
            session = None

        with caplog.at_level("WARNING", logger="ytm_player.services.auth"):
            result = read_vouched_stream_jar(jar_path, session)
        assert result.payload is None
        assert reason in caplog.text
        assert caplog.text.count("Stream cookie file") == 1

    @pytest.mark.parametrize("which", ["record", "auth", "jar"])
    def test_unreadable_file_refuses_even_when_absence_would_be_legacy(
        self, tmp_path, monkeypatch, caplog, which
    ):
        """Astra's reproduction: a strict record with ``stream_sha256`` null
        forbids the jar; making that record unreadable must not turn it into
        an absent (legacy, accepting) one. Same for the other two files."""
        auth = b'{"cookie": "SAPISID=a"}'
        record = {"schema_version": 1, "auth_sha256": _sha(auth), "stream_sha256": None}
        jar_path, session = self._files(tmp_path, auth=auth, record=record)
        if which == "auth":  # a legacy-shaped pair, so only the read error can refuse
            (tmp_path / "account.json").unlink()
        target = {"record": session[1], "auth": session[0], "jar": jar_path}[which]
        real_open = open

        def unreadable(path, *args, **kwargs):
            if str(path) == target:
                raise PermissionError("synthetic unreadable file")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(auth_module, "open", unreadable, raising=False)

        with caplog.at_level("WARNING", logger="ytm_player.services.auth"):
            result = read_vouched_stream_jar(jar_path, session)
        assert result.payload is None
        expected = {
            "record": "account.json cannot be read",
            "auth": "auth.json is unreadable",
            "jar": "the stream cookie file is unreadable",
        }[which]
        assert expected in caplog.text

    def test_record_changing_between_the_two_reads_refuses(self, tmp_path, monkeypatch, caplog):
        jar_path, session = self._files(tmp_path)
        real = auth_module._read_with_stat
        record_reads = [0]

        def flip(path):
            read = real(path)
            if str(path) == session[1]:
                record_reads[0] += 1
                if record_reads[0] % 2 == 0 and read.data is not None:
                    read = auth_module._FileRead(read.data + b" ", read.stat)
            return read

        monkeypatch.setattr(auth_module, "_read_with_stat", flip)
        with caplog.at_level("WARNING", logger="ytm_player.services.auth"):
            result = read_vouched_stream_jar(jar_path, session)
        assert result.payload is None
        assert "changed while they were being checked" in caplog.text
        assert record_reads[0] == 6  # three attempts


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_lock_file_and_written_files_are_private(tmp_path):
    mgr = _mgr(tmp_path)
    assert _commit(mgr, HEADERS_A, A)
    for path in (mgr._lock_path, mgr.auth_file, mgr._account_file, mgr._stream_cookies_file):
        assert path.stat().st_mode & 0o777 == 0o600, path
