"""Pull a Bubble plugin into a local folder and push the folder back.

``pull_plugin`` mirrors ``GET /appeditor/get_plugin`` into a folder (see ``folder.py``) and keeps
the remote document it came from in ``.bubble-plugin/base.json``. ``push_plugin`` rebuilds the
document from the folder and, when asked to execute, writes it whole with ``save_plugin``. It does
the following, in order:

1. Refuse when the plugin changed remotely since the last pull, unless forced.
2. Take the editor lock, and refuse when an editor tab holds it.
3. Rebuild changed npm packages.
4. Save.
5. Read back and verify.

Nothing here publishes a plugin version.
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import random
import re
import time
from pathlib import Path
from typing import Any, Protocol

from bubble_mcp.plugin_authoring.folder import (
    BASE_FILE,
    canonical_json,
    find_secret_echoes,
    read_folder,
    secret_gaps,
    slugify,
    unknown_files,
    write_folder,
    write_private,
)
from bubble_mcp.plugin_authoring.ids import decode_id

PLUGINS_DIR_ENV = "BUBBLE_MCP_PLUGINS_DIR"
SERVER_MANAGED_KEYS = ("human", "published_bv", "last_published_bv")
_REF = re.compile(r"^custom_(?:data|field)_([A-Za-z]{3}|NEW[A-Za-z0-9_-]*)$")


class PluginClient(Protocol):
    def get_plugin(self, plugin_id: str, session: Any) -> dict[str, Any]: ...
    def save_plugin(self, plugin_id: str, raw: dict[str, Any], session: Any) -> Any: ...
    def acquire_lock(self, plugin_id: str, holder: str, session: Any) -> bool: ...
    def mark_git_modified(self, plugin_id: str, session: Any) -> Any: ...
    def release_lock(self, plugin_id: str, holder: str, session: Any) -> Any: ...
    def build_package(self, plugin_id: str, package_json_string: str, session: Any) -> dict[str, Any]: ...


def default_plugins_root() -> Path:
    configured = os.environ.get(PLUGINS_DIR_ENV, "").strip()
    return Path(configured).expanduser() if configured else Path.home() / "Projects" / "bubble-plugins"


def default_plugin_folder(doc: dict[str, Any], plugin_id: str) -> Path:
    name = (doc.get("meta_data") or {}).get("name") or doc.get("human") or "plugin"
    return default_plugins_root() / f"{slugify(str(name))}-{plugin_id[-6:]}"


def _resolve(profile: str, client: PluginClient | None, session: Any) -> tuple[PluginClient, Any]:
    if client is None:
        from bubble_mcp.plugin_authoring.client import PluginEditorClient

        client = PluginEditorClient()
    if session is None:
        from bubble_mcp.execution.editor_api import _load_session_for_profile

        session = _load_session_for_profile(profile)
    return client, session


def _read_base(folder: Path) -> dict[str, Any] | None:
    path = folder / BASE_FILE
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def _write_base(folder: Path, doc: dict[str, Any]) -> None:
    write_private(folder / BASE_FILE, canonical_json(doc))


def plugin_id_of(folder: Path) -> str:
    meta_path = folder / ".bubble-plugin" / "plugin_id"
    if not meta_path.exists():
        raise ValueError(f"{folder} is not a pulled plugin folder; run plugin pull first.")
    return meta_path.read_text(encoding="utf-8").strip()


# ── diff ─────────────────────────────────────────────────────────────────────


def diff_documents(a: Any, b: Any, path: str = "") -> list[dict[str, Any]]:
    """Leaf-level differences from ``a`` to ``b`` as ``{path, op}`` (code shows line counts)."""

    if isinstance(a, dict) and isinstance(b, dict):
        changes: list[dict[str, Any]] = []
        for key in sorted(set(a) | set(b), key=str):
            child = f"{path}/{key}" if path else str(key)
            if key not in a:
                changes.append({"path": child, "op": "add"})
            elif key not in b:
                changes.append({"path": child, "op": "remove"})
            else:
                changes.extend(diff_documents(a[key], b[key], child))
        return changes
    if a == b and type(a) is type(b):
        return []
    change: dict[str, Any] = {"path": path, "op": "change"}
    if isinstance(a, str) and isinstance(b, str) and ("\n" in a or "\n" in b):
        change["lines"] = f"{a.count(chr(10)) + 1} -> {b.count(chr(10)) + 1}"
    return [change]


def _dangling_refs(doc: dict[str, Any]) -> list[str]:
    known: set[str] = set()
    refs: list[tuple[str, str, str]] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if decode_id(str(key)) is not None:
                    known.add(str(key))
                walk(child, f"{path}/{key}" if path else str(key))
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}/{index}")
        elif isinstance(node, str):
            match = _REF.match(node)
            if match:
                refs.append((path, node, match.group(1)))

    walk(doc, "")
    return [f"{path}: {value} points at missing id {ref}" for path, value, ref in refs if ref not in known]


def _changed_packages(remote: dict[str, Any], local: dict[str, Any]) -> list[str]:
    changed = []
    for action_id, action in (local.get("plugin_actions") or {}).items():
        code = action.get("code") if isinstance(action, dict) else None
        text = ((code or {}).get("package") or {}).get("fn") if isinstance(code, dict) else None
        if not isinstance(text, str):
            continue
        before = (((remote.get("plugin_actions") or {}).get(action_id) or {}).get("code") or {}).get("package") or {}
        if before.get("fn") != text:
            changed.append(action_id)
    return changed


def _without_absent_nulls(remote: dict[str, Any], local: dict[str, Any]) -> dict[str, Any]:
    """Drop what Bubble adds on its own (null meta_data keys, server-stamped keys) before comparing."""

    normalized = copy.deepcopy(remote)
    # Bubble stamps these on save; a document that never had them is not "different".
    for key in SERVER_MANAGED_KEYS:
        if key in normalized and key not in local:
            del normalized[key]
    meta = normalized.get("meta_data")
    local_meta = local.get("meta_data") or {}
    if isinstance(meta, dict):
        for key in [k for k, v in meta.items() if v is None and k not in local_meta]:
            del meta[key]
    return normalized


LOCK_HOLDER_FILE = ".bubble-plugin/lock_holder"


def _holder(folder: Path) -> str:
    """One editor-lock holder id per folder, reused so back-to-back pushes keep the lock."""

    path = folder / LOCK_HOLDER_FILE
    if path.exists():
        holder = path.read_text(encoding="utf-8").strip()
        if holder:
            return holder
    holder = f"{int(time.time() * 1000)}x{random.randint(10**17, 10**18 - 1)}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(holder + "\n", encoding="utf-8")
    return holder


def _move_new_dirs(folder: Path, new_ids: dict[str, str], local: dict[str, Any]) -> dict[str, str]:
    """Rename NEW-<slug> dirs to <id>-<slug>, keeping every file the user put inside."""

    moved = {}
    for new_dir, item_id in new_ids.items():
        kind = new_dir.split("/", 1)[0]
        doc_key = "plugin_elements" if kind == "elements" else "plugin_actions"
        item = (local.get(doc_key) or {}).get(item_id) or {}
        target = folder / kind / f"{item_id}-{slugify(str(item.get('display', '')))}"
        source = folder / new_dir
        if source.exists() and not target.exists():
            source.rename(target)
            moved[new_dir] = target.relative_to(folder).as_posix()
    return moved


# ── pull / push ──────────────────────────────────────────────────────────────


def pull_plugin(
    *,
    profile: str,
    plugin_id: str,
    folder: Path | None = None,
    force: bool = False,
    client: PluginClient | None = None,
    session: Any = None,
) -> dict[str, Any]:
    client, session = _resolve(profile, client, session)
    doc = client.get_plugin(plugin_id, session)
    target = Path(folder).expanduser() if folder else default_plugin_folder(doc, plugin_id)
    base = _read_base(target)
    id_file = target / ".bubble-plugin" / "plugin_id"
    if id_file.exists() and id_file.read_text(encoding="utf-8").strip() != plugin_id and not force:
        return {
            "ok": False,
            "reason": "different_plugin",
            "folder": str(target),
            "next_action": "This folder mirrors another plugin. Pick another folder, or pull with force=true to retarget it.",
        }
    if base is not None and not force and read_folder(target) != base:
        return {
            "ok": False,
            "reason": "local_changes",
            "folder": str(target),
            "local_changes": diff_documents(base, read_folder(target)),
            "next_action": "Push or discard the local edits first, or pull with force=true to overwrite them.",
        }
    written = write_folder(target, doc)
    _write_base(target, doc)
    id_file.write_text(plugin_id + "\n", encoding="utf-8")
    if read_folder(target) != doc:
        # A lossy filesystem (e.g. case-insensitive: ids AAE and AAe collide) would corrupt the
        # next push; stop here instead.
        return {
            "ok": False,
            "reason": "lossy_folder",
            "folder": str(target),
            "next_action": "This folder cannot hold the plugin losslessly (case-insensitive filesystem?). Pull into a folder on a case-sensitive filesystem.",
        }
    return {
        "ok": True,
        "plugin_id": plugin_id,
        "folder": str(target),
        "files": len(written),
        "element_count": len(doc.get("plugin_elements") or {}),
        "action_count": len(doc.get("plugin_actions") or {}),
        "secret_echoes": find_secret_echoes(doc),
        "next_action": "Edit the files, then run plugin push (preview) and plugin push with execute=true.",
    }


def push_plugin(
    *,
    profile: str,
    folder: Path,
    execute: bool = False,
    force: bool = False,
    client: PluginClient | None = None,
    session: Any = None,
) -> dict[str, Any]:
    folder = Path(folder).expanduser()
    base = _read_base(folder)
    if base is None:
        raise ValueError(f"{folder} has no {BASE_FILE}; run plugin pull first.")
    plugin_id = plugin_id_of(folder)
    client, session = _resolve(profile, client, session)

    new_ids: dict[str, str] = {}
    local = read_folder(folder, new_ids=new_ids)
    result: dict[str, Any] = {"plugin_id": plugin_id, "folder": str(folder), "executed": False}

    gaps = secret_gaps(base, local)
    if gaps and not force:
        return {
            **result,
            "ok": False,
            "reason": "secrets_missing",
            "secret_paths": gaps,
            "next_action": "Credentials from the last pull are missing (.bubble-plugin/secrets.json deleted or edited?). Pushing would erase them in Bubble; restore the file, pull again, or push with force=true.",
        }

    # Take the editor lock before reading the remote, so nothing can change it between the check
    # and the save; always give it back, or every editor tab stays read-only until it expires.
    holder = _holder(folder)
    if execute and not client.acquire_lock(plugin_id, holder, session):
        return {
            **result,
            "ok": False,
            "reason": "editor_open",
            "next_action": "Close (or reload) every plugin editor tab of this plugin, then push again.",
        }
    try:
        return _push_with_lock(client, session, folder, plugin_id, base, local, new_ids, result, execute=execute, force=force)
    finally:
        if execute:
            # The push result matters more than a failed release; the lock expires on its own.
            with contextlib.suppress(Exception):
                client.release_lock(plugin_id, holder, session)


def _push_with_lock(
    client: PluginClient,
    session: Any,
    folder: Path,
    plugin_id: str,
    base: dict[str, Any],
    local: dict[str, Any],
    new_ids: dict[str, str],
    result: dict[str, Any],
    *,
    execute: bool,
    force: bool,
) -> dict[str, Any]:
    remote = client.get_plugin(plugin_id, session)
    if remote != base and not force:
        return {
            **result,
            "ok": False,
            "reason": "remote_changed",
            "remote_changes": diff_documents(base, remote),
            "next_action": "The plugin changed in Bubble since the last pull. Pull it again (your edits need to be re-applied) or push with force=true to overwrite.",
        }

    changes = diff_documents(_without_absent_nulls(remote, local), local)
    packages = _changed_packages(remote, local)
    result.update(
        {
            "changes": changes,
            "would_build_packages": packages,
            "warnings": _dangling_refs(local),
            "unknown_files": unknown_files(folder),
            "new_ids": new_ids,
        }
    )
    if not changes:
        return {**result, "ok": True, "changed": False}
    if not execute:
        return {**result, "ok": True, "changed": True, "next_action": "Review the changes, then push with execute=true."}

    for action_id in packages:
        code = local["plugin_actions"][action_id]["code"]
        build = client.build_package(plugin_id, code["package"]["fn"], session)
        if not build.get("success"):
            return {**result, "ok": False, "reason": "package_build_failed", "action_id": action_id, "build": build}
        if "package_status" in code or "package_used" in code:
            code["package_status"] = "up_to_date"

    client.save_plugin(plugin_id, local, session)
    # Saved: from here on the folder and base must describe what Bubble now holds, whatever
    # happens next, or a retry would create the NEW items again.
    result["executed"] = True
    result["moved_dirs"] = _move_new_dirs(folder, new_ids, local)
    write_folder(folder, local)
    _write_base(folder, local)

    client.mark_git_modified(plugin_id, session)
    readback = client.get_plugin(plugin_id, session)
    _write_base(folder, readback)
    mismatch = diff_documents(local, _without_absent_nulls(readback, local))
    if mismatch:
        return {**result, "ok": False, "reason": "readback_mismatch", "readback_changes": mismatch}

    write_folder(folder, readback)
    next_action = "Pushed. Reload any plugin editor tab to see the changes."
    if packages:
        next_action += " npm packages were rebuilt; open the server action in the editor and confirm the deployment package shows up to date."
    return {**result, "ok": True, "changed": True, "next_action": next_action}
