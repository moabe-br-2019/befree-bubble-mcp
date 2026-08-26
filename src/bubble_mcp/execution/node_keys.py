"""Translation of node ROOT keys between the editor's tree and the write endpoint.

The editor holds nodes with decoded keys (``type``/``properties``); ``/appeditor/write``
requires encoded keys (``%x``/``%p``) at the node root, with the very same interior. Only the
root is translated here. The interior - the expression chain and its ``Message`` nodes - is
copied verbatim, because the interior is exactly the part that cannot be reconstructed, and
touching it is how a node ends up rendering as "[missing: null]".
"""

from __future__ import annotations

import copy
from typing import Any


# Mirrors write_lint._DECODED_TO_ENCODED. ``name`` is deliberately absent: it is ``%nm`` on an
# element root and an internal field name inside a Message, and there is no way to tell which
# one a caller meant without inspecting the interior this module refuses to inspect.
_DECODED_TO_ENCODED = {
    "type": "%x",
    "properties": "%p",
    "default_name": "%dn",
}
_ENCODED_TO_DECODED = {encoded: decoded for decoded, encoded in _DECODED_TO_ENCODED.items()}


def encode_node_root(node: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``node`` with its root keys encoded for ``/appeditor/write``."""

    if not isinstance(node, dict):
        raise TypeError("encode_node_root expects a node object")
    if any(key in node for key in _ENCODED_TO_DECODED):
        return copy.deepcopy(node)
    if "name" in node:
        raise ValueError(
            "node root carries 'name', which is '%nm' on an element and an internal field name "
            "inside a Message; this path edits workflow action nodes only"
        )
    return {
        _DECODED_TO_ENCODED.get(key, key): copy.deepcopy(value) for key, value in node.items()
    }


def decode_node_root(node: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``node`` with its root keys decoded, the inverse of ``encode_node_root``."""

    if not isinstance(node, dict):
        raise TypeError("decode_node_root expects a node object")
    if any(key in node for key in _DECODED_TO_ENCODED):
        return copy.deepcopy(node)
    if "%nm" in node:
        raise ValueError(
            "node root carries '%nm', which decodes to the ambiguous 'name'; this path edits "
            "workflow action nodes only"
        )
    return {
        _ENCODED_TO_DECODED.get(key, key): copy.deepcopy(value) for key, value in node.items()
    }
