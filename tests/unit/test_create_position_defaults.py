"""Created elements must always carry X/Y coordinates.

Bubble's Issue Checker reports "<element>: remember to fill out X" / "... Y"
when an element sits in a fixed-layout parent without %l/%t. The compiler does
not always know the parent's layout, so every created visual element ships
position defaults; responsive parents ignore them.
"""

from typing import Any

import pytest

from bubble_mcp.compiler.payload import compile_plan_to_write_payloads


def _create_body(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    step_args = {"context": "index", "parent": "root", **args}
    payload = compile_plan_to_write_payloads(
        {"steps": [{"id": "s1", "tool_name": tool_name, "args": step_args}]},
        app_id="synthetic-app",
    )["steps"][0]["args"]["write_payload"]
    return next(
        change
        for change in payload["changes"]
        if change.get("intent", {}).get("name") == "CreateElement"
    )["body"]


@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("create_group", {"name": "gp_page_wrap", "layout": "column"}),
        ("create_group", {"name": "gp_row", "layout": "row"}),
        ("create_text", {"name": "tx_title", "content": "Title"}),
    ],
)
def test_create_emits_position_defaults(tool_name: str, args: dict[str, Any]) -> None:
    properties = _create_body(tool_name, args)["%p"]

    assert properties.get("%t") == 0
    assert properties.get("%l") == 0


def test_explicit_position_is_preserved() -> None:
    properties = _create_body("create_group", {"name": "gp_abs", "top": 120, "left": 40})["%p"]

    assert properties["%t"] == 120
    assert properties["%l"] == 40
