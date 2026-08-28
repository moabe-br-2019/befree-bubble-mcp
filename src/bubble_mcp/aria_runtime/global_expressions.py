"""Global expression lifecycle: app-level named expressions with parameters.

The editor writes these under ``global_expressions.<id>`` with three changes per
creation: the ``_index.id_to_path`` alias, the ``CreateGlobalExpression`` node
itself, and the ``_index.issues_list`` entry that keeps the editor's issue
checker aware of the still-empty expression.
"""

from __future__ import annotations

import json
from typing import Any

try:
    from .bubble_sdk import PayloadBuilder, logger
except ImportError:  # pragma: no cover - direct BubbleCLI execution compatibility
    from bubble_sdk import PayloadBuilder, logger


class GlobalExpressionService:
    """Author Bubble global expressions without owning SDK wire construction."""

    def __init__(self, host: Any) -> None:
        self._host = host

    def create_global_expression(
        self,
        name: str,
        expression_type: str = "text",
        is_list: bool = False,
        dry_run: bool = False,
    ) -> bool:
        expression_id = self._host.id_gen.element_id()
        payload = PayloadBuilder(self._host.appname)
        payload.add_update_index(
            ["_index", "id_to_path", expression_id],
            f"global_expressions.{expression_id}",
        )
        payload.add_change(
            "CreateGlobalExpression",
            ["global_expressions", expression_id],
            {
                "%nm": name,
                "btype_id": expression_type,
                "id": expression_id,
                "is_list": bool(is_list),
            },
        )
        payload.add_update_index(
            ["_index", "issues_list", expression_id],
            self._pending_expression_issue(expression_id),
        )

        if dry_run:
            logger.info(f"\n DRY RUN - Global Expression Creation Payload ({expression_id}):")
            logger.log(payload.to_json())
            return True

        try:
            self._host.dispatch_global_expression_payload(payload)
        except Exception as exc:
            logger.error(f"Failed to create global expression '{name}': {exc}")
            return False
        return True

    def set_global_expression_parameter(
        self,
        expression: str,
        parameter_name: str,
        parameter_type: str = "text",
        is_list: bool = False,
        parameter_id: str | None = None,
        dry_run: bool = False,
    ) -> bool:
        expression_id = self._resolve_expression_id(expression)
        if not expression_id:
            logger.error(f"Global expression '{expression}' not found.")
            return False

        param_id = parameter_id or self._host.id_gen.element_id()
        payload = PayloadBuilder(self._host.appname)
        payload.add_change(
            "ModifyGlobalExpression",
            ["global_expressions", expression_id, "parameters", param_id],
            {
                "btype_id": parameter_type,
                "is_list": bool(is_list),
                "param_id": param_id,
                "param_name": parameter_name,
            },
        )

        if dry_run:
            logger.info(f"\n DRY RUN - Global Expression Parameter Payload ({expression_id}.{param_id}):")
            logger.log(payload.to_json())
            return True

        try:
            self._host.dispatch_global_expression_payload(payload)
        except Exception as exc:
            logger.error(f"Failed to set parameter '{parameter_name}' on '{expression}': {exc}")
            return False
        return True

    def set_global_expression_expression(
        self,
        expression: str,
        parameter: str,
        field: str | None = None,
        dry_run: bool = False,
    ) -> bool:
        expression_id = self._resolve_expression_id(expression)
        if not expression_id:
            logger.error(f"Global expression '{expression}' not found.")
            return False

        param_id = self._resolve_parameter_id(expression_id, parameter)
        if not param_id:
            logger.error(f"Parameter '{parameter}' not found on global expression '{expression}'.")
            return False

        node: dict[str, Any] = {
            "%x": "GlobalExpressionParameter",
            "%p": {"global_expression_id": expression_id, "param_id": param_id},
            "%n": None,
            "is_slidable": False,
        }
        if field:
            node["%n"] = {"%x": "Message", "%nm": field, "is_slidable": False}

        payload = PayloadBuilder(self._host.appname)
        payload.add_change(
            "ModifyGlobalExpression",
            ["global_expressions", expression_id, "expression"],
            node,
        )

        if dry_run:
            logger.info(f"\n DRY RUN - Global Expression Body Payload ({expression_id}):")
            logger.log(payload.to_json())
            return True

        try:
            self._host.dispatch_global_expression_payload(payload)
        except Exception as exc:
            logger.error(f"Failed to set expression on '{expression}': {exc}")
            return False
        return True

    def _resolve_parameter_id(self, expression_id: str, parameter: str) -> str | None:
        """Accept either the parameter id or its editor-visible name."""
        target = str(parameter or "").strip()
        if not target:
            return None
        snapshot = self._host.global_expression_snapshot()
        definition = snapshot.get(expression_id) if isinstance(snapshot, dict) else None
        parameters = definition.get("parameters") if isinstance(definition, dict) else None
        if not isinstance(parameters, dict):
            return None
        if target in parameters:
            return target
        lowered = target.lower()
        for param_id, param in parameters.items():
            if not isinstance(param, dict):
                continue
            if str(param.get("param_name") or "").strip().lower() == lowered:
                return param_id
        return None

    def _resolve_expression_id(self, expression: str) -> str | None:
        """Accept either the expression id or its display name."""
        target = str(expression or "").strip()
        if not target:
            return None
        snapshot = self._host.global_expression_snapshot()
        if not isinstance(snapshot, dict):
            return None
        if target in snapshot:
            return target
        lowered = target.lower()
        for expression_id, definition in snapshot.items():
            if not isinstance(definition, dict):
                continue
            name = str(definition.get("name") or definition.get("%nm") or "").strip().lower()
            if name == lowered:
                return expression_id
        return None

    @staticmethod
    def _pending_expression_issue(expression_id: str) -> str:
        return json.dumps(
            [
                {
                    "message": "Global expression: remember to fill out expression",
                    "node": {
                        "constructor_name": "GlobalExpression",
                        "args": [
                            {
                                "type": "json",
                                "value": f"global_expressions.{expression_id}",
                            }
                        ],
                    },
                }
            ]
        )
