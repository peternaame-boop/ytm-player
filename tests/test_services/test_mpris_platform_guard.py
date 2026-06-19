"""Import-safety guard for the MPRIS module on non-Linux platforms (#106).

dbus-fast is Linux-only — on Windows it raises AttributeError at import
(socket.CMSG_LEN). Because app/_app.py imports MPRISService unconditionally,
importing ytm_player.services.mpris must never pull in dbus_fast off Linux,
regardless of whether the `mpris` extra was accidentally installed.

These tests force sys.platform in a fresh subprocess interpreter so the
module's platform gate runs as it would on the target OS, without poisoning
this test session's already-imported module state.
"""

import subprocess
import sys
import textwrap


def _run_import_under_platform(platform: str) -> str:
    """Import the mpris module with sys.platform forced to *platform*.

    Returns the subprocess stdout ("OK" on success); raises on assertion
    failure inside the child so the test reports the child's traceback.
    """
    script = textwrap.dedent(
        f"""
        import sys
        sys.platform = {platform!r}

        import ytm_player.services.mpris as mpris

        # The platform gate must keep dbus unavailable...
        assert mpris._DBUS_AVAILABLE is False, "_DBUS_AVAILABLE should be False"
        # ...and must never have imported the Linux-only library.
        assert "dbus_fast" not in sys.modules, "dbus_fast was imported off-Linux"
        # The service class must still be importable (app imports it directly).
        assert mpris.MPRISService is not None
        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"child failed (platform={platform}):\n{result.stdout}\n{result.stderr}"
    )
    return result.stdout.strip()


def test_mpris_import_safe_on_windows():
    assert _run_import_under_platform("win32") == "OK"


def test_mpris_import_safe_on_macos():
    assert _run_import_under_platform("darwin") == "OK"
