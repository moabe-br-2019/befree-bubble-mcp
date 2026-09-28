"""Answer structural questions straight from the decoded ``.bubble`` export.

The context graph that ``bubble_context_find`` searches is a lossy projection: page workflows
keep only their id, reusable workflows are not imported at all, and no action says what it
changes. Agents that needed more parsed the export with ``python3 -c`` through bash - 19 times
in one session, 28 in another that never finished. The export already holds the answers; this
module reads them out in a compact form:

- ``element_subtree``: an element with its children, the workflows it and its children trigger,
  and the workflows elsewhere in the same page or reusable that point at any of them;
- ``workflows``: every workflow of a page, a reusable, or the backend, summarised;
- ``field_writers``: every action that sets field Y of type Z (create, change, change list,
  wherever it lives), and the database triggers on Z that can react to it.

The export is the decoded form (``type``/``properties``/``elements``), never the write form, and
is one version's picture of the app; the caller resolves which version it holds.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BACKEND_NAMES = {"backend", "api", "backend workflows"}
FIELD_SETTING_KEYS = ("changes", "initial_values")


class ExportQueryError(ValueError):
    """A query that cannot be answered as asked; ``payload`` says why and what would work."""

    def __init__(self, error: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.payload = {"ok": False, "error": error, "message": message, **extra}


@dataclass(frozen=True)
class Container:
    kind: str  # "page", "reusable" or "backend"
    key: str  # the slot key under pages / element_definitions, "api" for the backend
    id: str
    name: str
    node: dict[str, Any]

    @property
    def pointer(self) -> list[str]:
        if self.kind == "backend":
            return ["api"]
        return ["pages" if self.kind == "page" else "element_definitions", self.key]

    def summary(self) -> dict[str, Any]:
        return {"kind": self.kind, "id": self.id, "name": self.name}


def _dicts(value: Any) -> dict[str, dict[str, Any]]:
    """A map's node entries; the export mixes in bookkeeping such as ``length``."""

    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items() if isinstance(item, dict)}


def _props(node: dict[str, Any]) -> dict[str, Any]:
    props = node.get("properties")
    return props if isinstance(props, dict) else {}


def containers(app: dict[str, Any]) -> list[Container]:
    found: list[Container] = []
    for kind, section in (("page", "pages"), ("reusable", "element_definitions")):
        for key, node in _dicts(app.get(section)).items():
            found.append(
                Container(kind, key, str(node.get("id") or key), str(node.get("name") or key), node)
            )
    found.append(Container("backend", "api", "api", "backend", {"workflows": app.get("api") or {}}))
    return found


def _workflows(container: Container) -> dict[str, dict[str, Any]]:
    return _dicts(container.node.get("workflows"))


def _walk_elements(
    elements: Any, pointer: list[str], parent: str | None = None
) -> Iterator[tuple[dict[str, Any], list[str], str | None]]:
    for slot, element in _dicts(elements).items():
        here = [*pointer, "elements", slot]
        yield element, here, parent
        yield from _walk_elements(element.get("elements"), here, str(element.get("id") or slot))


def _element_names(container: Container) -> dict[str, str]:
    # A reusable's own id is an element reference too ("this reusable's thing").
    names = {container.id: container.name} if container.kind == "reusable" else {}
    for element, _, _ in _walk_elements(container.node.get("elements"), container.pointer):
        if element.get("id"):
            names[str(element["id"])] = str(element.get("name") or element.get("default_name") or "")
    return names


def find_container(app: dict[str, Any], target: str) -> Container:
    wanted = target.strip()
    if wanted.lower() in BACKEND_NAMES:
        return next(item for item in containers(app) if item.kind == "backend")
    matches = [
        item
        for item in containers(app)
        if item.kind != "backend" and wanted in (item.id, item.key)
    ] or [
        item
        for item in containers(app)
        if item.kind != "backend" and item.name.lower() == wanted.lower()
    ]
    if not matches:
        raise ExportQueryError(
            "container_not_found",
            f"No page or reusable named or with id '{target}' in the export. Pass 'backend' for "
            "backend workflows.",
        )
    if len(matches) > 1:
        raise ExportQueryError(
            "ambiguous_container",
            f"'{target}' names more than one page or reusable; pass its id.",
            candidates=[item.summary() for item in matches],
        )
    return matches[0]


