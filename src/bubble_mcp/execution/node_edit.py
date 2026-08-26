"""One in-place edit of a live editor node: read, change one part, write back, prove it landed.

/appeditor/write answers HTTP 200 for any body and performs no semantic validation, so a
successful response is not evidence. The only evidence is reading the node back and comparing
it to what was intended, which is what this module does on every executed edit.
"""

from __future__ import annotations

import random
from typing import Any, Callable, Sequence

from bubble_mcp.compiler.payload import bubble_session_id
from bubble_mcp.execution.client import BubbleEditorClient
from bubble_mcp.execution.live_node_read import read_live_node
from bubble_mcp.execution.node_keys import encode_node_root
from bubble_mcp.execution.raw_node_edit import (
    first_divergence,
    patch_expression_leaf,
    reorder_actions,
)
from bubble_mcp.sessions.store import load_session


SUPPORTED_OPS = ("patch", "reorder")

Reader = Callable[..., dict[str, Any]]
Writer = Callable[..., dict[str, Any]]


def _change(
    path_array: Sequence[str], body: Any, *, session_id: str, intent: str = "SetData"
) -> dict[str, Any]:
    """Return one /appeditor/write change entry in the shape PayloadBuilder already produces."""

    intent_obj: dict[str, Any] = {"name": intent}
    if intent == "SetData":
        intent_obj["id"] = random.randint(2, 999)
        intent_obj["source_appname"] = ""
    return {
        "intent": intent_obj,
        "path_array": [str(part) for part in path_array],
        "body": body,
        "version_control_api_version": 4,
        "changelog_data": [],
        "session_id": session_id,
    }


def build_patch_changes(
    pointer: Sequence[str], node: dict[str, Any], session_id: str
) -> list[dict[str, Any]]:
    """Return the single change that writes the whole edited node back at ``pointer``."""

    return [_change(pointer, encode_node_root(node), session_id=session_id)]


def build_reorder_changes(
    pointer: Sequence[str], actions: dict[str, Any], session_id: str
) -> list[dict[str, Any]]:
    """Return the map write plus one index repoint per action.

    Renumbering moves every action to a new path while ``_index.id_to_path`` still points at
    the old one, so the map write alone would leave the editor's index disagreeing with its
    tree. This mirrors the SetData + "Update index" pairing bubble_cli already uses. Every
    change in this list shares one ``session_id``, exactly as ``PayloadBuilder`` does with
    ``self.session_id`` across a batch of changes.
    """

    encoded = {key: encode_node_root(value) for key, value in actions.items()}
    changes = [_change(pointer, encoded, session_id=session_id)]
    prefix = ".".join(str(part) for part in pointer)
    for key, value in actions.items():
        action_id = value.get("id")
        if not action_id:
            continue
        changes.append(
            _change(
                ["_index", "id_to_path", str(action_id)],
                f"{prefix}.{key}",
                session_id=session_id,
                intent="Update index",
            )
        )
    return changes


def edit_live_node(
    *,
    profile: str,
    pointer: Sequence[str],
    op: str,
    leaf_pointer: Sequence[str] | None = None,
    patch: dict[str, Any] | None = None,
    order: Sequence[str] | None = None,
    execute: bool = False,
    app_id: str | None = None,
    reader: Reader | None = None,
    writer: Writer | None = None,
) -> dict[str, Any]:
    """Run one read -> modify -> write -> re-read -> compare cycle over a live editor node."""

    segments = [str(part) for part in pointer]
    if op not in SUPPORTED_OPS:
        return {
            "ok": False,
            "error": "unknown_op",
            "message": f"op must be one of {', '.join(SUPPORTED_OPS)}; got '{op}'",
        }

    read = reader or read_live_node
    before = read(profile, segments, app_id=app_id)
    if not before.get("ok"):
        return before
    current = before["node"]

    session_id = bubble_session_id()
    try:
        intended, changes = _apply(op, segments, current, leaf_pointer, patch, order, session_id)
    except KeyError as error:
        return {"ok": False, "error": "pointer_not_resolved", "message": str(error).strip("'\"")}
    except (ValueError, TypeError) as error:
        return {"ok": False, "error": "invalid_edit", "message": str(error)}

    payload: dict[str, Any] = {"changes": changes}
    if app_id or before.get("app_id"):
        payload["appname"] = str(app_id or before.get("app_id"))

    write = writer
    session = None
    if write is None:
        session = load_session(profile)
        if session is None:
            raise ValueError(f"No Bubble session stored for profile '{profile}'.")
        write = BubbleEditorClient().write

    write_result = write(payload, session, dry_run=not execute)

    result: dict[str, Any] = {
        "ok": True,
        "execute": execute,
        "pointer": segments,
        "before": current,
        "intended": intended,
        "write": write_result,
    }
    if not execute:
        return result
    if not write_result.get("ok"):
        return {**result, "ok": False, "verified": False, "divergence": None}

    after = read(profile, segments, app_id=app_id)
    if not after.get("ok"):
        return {**result, "ok": False, "verified": False, "divergence": None, "after": after}
    result["after"] = after["node"]
    result["divergence"] = first_divergence(intended, after["node"])
    result["verified"] = result["divergence"] is None
    return result


def _apply(
    op: str,
    pointer: Sequence[str],
    current: dict[str, Any],
    leaf_pointer: Sequence[str] | None,
    patch: dict[str, Any] | None,
    order: Sequence[str] | None,
    session_id: str,
) -> tuple[Any, list[dict[str, Any]]]:
    """Return (intended node in the decoded key space, changes to write)."""

    if op == "patch":
        if not leaf_pointer:
            raise ValueError("patch requires leaf_pointer")
        if not isinstance(patch, dict):
            raise ValueError("patch requires a patch object")
        intended = patch_expression_leaf(current, [str(part) for part in leaf_pointer], patch)
        return intended, build_patch_changes(pointer, intended, session_id)
    if not order:
        raise ValueError("reorder requires order")
    intended = reorder_actions(current, [str(part) for part in order])
    return intended, build_reorder_changes(pointer, intended, session_id)
