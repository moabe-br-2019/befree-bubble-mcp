"""Background style values must reach Bubble as wire enum values.

The MCP catalog exposes ``bg_style`` with the friendly enum
``none | color | image | gradient``. Bubble's editor stores the flat-color
option as ``bgcolor``; writing the literal ``color`` produces the Issue
Checker error "<element> - is not a possible option".
"""

from typing import Any

import pytest

from bubble_mcp.aria_runtime.bubble_sdk import ElementBuilder
from bubble_mcp.compiler.payload import compile_plan_to_write_payloads


def _create_properties(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    step_args = {"context": "index", "parent": "root", **args}
    payload = compile_plan_to_write_payloads(
        {"steps": [{"id": "s1", "tool_name": tool_name, "args": step_args}]},
        app_id="synthetic-app",
    )["steps"][0]["args"]["write_payload"]
    body = next(
        change
        for change in payload["changes"]
        if change.get("intent", {}).get("name") == "CreateElement"
    )["body"]
    properties = dict(body["%p"])
    for change in payload["changes"]:
        if change.get("intent", {}).get("name") == "SetData":
            path = change.get("path_array") or []
            if path[-2:-1] == ["%p"]:
                properties[path[-1]] = change.get("body")
    return properties


@pytest.mark.parametrize("bg_key", ["bg_style", "background_style"])
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("color", "bgcolor"),
        ("flat color", "bgcolor"),
        ("Flat-Color", "bgcolor"),
        ("bgcolor", "bgcolor"),
        ("image", "image"),
        ("gradient", "gradient"),
        ("none", "none"),
    ],
)
def test_create_group_normalizes_background_style(bg_key: str, raw: str, expected: str) -> None:
    properties = _create_properties(
        "create_group",
        {"name": "gp_tasks", bg_key: raw, "bg_color": "#FFFFFF"},
    )

    assert properties["%bas"] == expected


def test_update_group_normalizes_background_style() -> None:
    payload = compile_plan_to_write_payloads(
        {
            "steps": [
                {
                    "id": "s1",
                    "tool_name": "update_group",
                    "args": {
                        "context": "index",
                        "element_name": "gp_tasks",
                        "background_style": "color",
                    },
                }
            ]
        },
        app_id="synthetic-app",
    )["steps"][0]["args"]["write_payload"]

    values = [
        change.get("body")
        for change in payload["changes"]
        if (change.get("path_array") or [])[-1] == "%bas"
    ]
    bodies = [
        change.get("body", {}).get("%bas")
        for change in payload["changes"]
        if isinstance(change.get("body"), dict) and "%bas" in change.get("body", {})
    ]

    assert "bgcolor" in values + bodies
    assert "color" not in values + bodies


def test_element_builder_normalizes_background_style() -> None:
    body = ElementBuilder().group(name="gp_tasks", background_style="color", bg_color="#FFFFFF")

    assert body["%p"]["%bas"] == "bgcolor"
