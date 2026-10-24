"""Kitty image messages go through Textual's driver while an app runs.

Regression: textual-image wrote each image straight to sys.__stdout__ while
Textual wrote frames from its own thread. With many covers at once the writes
interleaved and the covers, and the text around them, stayed blank.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("textual_image")

from textual._context import active_app  # noqa: E402
from textual_image.renderable import tgp  # noqa: E402

from ytm_player.ui.widgets import cover  # noqa: E402


@pytest.fixture
def routed(monkeypatch: pytest.MonkeyPatch):
    sent_direct: list[dict] = []

    def direct(*, payload=None, **kwargs):
        sent_direct.append({"payload": payload, **kwargs})

    monkeypatch.setattr(tgp, "_send_tgp_message", direct)
    cover._route_kitty_messages_through_textual()
    return sent_direct


def test_with_a_running_app_messages_queue_through_the_driver(routed):
    written: list[str] = []
    app = SimpleNamespace(_driver=SimpleNamespace(write=written.append))
    token = active_app.set(app)  # type: ignore[arg-type]
    try:
        tgp._send_tgp_message(a="p", i=7, c=22, r=11, U=1, q=2)
    finally:
        active_app.reset(token)

    assert routed == []
    assert written == [tgp.prepare_terminal_sequence("\x1b_Ga=p,i=7,c=22,r=11,U=1,q=2\x1b\\")]


def test_payload_chunks_keep_their_data(routed):
    written: list[str] = []
    token = active_app.set(SimpleNamespace(_driver=SimpleNamespace(write=written.append)))  # type: ignore[arg-type]
    try:
        tgp._send_tgp_message(i=3, m=1, f=100, payload="QUJD", q=2)
    finally:
        active_app.reset(token)

    assert written[0].endswith("\x1b_Gi=3,m=1,f=100,q=2;QUJD\x1b\\")


def test_without_an_app_the_library_writes_directly(routed):
    tgp._send_tgp_message(a="p", i=9, U=1)
    assert routed == [{"payload": None, "a": "p", "i": 9, "U": 1}]


def test_routing_is_installed_once(routed):
    first = tgp._send_tgp_message
    cover._route_kitty_messages_through_textual()
    assert tgp._send_tgp_message is first


def test_cleanup_deletes_only_its_own_image(routed):
    """Regression: ``a=d,I=<id>`` meant 'delete every image on screen' to kitty."""
    written: list[str] = []
    token = active_app.set(SimpleNamespace(_driver=SimpleNamespace(write=written.append)))  # type: ignore[arg-type]
    try:
        tgp._send_tgp_message(a="d", I=42)
    finally:
        active_app.reset(token)

    assert written == [tgp.prepare_terminal_sequence("\x1b_Ga=d,d=I,i=42,q=2\x1b\\")]


def test_cleanup_without_an_app_is_narrowed_too(routed):
    tgp._send_tgp_message(a="d", I=5)
    assert routed == [{"payload": None, "a": "d", "d": "I", "i": 5, "q": 2}]
