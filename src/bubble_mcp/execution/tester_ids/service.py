"""plan / apply / restore for tester ids. Reader and writer are injected so tests need no Bubble.

Only the element's ``%p.unique_id`` leaf and the app's ``expose_id_option`` are ever written.

Apply records a ``pending`` line in the ledger, then writes to Bubble, then reads the written
leaves back and records the outcome per change: ``applied`` (the value read back is the one
written), ``failed`` (Bubble answered not-ok) or ``unknown`` (the writer raised, or Bubble said ok
but the value read back differs or could not be read, so the write may or may not have landed).
``applied`` and ``unknown`` changes are open. Restore compares the current value: still what the
tester set - it is put back and read back, and closed only if the old value is really there;
already the old value - closed without writing (``already_at_old``); anything else - a conflict.
Restore writes no pending line: a crashed restore leaves the change open, and the next restore
sees the old value and closes it without writing.

The ledger is keyed by the canonical profile name, so every spelling ``resolve_profile`` accepts
shares one ledger.
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
CONTEXT_ROOTS = ("%p3", "%ed")  # a whole page or reusable: ids must be unique across it
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


def _pointer_refusal(pointer: Any) -> dict[str, Any] | None:
    if (
        isinstance(pointer, list)
        and len(pointer) == 2
        and str(pointer[0]) in CONTEXT_ROOTS
        and str(pointer[1]).strip()
    ):
        return None
    return _refusal(
        "invalid_pointer",
        f"pointer must be a whole page ['%p3', <page key>] or reusable ['%ed', <reusable key>], got "
        f"{pointer!r}: ids are checked for uniqueness across the page, so a part of it is not enough.",
    )


def _stale_replacements(
    ids: list[dict[str, Any]], write: list[dict[str, Any]], resolved: Any, app_version: str,
    config_dir: Path | None,
) -> list[dict[str, Any]]:
    """replace=true items whose open tester change was since overwritten by someone else.

    Replacing them would record the other person's id as the tester's old value, so the next
    restore would delete it; the batch is refused instead.
    """

    replacing = {tuple(str(part) for part in item.get("pointer") or []) for item in ids if item.get("replace")}
    candidates = [w for w in write if tuple(w["pointer"]) in replacing]
    if not candidates:
        return []
    open_by_pointer = {
        tuple(change["pointer"]): change
        for change in ledger.open_changes(
            resolved.name, pointers=[w["pointer"] for w in candidates], app_id=resolved.app_id,
            app_version=app_version, config_dir=config_dir,
        )
    }
    return [
        {"error": "changed_since_tester", "pointer": w["pointer"]}
        for w in candidates
        if (change := open_by_pointer.get(tuple(w["pointer"]))) is not None
        and change["new_body"] != w["old_body"]
    ]


def _subject(line: dict[str, Any]) -> dict[str, Any]:
    return {"pointer": line["pointer"]} if "pointer" in line else {"expose_id": True}


def _matches(leaf: list[str], now: Any, expected: Any) -> bool:
    # An absent leaf reads as None; the expose option counts as off when absent.
    if leaf == EXPOSE_ID_POINTER:
        return bool(now) is bool(expected)
    return now == expected


def _confirm(
    read: Reader, profile: str, app_id: str, app_version: str,
    planned: list[tuple[dict[str, Any], list[str], Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    """After an ok write: a status per planned change from what Bubble now holds, and the unconfirmed."""

    try:
        current = _read(read, profile, app_id, app_version, [leaf for _, leaf, _ in planned])
    except _ReadFailed as error:
        return ["unknown"] * len(planned), [{**_subject(line), "read_failed": str(error)} for line, _, _ in planned]
    statuses: list[str] = []
    unconfirmed: list[dict[str, Any]] = []
    for line, leaf, expected in planned:
        now = current.get(tuple(leaf))
        if _matches(leaf, now, expected):
            statuses.append("applied")
        else:
            statuses.append("unknown")
            unconfirmed.append({**_subject(line), "current": bool(now) if leaf == EXPOSE_ID_POINTER else now})
    return statuses, unconfirmed


def plan_ids(profile: str, pointer: list[str], *, app_version: str = "test", reader: Reader | None = None) -> dict[str, Any]:
    resolved, refusal = _gate(profile, app_version)
    if refusal:
        return refusal
    if bad_pointer := _pointer_refusal(pointer):
        return bad_pointer
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
    if bad_pointer := _pointer_refusal(pointer):
        return bad_pointer
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
        bad += _stale_replacements(ids, checked["write"], resolved, app_version, config_dir)
        if bad:
            checked = {**checked, "ok": False, "problems": [*checked["problems"], *bad]}
    if not checked["ok"]:
        return {"ok": False, "error": "invalid_batch", "executed": False, "problems": checked["problems"],
                "kept": checked["kept"]}
    expose_on = bool(nodes.get(tuple(EXPOSE_ID_POINTER)))
    changes: list[tuple[list[str], Any]] = []
    if not expose_on:
        # Also when every item is kept: the ids reach the page's HTML only with the option on.
        changes.append((EXPOSE_ID_POINTER, True))
    changes += [(_id_leaf(w["pointer"]), html_id_body(w["html_id"])) for w in checked["write"]]
    preview = {
        "write": [{"pointer": w["pointer"], "html_id": w["html_id"]} for w in checked["write"]],
        "kept": checked["kept"],
        "turns_expose_id_on": not expose_on,
    }
    if not execute or not changes:
        return {"ok": True, "executed": False, **preview}

    batch_id = uuid.uuid4().hex[:12]
    base = {"batch_id": batch_id, "app_id": resolved.app_id, "app_version": app_version}
    # (ledger line, leaf read back after the write, value that leaf must then hold)
    planned: list[tuple[dict[str, Any], list[str], Any]] = []
    if not expose_on:
        planned.append(({**base, "kind": "expose_id", "old": False, "new": True}, EXPOSE_ID_POINTER, True))
    for w in checked["write"]:
        new_body = html_id_body(w["html_id"])
        planned.append((
            {**base, "kind": "id", "pointer": w["pointer"], "old_body": w["old_body"], "new_body": new_body},
            _id_leaf(w["pointer"]),
            new_body,
        ))
    for line, _, _ in planned:
        ledger.append(resolved.name, {**line, "status": "pending"}, config_dir)
    raised = False
    try:
        result = (writer or _default_writer(profile))(_payload(resolved.app_id, app_version, changes))
    except Exception as error:  # noqa: BLE001 - recorded, then reported
        result = {"ok": False, "error": f"{type(error).__name__}: {error}"}
        raised = True  # the write may have landed: recorded as unknown, not failed
    not_confirmed: list[dict[str, Any]] = []
    if result.get("ok"):
        statuses, not_confirmed = _confirm(read, profile, resolved.app_id, app_version, planned)
    else:
        statuses = ["unknown" if raised else "failed"] * len(planned)
    for (line, _, _), status in zip(planned, statuses):
        ledger.append(resolved.name, {**line, "status": status}, config_dir)
    return {"ok": all(status == "applied" for status in statuses), "executed": True, "batch_id": batch_id,
            **preview, "not_confirmed": not_confirmed, "write_result": result}


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
    if restore_all and (batch_id or pointers):
        return _refusal(
            "conflicting_selection",
            "all=true restores everything and turns expose-id off; pass it alone, or pass batch_id / "
            "element pointers without it.",
        )
    if missing := _need_session(profile, reader=reader, writer=writer, execute=execute):
        return missing
    read = reader or _default_reader()
    open_changes = ledger.open_changes(
        resolved.name, batch_id=batch_id, pointers=pointers, app_id=resolved.app_id,
        app_version=app_version, config_dir=config_dir,
    )
    flips = (
        ledger.open_expose_flips(resolved.name, app_id=resolved.app_id, app_version=app_version,
                                 config_dir=config_dir)
        if restore_all else []
    )
    leaves = [_id_leaf(c["pointer"]) for c in open_changes] + ([EXPOSE_ID_POINTER] if flips else [])
    try:
        current = _read(read, profile, resolved.app_id, app_version, leaves) if leaves else {}
    except _ReadFailed as error:
        return _read_failed(error)
    restorable, already, conflicts = [], [], []
    for change in open_changes:
        now = current.get(tuple(_id_leaf(change["pointer"])))
        if now == change["new_body"]:
            restorable.append(change)
        elif now == change["old_body"]:
            already.append(change)  # an unknown write that never landed, or a lost restore line
        else:
            conflicts.append({"pointer": change["pointer"], "current": now, "expected": change["new_body"]})
    expose_to_turn_off = bool(flips) and bool(current.get(tuple(EXPOSE_ID_POINTER)))
    expose_already_off = bool(flips) and not expose_to_turn_off
    changes: list[tuple[list[str], Any]] = [(_id_leaf(c["pointer"]), c["old_body"]) for c in restorable]
    if expose_to_turn_off:
        changes.append((EXPOSE_ID_POINTER, False))
    summary = {
        "restored": [{"pointer": c["pointer"]} for c in restorable],
        "already_restored": [{"pointer": c["pointer"]} for c in already],
        "conflicts": conflicts,
        "turns_expose_id_off": expose_to_turn_off,
    }
    if not execute:
        return {"ok": True, "executed": False, **summary}

    base = {"kind": "restore", "app_id": resolved.app_id, "app_version": app_version}
    for change in already:  # nothing to write: only closed
        ledger.append(resolved.name, {**base, "pointer": change["pointer"], "batch_id": change["batch_ids"][-1],
                                      "note": "already_at_old"}, config_dir)
    if expose_already_off:
        ledger.append(resolved.name, {**base, "expose_id": True, "batch_id": flips[-1]["batch_id"],
                                      "note": "already_at_old"}, config_dir)
    if not changes:
        return {"ok": True, "executed": bool(already or expose_already_off), **summary, "not_restored": []}
    try:
        result = (writer or _default_writer(profile))(_payload(resolved.app_id, app_version, changes))
    except Exception as error:  # noqa: BLE001 - reported; nothing is marked restored
        result = {"ok": False, "error": f"{type(error).__name__}: {error}"}
    if not result.get("ok"):
        return {"ok": False, "executed": True, **summary, "restored": [], "not_restored": [],
                "write_result": result}
    # Bubble said ok: close only what now really holds the old value; the rest stays open.
    planned: list[tuple[dict[str, Any], list[str], Any]] = [
        ({**base, "pointer": c["pointer"], "batch_id": c["batch_ids"][-1]}, _id_leaf(c["pointer"]), c["old_body"])
        for c in restorable
    ]
    if expose_to_turn_off:
        planned.append(({**base, "expose_id": True, "batch_id": flips[-1]["batch_id"]}, EXPOSE_ID_POINTER, False))
    statuses, not_restored = _confirm(read, profile, resolved.app_id, app_version, planned)
    confirmed = [line for (line, _, _), status in zip(planned, statuses) if status == "applied"]
    for line in confirmed:
        ledger.append(resolved.name, line, config_dir)
    return {
        "ok": not not_restored,
        "executed": True,
        **summary,
        "restored": [{"pointer": line["pointer"]} for line in confirmed if "pointer" in line],
        "not_restored": not_restored,
        "write_result": result,
    }
