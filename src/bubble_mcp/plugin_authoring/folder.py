"""Mirror a Bubble plugin document into a local folder and rebuild it losslessly.

A plugin is one JSON document (``GET /appeditor/get_plugin`` / ``POST /appeditor/save_plugin``).
``explode`` splits it into code files (``.js``/``.html``/``package.json``) plus canonical JSON,
and moves secret values into a side file. ``assemble`` is its exact inverse:
``assemble(*explode(doc)) == doc`` for every real plugin seen so far. Nothing here knows a closed
schema: keys the mirror does not split stay in the JSON untouched.

Layout::

    plugin.json                                   # rest of the doc; element/action maps point at dirs
    shared/header.html                            # html_header.snippet
    elements/<id>-<slug>/element.json
    elements/<id>-<slug>/header.html              # headers.snippet
    elements/<id>-<slug>/<code key>.js            # code.<key>.fn (initialize, update, preview, ...)
    elements/<id>-<slug>/states/<stateId>.init.js # states.<id>.initialization_code.fn
    elements/<id>-<slug>/actions/<actId>-<slug>.js
    actions/<id>-<slug>/action.json
    actions/<id>-<slug>/<code key>.js             # code.client.fn, code.server.fn, ...
    actions/<id>-<slug>/package.json              # code.package.fn, raw text
    .bubble-plugin/secrets.json                   # git-ignored
"""

from __future__ import annotations

import copy
import json
import os
import re
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any

from bubble_mcp.plugin_authoring.ids import allocate_id, decode_id

META_DIR = ".bubble-plugin"
SECRETS_FILE = f"{META_DIR}/secrets.json"
MANIFEST_FILE = f"{META_DIR}/manifest.json"
BASE_FILE = f"{META_DIR}/base.json"
GITIGNORE_LINES = (SECRETS_FILE, BASE_FILE)

PLUGIN_JSON = "plugin.json"
SHARED_HEADER = "shared/header.html"
ELEMENT_JSON = "element.json"
ACTION_JSON = "action.json"
HEADER_HTML = "header.html"
PACKAGE_JSON = "package.json"
NEW_PREFIX = "NEW"

# Keys under plugin_api whose values are credentials.
SECRET_API_KEYS = ("private_key", "private_key_test", "appsecret", "password", "username")
# Containers of {key, value, private|visibility} parameter objects.
PARAM_CONTAINERS = ("headers", "url_params", "body_params", "params", "shared_headers", "shared_params")
# Id-keyed children that a copied (NEW-) item must renumber.
ELEMENT_CHILDREN: tuple[str, ...] = ("fields", "states", "events", "actions")
ACTION_CHILDREN: tuple[str, ...] = ("fields", "return_value")
_REF_PATTERN = re.compile(r"^(custom_data_|custom_field_)([A-Za-z]{3}|NEW[A-Za-z0-9_-]*)$")
# Names used as file or dir names (code keys, state ids, element-action ids, item ids).
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
KNOWN_ELEMENT_CODE = frozenset({"initialize", "update", "preview", "reset", "rn_component"})
KNOWN_ACTION_CODE = frozenset({"client", "server"})


def _safe_name(name: str, what: str) -> str:
    if not _SAFE_NAME.match(name):
        raise ValueError(f"Unsafe {what} {name!r}: only letters, digits, '_' and '-' can be mirrored to files.")
    return name


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def slugify(text: str, *, limit: int = 40) -> str:
    ascii_text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", ascii_text).strip("-.")[:limit].strip("-.")
    return slug or "x"


# ── secrets ──────────────────────────────────────────────────────────────────


def _is_secret_param(param: Any, container: str) -> bool:
    if not isinstance(param, dict) or "value" not in param:
        return False
    visibility = param.get("visibility")
    if visibility is not None:
        return bool(visibility == "secret")
    if "private" not in param and container in ("headers", "shared_headers"):
        # Old-style headers ("0", "1", ...) carry no flag and often hold Authorization values.
        return True
    # Older objects only carry "private". The editor sets it even for values shown as Public,
    # so this over-collects; storing a harmless value in the side file costs nothing.
    return param.get("private") is True


