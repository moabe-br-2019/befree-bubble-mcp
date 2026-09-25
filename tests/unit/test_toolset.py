"""BUBBLE_MCP_TOOLSET=core: a small tools/list, and the whole catalog behind two meta tools.

Team server, 2026-09-25: the full tools/list is ~350 tools and ~300k tokens. Through OpenRouter,
without deferred tool loading, Opus received ~432k tokens per request and a 262k-context model
failed with "maximum context length".
"""

from __future__ import annotations

import json

import pytest

from bubble_mcp.core.config import BubbleMcpSettings, BubbleProfile, save_settings
from bubble_mcp.server import tools as tools_module
from bubble_mcp.server.schemas import list_tool_schemas
from bubble_mcp.server.stdio import handle_request
from bubble_mcp.server.toolset import DEFAULT_CORE_TOOLS


def _listed_names() -> list[str]:
    response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response is not None
    return [tool["name"] for tool in response["result"]["tools"]]


def _call(name: str, arguments: dict) -> dict:
    response = handle_request(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    )
    assert response is not None
    return response["result"]


def test_full_is_the_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("BUBBLE_MCP_TOOLSET", raising=False)

    names = _listed_names()

    assert len(names) == len(list_tool_schemas())
    assert "bubble_call" not in names


def test_core_lists_the_core_tools_and_the_two_meta_tools(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("BUBBLE_MCP_TOOLSET", "core")

    names = _listed_names()

    assert names == [*DEFAULT_CORE_TOOLS, "bubble_tool_schema", "bubble_call"]
    response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response is not None
    assert len(json.dumps(response["result"]["tools"])) < 60_000  # ~15k tokens at most


def test_the_core_set_can_be_configured(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("BUBBLE_MCP_TOOLSET", "core")
    monkeypatch.setenv("BUBBLE_MCP_CORE_TOOLS", "bubble_context_find, delete_event,not_a_tool")

    assert _listed_names() == ["bubble_context_find", "delete_event", "bubble_tool_schema", "bubble_call"]


def test_core_instructions_point_at_the_meta_tools(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("BUBBLE_MCP_TOOLSET", "core")
    response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})

    assert response is not None
    assert "bubble_call" in response["result"]["instructions"]


def test_tool_schema_returns_full_schemas_by_name() -> None:
    result = tools_module.call_tool("bubble_tool_schema", {"names": ["delete_event", "nope"]})

    expected = next(tool for tool in list_tool_schemas() if tool["name"] == "delete_event")
    assert result["tools"] == [expected]
    assert result["unknown"] == ["nope"]


def test_tool_schema_searches_the_whole_catalog() -> None:
    result = tools_module.call_tool("bubble_tool_schema", {"query": "delete event", "limit": 5})

    assert "delete_event" in [match["name"] for match in result["matches"]]


def test_bubble_call_runs_the_named_tool() -> None:
    direct = tools_module.call_tool("bubble_tool_search", {"query": "create button", "limit": 3})

    assert tools_module.call_tool(
        "bubble_call", {"name": "bubble_tool_search", "arguments": {"query": "create button", "limit": 3}}
    ) == direct


def test_bubble_call_keeps_main_read_only(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("BUBBLE_MCP_CONFIG_DIR", str(tmp_path))
    save_settings(
        BubbleMcpSettings(
            config_dir=tmp_path,
            default_profile="team",
            profiles={"team": BubbleProfile(name="team", app_id="kaimia-app", appname="kaimia-app", app_version="test")},
        )
    )

    result = tools_module.call_tool(
        "bubble_call",
        {"name": "delete_event", "arguments": {"profile": "team", "execute": True, "context": "index", "event": "bX"}},
    )

    assert result["error"] == "main_is_read_only"


def test_bubble_call_rejects_unknown_and_meta_targets() -> None:
    with pytest.raises(ValueError, match="Unknown tool"):
        tools_module.call_tool("bubble_call", {"name": "no_such_tool"})
    with pytest.raises(ValueError, match="cannot call"):
        tools_module.call_tool("bubble_call", {"name": "bubble_call"})
    assert _call("bubble_call", {"name": "no_such_tool"})["isError"] is True
