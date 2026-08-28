from __future__ import annotations

import json
from typing import Any

from bubble_mcp.aria_runtime.global_expressions import GlobalExpressionService


class _FixedIds:
    def element_id(self, length: int = 5) -> str:
        return "bGEX0"

    def session_id(self) -> str:
        return "1787859849554x48"


class _Host:
    appname = "global-expression-test"

    def __init__(self, expressions: dict[str, Any] | None = None) -> None:
        self.id_gen = _FixedIds()
        self.discovery: dict[str, Any] = {"global_expressions": expressions or {}}
        self.dispatched: list[dict[str, Any]] = []

    def global_expression_snapshot(self) -> dict[str, Any]:
        return self.discovery["global_expressions"]

    def dispatch_global_expression_payload(self, payload: Any) -> None:
        self.dispatched.append(payload.build())


def _changes_by_path(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {"/".join(change["path_array"]): change for change in payload["changes"]}


def test_create_global_expression_registers_node_and_index() -> None:
    host = _Host()
    service = GlobalExpressionService(host)

    assert service.create_global_expression("User email") is True

    assert len(host.dispatched) == 1
    changes = _changes_by_path(host.dispatched[0])

    node = changes["global_expressions/bGEX0"]
    assert node["intent"]["name"] == "CreateGlobalExpression"
    assert node["body"] == {
        "%nm": "User email",
        "btype_id": "text",
        "id": "bGEX0",
        "is_list": False,
    }

    id_to_path = changes["_index/id_to_path/bGEX0"]
    assert id_to_path["intent"]["name"] == "Update index"
    assert id_to_path["body"] == "global_expressions.bGEX0"

    issues = changes["_index/issues_list/bGEX0"]
    assert issues["intent"]["name"] == "Update index"
    assert json.loads(issues["body"])[0]["node"]["constructor_name"] == "GlobalExpression"


def test_set_parameter_targets_existing_expression_by_name() -> None:
    host = _Host(
        {
            "bTGOw0": {
                "id": "bTGOw0",
                "%nm": "User email",
                "btype_id": "text",
                "is_list": False,
            }
        }
    )
    service = GlobalExpressionService(host)

    assert service.set_global_expression_parameter("User email", "user", parameter_type="user") is True

    changes = _changes_by_path(host.dispatched[0])
    parameter = changes["global_expressions/bTGOw0/parameters/bGEX0"]
    assert parameter["intent"]["name"] == "ModifyGlobalExpression"
    assert parameter["body"] == {
        "btype_id": "user",
        "is_list": False,
        "param_id": "bGEX0",
        "param_name": "user",
    }


def test_set_parameter_refuses_unknown_expression() -> None:
    host = _Host()
    service = GlobalExpressionService(host)

    assert service.set_global_expression_parameter("Missing", "user") is False
    assert host.dispatched == []


def _expression_with_parameter() -> _Host:
    return _Host(
        {
            "bTGOw0": {
                "id": "bTGOw0",
                "%nm": "User email",
                "btype_id": "text",
                "is_list": False,
                "parameters": {
                    "bTGPA0": {
                        "param_id": "bTGPA0",
                        "param_name": "user",
                        "btype_id": "user",
                        "is_list": False,
                    }
                },
            }
        }
    )


def test_set_expression_roots_on_a_parameter_reference() -> None:
    host = _expression_with_parameter()
    service = GlobalExpressionService(host)

    assert service.set_global_expression_expression("User email", parameter="user") is True

    changes = _changes_by_path(host.dispatched[0])
    expression = changes["global_expressions/bTGOw0/expression"]
    assert expression["intent"]["name"] == "ModifyGlobalExpression"
    assert expression["body"] == {
        "%x": "GlobalExpressionParameter",
        "%p": {"global_expression_id": "bTGOw0", "param_id": "bTGPA0"},
        "%n": None,
        "is_slidable": False,
    }


def test_set_expression_chains_a_field_onto_the_parameter() -> None:
    host = _expression_with_parameter()
    service = GlobalExpressionService(host)

    assert service.set_global_expression_expression("User email", parameter="user", field="email") is True

    expression = _changes_by_path(host.dispatched[0])["global_expressions/bTGOw0/expression"]
    assert expression["body"]["%n"] == {"%x": "Message", "%nm": "email", "is_slidable": False}


def test_set_expression_refuses_unknown_parameter() -> None:
    host = _expression_with_parameter()
    service = GlobalExpressionService(host)

    assert service.set_global_expression_expression("User email", parameter="missing") is False
    assert host.dispatched == []


def test_expression_resolves_by_its_decoded_export_name() -> None:
    """The .bubble export decodes ``%nm`` to ``name``; both forms must resolve."""
    host = _Host(
        {
            "bTGOu0": {
                "id": "bTGOu0",
                "name": "zz-expr-1",
                "btype_id": "text",
                "is_list": False,
            }
        }
    )
    service = GlobalExpressionService(host)

    assert service.set_global_expression_parameter("zz-expr-1", "user") is True

    changes = _changes_by_path(host.dispatched[0])
    assert "global_expressions/bTGOu0/parameters/bGEX0" in changes
