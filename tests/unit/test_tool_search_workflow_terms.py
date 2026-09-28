"""A Bubble workflow is an "event" in tool names, and "remove" means delete.

bubble_tool_search("delete a workflow event") did not find delete_event: "delete" is pruned as
a generic verb, and neither "workflow" nor "event" was a target the verb could pair with. The
same weakness hid the clone tool on the team server (2026-09-25).
"""

from __future__ import annotations

import pytest

from bubble_mcp.server.agent_guide import search_tool_catalog


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("delete a workflow event", "delete_event"),
        ("delete a workflow", "delete_event"),
        ("remove a workflow", "delete_event"),
        ("apagar workflow", "delete_event"),
        ("excluir o workflow", "delete_event"),
        ("delete event", "delete_event"),
        ("delete an action from a workflow", "delete_action"),
        ("list workflows", "list_events"),
    ],
)
def test_workflow_queries_rank_the_event_tool_first(query: str, expected: str) -> None:
    names = [match["name"] for match in search_tool_catalog(query, limit=3)["matches"]]

    assert names[0] == expected, (query, names)
