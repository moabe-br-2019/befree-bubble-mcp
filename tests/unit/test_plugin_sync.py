from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from bubble_mcp.plugin_authoring.folder import BASE_FILE, read_folder
from bubble_mcp.plugin_authoring.sync import diff_documents, pull_plugin, push_plugin

PID = "1795610633201x773816577862139900"
SESSION = object()


def _doc() -> dict[str, Any]:
    return {
        "dependencies": {"plugin_api_version": 4, "use_jquery": True},
        "editor_counter": 4,
        "human": "Test",
        "meta_data": {"name": "Test", "categories": [], "description": None},
        "plugin_elements": {
            "AAC": {
                "display": "el",
                "code": {"update": {"fn": "function(i, p, c) {\n}"}},
                "fields": {"AAD": {"name": "f", "editor": "DynamicValue", "value": "custom_data_AEn"}},
            }
        },
        "plugin_actions": {
            "AAE": {
                "display": "srv",
                "type": "server_side",
                "code": {
                    "server": {"fn": "function(p, c) {}"},
                    "package": {"fn": '{"dependencies": {}}'},
                    "package_status": "up_to_date",
                    "package_hash": "abc",
                },
            }
        },
    }


class FakeClient:
    def __init__(self, doc: dict[str, Any], *, lock: bool = True, build_ok: bool = True) -> None:
        self.remote = copy.deepcopy(doc)
        self.lock = lock
        self.build_ok = build_ok
        self.calls: list[str] = []
        self.readback_override: dict[str, Any] | None = None

    def get_plugin(self, plugin_id: str, session: Any) -> dict[str, Any]:
        self.calls.append("get")
        if self.readback_override is not None and "save" in self.calls:
            return copy.deepcopy(self.readback_override)
        doc = copy.deepcopy(self.remote)
        doc["meta_data"].setdefault("image", None)  # GET carries null meta keys the save omits
        return doc

    def save_plugin(self, plugin_id: str, raw: dict[str, Any], session: Any) -> None:
        self.calls.append("save")
        self.remote = copy.deepcopy(raw)

    def acquire_lock(self, plugin_id: str, holder: str, session: Any) -> bool:
        self.calls.append("lock")
        return self.lock

    def mark_git_modified(self, plugin_id: str, session: Any) -> None:
        self.calls.append("git")

    def build_package(self, plugin_id: str, package_json_string: str, session: Any) -> dict[str, Any]:
        self.calls.append("build")
        return {"success": self.build_ok}


def _pull(tmp_path: Path, client: FakeClient) -> Path:
    folder = tmp_path / "plug"
    result = pull_plugin(profile="p", plugin_id=PID, folder=folder, client=client, session=SESSION)
    assert result["ok"] is True
    return folder


def test_pull_writes_folder_and_base(tmp_path: Path) -> None:
    client = FakeClient(_doc())
    folder = _pull(tmp_path, client)
    assert (folder / "elements/AAC-el/update.js").read_text(encoding="utf-8") == "function(i, p, c) {\n}"
    base = json.loads((folder / BASE_FILE).read_text(encoding="utf-8"))
    assert read_folder(folder) == base


def test_pull_refuses_over_local_edits_unless_forced(tmp_path: Path) -> None:
    client = FakeClient(_doc())
    folder = _pull(tmp_path, client)
    (folder / "elements/AAC-el/update.js").write_text("changed", encoding="utf-8")
    refused = pull_plugin(profile="p", plugin_id=PID, folder=folder, client=client, session=SESSION)
    assert refused["ok"] is False and refused["reason"] == "local_changes"
    forced = pull_plugin(profile="p", plugin_id=PID, folder=folder, client=client, session=SESSION, force=True)
    assert forced["ok"] is True


def test_push_without_changes(tmp_path: Path) -> None:
    client = FakeClient(_doc())
    folder = _pull(tmp_path, client)
    result = push_plugin(profile="p", folder=folder, execute=True, client=client, session=SESSION)
    assert result["ok"] is True and result["changed"] is False
    assert "save" not in client.calls


