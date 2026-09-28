"""Which tools tools/list advertises: the whole catalog, or a core set plus two meta tools.

The full catalog is ~350 tools and ~300k tokens of schemas. A client with deferred tool loading
(Claude Code with tool search) copes; one without it sends every schema with every request, and
a 262k-context model cannot start at all. ``BUBBLE_MCP_TOOLSET=core`` lists the tools most tasks
start from, plus ``bubble_tool_schema`` (search the catalog, or fetch full schemas by name) and
``bubble_call`` (call any catalog tool by name). Every tool stays callable in either mode; only
the listing changes.

``BUBBLE_MCP_CORE_TOOLS`` (comma-separated names) replaces the default core set.
"""

from __future__ import annotations

import os
from typing import Any

TOOLSET_ENV = "BUBBLE_MCP_TOOLSET"
CORE_TOOLS_ENV = "BUBBLE_MCP_CORE_TOOLS"

TOOL_SCHEMA_TOOL = "bubble_tool_schema"
CALL_TOOL = "bubble_call"
META_TOOL_NAMES = frozenset({TOOL_SCHEMA_TOOL, CALL_TOOL})

DEFAULT_CORE_TOOLS: tuple[str, ...] = (
    "bubble_task_runbook",
    "bubble_agent_guide",
    "bubble_readiness_check",
    "bubble_profile_status",
    "bubble_session_check",
    "bubble_session_login",
    "bubble_branch_list",
    "bubble_context_find",
    "bubble_context_query",
    "bubble_context_summary",
    "bubble_live_node_read",
    "bubble_node_edit",
    "bubble_clone_workflow",
    "bubble_duplicate_element",
    "bubble_editor_write",
    "bubble_savepoint_list",
    "bubble_e2e_run",
    "batch",
)


def active_toolset() -> str:
    """``core`` or ``full`` (the default, and what any other value means)."""

    return "core" if os.environ.get(TOOLSET_ENV, "").strip().lower() == "core" else "full"


def core_tool_names() -> tuple[str, ...]:
    configured = [name.strip() for name in os.environ.get(CORE_TOOLS_ENV, "").split(",") if name.strip()]
    return tuple(dict.fromkeys(configured)) if configured else DEFAULT_CORE_TOOLS


def meta_tool_schemas() -> list[dict[str, Any]]:
    read_only = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
    return [
        {
            "name": TOOL_SCHEMA_TOOL,
            "description": (
                "Find a Bubble MCP tool that tools/list does not show, and get its full input schema. "
                "Pass query to search the whole catalog (compact matches), or names to get the complete "
                "inputSchema of those tools. Then run one with bubble_call."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What the tool should do, e.g. 'delete a workflow'."},
                    "names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Exact tool names whose full schemas to return.",
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 8, "description": "Most search matches to return."},
                },
                "anyOf": [{"required": ["query"]}, {"required": ["names"]}],
            },
            "annotations": read_only,
        },
        {
            "name": CALL_TOOL,
            "description": (
                "Call any Bubble MCP catalog tool by name, including ones tools/list does not show. "
                "arguments is exactly what that tool's inputSchema takes (see bubble_tool_schema). The "
                "call runs through the same checks as a direct one: previews by default, main is read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The catalog tool to call, e.g. delete_event."},
                    "arguments": {"type": "object", "description": "That tool's arguments.", "default": {}},
                },
                "required": ["name"],
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
        },
    ]


def listed_tool_schemas(all_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What tools/list returns for the active toolset."""

    if active_toolset() != "core":
        return all_tools
    by_name = {str(tool.get("name")): tool for tool in all_tools}
    core = [by_name[name] for name in core_tool_names() if name in by_name]
    return [*core, *meta_tool_schemas()]


def tool_schema_payload(args: dict[str, Any], all_tools: list[dict[str, Any]]) -> dict[str, Any]:
    names = [str(name) for name in args.get("names") or [] if str(name).strip()]
    if names:
        by_name = {str(tool.get("name")): tool for tool in [*all_tools, *meta_tool_schemas()]}
        return {
            "ok": True,
            "tools": [by_name[name] for name in names if name in by_name],
            "unknown": [name for name in names if name not in by_name],
        }
    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("bubble_tool_schema needs query (to search) or names (to fetch schemas).")
    from bubble_mcp.server.agent_guide import search_tool_catalog

    result = search_tool_catalog(query, limit=int(args.get("limit") or 8), tool_schemas=all_tools)
    return {**result, "next": "Pass names=[...] for the full inputSchema, then run the tool with bubble_call."}
