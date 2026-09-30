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


def open_changes(
    profile: str,
    *,
    batch_id: str | None = None,
    pointers: list[list[str]] | None = None,
    config_dir: Path | None = None,
) -> list[dict[str, Any]]:
    state: dict[tuple[str, ...], dict[str, Any]] = {}
    for entry in read_entries(profile, config_dir):
        key = tuple(str(part) for part in entry.get("pointer") or [])
        if entry.get("kind") == "restore" and key:
            state.pop(key, None)
        elif entry.get("kind") == "id" and entry.get("status") == "applied" and key:
            current = state.get(key)
            if current is None:
                state[key] = {
                    "pointer": list(key),
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
        if (batch_id is None or batch_id in change["batch_ids"]) and (wanted is None or key in wanted)
    ]


def open_expose_flips(profile: str, config_dir: Path | None = None) -> list[dict[str, Any]]:
    flips: dict[str, dict[str, Any]] = {}
    for entry in read_entries(profile, config_dir):
        if entry.get("kind") == "expose_id" and entry.get("status") == "applied":
            flips[str(entry.get("batch_id"))] = entry
        elif entry.get("kind") == "restore" and entry.get("expose_id"):
            flips.clear()
    return list(flips.values())
