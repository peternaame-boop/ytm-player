from ytm_player.config.settings import YtDlpSettings
from ytm_player.services.yt_dlp_options import (
    apply_configured_yt_dlp_options,
    normalize_cookiefile,
    normalize_js_runtimes,
    normalize_remote_components,
)


def test_normalize_cookiefile_expands_home():
    got = normalize_cookiefile("~/cookies.txt")
    assert got is not None
    assert got.endswith("/cookies.txt")
    assert got.startswith("/")


def test_normalize_cookiefile_empty_none():
    assert normalize_cookiefile("") is None
    assert normalize_cookiefile(None) is None


def test_normalize_remote_components_string_to_list():
    assert normalize_remote_components("ejs:github, ejs:npm") == ["ejs:github", "ejs:npm"]


def test_normalize_remote_components_empty_and_none():
    assert normalize_remote_components("") is None
    assert normalize_remote_components([]) is None
    assert normalize_remote_components(None) is None


def test_normalize_js_runtimes_string_to_dict():
    assert normalize_js_runtimes("bun") == {"bun": {}}


def test_normalize_js_runtimes_with_paths():
    assert normalize_js_runtimes("bun:/path/to/bun node:/path/to/node") == {
        "bun": {"path": "/path/to/bun"},
        "node": {"path": "/path/to/node"},
    }


def test_normalize_js_runtimes_list_to_dict():
    assert normalize_js_runtimes(["bun", "node:/opt/node"]) == {
        "bun": {},
        "node": {"path": "/opt/node"},
    }


def test_normalize_js_runtimes_empty_and_none():
    assert normalize_js_runtimes("") is None
    assert normalize_js_runtimes([]) is None
    assert normalize_js_runtimes(None) is None


def test_apply_configured_options_converts_all_fields():
    settings = YtDlpSettings(
        cookies_file="~/cookies.txt",
        remote_components="ejs:github",
        js_runtimes="bun:/tmp/bun",
    )

    opts = apply_configured_yt_dlp_options({"quiet": True}, settings)

    assert opts["quiet"] is True
    assert opts["cookiefile"].endswith("/cookies.txt")
    assert opts["remote_components"] == ["ejs:github"]
    assert opts["js_runtimes"] == {"bun": {"path": "/tmp/bun"}}


def test_apply_configured_options_skips_unset_fields():
    settings = YtDlpSettings()
    opts = apply_configured_yt_dlp_options({"quiet": True}, settings)
    assert opts == {"quiet": True}
