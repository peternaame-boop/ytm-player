from types import SimpleNamespace

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


def test_normalize_remote_components_string_to_list():
    assert normalize_remote_components("ejs:github, ejs:npm") == ["ejs:github", "ejs:npm"]


def test_normalize_js_runtimes_string_to_dict():
    assert normalize_js_runtimes("bun") == {"bun": {}}


def test_normalize_js_runtimes_list_to_dict():
    assert normalize_js_runtimes(["bun", "node"]) == {"bun": {}, "node": {}}


def test_apply_configured_options_converts_all_fields():
    settings = SimpleNamespace(
        cookies_file="~/cookies.txt",
        remote_components="ejs:github",
        js_runtimes="bun",
    )

    opts = apply_configured_yt_dlp_options({"quiet": True}, settings)

    assert opts["quiet"] is True
    assert opts["cookiefile"].endswith("/cookies.txt")
    assert opts["remote_components"] == ["ejs:github"]
    assert opts["js_runtimes"] == {"bun": {}}
