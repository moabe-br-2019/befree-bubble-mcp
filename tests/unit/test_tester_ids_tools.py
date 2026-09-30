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


def test_the_main_lock_leaves_the_tester_tools_to_their_own_gate() -> None:
    for name in ("bubble_test_ids_apply", "bubble_test_ids_restore"):
        assert tools.main_write_refusal(name, {"profile": "p", "execute": True, "app_version": "test"}) is None
