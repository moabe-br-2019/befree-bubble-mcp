"""Every exposed tool must carry a description an agent can pick it by — no shared category blurbs."""

from __future__ import annotations

import collections
import re

from bubble_mcp.server.agent_catalog import legacy_description
from bubble_mcp.server.schemas import list_tool_schemas


def _core(description: str) -> str:
    """Strip the docs-enrichment suffix so we compare the agent-facing sentence only."""

    return re.split(r"\s*Docs-enrichment family:", description, maxsplit=1)[0].strip()


def test_no_two_tools_share_the_same_description() -> None:
    tools = list_tool_schemas()
    by_description: dict[str, list[str]] = collections.defaultdict(list)
    for tool in tools:
        by_description[_core(str(tool.get("description") or ""))].append(str(tool["name"]))

    duplicates = {desc: names for desc, names in by_description.items() if len(names) > 1}
    assert duplicates == {}, "\n".join(f"{len(names)}x {desc[:90]!r}: {names}" for desc, names in duplicates.items())


def test_workflow_tools_describe_their_own_operation() -> None:
    tools = {tool["name"]: tool for tool in list_tool_schemas()}

    create_workflow = _core(tools["create_workflow"]["description"])
    add_action = _core(tools["add_action"]["description"])
    delete_action = _core(tools["delete_action"]["description"])
    create_event = _core(tools["create_event"]["description"])

    assert create_workflow != add_action != delete_action != create_event
    assert "workflow" in create_workflow.lower()
    assert "action" in add_action.lower() and "add" in add_action.lower()
    assert "delete" in delete_action.lower() and "action" in delete_action.lower()
    assert "event" in create_event.lower()


def test_legacy_descriptions_drop_generic_boilerplate() -> None:
    for name in ("create_workflow", "create_data_type", "update_color", "create_option_set", "set_app_setting"):
        description = legacy_description(name)
        assert "Aria-compatible" not in description
        assert "not because the user named the tool" not in description
        assert len(description) <= 420, (name, len(description))


def test_api_token_tools_warn_against_api_connector_confusion() -> None:
    tools = {tool["name"]: tool for tool in list_tool_schemas()}
    for name in ("create_api_token", "rename_api_token", "regenerate_api_token", "delete_api_token"):
        description = tools[name]["description"]
        assert "Data API" in description
        assert "API Connector" in description


def test_every_tool_description_is_specific_and_bounded() -> None:
    for tool in list_tool_schemas():
        description = _core(str(tool.get("description") or ""))
        assert len(str(tool.get("description") or "")) >= 80, tool["name"]  # catalog quality gate minimum
        assert "Operate on Bubble editor metadata or project structure." not in description, tool["name"]