def _extract_secrets(doc: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    api = doc.get("plugin_api")
    if not isinstance(api, dict):
        return found

    def take(container: dict[str, Any], key: str, path: list[str]) -> None:
        found.append({"path": path + [key], "value": container.pop(key)})

    for key in SECRET_API_KEYS:
        if key in api:
            take(api, key, ["plugin_api"])

    def walk(node: Any, path: list[str]) -> None:
        if not isinstance(node, dict):
            return
        for key, child in list(node.items()):
            if key in PARAM_CONTAINERS and isinstance(child, dict):
                for param_id, param in child.items():
                    if _is_secret_param(param, key):
                        take(param, "value", path + [key, param_id])
            walk(child, path + [key])

    walk(api, ["plugin_api"])
    return found


def find_secret_echoes(doc: dict[str, Any], *, min_length: int = 8) -> list[str]:
    """Paths of strings that still contain a secret value after extraction.

    Response samples (``types``, ``full_response``), request ``body`` templates, instructions or
    code can repeat a credential verbatim; the mirror cannot split those, so pull reports them.
    """

    stripped = copy.deepcopy(doc)
    values = {str(e["value"]) for e in _extract_secrets(stripped) if isinstance(e["value"], str)}
    values = {value for value in values if len(value) >= min_length}
    found: list[str] = []

    def walk(node: Any, path: list[str]) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                walk(child, path + [str(key)])
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, path + [str(index)])
        elif isinstance(node, str) and any(value in node for value in values):
            found.append("/".join(path))

    walk(stripped, [])
    return found


def _apply_secrets(doc: dict[str, Any], secrets: dict[str, Any]) -> None:
    for entry in secrets.get("values", []):
        node: Any = doc
        *parents, last = entry["path"]
        for part in parents:
            if not isinstance(node, dict) or part not in node:
                node = None
                break
            node = node[part]
        if isinstance(node, dict):
            node[last] = entry["value"]


# ── explode ──────────────────────────────────────────────────────────────────


def _pop_fn(holder: Any) -> str | None:
    if isinstance(holder, dict) and isinstance(holder.get("fn"), str):
        return str(holder.pop("fn"))
    return None


def _explode_element(element: dict[str, Any], base: str, files: dict[str, str]) -> None:
    code = element.get("code")
    if isinstance(code, dict):
        for code_key, holder in code.items():
            text = _pop_fn(holder)
            if text is not None:
                files[f"{base}/{_safe_name(code_key, 'code key')}.js"] = text
    headers = element.get("headers")
    if isinstance(headers, dict) and isinstance(headers.get("snippet"), str):
        files[f"{base}/{HEADER_HTML}"] = headers.pop("snippet")
    states = element.get("states")
    if isinstance(states, dict):
        for state_id, state in states.items():
            text = _pop_fn(state.get("initialization_code")) if isinstance(state, dict) else None
            if text is not None:
                files[f"{base}/states/{_safe_name(state_id, 'state id')}.init.js"] = text
    actions = element.get("actions")
    if isinstance(actions, dict):
        for action_id, action in actions.items():
            text = _pop_fn(action.get("code")) if isinstance(action, dict) else None
            if text is not None:
                files[f"{base}/actions/{_safe_name(action_id, 'element action id')}-{slugify(action.get('caption', ''))}.js"] = text
    files[f"{base}/{ELEMENT_JSON}"] = canonical_json(element)


def _explode_action(action: dict[str, Any], base: str, files: dict[str, str]) -> None:
    code = action.get("code")
    if isinstance(code, dict):
        for code_key, holder in code.items():
            text = _pop_fn(holder)
            if text is not None:
                name = PACKAGE_JSON if code_key == "package" else f"{_safe_name(code_key, 'code key')}.js"
                files[f"{base}/{name}"] = text
    files[f"{base}/{ACTION_JSON}"] = canonical_json(action)


