"""The tester tools are wired, pass their arguments through, and are not blocked by the main lock."""

from __future__ import annotations

from typing import Any

import pytest

from bubble_mcp.server import tools
from bubble_mcp.server.agent_catalog import tool_annotations
from bubble_mcp.server.schemas import list_tool_schemas

TESTER_TOOLS = ("bubble_test_ids_plan", "bubble_test_ids_apply", "bubble_test_ids_restore")


def test_the_tools_are_listed_with_schemas() -> None:
    names = {tool["name"] for tool in list_tool_schemas()}

    assert set(TESTER_TOOLS) <= names
    assert tool_annotations("bubble_test_ids_plan")["readOnlyHint"] is True
    assert tool_annotations("bubble_test_ids_apply")["readOnlyHint"] is False


def test_apply_passes_its_arguments_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_apply(profile: str, pointer: list[str], ids: list[dict], **kwargs: Any) -> dict:
        seen.update(profile=profile, pointer=pointer, ids=ids, **kwargs)
        return {"ok": True}

    monkeypatch.setattr(tools, "apply_tester_ids", fake_apply)

    tools._call_tool("bubble_test_ids_apply", {
        "profile": "p", "pointer": ["%p3", "pg"], "execute": True,
        "ids": [{"pointer": ["%p3", "pg", "%el", "c"], "html_id": "x"}],
    })

    assert seen["pointer"] == ["%p3", "pg"] and seen["execute"] is True
    assert seen["ids"][0]["html_id"] == "x"


def test_restore_maps_all_and_element_pointers(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(tools, "restore_tester_ids", lambda profile, **kw: seen.update(kw) or {"ok": True})

    tools._call_tool("bubble_test_ids_restore", {"profile": "p", "all": True,
                                                 "element_pointers": [["%p3", "pg", "%el", "c"]]})

    assert seen["restore_all"] is True
    assert seen["pointers"] == [["%p3", "pg", "%el", "c"]]


def _profile(monkeypatch: pytest.MonkeyPatch, *, tester_mode: bool) -> list[str]:
    """A stored profile and a savepoint recorder; nothing here touches the network."""

    from types import SimpleNamespace

    from bubble_mcp.execution.tester_ids import service

    monkeypatch.setattr(service, "load_settings", lambda: object())
    monkeypatch.setattr(
        service, "resolve_profile",
        lambda settings, name: SimpleNamespace(tester_mode=tester_mode, app_id="solo-app"),
    )
    monkeypatch.setattr(tools, "load_session", lambda profile: SimpleNamespace(app_id="solo-app", app_version="test"))
    monkeypatch.setattr(tools, "check_session", lambda profile: {"logged_in": True})
    savepoints: list[str] = []
    monkeypatch.setattr(tools, "create_savepoint", lambda **kw: savepoints.append(kw["message"]) or {"ok": True})
    return savepoints


APPLY = {"profile": "p", "pointer": ["%p3", "pg"], "execute": True,
         "ids": [{"pointer": ["%p3", "pg", "%el", "c"], "html_id": "x"}]}


def test_an_executed_apply_on_live_is_refused_before_any_savepoint(monkeypatch: pytest.MonkeyPatch) -> None:
    savepoints = _profile(monkeypatch, tester_mode=True)

    result = tools.call_tool("bubble_test_ids_apply", {**APPLY, "app_version": "live"})

    assert result["error"] == "live_never" and result["executed"] is False
    assert savepoints == []


def test_an_executed_apply_without_tester_mode_is_refused_before_any_savepoint(monkeypatch: pytest.MonkeyPatch) -> None:
    savepoints = _profile(monkeypatch, tester_mode=False)

    for name, args in (("bubble_test_ids_apply", APPLY), ("bubble_test_ids_restore", {"profile": "p", "all": True, "execute": True})):
        result = tools.call_tool(name, {**args, "app_version": "test"})
        assert result["error"] == "tester_mode_off" and result["executed"] is False
    assert savepoints == []


def test_the_main_lock_stops_ordinary_writes_but_leaves_the_tester_tools_to_their_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    _profile(monkeypatch, tester_mode=True)
    monkeypatch.setattr(tools, "main_write_allowed", lambda version, session, app_id: (False, "use a branch"))
    args = {"profile": "p", "execute": True, "app_version": "test"}

    assert tools.main_write_refusal("create_text", args)["error"] == "main_is_read_only"
    for name in ("bubble_test_ids_apply", "bubble_test_ids_restore"):
        assert tools.main_write_refusal(name, args) is None


def test_restore_rejects_element_pointers_that_are_not_lists() -> None:
    with pytest.raises(ValueError, match="element_pointers"):
        tools._call_tool("bubble_test_ids_restore", {"profile": "p", "element_pointers": ["%p3/pg"]})
