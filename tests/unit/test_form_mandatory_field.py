"""`mandatory` on the form-element update tools, and the silent drop that hid it.

A Bubble input's "This input should not be empty" checkbox is the ``mandatory`` property, and
the runtime has always written it - under the parameter name ``required``. Neither name was in
the tools' schemas, and every one of these tools declares ``additionalProperties: true``, so
``mandatory=false`` was accepted, matched no parameter, and vanished. The call then reported
"No update fields were provided" and wrote nothing, which reads as "there is nothing to
change" rather than "you used a name I do not know".
"""

from __future__ import annotations

import inspect

import pytest

from bubble_mcp.aria_dispatch import public_aliases_for_runtime_parameter, undeclared_arguments
from bubble_mcp.aria_runtime.bubble_cli import BubbleCLI
from bubble_mcp.server.agent_catalog import enhance_tool_schema
from bubble_mcp.server.catalog import legacy_tool_schema


FORM_UPDATE_TOOLS = (
    "update_input",
    "update_multiline_input",
    "update_dropdown",
    "update_searchbox",
    "update_checkbox",
    "update_datepicker",
    "update_radio",
    "update_file_uploader",
    "update_picture_uploader",
)


@pytest.mark.parametrize("tool", FORM_UPDATE_TOOLS)
def test_every_form_update_tool_declares_required_and_mandatory(tool: str) -> None:
    properties = enhance_tool_schema(legacy_tool_schema(tool))["inputSchema"]["properties"]

    assert properties["required"]["type"] == "boolean"
    assert properties["mandatory"]["type"] == "boolean"


@pytest.mark.parametrize("tool", FORM_UPDATE_TOOLS)
def test_the_runtime_behind_each_of_them_really_takes_required(tool: str) -> None:
    """The schema may only advertise what the runtime accepts, or it advertises a silent drop."""

    assert "required" in inspect.signature(getattr(BubbleCLI, tool)).parameters


def test_mandatory_is_dispatched_as_the_runtime_s_required_parameter() -> None:
    assert "mandatory" in public_aliases_for_runtime_parameter("update_input", "required")


def test_undeclared_arguments_names_an_argument_the_tool_does_not_know() -> None:
    assert undeclared_arguments("update_input", {"context": "c", "element_name": "e", "mandatry": False}) == [
        "mandatry"
    ]


def test_undeclared_arguments_accepts_the_names_the_schema_declares() -> None:
    assert (
        undeclared_arguments(
            "update_input",
            {"profile": "p", "context": "c", "element_name": "e", "mandatory": False, "execute": True},
        )
        == []
    )


def test_undeclared_arguments_is_empty_for_a_tool_with_no_declared_fields() -> None:
    """No field list means nothing to compare against - reporting everything would be noise."""

    assert undeclared_arguments("a_tool_that_does_not_exist", {"anything": 1}) == []
