"""Lint /appeditor/write payloads for decoded-key node bodies.

Bubble's internal app tree uses encoded keys (``%x`` = type, ``%p`` = properties,
``%nm`` = name, ``%dn`` = default_name). The .bubble export is the DECODED form
(``type``/``properties``/``name``/``default_name``). A body written with decoded
keys at a node position (under ``%el``, ``%wf``, or ``actions``) is accepted by the
server with HTTP 200 and even round-trips through the exporter — but the editor
cannot interpret the node and renders it as ``[missing: null]``.

This lint flags exactly that case so agents copy serialization from the live app
tree (encoded), not from the export (decoded).
"""

from __future__ import annotations

from typing import Any

_NODE_MARKERS = {"%el", "%wf", "actions"}
_DECODED_TO_ENCODED = {
    "type": "%x",
    "properties": "%p",
    "default_name": "%dn",
}


def _is_node_position(path_array: Any) -> bool:
    if not isinstance(path_array, list):
        return False
    parts = [str(part) for part in path_array]
    for index, part in enumerate(parts):
        if part in _NODE_MARKERS and index < len(parts) - 1:
            return True
    return False


def _body_issues(path_str: str, body: Any) -> list[str]:
    if not isinstance(body, dict):
        return []
    decoded_present = [key for key in _DECODED_TO_ENCODED if key in body]
    if not decoded_present:
        return []
    encoded_present = any(key in body for key in ("%x", "%p"))
    if encoded_present:
        return []
    mapping = ", ".join(f"'{key}' -> '{_DECODED_TO_ENCODED[key]}'" for key in decoded_present)
    return [
        f"{path_str}: node body uses decoded export keys ({mapping}). The editor stores nodes with "
        "encoded keys (%x=type, %p=properties, %nm=name, %dn=default_name); decoded keys are accepted "
        "by the server but render as '[missing: null]' in the editor. Copy the serialization from a "
        "sibling node in the live app tree, not from the .bubble export."
    ]


def lint_editor_write_changes(changes: Any) -> list[str]:
    """Return human-readable issues for decoded-key node bodies in a changes list."""

    issues: list[str] = []
    if not isinstance(changes, list):
        return issues
    for change in changes:
        if not isinstance(change, dict):
            continue
        path_array = change.get("path_array")
        if not _is_node_position(path_array):
            continue
        path_str = "/".join(str(part) for part in path_array) if isinstance(path_array, list) else "?"
        issues.extend(_body_issues(path_str, change.get("body")))
    return issues
