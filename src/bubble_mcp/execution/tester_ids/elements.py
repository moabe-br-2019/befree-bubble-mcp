"""Read HTML ids out of an encoded page tree and check a batch of new ones.

An element's id is ``%p.unique_id``, a TextExpression - ``{"%x": "TextExpression", "%e":
{"0": "btn-start"}}`` - the encoded form of ``properties.unique_id`` in the .bubble export.
Elements nest under ``%el`` maps, so the only unambiguous address is the full pointer.
"""

from __future__ import annotations

import json
import re
from typing import Any

HTML_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
EXPOSE_ID_POINTER = ["settings", "client_safe", "advanced_features", "expose_id_option"]


def html_id_body(value: str) -> dict[str, Any]:
    return {"%x": "TextExpression", "%e": {"0": value}}


def html_id_text(body: object) -> str | None:
    """The plain id, or a stable text for an expression id, or None when there is no id."""

    if not isinstance(body, dict):
        return None
    entries = body.get("%e")
    if not isinstance(entries, dict) or not entries:
        return None
    if list(entries) == ["0"] and isinstance(entries["0"], str):
        return entries["0"] or None
    return json.dumps(body, sort_keys=True)


def collect_elements(context_node: dict[str, Any], context_pointer: list[str]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], pointer: list[str]) -> None:
        children = node.get("%el")
        if not isinstance(children, dict):
            return
        for key, child in children.items():
            if not isinstance(child, dict):
                continue
            child_pointer = [*pointer, "%el", str(key)]
            props = child.get("%p") if isinstance(child.get("%p"), dict) else {}
            body = props.get("unique_id")
            found.append(
                {
                    "pointer": child_pointer,
                    "name": str(child.get("%nm") or ""),
                    "type": str(child.get("%x") or ""),
                    "html_id": html_id_text(body),
                    "html_id_body": body,
                }
            )
            walk(child, child_pointer)

    walk(context_node, [str(part) for part in context_pointer])
    return found


def validate_batch(items: list[dict[str, Any]], elements: list[dict[str, Any]]) -> dict[str, Any]:
    by_pointer = {tuple(e["pointer"]): e for e in elements}
    taken = {e["html_id"]: tuple(e["pointer"]) for e in elements if e["html_id"]}
    problems: list[dict[str, Any]] = []
    write: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    claimed: dict[str, tuple[str, ...]] = {}
    for item in items:
        pointer = tuple(str(part) for part in item.get("pointer") or [])
        new_id = str(item.get("html_id") or "")
        element = by_pointer.get(pointer)
        if element is None:
            problems.append({"error": "element_not_found", "pointer": list(pointer)})
            continue
        if element["html_id"] and not item.get("replace"):
            kept.append({"pointer": list(pointer), "html_id": element["html_id"]})
            continue
        if not HTML_ID_PATTERN.match(new_id):
            problems.append({"error": "invalid_html_id", "pointer": list(pointer), "html_id": new_id})
            continue
        owner = taken.get(new_id)
        if (owner is not None and owner != pointer) or new_id in claimed:
            problems.append({"error": "duplicate_html_id", "pointer": list(pointer), "html_id": new_id})
            continue
        claimed[new_id] = pointer
        write.append(
            {"pointer": list(pointer), "html_id": new_id, "old_body": element["html_id_body"]}
        )
    return {"ok": not problems, "problems": problems, "write": write, "kept": kept}
