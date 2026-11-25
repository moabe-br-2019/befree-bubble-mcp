"""Summarize and export plugin editor discovery captures.

The Chrome extension records every request the Bubble plugin editor makes and the
companion appends them to ``plugin-editor-discovery.jsonl``. These helpers group
those events by endpoint so the editor's load/save format can be documented, and
export redacted samples that are safe to commit under ``docs/``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bubble_mcp.core.redaction import redact_sensitive

MAX_SAMPLES_PER_SIGNATURE = 5

_ID_SEGMENT_PATTERNS = (
    re.compile(r"^\d{10,}x\d+$"),  # Bubble ids: 1700000000000x123
    re.compile(r"^[0-9a-fA-F]{16,}$"),
    re.compile(r"^\d{6,}$"),
)


def _is_id_segment(segment: str) -> bool:
    return any(pattern.match(segment) for pattern in _ID_SEGMENT_PATTERNS)


def endpoint_signature(method: str, url: str) -> str:
    """Return ``"METHOD /path"`` with id-like path segments replaced by ``{id}``."""

    path = urlparse(url).path or "/"
    segments = ["{id}" if _is_id_segment(part) else part for part in path.split("/")]
    return f"{str(method or '').upper()} {'/'.join(segments)}"


def _iter_events(
    path: Path,
    *,
    plugin_id: str | None = None,
    since: str | None = None,
) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if plugin_id and event.get("plugin_id") != plugin_id:
                continue
            # received_at is ISO-8601 UTC ("...Z"), so string order is time order.
            if since and str(event.get("received_at") or "") < since:
                continue
            yield event


def _signature_of(event: dict[str, Any]) -> str:
    return endpoint_signature(str(event.get("method") or ""), str(event.get("endpoint") or ""))


def _top_keys(value: Any) -> set[str]:
    return {str(key) for key in value} if isinstance(value, dict) else set()


def summarize_discovery_log(
    path: Path,
    *,
    plugin_id: str | None = None,
    since: str | None = None,
) -> list[dict[str, Any]]:
    """One row per endpoint signature, ordered by first appearance."""

    rows: dict[str, dict[str, Any]] = {}
    for event in _iter_events(path, plugin_id=plugin_id, since=since):
        signature = _signature_of(event)
        raw_payload = event.get("payload")
        payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
        received_at = str(event.get("received_at") or "") or None
        row = rows.setdefault(
            signature,
            {
                "signature": signature,
                "count": 0,
                "statuses": set(),
                "first_seen": received_at,
                "last_seen": received_at,
                "request_keys": set(),
                "response_keys": set(),
            },
        )
        row["count"] += 1
        if isinstance(event.get("status"), int):
            row["statuses"].add(event["status"])
        row["last_seen"] = received_at or row["last_seen"]
        row["request_keys"] |= _top_keys(payload.get("requestBody"))
        row["response_keys"] |= _top_keys(payload.get("responseData"))

    return [
        {
            **row,
            "statuses": sorted(row["statuses"]),
            "request_keys": sorted(row["request_keys"]),
            "response_keys": sorted(row["response_keys"]),
        }
        for row in rows.values()
    ]


def _slug(signature: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", signature.lower().replace("{id}", "id")).strip("-") or "root"


def export_discovery_samples(
    path: Path,
    out_dir: Path,
    *,
    plugin_id: str | None = None,
    since: str | None = None,
) -> list[Path]:
    """Write up to five redacted events per signature to ``out_dir/<slug>.json``."""

    samples: dict[str, list[dict[str, Any]]] = {}
    for event in _iter_events(path, plugin_id=plugin_id, since=since):
        bucket = samples.setdefault(_signature_of(event), [])
        if len(bucket) < MAX_SAMPLES_PER_SIGNATURE:
            bucket.append(redact_sensitive(event))

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for signature, events in samples.items():
        target = out_dir / f"{_slug(signature)}.json"
        target.write_text(
            json.dumps({"signature": signature, "events": events}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        written.append(target)
    return written
