"""API Connector requests must route to the API Connector tool, not to API tokens or visual edits."""

from __future__ import annotations

import json

import pytest

from bubble_mcp.language.intents import tool_for_intent
from bubble_mcp.server import agent_guide as guide
from bubble_mcp.server.stdio import handle_request


API_CONNECTOR_TASKS = (
    "criar uma chamada de API no API Connector",
    "crie uma chamada de API GET para https://api.example.com/products",
    "create an API Connector call named Get Products",
    "add an external API call to the API Connector plugin",
    "adicionar uma API call no plugin API Connector",
)

FAKE_API_CONNECTOR_TOOL = {
    "name": "create_api_connector_call",
    "description": "Create a new API call inside the Bubble API Connector plugin (chamada de API externa).",
    "inputSchema": {
        "type": "object",
        "properties": {"profile": {}, "name": {}, "method": {}, "url": {}},
        "required": ["profile", "name", "method", "url"],
    },
    "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
}


def _patch_catalog(monkeypatch, *, with_api_connector_tool: bool) -> None:
    from bubble_mcp.server import schemas as schemas_module

    base_tools = [tool for tool in schemas_module.list_tool_schemas() if tool["name"] != "create_api_connector_call"]
    tools = [*base_tools, FAKE_API_CONNECTOR_TOOL] if with_api_connector_tool else base_tools
    monkeypatch.setattr(schemas_module, "list_tool_schemas", lambda: tools)


@pytest.mark.parametrize("task", API_CONNECTOR_TASKS)
def test_agent_guide_routes_api_connector_tasks(task: str) -> None:
    payload = guide.agent_guide(task)

    intents = [route["intent"] for route in payload["recommended_routes"]]
    assert intents[0] == "manage_api_connector", intents
    route = payload["recommended_routes"][0]
    assert route["tools"][0] == "create_api_connector_call"
    assert "create_api_token" not in route["tools"]
    assert "bubble_tool_wizard_start" in route["tools"]
    assert "create_api_token" in route["notes"]


def test_agent_guide_api_token_task_does_not_route_to_api_connector() -> None:
    payload = guide.agent_guide("create a new Data API token called backend")

    intents = [route["intent"] for route in payload["recommended_routes"]]
    assert "manage_api_connector" not in intents


@pytest.mark.parametrize("task", API_CONNECTOR_TASKS)
def test_task_recipe_selects_api_connector_recipe(task: str) -> None:
    recipe = guide.task_recipe(task, profile="client", execute=False)

    assert recipe["recipe"] == "api_connector"
    tools = [step["tool"] for step in recipe["steps"]]
    assert "create_api_connector_call" in tools
    assert "bubble_extension_list" in tools
    assert tools.index("bubble_extension_list") < tools.index("create_api_connector_call")
    assert "create_text" not in tools
    assert any("create_api_token" in gate for gate in recipe["quality_gates"])
    assert any("bubble_tool_wizard_start" in condition for condition in recipe["stop_conditions"])


def test_task_runbook_api_connector_reports_missing_extension_tool(monkeypatch) -> None:
    _patch_catalog(monkeypatch, with_api_connector_tool=False)

    payload = guide.task_runbook("criar uma chamada de API no API Connector", profile="client")

    assert payload["recipe"] == "api_connector"
    names = [match["name"] for match in payload["tool_search"]["matches"]]
    assert "create_api_connector_call" not in names
    assert payload["api_connector_tool"]["available"] is False
    assert "bubble_tool_wizard_start" in payload["api_connector_tool"]["guidance"]
    # Search fallback must not suggest Data API token tools as the primary answer.
    assert names[:1] != ["create_api_token"]


def test_task_runbook_api_connector_prefers_enabled_extension_tool(monkeypatch) -> None:
    _patch_catalog(monkeypatch, with_api_connector_tool=True)

    payload = guide.task_runbook("criar uma chamada de API no API Connector", profile="client")

    names = [match["name"] for match in payload["tool_search"]["matches"]]
    assert names[0] == "create_api_connector_call"
    assert payload["api_connector_tool"]["available"] is True
    assert payload["recommended_next_call"]["tool"] == "bubble_extension_list"


def test_tool_search_ranks_api_connector_call_first_when_enabled(monkeypatch) -> None:
    _patch_catalog(monkeypatch, with_api_connector_tool=True)

    for query in ("chamada de API", "create API Connector call", "api call GET url", "criar chamada de api"):
        result = guide.search_tool_catalog(query, limit=5)
        names = [match["name"] for match in result["matches"]]
        assert names[0] == "create_api_connector_call", (query, names)


def test_create_api_call_intent_points_to_exposed_tool_name() -> None:
    assert tool_for_intent("create_api_call") == "create_api_connector_call"


def test_mcp_agent_guide_api_connector_via_stdio() -> None:
    response = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "bubble_agent_guide", "arguments": {"task": "criar chamada de API no API Connector"}},
        }
    )
    assert response is not None
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["recommended_routes"][0]["intent"] == "manage_api_connector"
