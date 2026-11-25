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


def max_id_in(doc: Any) -> int:
    """Largest decoded id among all dict keys anywhere in ``doc`` (-1 when there is none)."""

    best = -1
    stack = [doc]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                decoded = decode_id(str(key))
                if decoded is not None and decoded > best:
                    best = decoded
                stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
    return best


def allocate_id(doc: dict[str, Any]) -> str:
    """Return the next free id and record it as ``doc["editor_counter"]``."""

    counter = doc.get("editor_counter")
    last = max(int(counter) if isinstance(counter, (int, str)) and str(counter).isdigit() else -1, max_id_in(doc))
    value = max(last + 1, _FIRST_ID)
    doc["editor_counter"] = value
    return encode_id(value)
