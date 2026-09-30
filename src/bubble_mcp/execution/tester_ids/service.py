"""plan / apply / restore for tester ids. Reader and writer are injected so tests need no Bubble.

Only the element's ``%p.unique_id`` leaf and the app's ``expose_id_option`` are ever written.

Apply records a ``pending`` line in the ledger, then writes to Bubble, then records the outcome:
``applied``, ``failed`` (Bubble answered not-ok) or ``unknown`` (the writer raised, so the write
may have landed). ``applied`` and ``unknown`` changes are open; restore compares the current
value and restores it only if it is still what the tester set, otherwise reports a conflict.
Restore writes no pending line: a crashed restore leaves the change open, and the next restore
sees the old value, reports a conflict and overwrites nothing.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable

from bubble_mcp.core.config import load_settings, resolve_profile
from bubble_mcp.sessions.store import load_session
from bubble_mcp.execution.tester_ids import ledger
from bubble_mcp.execution.tester_ids.elements import (
    EXPOSE_ID_POINTER,
    collect_elements,
    html_id_body,
    validate_batch,
)

LIVE = "live"
Reader = Callable[..., dict[tuple, Any]]
Writer = Callable[[dict[str, Any]], dict[str, Any]]


def _refusal(error: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": error, "message": message}


def _checked_profile(profile: str, app_version: str) -> tuple[Any, dict[str, Any] | None]:
    if str(app_version).strip().lower() == LIVE:
        return None, _refusal("live_never", "live is the deployed app and is never written by this MCP.")
    resolved = resolve_profile(load_settings(), profile)
    if resolved is None or not resolved.tester_mode:
        return None, _refusal(
            "tester_mode_off",
            f"Profile {profile!r} does not have tester_mode: true in settings.json. Only whoever "
            "installs the MCP can turn it on.",
        )
    return resolved, None


def tester_gate(profile: str, app_version: str) -> dict[str, Any] | None:
    """The refusal (live_never or tester_mode_off) for this profile and version, or None."""

    return _checked_profile(profile, app_version)[1]


def _gate(profile: str, app_version: str) -> tuple[Any, dict[str, Any] | None]:
    return _checked_profile(profile, app_version)


def _default_reader() -> Reader:
    from bubble_mcp.execution.deploy_preview import read_nodes_over_http

    return read_nodes_over_http


def _default_writer(profile: str) -> Writer:
    from bubble_mcp.execution.client import BubbleEditorClient
    session = load_session(profile)
    if session is None:
        raise RuntimeError("no_session")
    client = BubbleEditorClient()
    return lambda payload: client.write(payload, session, tester_ids=True)


def _need_session(profile: str, *, reader: Any, writer: Any, execute: bool) -> dict[str, Any] | None:
    if (reader is None or (execute and writer is None)) and load_session(profile) is None:
        return _refusal(
            "no_session",
            f"No stored Bubble editor session for profile {profile!r}; log in with bubble_session_login.",
        )
    return None


class _ReadFailed(Exception):
    pass


def _read(reader: Reader, profile: str, app_id: str, app_version: str, pointers: list[list[str]]) -> dict[tuple, Any]:
    try:
        return reader(profile, [tuple(p) for p in pointers], app_id=app_id, app_version=app_version)
    except Exception as error:  # noqa: BLE001 - reported before any ledger write
        raise _ReadFailed(f"{type(error).__name__}: {error}") from error


def _read_failed(error: _ReadFailed) -> dict[str, Any]:
    return {"ok": False, "error": "read_failed", "message": str(error)}


def _is_plain_text_body(body: Any) -> bool:
    if not isinstance(body, dict) or not isinstance(body.get("%e"), dict):
        return False
    entries = body["%e"]
    return list(entries) == ["0"] and isinstance(entries["0"], str)


def _payload(app_id: str, app_version: str, changes: list[tuple[list[str], Any]]) -> dict[str, Any]:
    from bubble_mcp.compiler.payload import bubble_session_id
    from bubble_mcp.execution.node_edit import _change

    session_id = bubble_session_id()
    return {
        "appname": app_id,
        "app_version": app_version,
        "changes": [_change(path, body, session_id=session_id) for path, body in changes],
    }


def _id_leaf(pointer: list[str]) -> list[str]:
    return [*pointer, "%p", "unique_id"]


def plan_ids(profile: str, pointer: list[str], *, app_version: str = "test", reader: Reader | None = None) -> dict[str, Any]:
    resolved, refusal = _gate(profile, app_version)
    if refusal:
        return refusal
    if missing := _need_session(profile, reader=reader, writer=None, execute=False):
        return missing
    try:
        nodes = _read(reader or _default_reader(), profile, resolved.app_id, app_version, [pointer, EXPOSE_ID_POINTER])
    except _ReadFailed as error:
        return _read_failed(error)
    context = nodes.get(tuple(pointer))
    if not isinstance(context, dict):
        return _refusal("element_not_found", f"Nothing at {pointer} in {app_version}.")
    elements = collect_elements(context, pointer)
    public = [{k: v for k, v in e.items() if k != "html_id_body"} for e in elements]
    return {
        "ok": True,
        "pointer": pointer,
        "app_version": app_version,
        "expose_id_option": bool(nodes.get(tuple(EXPOSE_ID_POINTER))),
        "reusable": [e for e in public if e["html_id"]],
        "missing": [e for e in public if not e["html_id"]],
    }


def apply_ids(
    profile: str,
    pointer: list[str],
    ids: list[dict[str, Any]],
    *,
    app_version: str = "test",
    execute: bool = False,
    reader: Reader | None = None,
    writer: Writer | None = None,
    config_dir: Path | None = None,
) -> dict[str, Any]:
    resolved, refusal = _gate(profile, app_version)
    if refusal:
        return refusal
    if missing := _need_session(profile, reader=reader, writer=writer, execute=execute):
        return missing
    read = reader or _default_reader()
    try:
        nodes = _read(read, profile, resolved.app_id, app_version, [pointer, EXPOSE_ID_POINTER])
    except _ReadFailed as error:
        return _read_failed(error)
    context = nodes.get(tuple(pointer))
    if not isinstance(context, dict):
        return _refusal("element_not_found", f"Nothing at {pointer} in {app_version}.")
    checked = validate_batch(ids, collect_elements(context, pointer))
    if checked["ok"]:
        # The reader drops null-valued keys, so only a plain single-text id can be put back intact.
        bad = [
            {"error": "expression_id_not_replaceable", "pointer": w["pointer"]}
            for w in checked["write"]
            if w["old_body"] is not None and not _is_plain_text_body(w["old_body"])
        ]
        if bad:
            checked = {**checked, "ok": False, "problems": [*checked["problems"], *bad]}
    if not checked["ok"]:
        return {"ok": False, "executed": False, "problems": checked["problems"], "kept": checked["kept"]}
    expose_on = bool(nodes.get(tuple(EXPOSE_ID_POINTER)))
    changes: list[tuple[list[str], Any]] = []
    if not expose_on and checked["write"]:
        changes.append((EXPOSE_ID_POINTER, True))
    changes += [(_id_leaf(w["pointer"]), html_id_body(w["html_id"])) for w in checked["write"]]
    preview = {
        "write": [{"pointer": w["pointer"], "html_id": w["html_id"]} for w in checked["write"]],
        "kept": checked["kept"],
        "turns_expose_id_on": not expose_on and bool(checked["write"]),
    }
    if not execute or not changes:
        return {"ok": True, "executed": False, **preview}

    batch_id = uuid.uuid4().hex[:12]
    base = {"batch_id": batch_id, "app_id": resolved.app_id, "app_version": app_version}
    lines: list[dict[str, Any]] = []
    if preview["turns_expose_id_on"]:
        lines.append({**base, "kind": "expose_id", "old": False, "new": True})
    lines += [
        {**base, "kind": "id", "pointer": w["pointer"], "old_body": w["old_body"],
         "new_body": html_id_body(w["html_id"])}
        for w in checked["write"]
    ]
    for line in lines:
        ledger.append(profile, {**line, "status": "pending"}, config_dir)
    raised = False
    try:
        result = (writer or _default_writer(profile))(_payload(resolved.app_id, app_version, changes))
    except Exception as error:  # noqa: BLE001 - recorded, then reported
        result = {"ok": False, "error": f"{type(error).__name__}: {error}"}
        raised = True  # the write may have landed: recorded as unknown, not failed
    status = "applied" if result.get("ok") else ("unknown" if raised else "failed")
    for line in lines:
        ledger.append(profile, {**line, "status": status}, config_dir)
    return {"ok": status == "applied", "executed": True, "batch_id": batch_id, **preview,
            "write_result": result}


def restore_ids(
    profile: str,
    *,
    batch_id: str | None = None,
    pointers: list[list[str]] | None = None,
    restore_all: bool = False,
    app_version: str = "test",
    execute: bool = False,
    reader: Reader | None = None,
    writer: Writer | None = None,
    config_dir: Path | None = None,
) -> dict[str, Any]:
    resolved, refusal = _gate(profile, app_version)
    if refusal:
        return refusal
    if not (batch_id or pointers or restore_all):
        return _refusal("nothing_selected", "Pass batch_id, element pointers, or all=true.")
    if missing := _need_session(profile, reader=reader, writer=writer, execute=execute):
        return missing
    open_changes = ledger.open_changes(
        profile, batch_id=batch_id, pointers=pointers, app_id=resolved.app_id,
        app_version=app_version, config_dir=config_dir,
    )
    try:
        current = _read(reader or _default_reader(), profile, resolved.app_id, app_version,
                        [_id_leaf(c["pointer"]) for c in open_changes]) if open_changes else {}
    except _ReadFailed as error:
        return _read_failed(error)
    restorable, conflicts = [], []
    for change in open_changes:
        now = current.get(tuple(_id_leaf(change["pointer"])))
        if now == change["new_body"]:
            restorable.append(change)
        else:
            conflicts.append({"pointer": change["pointer"], "current": now, "expected": change["new_body"]})
    flips = (
        ledger.open_expose_flips(profile, app_id=resolved.app_id, app_version=app_version, config_dir=config_dir)
        if restore_all else []
    )
    changes: list[tuple[list[str], Any]] = [(_id_leaf(c["pointer"]), c["old_body"]) for c in restorable]
    if flips:
        changes.append((EXPOSE_ID_POINTER, False))
    summary = {
        "restored": [{"pointer": c["pointer"]} for c in restorable],
        "conflicts": conflicts,
        "turns_expose_id_off": bool(flips),
    }
    if not execute or not changes:
        return {"ok": True, "executed": False, **summary}
    try:
        result = (writer or _default_writer(profile))(_payload(resolved.app_id, app_version, changes))
    except Exception as error:  # noqa: BLE001 - reported; nothing is marked restored
        result = {"ok": False, "error": f"{type(error).__name__}: {error}"}
    if not result.get("ok"):
        return {"ok": False, "executed": True, **summary, "restored": [], "write_result": result}
    for change in restorable:
        ledger.append(profile, {"kind": "restore", "pointer": change["pointer"],
                                "batch_id": change["batch_ids"][-1], "app_id": resolved.app_id,
                                "app_version": app_version}, config_dir)
    if flips:
        ledger.append(profile, {"kind": "restore", "expose_id": True, "batch_id": flips[-1]["batch_id"],
                                "app_id": resolved.app_id, "app_version": app_version}, config_dir)
    return {"ok": True, "executed": True, **summary, "write_result": result}
