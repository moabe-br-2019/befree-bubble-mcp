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


def _mixed_root_error(decoded: list[str], encoded: list[str]) -> ValueError:
    """Return the refusal for a root that spells the same mapping both ways at once."""

    return ValueError(
        "node root mixes decoded key(s) "
        f"{', '.join(repr(key) for key in decoded)} with encoded key(s) "
        f"{', '.join(repr(key) for key in encoded)}. A half-translated root is either a node "
        "this path does not cover or a body assembled by hand; translating it would guess at "
        "which half is authoritative, and guessing is what renders '[missing: null]'."
    )


def encode_node_root(node: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``node`` with its root keys encoded for ``/appeditor/write``."""

    if not isinstance(node, dict):
        raise TypeError("encode_node_root expects a node object")
    if "name" in node:
        raise ValueError(
            "node root carries 'name', which is '%nm' on an element and an internal field name "
            "inside a Message; this path edits workflow action nodes only"
        )
    decoded_present = [key for key in _DECODED_TO_ENCODED if key in node]
    encoded_present = [key for key in _ENCODED_TO_DECODED if key in node]
    if decoded_present and encoded_present:
        raise _mixed_root_error(decoded_present, encoded_present)
    if encoded_present:
        return copy.deepcopy(node)
    return {
        _DECODED_TO_ENCODED.get(key, key): copy.deepcopy(value) for key, value in node.items()
    }


def decode_node_root(node: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``node`` with its root keys decoded, the inverse of ``encode_node_root``."""

    if not isinstance(node, dict):
        raise TypeError("decode_node_root expects a node object")
    if "%nm" in node:
        raise ValueError(
            "node root carries '%nm', which decodes to the ambiguous 'name'; this path edits "
            "workflow action nodes only"
        )
    decoded_present = [key for key in _DECODED_TO_ENCODED if key in node]
    encoded_present = [key for key in _ENCODED_TO_DECODED if key in node]
    if decoded_present and encoded_present:
        raise _mixed_root_error(decoded_present, encoded_present)
    if decoded_present:
        return copy.deepcopy(node)
    return {
        _ENCODED_TO_DECODED.get(key, key): copy.deepcopy(value) for key, value in node.items()
    }