def test_push_dry_run_lists_changed_code_and_warns_dangling_refs(tmp_path: Path) -> None:
    client = FakeClient(_doc())
    folder = _pull(tmp_path, client)
    (folder / "elements/AAC-el/update.js").write_text("function(i, p, c) {\n  x();\n}", encoding="utf-8")
    result = push_plugin(profile="p", folder=folder, client=client, session=SESSION)
    assert result["ok"] is True and result["executed"] is False
    assert [c["path"] for c in result["changes"]] == ["plugin_elements/AAC/code/update/fn"]
    assert any("custom_data_AEn" in warning for warning in result["warnings"])
    assert "save" not in client.calls


def test_push_refuses_when_remote_changed_unless_forced(tmp_path: Path) -> None:
    client = FakeClient(_doc())
    folder = _pull(tmp_path, client)
    client.remote["human"] = "edited in the UI"
    (folder / "elements/AAC-el/update.js").write_text("x", encoding="utf-8")
    refused = push_plugin(profile="p", folder=folder, execute=True, client=client, session=SESSION)
    assert refused["ok"] is False and refused["reason"] == "remote_changed"
    assert "save" not in client.calls
    forced = push_plugin(profile="p", folder=folder, execute=True, force=True, client=client, session=SESSION)
    assert forced["ok"] is True and forced["executed"] is True


def test_push_refuses_when_editor_open(tmp_path: Path) -> None:
    client = FakeClient(_doc(), lock=False)
    folder = _pull(tmp_path, client)
    (folder / "elements/AAC-el/update.js").write_text("x", encoding="utf-8")
    result = push_plugin(profile="p", folder=folder, execute=True, client=client, session=SESSION)
    assert result["ok"] is False and result["reason"] == "editor_open"
    assert "save" not in client.calls


def test_push_builds_changed_package_before_saving(tmp_path: Path) -> None:
    client = FakeClient(_doc())
    folder = _pull(tmp_path, client)
    (folder / "actions/AAE-srv/package.json").write_text('{"dependencies": {"lodash": "latest"}}', encoding="utf-8")
    result = push_plugin(profile="p", folder=folder, execute=True, client=client, session=SESSION)
    assert result["ok"] is True
    assert client.calls.index("build") < client.calls.index("save")
    assert client.remote["plugin_actions"]["AAE"]["code"]["package_status"] == "up_to_date"
    assert client.remote["plugin_actions"]["AAE"]["code"]["package_hash"] == "abc"


def test_push_package_build_failure_does_not_save(tmp_path: Path) -> None:
    client = FakeClient(_doc(), build_ok=False)
    folder = _pull(tmp_path, client)
    (folder / "actions/AAE-srv/package.json").write_text('{"dependencies": {"x": "1"}}', encoding="utf-8")
    result = push_plugin(profile="p", folder=folder, execute=True, client=client, session=SESSION)
    assert result["ok"] is False and result["reason"] == "package_build_failed"
    assert "save" not in client.calls


def test_push_readback_mismatch(tmp_path: Path) -> None:
    client = FakeClient(_doc())
    folder = _pull(tmp_path, client)
    (folder / "elements/AAC-el/update.js").write_text("x", encoding="utf-8")
    client.readback_override = _doc()
    result = push_plugin(profile="p", folder=folder, execute=True, client=client, session=SESSION)
    assert result["ok"] is False and result["reason"] == "readback_mismatch"


def test_push_new_dir_gets_id_and_is_renamed(tmp_path: Path) -> None:
    client = FakeClient(_doc())
    folder = _pull(tmp_path, client)
    new = folder / "elements/NEW-widget"
    new.mkdir()
    (new / "element.json").write_text('{"display": "widget", "code": {"update": {}}}\n', encoding="utf-8")
    (new / "update.js").write_text("function(i, p, c) {}", encoding="utf-8")
    result = push_plugin(profile="p", folder=folder, execute=True, client=client, session=SESSION)
    assert result["ok"] is True, result
    assert result["new_ids"] == {"elements/NEW-widget": "AAF"}
    assert not new.exists()
    assert (folder / "elements/AAF-widget/update.js").exists()
    assert client.remote["plugin_elements"]["AAF"]["display"] == "widget"


def test_diff_documents_reports_paths() -> None:
    a = {"x": {"y": 1, "z": "a\nb"}, "gone": 1}
    b = {"x": {"y": 2, "z": "a\nb\nc"}, "new": True}
    ops = {(c["path"], c["op"]) for c in diff_documents(a, b)}
    assert ops == {("x/y", "change"), ("x/z", "change"), ("gone", "remove"), ("new", "add")}