def explode(doc: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    """Split a plugin document into ``(files, secrets)``. ``doc`` is not modified."""

    doc = copy.deepcopy(doc)
    secrets = {"values": _extract_secrets(doc)}
    files: dict[str, str] = {}

    html_header = doc.get("html_header")
    if isinstance(html_header, dict) and isinstance(html_header.get("snippet"), str):
        files[SHARED_HEADER] = html_header.pop("snippet")

    for doc_key, folder, explode_item, label in (
        ("plugin_elements", "elements", _explode_element, "display"),
        ("plugin_actions", "actions", _explode_action, "display"),
    ):
        items = doc.get(doc_key)
        if not isinstance(items, dict):
            continue
        index: dict[str, str] = {}
        for item_id, item in items.items():
            base = f"{folder}/{_safe_name(item_id, 'item id')}-{slugify(item.get(label, '') if isinstance(item, dict) else '')}"
            index[item_id] = base
            explode_item(item, base, files)
        doc[doc_key] = index

    files[PLUGIN_JSON] = canonical_json(doc)
    return files, secrets


# ── assemble ─────────────────────────────────────────────────────────────────


def _put_fn(container: dict[str, Any], key: str, text: str) -> None:
    holder = container.setdefault(key, {})
    if isinstance(holder, dict):
        holder["fn"] = text


def _children_with_prefix(files: dict[str, str], prefix: str) -> dict[str, str]:
    return {path[len(prefix):]: text for path, text in files.items() if path.startswith(prefix)}


def _assemble_element(files: dict[str, str], base: str) -> dict[str, Any]:
    element: dict[str, Any] = json.loads(files[f"{base}/{ELEMENT_JSON}"])
    inside = _children_with_prefix(files, f"{base}/")
    code_keys = set(element.get("code") or {}) | KNOWN_ELEMENT_CODE
    action_keys = sorted(element.get("actions") or {}, key=len, reverse=True)
    for rel, text in inside.items():
        parts = PurePosixPath(rel).parts
        if len(parts) == 1 and rel.endswith(".js"):
            if rel[: -len(".js")] in code_keys:
                _put_fn(element.setdefault("code", {}), rel[: -len(".js")], text)
        elif rel == HEADER_HTML:
            element.setdefault("headers", {})["snippet"] = text
        elif len(parts) == 2 and parts[0] == "states" and rel.endswith(".init.js"):
            state = element.setdefault("states", {}).setdefault(parts[1][: -len(".init.js")], {})
            _put_fn(state, "initialization_code", text)
        elif len(parts) == 2 and parts[0] == "actions" and rel.endswith(".js"):
            # Keys may contain "-" (NEW-go), so match the longest known key before the slug.
            action_id = next((key for key in action_keys if parts[1].startswith(f"{key}-")), None)
            if action_id is None:
                action_id = parts[1].split("-", 1)[0].removesuffix(".js")
            _put_fn(element.setdefault("actions", {}).setdefault(action_id, {}), "code", text)
    return element


def _assemble_action(files: dict[str, str], base: str) -> dict[str, Any]:
    action: dict[str, Any] = json.loads(files[f"{base}/{ACTION_JSON}"])
    inside = _children_with_prefix(files, f"{base}/")
    code_keys = set(action.get("code") or {}) | KNOWN_ACTION_CODE
    for rel, text in inside.items():
        if "/" in rel:
            continue
        if rel == PACKAGE_JSON:
            _put_fn(action.setdefault("code", {}), "package", text)
        elif rel.endswith(".js") and rel[: -len(".js")] in code_keys:
            _put_fn(action.setdefault("code", {}), rel[: -len(".js")], text)
    return action


def _rekey_new_children(doc: dict[str, Any], item: dict[str, Any], children: tuple[str, ...], *, renew_all: bool) -> None:
    """Give fresh ids to children keyed ``NEW*`` (or all children of a copied item)."""

    renamed: dict[str, str] = {}
    for child_key in children:
        container = item.get(child_key)
        if not isinstance(container, dict):
            continue
        for old_id in sorted(container):
            if renew_all or old_id.startswith(NEW_PREFIX):
                new_id = allocate_id(doc)
                container[new_id] = container.pop(old_id)
                renamed[old_id] = new_id
        # Element actions carry their own fields (and plugin actions their return values' fields
        # never nest further); renew those too.
        for child in container.values():
            if isinstance(child, dict) and isinstance(child.get("fields"), dict):
                fields = child["fields"]
                for old_id in sorted(fields):
                    if renew_all or old_id.startswith(NEW_PREFIX):
                        new_id = allocate_id(doc)
                        fields[new_id] = fields.pop(old_id)
                        renamed[old_id] = new_id
    if renamed:
        _rewrite_refs(item, renamed)


def _rewrite_refs(node: Any, renamed: dict[str, str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str):
                match = _REF_PATTERN.match(value)
                if match and match.group(2) in renamed:
                    node[key] = match.group(1) + renamed[match.group(2)]
            else:
                _rewrite_refs(value, renamed)
    elif isinstance(node, list):
        for value in node:
            _rewrite_refs(value, renamed)


def _new_dirs(files: dict[str, str], folder: str, marker: str, known: set[str]) -> list[str]:
    found = set()
    for path in files:
        parts = PurePosixPath(path).parts
        if len(parts) == 3 and parts[0] == folder and parts[2] == marker:
            base = f"{folder}/{parts[1]}"
            if base not in known and parts[1].startswith(f"{NEW_PREFIX}-"):
                found.add(base)
    return sorted(found)


def assemble(
    files: dict[str, str],
    secrets: dict[str, Any],
    *,
    new_ids: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Rebuild the plugin document from mirrored files.

    Dirs named ``NEW-<slug>`` under ``elements/`` or ``actions/`` become new items: each gets a
    fresh id (recorded in ``new_ids`` as ``{dir: id}``) and its inner ids are renewed so a copy
    cannot collide with its source. Children keyed ``NEW*`` inside any item also get fresh ids.
    """

    doc: dict[str, Any] = json.loads(files[PLUGIN_JSON])
    if SHARED_HEADER in files:
        doc.setdefault("html_header", {})["snippet"] = files[SHARED_HEADER]

    kinds = (
        ("plugin_elements", "elements", ELEMENT_JSON, _assemble_element, ELEMENT_CHILDREN),
        ("plugin_actions", "actions", ACTION_JSON, _assemble_action, ACTION_CHILDREN),
    )
    # Pass 1: every existing item, so id allocation below sees all ids already in use.
    pending: list[tuple[str, Any, tuple[str, ...], list[str]]] = []
    for doc_key, folder, marker, assemble_item, children in kinds:
        index = doc.get(doc_key)
        known = set(index.values()) if isinstance(index, dict) else set()
        if isinstance(index, dict):
            doc[doc_key] = {item_id: assemble_item(files, base) for item_id, base in index.items()}
        pending.append((doc_key, assemble_item, children, _new_dirs(files, folder, marker, known)))
    # Pass 2: new items and new children.
    for doc_key, assemble_item, children, additions in pending:
        for base in additions:
            item = assemble_item(files, base)
            item_id = allocate_id(doc)
            _rekey_new_children(doc, item, children, renew_all=True)
            doc.setdefault(doc_key, {})[item_id] = item
            if new_ids is not None:
                new_ids[base] = item_id
        for item_id, item in list((doc.get(doc_key) or {}).items()):
            if item_id in (new_ids or {}).values():
                continue
            if isinstance(item, dict):
                _rekey_new_children(doc, item, children, renew_all=False)

    _apply_secrets(doc, secrets)
    return doc


# ── disk ─────────────────────────────────────────────────────────────────────


def _read_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_private(path: Path, text: str) -> None:
    """Write a file readable only by the owner from the moment it exists."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    path.chmod(0o600)


def _inside(folder: Path, rel: str) -> Path | None:
    target = folder / rel
    try:
        resolved = target.resolve()
        resolved.relative_to(folder.resolve())
    except (ValueError, OSError):
        return None
    if target.is_symlink():
        return None
    return target


def _ensure_gitignore(folder: Path) -> None:
    path = folder / ".gitignore"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    missing = [line for line in GITIGNORE_LINES if line not in lines]
    if missing:
        path.write_text("\n".join(lines + missing) + "\n", encoding="utf-8")


def write_folder(folder: Path, doc: dict[str, Any]) -> list[Path]:
    """Write the mirror of ``doc`` into ``folder``; remove files a previous mirror wrote and this one does not."""

    files, secrets = explode(doc)
    folder.mkdir(parents=True, exist_ok=True)
    previous = set(_read_json_file(folder / MANIFEST_FILE, []))
    for stale in sorted(previous - set(files)):
        target = _inside(folder, str(stale))
        if target is not None and target.is_file():
            target.unlink()
            parent = target.parent
            while parent != folder and parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
    written = []
    for rel, text in files.items():
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        written.append(target)
    (folder / META_DIR).mkdir(exist_ok=True)
    write_private(folder / SECRETS_FILE, canonical_json(secrets))
    (folder / MANIFEST_FILE).write_text(canonical_json(sorted(files)), encoding="utf-8")
    _ensure_gitignore(folder)
    return written


def _is_layout_path(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    if rel in (PLUGIN_JSON, SHARED_HEADER):
        return True
    if len(parts) < 3 or parts[0] not in ("elements", "actions"):
        return False
    tail = parts[2:]
    if len(tail) == 1:
        return tail[0].endswith((".js", ".json", ".html"))
    return parts[0] == "elements" and len(tail) == 2 and tail[0] in ("states", "actions") and tail[1].endswith(".js")


def _walk_files(folder: Path) -> list[str]:
    found = []
    for path in folder.rglob("*"):
        rel = path.relative_to(folder).as_posix()
        parts = PurePosixPath(rel).parts
        if any(part.startswith(".") or part == "node_modules" for part in parts):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        found.append(rel)
    return sorted(found)


def collect_files(folder: Path) -> dict[str, str]:
    """Read the mirrored files only (dotfiles, node_modules, symlinks and other files are skipped)."""

    files: dict[str, str] = {}
    for rel in _walk_files(folder):
        if _is_layout_path(rel):
            with (folder / rel).open(encoding="utf-8", newline="") as handle:
                files[rel] = handle.read()
    return files


def unknown_files(folder: Path) -> list[str]:
    """Files in the folder that the mirror does not use (they are never sent to Bubble)."""

    used, _ = explode(read_folder(folder))
    return [rel for rel in _walk_files(folder) if rel not in used and not rel.startswith("elements/NEW-") and not rel.startswith("actions/NEW-")]


def secret_gaps(base: dict[str, Any], local: dict[str, Any]) -> list[str]:
    """Secret values present in ``base`` whose holder still exists in ``local`` without them."""

    gaps = []
    for entry in _extract_secrets(copy.deepcopy(base)):
        node: Any = local
        *parents, last = entry["path"]
        for part in parents:
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, dict) and last not in node:
            gaps.append("/".join(entry["path"]))
    return gaps


def read_folder(folder: Path, *, new_ids: dict[str, str] | None = None) -> dict[str, Any]:
    secrets = _read_json_file(folder / SECRETS_FILE, {"values": []})
    return assemble(collect_files(folder), secrets, new_ids=new_ids)


def id_of_dir(base: str) -> str | None:
    """``elements/AAC-test`` → ``AAC`` (None for ``NEW-`` dirs)."""

    name = PurePosixPath(base).name.split("-", 1)[0]
    return name if decode_id(name) is not None else None
