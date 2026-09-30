"""Bootstrap and `profile add` change only what they are told to.

They used to rebuild the profile from a fixed field list, so main_write_policy and tester_mode
(which save_settings omits at their defaults) went back to auto/off - an agent could reopen a
`never` main through a tool - and unset fields were stored as the string "None".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bubble_mcp.cli.main import build_parser
from bubble_mcp.core.config import BubbleMcpSettings, BubbleProfile, load_settings, save_settings
from bubble_mcp.server import tools
from bubble_mcp.server.tools import call_tool


@pytest.fixture(autouse=True)
def _config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUBBLE_MCP_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(tools, "profile_status", lambda *a, **k: {"ok": True, "ready": True, "next_actions": []})


def _seed(tmp_path: Path) -> None:
    (tmp_path / "settings.json").write_text(
        json.dumps({"profiles": {"p": {"app_id": "app", "appname": "app",
                                       "main_write_policy": "never", "tester_mode": True}}}),
        encoding="utf-8",
    )


def _raw(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))["profiles"]


def _no_none_strings(tmp_path: Path) -> None:
    for fields in _raw(tmp_path).values():
        assert "None" not in fields.values()


def test_bootstrap_keeps_the_write_policy_and_tester_mode(tmp_path: Path) -> None:
    _seed(tmp_path)

    call_tool("bubble_project_bootstrap", {"profile": "p"})

    raw = _raw(tmp_path)["p"]
    assert raw["main_write_policy"] == "never"
    assert raw["tester_mode"] is True
    _no_none_strings(tmp_path)


def test_bootstrap_changes_only_the_fields_passed(tmp_path: Path) -> None:
    _seed(tmp_path)

    call_tool("bubble_project_bootstrap", {"profile": "P", "editor_url": "https://bubble.io/page?id=app"})

    assert list(_raw(tmp_path)) == ["p"]  # the tolerant match updates p, not a new "P"
    profile = load_settings().profiles["p"]
    assert profile.editor_url == "https://bubble.io/page?id=app"
    assert profile.main_write_policy == "never" and profile.tester_mode is True
    assert profile.app_version is None
    _no_none_strings(tmp_path)


def test_bootstrap_of_a_new_profile_stores_no_none_strings(tmp_path: Path) -> None:
    call_tool("bubble_project_bootstrap", {"profile": "n", "app_id": "new-app"})

    profile = load_settings().profiles["n"]
    assert (profile.appname, profile.app_version, profile.editor_url) == ("new-app", "test", None)
    _no_none_strings(tmp_path)


def test_cli_profile_add_keeps_the_write_policy_and_tester_mode(tmp_path: Path) -> None:
    _seed(tmp_path)
    args = build_parser().parse_args(["profile", "add", "p", "--app-id", "app", "--editor-url", "https://e"])

    args.func(args)

    raw = _raw(tmp_path)["p"]
    assert raw["main_write_policy"] == "never" and raw["tester_mode"] is True
    assert raw["editor_url"] == "https://e"
    assert "app_version" not in raw
    _no_none_strings(tmp_path)


def test_cli_profile_add_of_a_new_profile_defaults_to_test(tmp_path: Path) -> None:
    args = build_parser().parse_args(["profile", "add", "n", "--app-id", "new-app"])

    args.func(args)

    profile = load_settings().profiles["n"]
    assert (profile.appname, profile.app_version) == ("new-app", "test")
    _no_none_strings(tmp_path)


def test_save_settings_round_trips_never_and_tester_mode(tmp_path: Path) -> None:
    profile = BubbleProfile(name="p", app_id="a", appname="a", main_write_policy="never", tester_mode=True)
    save_settings(BubbleMcpSettings(config_dir=tmp_path, default_profile="p", profiles={"p": profile}))

    loaded = load_settings(tmp_path).profiles["p"]

    assert loaded.main_write_policy == "never" and loaded.tester_mode is True
