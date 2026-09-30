"""Append-only record of every id the tester set, so each can be put back.

One JSON object per line. Nothing is edited in place: a restore appends a ``restore`` line. The
open state of an element is the fold of its lines since its last restore, so a second change
before a restore still returns to the value before the first one.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bubble_mcp.core.config import get_config_dir


def ledger_path(profile: str, config_dir: Path | None = None) -> Path:
    return (config_dir or get_config_dir()) / "tester" / profile / "id-ledger.jsonl"


def append(profile: str, entry: dict[str, Any], config_dir: Path | None = None) -> None:
    path = ledger_path(profile, config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {**entry, "at": datetime.now(timezone.utc).isoformat()}
    with path.open("a", encoding="utf-8") as handle:
        # If the file exists, is non-empty, and doesn't end with a newline,
        # prepend a newline to recover from a process killed mid-write
        if path.exists() and path.stat().st_size > 0:
            with path.open("rb") as rb:
                rb.seek(-1, 2)  # Seek to last byte
                if rb.read(1) != b"\n":
                    handle.write("\n")
        handle.write(json.dumps(line, sort_keys=True) + "\n")


def read_entries(profile: str, config_dir: Path | None = None) -> list[dict[str, Any]]:
    path = ledger_path(profile, config_dir)
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue  # a line torn by a killed process; the rest stays usable
        if isinstance(value, dict):
            entries.append(value)
    return entries


OPEN_STATUSES = ("applied", "unknown")  # unknown: the write may have landed; restore checks the value


def open_changes(
    profile: str,
    *,
    batch_id: str | None = None,
    pointers: list[list[str]] | None = None,
    app_id: str | None = None,
    app_version: str | None = None,
    config_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Open changes, folded per (app_id, app_version, pointer).

    A restore line carrying app_id/app_version closes only that version's change; one without
    (older ledgers) closes every version of the pointer.
    """
    state: dict[tuple[Any, ...], dict[str, Any]] = {}
    for entry in read_entries(profile, config_dir):
        pointer = tuple(str(part) for part in entry.get("pointer") or [])
        if not pointer:
            continue
        key = (entry.get("app_id"), entry.get("app_version"), pointer)
        if entry.get("kind") == "restore":
            if entry.get("app_id") is None and entry.get("app_version") is None:
                for other in [k for k in state if k[2] == pointer]:
                    del state[other]
            else:
                state.pop(key, None)
        elif entry.get("kind") == "id" and entry.get("status") in OPEN_STATUSES:
            current = state.get(key)
            if current is None:
                state[key] = {
                    "pointer": list(pointer),
                    "app_id": entry.get("app_id"),
                    "app_version": entry.get("app_version"),
                    "old_body": entry.get("old_body"),
                    "new_body": entry.get("new_body"),
                    "batch_ids": [entry.get("batch_id")],
                }
            else:
                current["new_body"] = entry.get("new_body")
                current["batch_ids"].append(entry.get("batch_id"))
    wanted = {tuple(str(p) for p in pointer) for pointer in pointers} if pointers else None
    return [
        change
        for key, change in state.items()
        if (batch_id is None or batch_id in change["batch_ids"])
        and (wanted is None or key[2] in wanted)
        and (app_id is None or change["app_id"] == app_id)
        and (app_version is None or change["app_version"] == app_version)
    ]


def open_expose_flips(
    profile: str,
    *,
    app_id: str | None = None,
    app_version: str | None = None,
    config_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Open expose_id_option flips; a restore line closes only flips of its own app/version."""
    flips: dict[str, dict[str, Any]] = {}
    for entry in read_entries(profile, config_dir):
        if entry.get("kind") == "expose_id" and entry.get("status") == "applied":
            flips[str(entry.get("batch_id"))] = entry
        elif entry.get("kind") == "restore" and entry.get("expose_id"):
            if entry.get("app_id") is None and entry.get("app_version") is None:
                flips.clear()
            else:
                for k in [
                    k for k, f in flips.items()
                    if f.get("app_id") == entry.get("app_id") and f.get("app_version") == entry.get("app_version")
                ]:
                    del flips[k]
    return [
        f for f in flips.values()
        if (app_id is None or f.get("app_id") == app_id)
        and (app_version is None or f.get("app_version") == app_version)
    ]
