"""Tests for services/update_check.py — the PyPI version probe."""

from __future__ import annotations

import json
import time
from unittest.mock import patch

from ytm_player.services.update_check import (
    _is_newer,
    check_for_update,
)


class TestIsNewer:
    def test_newer_patch(self):
        assert _is_newer("1.6.1", "1.6.0") is True

    def test_newer_minor(self):
        assert _is_newer("1.7.0", "1.6.5") is True

    def test_same_version(self):
        assert _is_newer("1.6.0", "1.6.0") is False

    def test_older(self):
        assert _is_newer("1.5.9", "1.6.0") is False

    def test_unparseable_returns_false(self):
        assert _is_newer("garbage", "1.6.0") is False

    def test_double_digit_minor(self):
        # Trivially correct under tuple-int compare too, but this is the
        # canonical "lex compare gets it wrong" test that motivated the
        # switch to packaging.version.Version.
        assert _is_newer("1.10.0", "1.9.0") is True
        assert _is_newer("1.9.0", "1.10.0") is False

    def test_post_release_newer_than_release(self):
        # 1.6.0.post1 IS strictly newer than 1.6.0 under PEP 440. The
        # old hand-rolled parser couldn't see this — `.post1` had no
        # numeric prefix so the chunk parsed to () and _is_newer
        # returned False. packaging.version.Version handles it.
        assert _is_newer("1.6.0.post1", "1.6.0") is True

    def test_pre_release_older_than_release(self):
        assert _is_newer("1.6.0rc1", "1.6.0") is False
        assert _is_newer("1.6.0", "1.6.0rc1") is True


class TestCacheShape:
    """A wrong-shape update_check.json is discarded, never raised on."""

    def test_list_cache_is_ignored(self, tmp_path):
        cache = tmp_path / "update_check.json"
        cache.write_text("[]", encoding="utf-8")
        with patch(
            "ytm_player.services.update_check._fetch_latest_from_pypi",
            return_value="1.7.0",
        ) as fetch:
            result = check_for_update("1.6.0", cache)
        assert result == "1.7.0"
        fetch.assert_called_once()

    def test_bad_checked_at_is_ignored(self, tmp_path):
        cache = tmp_path / "update_check.json"
        cache.write_text(
            json.dumps({"checked_at": "bad", "latest": "9.9.9"}),
            encoding="utf-8",
        )
        with patch(
            "ytm_player.services.update_check._fetch_latest_from_pypi",
            return_value="1.7.0",
        ) as fetch:
            result = check_for_update("1.6.0", cache)
        assert result == "1.7.0"
        fetch.assert_called_once()

    def test_non_string_latest_is_ignored(self, tmp_path):
        cache = tmp_path / "update_check.json"
        cache.write_text(
            json.dumps({"checked_at": time.time(), "latest": 5}),
            encoding="utf-8",
        )
        with patch("ytm_player.services.update_check._fetch_latest_from_pypi") as fetch:
            result = check_for_update("1.6.0", cache)
        assert result is None
        fetch.assert_not_called()


class TestCheckForUpdate:
    def test_cache_within_24h_skips_network(self, tmp_path):
        cache = tmp_path / "update_check.json"
        cache.write_text(
            json.dumps({"checked_at": time.time(), "latest": "1.7.0"}),
            encoding="utf-8",
        )
        with patch("ytm_player.services.update_check._fetch_latest_from_pypi") as fetch:
            result = check_for_update("1.6.0", cache)
        assert result == "1.7.0"
        fetch.assert_not_called()

    def test_cache_within_24h_no_update_returns_none(self, tmp_path):
        cache = tmp_path / "update_check.json"
        cache.write_text(
            json.dumps({"checked_at": time.time(), "latest": "1.6.0"}),
            encoding="utf-8",
        )
        result = check_for_update("1.6.0", cache)
        assert result is None

    def test_stale_cache_triggers_fetch(self, tmp_path):
        cache = tmp_path / "update_check.json"
        cache.write_text(
            json.dumps({"checked_at": 0, "latest": "1.5.0"}),
            encoding="utf-8",
        )
        with patch(
            "ytm_player.services.update_check._fetch_latest_from_pypi",
            return_value="1.7.0",
        ):
            result = check_for_update("1.6.0", cache)
        assert result == "1.7.0"
        # Cache should now be updated.
        new_cache = json.loads(cache.read_text(encoding="utf-8"))
        assert new_cache["latest"] == "1.7.0"

    def test_no_cache_file_triggers_fetch(self, tmp_path):
        cache = tmp_path / "missing.json"
        with patch(
            "ytm_player.services.update_check._fetch_latest_from_pypi",
            return_value="1.7.0",
        ):
            result = check_for_update("1.6.0", cache)
        assert result == "1.7.0"
        assert cache.exists()

    def test_network_failure_returns_none(self, tmp_path):
        cache = tmp_path / "missing.json"
        with patch(
            "ytm_player.services.update_check._fetch_latest_from_pypi",
            return_value=None,
        ):
            result = check_for_update("1.6.0", cache)
        assert result is None
        # Cache NOT written on failure.
        assert not cache.exists()

    def test_pypi_returns_older_version(self, tmp_path):
        """Should not surface an "update" if PyPI has an older version (e.g. yanked)."""
        cache = tmp_path / "missing.json"
        with patch(
            "ytm_player.services.update_check._fetch_latest_from_pypi",
            return_value="1.5.0",
        ):
            result = check_for_update("1.6.0", cache)
        assert result is None
        # Cache IS written (we successfully fetched) — just nothing to surface.
        assert cache.exists()

    def test_clock_skew_triggers_fetch(self, tmp_path):
        """checked_at in the future (clock went backwards) → re-fetch."""
        cache = tmp_path / "update_check.json"
        cache.write_text(
            json.dumps({"checked_at": time.time() + 86400, "latest": "1.5.0"}),
            encoding="utf-8",
        )
        with patch(
            "ytm_player.services.update_check._fetch_latest_from_pypi",
            return_value="1.7.0",
        ):
            result = check_for_update("1.6.0", cache)
        assert result == "1.7.0"
