"""Plugin-wide id allocation, matching the Bubble plugin editor.

Every element, field, state, event, action and API call in a plugin document is keyed by a
three-letter id in base 52 (``A``-``Z`` then ``a``-``z``). ``editor_counter`` holds the value of
the last id the editor issued, so the next id is one above the larger of the counter and every id
already present (real plugins carry ids above the counter, e.g. copied return values).
"""

from __future__ import annotations

import string
from typing import Any

ALPHABET = string.ascii_uppercase + string.ascii_lowercase
_BASE = len(ALPHABET)
_WIDTH = 3
_MAX = _BASE**_WIDTH - 1
# A fresh plugin's first item is AAC with editor_counter 2, so ids below 2 are never handed out.
_FIRST_ID = 2


def encode_id(n: int) -> str:
    if n < 0 or n > _MAX:
        raise ValueError(f"Plugin id value out of range: {n}")
    chars = []
    for _ in range(_WIDTH):
        n, digit = divmod(n, _BASE)
        chars.append(ALPHABET[digit])
    return "".join(reversed(chars))


def decode_id(value: str) -> int | None:
    if len(value) != _WIDTH or any(char not in ALPHABET for char in value):
        return None
    n = 0
    for char in value:
        n = n * _BASE + ALPHABET.index(char)
    return n


# Containers whose keys are ids. Other keys ("key", "url", "doc", "ios", ...) are plain field
# names that merely look like three letters and must not move the counter.
ID_CONTAINERS = frozenset(
    {
        "plugin_elements", "plugin_actions", "fields", "states", "events", "actions", "return_value",
        "calls", "headers", "url_params", "body_params", "params", "shared_headers", "shared_params",
        "shared_keys", "assets", "ios", "android",
    }
)
NEW_KEY_PREFIX = "NEW"


def max_id_in(doc: Any) -> int:
    """Largest decoded id used as a key of an id container anywhere in ``doc`` (-1 when none)."""

    best = -1
    stack: list[tuple[str | None, Any]] = [(None, doc)]
    while stack:
        parent_key, node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                key_text = str(key)
                if parent_key in ID_CONTAINERS and not key_text.startswith(NEW_KEY_PREFIX):
                    decoded = decode_id(key_text)
                    if decoded is not None and decoded > best:
                        best = decoded
                stack.append((key_text, value))
        elif isinstance(node, list):
            stack.extend((parent_key, item) for item in node)
    return best


def allocate_id(doc: dict[str, Any]) -> str:
    """Return the next free id and record it as ``doc["editor_counter"]``."""

    counter = doc.get("editor_counter")
    last = max(int(counter) if isinstance(counter, (int, str)) and str(counter).isdigit() else -1, max_id_in(doc))
    value = max(last + 1, _FIRST_ID)
    doc["editor_counter"] = value
    return encode_id(value)
