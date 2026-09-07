"""The sign-in lock across processes.

A child process holds ``auth.json.lock`` while the parent commits or reads.
Synchronisation is by pipe ("locked" / "released" lines from the child,
"release" on its stdin) and by events — never by elapsed time.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import sys
import threading
from http.cookiejar import Cookie
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import make_ytmusic_service
from ytm_player.services import auth as auth_module
from ytm_player.services.auth import AuthManager, SessionBusyError, _ProbedAccount

A_ID = "UC" + "a" * 22
B_ID = "UC" + "b" * 22
A = _ProbedAccount(slot=0, name="Synthetic A", handle="", channel_id=A_ID)
B = _ProbedAccount(slot=1, name="Synthetic B", handle="", channel_id=B_ID)
HEADERS_A = {"cookie": "SAPISID=a", "x-goog-authuser": "0"}
HEADERS_B = {"cookie": "SAPISID=b", "x-goog-authuser": "1"}

_CHILD = r"""
import sys
from pathlib import Path

from ytm_player.services.auth import _SessionLock

lock = _SessionLock(Path(sys.argv[1]))
lock.acquire(timeout=10)
print("locked", flush=True)
for line in sys.stdin:
    if line.strip() == "release":
        break
lock.release()
print("released", flush=True)
"""


@pytest.fixture(autouse=True)
def _no_real_browser_or_network(monkeypatch):
    """Nothing in this module may reach a browser profile or the network."""
    monkeypatch.setattr(AuthManager, "_detect_browser", staticmethod(lambda: None))

    def _no_network(*args, **kwargs):
        raise RuntimeError("network isolation: YTMusic constructed in a test")

    monkeypatch.setattr("ytm_player.services.auth.YTMusic", _no_network)


def _payload(headers: dict) -> bytes:
    return json.dumps(headers, ensure_ascii=True, indent=4, sort_keys=True).encode("utf-8")


def _cookie(value: str) -> Cookie:
    return Cookie(
        0,
        "SAPISID",
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


def _commit(mgr: AuthManager, headers: dict, account: _ProbedAccount) -> bool:
    return mgr._commit_session(_payload(headers), account, [_cookie(account.name[-1].lower())])


@contextlib.contextmanager
def _child_holding(lock_path: Path):
    """Run a child process that holds *lock_path* until told to release."""
    src_dir = Path(auth_module.__file__).parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(src_dir) + os.pathsep + env.get("PYTHONPATH", "")
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(lock_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert child.stdin is not None and child.stdout is not None
    try:
        line = child.stdout.readline().strip()
        if line != "locked":
            _, err = child.communicate(timeout=10)
            pytest.fail(f"child did not take the lock: {line!r} {err}")
        yield child
    finally:
        if child.poll() is None:
            with contextlib.suppress(OSError):
                child.stdin.write("release\n")
                child.stdin.flush()
            child.communicate(timeout=10)


def _release(child: subprocess.Popen) -> None:
    assert child.stdin is not None and child.stdout is not None
    child.stdin.write("release\n")
    child.stdin.flush()
    assert child.stdout.readline().strip() == "released"


def test_commit_waits_for_another_process_and_runs_after_its_release(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    assert _commit(mgr, HEADERS_A, A)
    marker = tmp_path / "released.marker"
    attempted = threading.Event()
    real_try = auth_module._try_os_lock

    def spy_try(fd):
        ok = real_try(fd)
        if not ok:
            attempted.set()
        return ok

    monkeypatch.setattr(auth_module, "_try_os_lock", spy_try)
    observed: list[bool] = []
    real_read = mgr._read_pair_locked

    def spy_read():
        observed.append(marker.exists())  # True only if the lock was taken after the release
        return real_read()

    monkeypatch.setattr(mgr, "_read_pair_locked", spy_read)
    result: list[bool] = []

    with _child_holding(mgr._lock_path) as child:
        worker = threading.Thread(target=lambda: result.append(_commit(mgr, HEADERS_B, B)))
        worker.start()
        assert attempted.wait(10)  # the parent's commit has tried the OS lock and failed
        marker.write_text("x", encoding="utf-8")
        _release(child)
        worker.join(30)

    assert result == [True]
    assert observed == [True]
    assert json.loads(mgr.auth_file.read_bytes()) == HEADERS_B
    record = json.loads(mgr._account_file.read_text(encoding="utf-8"))
    assert record["auth_sha256"] == hashlib.sha256(mgr.auth_file.read_bytes()).hexdigest()


def test_commit_refuses_when_another_process_holds_the_lock_for_the_whole_wait(
    tmp_path, monkeypatch, caplog
):
    mgr = _mgr(tmp_path)
    assert _commit(mgr, HEADERS_A, A)
    before = (mgr.auth_file.read_bytes(), mgr._account_file.read_bytes())
    monkeypatch.setattr(auth_module, "_LOCK_TIMEOUT_SECONDS", 0.2)

    with _child_holding(mgr._lock_path) as child:
        with caplog.at_level("WARNING", logger="ytm_player.services.auth"):
            assert _commit(mgr, HEADERS_B, B) is False
        _release(child)

    assert "Another ytm process is updating the sign-in" in caplog.text
    assert (mgr.auth_file.read_bytes(), mgr._account_file.read_bytes()) == before


async def test_reads_never_wait_and_fail_closed_under_contention(tmp_path, monkeypatch, caplog):
    mgr = _mgr(tmp_path)
    assert _commit(mgr, HEADERS_A, A)
    identity_client = lambda h, **kw: h  # noqa: E731

    with _child_holding(mgr._lock_path) as child:
        # Consistent pair, lock held elsewhere: the read returns at once.
        with patch("ytm_player.services.auth.YTMusic", side_effect=identity_client):
            client, identity = mgr.create_bound_client()
        assert (client, identity) == (HEADERS_A, A_ID)

        # The pair becomes inconsistent while the child still holds the lock:
        # a writer may be mid-commit, so fail closed — no client at all.
        mgr.auth_file.write_bytes(_payload(HEADERS_B))
        with patch("ytm_player.services.auth.YTMusic") as ctor:
            with pytest.raises(SessionBusyError):
                mgr.create_bound_client()
        ctor.assert_not_called()

        refresh = MagicMock(return_value=True)
        monkeypatch.setattr(mgr, "try_auto_refresh", refresh)
        service = make_ytmusic_service(_ytm=None, _auth_manager=mgr)
        assert await service.get_home() is None
        refresh.assert_not_called()

        if sys.platform != "win32" and os.geteuid() != 0:
            # An unopenable lock file proves nothing about other holders.
            os.chmod(mgr._lock_path, 0)
            try:
                with caplog.at_level("WARNING", logger="ytm_player.services.auth"):
                    with pytest.raises(SessionBusyError):
                        mgr.create_bound_client()
                assert str(mgr._lock_path) in caplog.text
            finally:
                os.chmod(mgr._lock_path, 0o600)
        _release(child)

    # Lock free, same pair: a crash leftover → usable, unbound, with a warning.
    with caplog.at_level("WARNING", logger="ytm_player.services.auth"):
        with patch("ytm_player.services.auth.YTMusic", side_effect=identity_client):
            client, identity = mgr.create_bound_client()
    assert (client, identity) == (HEADERS_B, None)
    assert "does not describe the current auth.json" in caplog.text
