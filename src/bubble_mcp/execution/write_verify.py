"""Universal post-write verification: re-read every path a change wrote and compare it to intent.

/appeditor/write answers HTTP 200 for any body and performs no semantic validation
(``docs/session-findings-2026-08-26.md``). ``bubble_node_edit`` (``node_edit.py``) already reads
its one hand-built change back and compares it - that pattern caught three real defects in one
session that a bare 200 hid completely (an action written to a path that did not exist, a page
NAME written where a node key belonged, an element NAME written where a resolved id belonged).
Every OTHER mutating tool only ever sees the 200.

The key fact that makes verification generic instead of per-tool: every change entry in a
compiled write payload is self-describing. It carries ``path_array`` (where it wrote) and
``body`` (what it wrote, already in the encoded key space ``read_live_node``/``read_live_nodes``
read with - see ``live_node_read``'s module docstring). So a generic verifier can re-read each
written path and diff it against the body that was sent, for any tool, without knowing anything
about what that tool does.
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from bubble_mcp.execution.live_node_read import read_live_nodes
from bubble_mcp.execution.raw_node_edit import first_divergence


VERIFIED_MEANING = (
    "verified=true means every written path was read back after the write and its bytes matched "
    "the intent. It does NOT mean the Bubble editor renders the change: a wrongly assembled "
    "interior can round-trip byte-identically through /appeditor/write and ._raw(), so "
    "render_unverified stays true until a human opens the editor and confirms the change "
    "displays correctly."
)

Reader = Callable[..., dict[tuple[str, ...], dict[str, Any]]]


def _intent_name(change: dict[str, Any]) -> str:
    intent = change.get("intent")
    if isinstance(intent, dict):
        return str(intent.get("name") or "").strip()
    return str(intent or "").strip()


def _is_delete_intent(change: dict[str, Any]) -> bool:
    return _intent_name(change).lower() == "delete"


def _is_bookkeeping_path(segments: Sequence[str]) -> bool:
    # `_index` is the editor's own lookup/bookkeeping tree (id_to_path, issues_list, ...), not
    # the app tree: it is not readable through appquery the same way a normal node pointer is,
    # and it is not what an author intended to change.
    return bool(segments) and segments[0] == "_index"


def _plan_change(change: dict[str, Any]) -> dict[str, Any] | None:
    """Return a verification plan for one change, or None when the change should be skipped."""

    path_array = change.get("path_array")
    if not isinstance(path_array, list) or not path_array:
        return None
    segments = [str(part) for part in path_array]
    if _is_bookkeeping_path(segments):
        return None

    body = change.get("body")
    is_delete = _is_delete_intent(change)
    if body is None and not is_delete:
        # A null body outside a Delete intent carries nothing to compare against - skip it
        # rather than manufacture a false divergence against "nothing was sent".
        return None

    dotted = ".".join(segments)
    if is_delete:
        return {"kind": "delete", "path": dotted, "pointer": tuple(segments)}

    if isinstance(body, dict):
        # CreateAction/ReorderActions-shaped writes: body IS the whole node/container at
        # path_array, so the pointer to read is path_array itself and the comparison is a
        # straight dict diff.
        return {"kind": "node", "path": dotted, "pointer": tuple(segments), "expected": body}

    # SetData-shaped writes: body is a bare scalar ("bS35G", true, 48, ...) addressing a leaf
    # under the parent node, e.g. path_array [..., "actions", "0", "%p", "%ei"]. window.appquery's
    # `_child()` chain (see live_node_read.build_appquery_script) walks Bubble AST node
    # wrappers, and a bare scalar is not one, so the pointer walk cannot be trusted to resolve a
    # leaf directly the way it resolves a node. The reliable read is the PARENT node - which
    # `_child()` walks fine, since every path_array we see here was itself produced by walking
    # dict-shaped nodes down to a leaf key - with the comparison done against its value at the
    # final key. This is also exactly the shape `patch_expression_leaf` already writes against
    # (pointer to a dict, key to patch), so it costs nothing extra to reason about.
    if len(segments) < 2:
        return {
            "kind": "unreadable",
            "path": dotted,
            "message": (
                f"path '{dotted}' has no parent to read a leaf value from; cannot verify a "
                "scalar body written at the root."
            ),
        }
    return {
        "kind": "leaf",
        "path": dotted,
        "pointer": tuple(segments[:-1]),
        "leaf_key": segments[-1],
        "expected": body,
    }


def verify_changes(
    profile: str,
    changes: Sequence[dict[str, Any]],
    *,
    app_id: str | None = None,
    app_version: str = "test",
    reader: Reader | None = None,
) -> dict[str, Any]:
    """Re-read every path `changes` wrote and report where the app tree differs from intent.

    Never turns a successful write into a failure: `ok` reports that verification RAN, not that
    the write was good, and a read failure (playwright_missing, editor_unstable,
    not_logged_in, ...) is reported as `unverified` rather than as a divergence - it means "we
    don't know", not "it's wrong".
    """

    read = reader or read_live_nodes

    plans: list[dict[str, Any]] = []
    for change in changes:
        if not isinstance(change, dict):
            continue
        plan = _plan_change(change)
        if plan is not None:
            plans.append(plan)

    # Several changes in one payload often touch the same node (a SetData plus its sibling
    # property edits, or two leaves under one parent): read each distinct pointer once no
    # matter how many plans reference it.
    pointers: list[tuple[str, ...]] = []
    seen_pointers: set[tuple[str, ...]] = set()
    for plan in plans:
        pointer = plan.get("pointer")
        if pointer is not None and pointer not in seen_pointers:
            seen_pointers.add(pointer)
            pointers.append(pointer)

    read_results: dict[tuple[str, ...], dict[str, Any]] = {}
    if pointers:
        read_results = read(profile, pointers, app_id=app_id, app_version=app_version) or {}

    divergences: list[dict[str, Any]] = []
    unverified: list[dict[str, Any]] = []

    for plan in plans:
        kind = plan["kind"]

        if kind == "unreadable":
            unverified.append(
                {"path": plan["path"], "error": "unreadable_leaf", "message": plan["message"]}
            )
            continue

        result = read_results.get(plan["pointer"])
        if not isinstance(result, dict):
            unverified.append(
                {
                    "path": plan["path"],
                    "error": "no_read_result",
                    "message": f"No read result for pointer '{'.'.join(plan['pointer'])}'.",
                }
            )
            continue

        if kind == "delete":
            if result.get("ok"):
                divergences.append(
                    {
                        "path": plan["path"],
                        "divergence": "<root>",
                        "expected": "absent",
                        "actual": result.get("node"),
                    }
                )
            elif result.get("error") == "pointer_not_found":
                pass  # exactly the pass condition for a Delete: the path is gone
            else:
                unverified.append(
                    {
                        "path": plan["path"],
                        "error": result.get("error") or "unverified",
                        "message": result.get("message") or "",
                    }
                )
            continue

        if not result.get("ok"):
            unverified.append(
                {
                    "path": plan["path"],
                    "error": result.get("error") or "unverified",
                    "message": result.get("message") or "",
                }
            )
            continue

        actual_node = result.get("node")
        if kind == "node":
            expected: Any = plan["expected"]
            actual: Any = actual_node
        else:  # leaf
            expected = plan["expected"]
            actual = actual_node.get(plan["leaf_key"]) if isinstance(actual_node, dict) else None

        divergence = first_divergence(expected, actual)
        if divergence is not None:
            divergences.append(
                {
                    "path": plan["path"],
                    "divergence": divergence,
                    "expected": expected,
                    "actual": actual,
                }
            )

    checked = len(plans)
    verified = not divergences and not unverified

    return {
        "ok": True,
        "checked": checked,
        "verified": verified,
        "divergences": divergences,
        "unverified": unverified,
        "render_unverified": True,
        "verified_meaning": VERIFIED_MEANING,
    }
