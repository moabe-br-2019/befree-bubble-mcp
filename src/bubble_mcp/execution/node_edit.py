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
DEFAULT_APP_VERSION = "test"
VERIFIED_MEANING = (
    "verified=true means the node was read back after the write and its bytes matched the "
    "intent. It does NOT mean the Bubble editor renders the node: a wrongly decoded interior "
    "round-trips byte-identically through /appeditor/write and .raw(), so render_unverified "
    "stays true until a human opens the editor and confirms the step displays correctly."
)

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


# Keys whose values hold further nodes. The guard descends through these and nothing else:
# everything under a node's own %p is the expression interior, which is legitimately spelled
# with decoded-looking keys ("type": "Message") and must never be inspected or rewritten.
_CONTAINER_KEYS = ("actions", "%el", "%wf")


def _is_decoded_node_root(value: dict[str, Any]) -> bool:
    """True when ``value`` looks like a node written with export keys instead of write keys."""

    return ("type" in value or "properties" in value) and not ("%x" in value or "%p" in value)


def _assert_encoded_node_roots(value: Any, path: tuple[str, ...] = ()) -> None:
    """Refuse a change body carrying a node root that was never translated.

    ``encode_node_root`` translates exactly one root, so any body assembled around more than
    one node - an actions map, a workflow root, an element tree - can carry untranslated nodes
    underneath the one that was encoded. Those are accepted by /appeditor/write with HTTP 200
    and render "[missing: null]". ``lint_editor_write_changes`` cannot catch them: it is
    path-shaped and only inspects the body at a path that already looks like a node position.
    """

    if not isinstance(value, dict):
        return
    if _is_decoded_node_root(value) or "%x" in value or "%p" in value:
        _assert_encoded_node(value, path)
        return
    _assert_encoded_container(value, path)


def _assert_encoded_node(node: dict[str, Any], path: tuple[str, ...]) -> None:
    if _is_decoded_node_root(node):
        where = ".".join(path) or "<root>"
        raise ValueError(
            f"change body at '{where}' carries a decoded node root "
            f"({', '.join(sorted(key for key in ('type', 'properties') if key in node))}); "
            "every node in a written body must have its root encoded (%x/%p). Point this edit "
            "at a single action node instead of a container."
        )
    for key in _CONTAINER_KEYS:
        child = node.get(key)
        if isinstance(child, dict):
            _assert_encoded_container(child, (*path, key))


def _assert_encoded_container(container: dict[str, Any], path: tuple[str, ...]) -> None:
    for key, child in container.items():
        if isinstance(child, dict):
            _assert_encoded_node(child, (*path, str(key)))


def _guard_changes(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Walk every assembled change body and refuse any untranslated node root inside it."""

    for change in changes:
        _assert_encoded_node_roots(change.get("body"))
    return changes


def build_patch_changes(
    pointer: Sequence[str], node: dict[str, Any], session_id: str
) -> list[dict[str, Any]]:
    """Return the single change that writes the whole edited node back at ``pointer``."""

    return _guard_changes([_change(pointer, encode_node_root(node), session_id=session_id)])


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
            raise ValueError(
                f"action at position '{key}' has no id, so its index entry cannot be repointed; "
                "a reorder that renumbers a step without moving its index leaves the editor's "
                "_index.id_to_path pointing at another action"
            )
        changes.append(
            _change(
                ["_index", "id_to_path", str(action_id)],
                f"{prefix}.{key}",
                session_id=session_id,
                intent="Update index",
            )
        )
    return _guard_changes(changes)


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
    app_version: str = DEFAULT_APP_VERSION,
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
    try:
        _check_op_arguments(op, leaf_pointer, patch, order)
    except (ValueError, TypeError) as error:
        # Checked before the read so a missing argument costs a typo, not a browser launch.
        return {"ok": False, "error": "invalid_edit", "message": str(error)}

    version = str(app_version or DEFAULT_APP_VERSION)
    read = reader or read_live_node
    before = read(profile, segments, app_id=app_id, app_version=version)
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

    payload: dict[str, Any] = {"changes": changes, "app_version": version}
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

    result["render_unverified"] = True
    result["verified_meaning"] = VERIFIED_MEANING
    if not write_result.get("ok"):
        return {**result, "ok": False, "verified": False, "divergence": None}

    after_read = read(profile, segments, app_id=app_id, app_version=version)
    if not after_read.get("ok"):
        # ``after`` is reserved for the node itself; a failed re-read is a different shape.
        return {
            **result,
            "ok": False,
            "verified": False,
            "divergence": None,
            "after_read": after_read,
        }
    result["after"] = after_read["node"]
    result["divergence"] = first_divergence(intended, after_read["node"])
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

    _check_op_arguments(op, leaf_pointer, patch, order)
    if op == "patch":
        if not isinstance(current, dict) or not ("type" in current or "%x" in current):
            raise ValueError(
                "patch requires a pointer to a node, not a container: encode_node_root "
                "translates exactly one root, so every action under a container pointer would "
                f"be written with decoded keys. Point at one action, e.g. "
                f"{[*[str(part) for part in pointer], '0']}."
            )
        intended = patch_expression_leaf(current, [str(part) for part in leaf_pointer or []], patch or {})
        return intended, build_patch_changes(pointer, intended, session_id)
    intended = reorder_actions(current, [str(part) for part in order or []])
    return intended, build_reorder_changes(pointer, intended, session_id)


def _check_op_arguments(
    op: str,
    leaf_pointer: Sequence[str] | None,
    patch: dict[str, Any] | None,
    order: Sequence[str] | None,
) -> None:
    """Raise when the arguments the op needs are missing, before anything is read."""

    if op == "patch":
        if not leaf_pointer:
            raise ValueError("patch requires leaf_pointer")
        if not isinstance(patch, dict):
            raise ValueError("patch requires a patch object")
        return
    if not order:
        raise ValueError("reorder requires order")