# --- expressions -----------------------------------------------------------------------------


def _element_refs(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "element_id" and isinstance(child, str):
                found.add(child)
            else:
                found |= _element_refs(child)
    elif isinstance(value, list):
        for item in value:
            found |= _element_refs(item)
    return found


def _message_names(value: Any) -> set[str]:
    """Every field read through a ``Message`` step anywhere in an expression."""

    found: set[str] = set()
    if isinstance(value, dict):
        if value.get("type") == "Message" and isinstance(value.get("name"), str):
            found.add(value["name"])
        for child in value.values():
            found |= _message_names(child)
    elif isinstance(value, list):
        for item in value:
            found |= _message_names(item)
    return found


def _fields_set(action: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for holder in FIELD_SETTING_KEYS:
        for entry in _dicts(_props(action).get(holder)).values():
            key = entry.get("key")
            if isinstance(key, str) and key not in keys:
                keys.append(key)
    return keys


class _TypeResolver:
    """Best-effort data type of the thing an expression evaluates to.

    Enough to tell whether "change thing -> status" is about a client or a booking in the common
    shapes (a workflow parameter, the trigger's thing, a previous step's new thing, a group's
    thing, and a chain of fields from any of those). Anything else is None - unknown, not wrong.
    """

    def __init__(self, app: dict[str, Any]) -> None:
        self.types = _dicts(app.get("user_types"))
        self.content_types: dict[str, str] = {}
        for container in containers(app):
            for element, _, _ in _walk_elements(container.node.get("elements"), container.pointer):
                content = _props(element).get("group_type")
                if isinstance(content, str) and element.get("id"):
                    self.content_types[str(element["id"])] = content

    def field_type(self, type_ref: str | None, field: str) -> str | None:
        if not type_ref:
            return None
        spec = self.types.get(_type_key(type_ref)) or {}
        field_spec = _dicts(spec.get("fields")).get(field) or {}
        value = field_spec.get("value")
        return value if isinstance(value, str) and (value.startswith("custom.") or value == "user") else None

    def of(
        self,
        expression: Any,
        *,
        workflow: dict[str, Any],
        trigger_type: str | None,
    ) -> str | None:
        if not isinstance(expression, dict):
            return None
        root = self._root(expression, workflow=workflow, trigger_type=trigger_type)
        step = expression.get("next")
        while isinstance(step, dict) and root is not None:
            if step.get("type") != "Message" or not isinstance(step.get("name"), str):
                return None
            root = self.field_type(root, step["name"])
            step = step.get("next")
        return root

    def _root(self, expression: dict[str, Any], *, workflow: dict[str, Any], trigger_type: str | None) -> str | None:
        kind = expression.get("type")
        props = _props(expression)
        if kind == "APIEventParameter":
            return props.get("btype_id") if isinstance(props.get("btype_id"), str) else None
        if kind in ("CurrentDataItem", "OldDataItem"):
            return trigger_type
        if kind == "CurrentUser":
            return "user"
        if kind == "GetElement":
            return self.content_types.get(str(props.get("element_id")))
        if kind == "PreviousStep":
            for action in _dicts(workflow.get("actions")).values():
                if action.get("id") == props.get("action_id") and action.get("type") == "NewThing":
                    thing = _props(action).get("thing_type")
                    return thing if isinstance(thing, str) else None
        return None


def _type_key(type_ref: str) -> str:
    return type_ref.split(".", 1)[1] if type_ref.startswith("custom.") else type_ref


# --- summaries -------------------------------------------------------------------------------


def _workflow_summary(
    container: Container, slot: str, workflow: dict[str, Any], names: dict[str, str]
) -> dict[str, Any]:
    props = _props(workflow)
    summary: dict[str, Any] = {
        "id": workflow.get("id"),
        "pointer": ["api", slot] if container.kind == "backend" else [*container.pointer, "workflows", slot],
        "event": workflow.get("type"),
    }
    name = props.get("wf_name") or props.get("event_name")
    if name:
        summary["name"] = name
    trigger = props.get("element_id")
    if isinstance(trigger, str):
        summary["trigger_element"] = {"id": trigger, "name": names.get(trigger)}
    if isinstance(props.get("data_trigger_type"), str):
        summary["data_trigger_type"] = props["data_trigger_type"]
    if props.get("condition") is not None:
        summary["has_condition"] = True
    actions = []
    for index, action in sorted(_dicts(workflow.get("actions")).items(), key=lambda item: _order(item[0])):
        entry: dict[str, Any] = {"index": index, "id": action.get("id"), "type": action.get("type")}
        refs = sorted(_element_refs(action.get("properties")))
        if refs:
            entry["elements"] = [{"id": ref, "name": names.get(ref)} for ref in refs]
        fields = _fields_set(action)
        if fields:
            entry["fields_set"] = fields
        thing = _props(action).get("thing_type") or _props(action).get("type_to_change")
        if isinstance(thing, str):
            entry["thing_type"] = thing
        actions.append(entry)
    summary["actions"] = actions
    return summary


def _order(key: str) -> tuple[int, str]:
    return (int(key), "") if key.isdigit() else (1 << 30, key)


def _tree(element: dict[str, Any], depth: int | None) -> dict[str, Any]:
    node: dict[str, Any] = {
        "id": element.get("id"),
        "name": element.get("name") or element.get("default_name"),
        "type": element.get("type"),
    }
    content = _props(element).get("group_type")
    if isinstance(content, str):
        node["content_type"] = content
    children = list(_dicts(element.get("elements")).values())
    if children:
        if depth is not None and depth <= 0:
            node["children_omitted"] = len(children)
        else:
            node["children"] = [_tree(child, None if depth is None else depth - 1) for child in children]
    return node


# --- queries ---------------------------------------------------------------------------------


def element_subtree(
    app: dict[str, Any], element: str, *, container: str | None = None, depth: int | None = None
) -> dict[str, Any]:
    scopes = [find_container(app, container)] if container else [c for c in containers(app) if c.kind != "backend"]
    wanted = element.strip()
    hits: list[tuple[Container, dict[str, Any], list[str]]] = []
    for scope in scopes:
        for node, pointer, _ in _walk_elements(scope.node.get("elements"), scope.pointer):
            if node.get("id") == wanted:
                hits.append((scope, node, pointer))
    if not hits:
        for scope in scopes:
            for node, pointer, _ in _walk_elements(scope.node.get("elements"), scope.pointer):
                if str(node.get("name") or "").lower() == wanted.lower():
                    hits.append((scope, node, pointer))
    if not hits:
        raise ExportQueryError("element_not_found", f"No element with id or name '{element}' in the export.")
    if len(hits) > 1:
        raise ExportQueryError(
            "ambiguous_element",
            f"'{element}' names {len(hits)} elements; pass its id, or container to narrow it.",
            candidates=[{"id": node.get("id"), "container": scope.summary(), "pointer": pointer} for scope, node, pointer in hits],
        )
    scope, node, pointer = hits[0]
    subtree_ids = {str(item.get("id")) for item, _, _ in _walk_elements({"root": node}, []) if item.get("id")}
    names = _element_names(scope)
    triggered, referencing = [], []
    for slot, workflow in _workflows(scope).items():
        trigger = _props(workflow).get("element_id")
        if trigger in subtree_ids:
            triggered.append(_workflow_summary(scope, slot, workflow, names))
        elif _element_refs(workflow) & subtree_ids:
            summary = _workflow_summary(scope, slot, workflow, names)
            summary["references"] = sorted(_element_refs(workflow) & subtree_ids)
            referencing.append(summary)
    return {
        "container": scope.summary(),
        "pointer": pointer,
        "element": _tree(node, depth),
        "workflows_triggered": triggered,
        "workflows_referencing": referencing,
    }


def container_workflows(app: dict[str, Any], container: str) -> dict[str, Any]:
    scope = find_container(app, container)
    names = _element_names(scope)
    return {
        "container": scope.summary(),
        "workflows": [_workflow_summary(scope, slot, workflow, names) for slot, workflow in _workflows(scope).items()],
    }


def _resolve_type(app: dict[str, Any], data_type: str) -> tuple[str, str, dict[str, Any]]:
    types = _dicts(app.get("user_types"))
    wanted = data_type.strip()
    key = _type_key(wanted)
    if key not in types:
        lowered = wanted.lower()
        matches = [name for name in types if name.lower() == _type_key(lowered)] or [
            name for name, spec in types.items() if str(spec.get("display") or "").lower() == lowered
        ]
        if len(matches) != 1:
            near = matches or [
                name for name, spec in types.items() if lowered in f"{name} {spec.get('display') or ''}".lower()
            ]
            raise ExportQueryError(
                "data_type_not_found",
                f"No single data type with key or display name '{data_type}'.",
                candidates=sorted(f"{name} ({types[name].get('display')})" for name in near),
            )
        key = matches[0]
    spec = types[key]
    return ("user" if key == "user" else f"custom.{key}"), str(spec.get("display") or key), spec


def _resolve_field(spec: dict[str, Any], field: str) -> tuple[str, dict[str, Any]]:
    fields = _dicts(spec.get("fields"))
    wanted = field.strip()
    if wanted in fields:
        return wanted, fields[wanted]
    matches = [key for key, item in fields.items() if str(item.get("display") or "").lower() == wanted.lower()]
    if len(matches) != 1:
        raise ExportQueryError(
            "field_not_found",
            f"No single field with key or display name '{field}' on this type.",
            candidates=sorted(matches) or sorted(f"{key} ({item.get('display')})" for key, item in fields.items()),
        )
    return matches[0], fields[matches[0]]


def field_writers(app: dict[str, Any], data_type: str, field: str) -> dict[str, Any]:
    type_ref, type_display, spec = _resolve_type(app, data_type)
    field_key, field_spec = _resolve_field(spec, field)
    resolver = _TypeResolver(app)

    writers: list[dict[str, Any]] = []
    triggers: list[dict[str, Any]] = []
    for scope in containers(app):
        names = _element_names(scope)
        for slot, workflow in _workflows(scope).items():
            props = _props(workflow)
            trigger_type = props.get("data_trigger_type") if isinstance(props.get("data_trigger_type"), str) else None
            summary = None
            for index, action in sorted(_dicts(workflow.get("actions")).items(), key=lambda item: _order(item[0])):
                if field_key not in _fields_set(action):
                    continue
                target = _action_target_type(resolver, action, workflow, trigger_type)
                if target is not None and target != type_ref:
                    continue
                summary = summary or _workflow_summary(scope, slot, workflow, names)
                writers.append(
                    {
                        "container": scope.summary(),
                        "workflow": {key: summary[key] for key in ("id", "pointer", "event", "name", "trigger_element") if key in summary},
                        "action": {"index": index, "id": action.get("id"), "type": action.get("type")},
                        "target_type": target,
                        "type_confirmed": target is not None,
                    }
                )
            if workflow.get("type") == "DatabaseTriggerEvent" and trigger_type == type_ref:
                entry = _workflow_summary(scope, slot, workflow, names)
                entry["condition_reads_field"] = field_key in _message_names(props.get("condition"))
                triggers.append(entry)
    return {
        "data_type": {"type": type_ref, "display": type_display},
        "field": {"key": field_key, "display": field_spec.get("display"), "value_type": field_spec.get("value")},
        "writers": writers,
        "triggers_on_type": triggers,
        "notes": (
            "type_confirmed=false means the action sets a field with this key on a thing whose type "
            "could not be worked out from the expression; check it before relying on it. "
            "triggers_on_type are the database triggers on this type: one whose condition does not "
            "read the field still runs on every change of the thing, this field included."
        ),
    }


def _action_target_type(
    resolver: _TypeResolver, action: dict[str, Any], workflow: dict[str, Any], trigger_type: str | None
) -> str | None:
    props = _props(action)
    if action.get("type") == "NewThing":
        return props.get("thing_type") if isinstance(props.get("thing_type"), str) else None
    if isinstance(props.get("type_to_change"), str):
        return props["type_to_change"]
    if action.get("type") in ("ChangeCurrentUser", "MakeChangeCurrentUser"):
        return "user"
    return resolver.of(props.get("to_change"), workflow=workflow, trigger_type=trigger_type)


# --- the export a query reads ----------------------------------------------------------------


QUERY_KINDS = ("element_subtree", "workflows", "field_writers")
_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}


def _load(path: Path) -> dict[str, Any]:
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key not in _CACHE:
        _CACHE.clear()  # one export at a time: the big ones are tens of megabytes parsed
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ExportQueryError("invalid_export", f"{path} does not hold a Bubble export object.")
        _CACHE[key] = data
    return _CACHE[key]


def run_context_query(
    *,
    kind: str,
    profile: str | None = None,
    app_id: str | None = None,
    app_version: str | None = None,
    file: str | None = None,
    element: str | None = None,
    container: str | None = None,
    data_type: str | None = None,
    field: str | None = None,
    depth: int | None = None,
    refresh: Callable[..., Path] | None = None,
) -> dict[str, Any]:
    """Answer one query from the export of ``app_version``, downloading it when the cached one
    is another version's."""

    if kind not in QUERY_KINDS:
        return ExportQueryError("unknown_kind", f"kind must be one of {', '.join(QUERY_KINDS)}.").payload
    try:
        path, version, provenance = _export_for(profile, app_id, app_version, file, refresh)
        app = _load(path)
        if kind == "element_subtree":
            answer = element_subtree(app, _required(element, "element"), container=container, depth=depth)
        elif kind == "workflows":
            answer = container_workflows(app, _required(container, "container"))
        else:
            answer = field_writers(app, _required(data_type, "data_type"), _required(field, "field"))
    except ExportQueryError as error:
        return error.payload
    return {
        "ok": True,
        "kind": kind,
        "export": {"path": str(path), "app_version": version, **provenance},
        **answer,
    }


def _required(value: str | None, name: str) -> str:
    if not value or not str(value).strip():
        raise ExportQueryError("missing_argument", f"This kind of query needs {name}.")
    return str(value)


def _export_for(
    profile: str | None,
    app_id: str | None,
    app_version: str | None,
    file: str | None,
    refresh: Callable[..., Path] | None,
) -> tuple[Path, str | None, dict[str, Any]]:
    from bubble_mcp.context.detector import (
        bubble_export_meta_path,
        cached_bubble_export_version,
        default_bubble_export_path,
        refresh_bubble_export,
    )

    if file:
        path = Path(file).expanduser()
        if not path.exists():
            raise ExportQueryError("export_not_found", f"No export at {path}.")
        return path, cached_bubble_export_version(path), _provenance(bubble_export_meta_path(path))
    if not profile or not app_id:
        raise ExportQueryError("missing_argument", "Pass a profile (with its app) or file, the export to read.")

    path = default_bubble_export_path(profile, app_id)
    cached = cached_bubble_export_version(path)
    wanted = (app_version or "").strip() or None
    downloaded = False
    # A branch's question answered from main's export finds the wrong workflows, or none - the
    # same trap delete_event fell into - so another version's export is replaced first.
    if not path.exists() or (wanted and cached and cached != wanted):
        if not wanted:
            raise ExportQueryError(
                "export_not_found",
                f"No cached export for profile '{profile}'. Pass app_version so it can be downloaded.",
            )
        try:
            path = (refresh or refresh_bubble_export)(profile=profile, app_id=app_id, app_version=wanted)
        except Exception as error:
            raise ExportQueryError(
                "export_version_mismatch",
                f"The cached export is version '{cached}', not '{wanted}', and downloading "
                f"'{wanted}' failed: {error}. Log in again (bubble_session_login) and retry.",
                cached_version=cached,
            ) from error
        cached = cached_bubble_export_version(path) or wanted
        downloaded = True
    provenance = _provenance(bubble_export_meta_path(path))
    provenance["downloaded_now"] = downloaded
    if cached is None:
        provenance["version_unknown"] = True
    return path, cached, provenance


def _provenance(meta_path: Path) -> dict[str, Any]:
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {"fetched_at": meta.get("fetched_at")} if isinstance(meta, dict) and meta.get("fetched_at") else {}
