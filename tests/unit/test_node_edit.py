"""The read -> patch -> write -> re-read -> compare cycle, with no browser and no Bubble."""

from __future__ import annotations

import copy
from typing import Any

from bubble_mcp.execution.node_edit import edit_live_node


def _action() -> dict[str, Any]:
    return {
        "id": "act-1",
        "type": "ChangeThing",
        "properties": {
            "to_change": {
                "type": "APIEventParameter",
                "is_slidable": False,
                "properties": {
                    "btype_id": "custom.client",
                    "event_id": "ev-1",
                    "param_id": "Client",
                    "param_name": "Client",
                },
            }
        },
    }


class _Reader:
    """Returns a fresh copy of whatever the fake editor currently holds."""

    def __init__(self, node: dict[str, Any]) -> None:
        self.node = node
        self.calls = 0

    def __call__(self, profile, pointer, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        return {
            "ok": True,
            "pointer": list(pointer),
            "node": copy.deepcopy(self.node),
            "app_id": "mcp-test-app",
        }


def _writer_that_applies(reader: _Reader, *, drop_key: str | None = None):  # type: ignore[no-untyped-def]
    """A fake write endpoint that stores the body, optionally losing one key on the way."""

    def write(payload, session, *, dry_run=False, calculate_derived=False):  # type: ignore[no-untyped-def]
        if not dry_run:
            body = copy.deepcopy(payload["changes"][0]["body"])
            if drop_key is not None:
                body["%p"]["to_change"]["properties"].pop(drop_key, None)
            reader.node = {
                "id": body["id"],
                "type": body["%x"],
                "properties": body["%p"],
            }
        return {"ok": True, "dry_run": dry_run, "request": {"payload": payload}}

    return write


def test_patch_previews_without_writing_when_execute_is_false() -> None:
    reader = _Reader(_action())

    result = edit_live_node(
        profile="mcp-test",
        pointer=["api", "wf-1", "actions", "0"],
        op="patch",
        leaf_pointer=["properties", "to_change", "properties"],
        patch={"param_id": "Order", "param_name": "Order"},
        execute=False,
        reader=reader,
        writer=_writer_that_applies(reader),
    )

    assert result["ok"] is True
    assert result["execute"] is False
    assert "verified" not in result
    assert reader.calls == 1
    change = result["write"]["request"]["payload"]["changes"][0]
    assert change["path_array"] == ["api", "wf-1", "actions", "0"]
    assert change["body"]["%x"] == "ChangeThing"
    assert change["body"]["%p"]["to_change"]["properties"]["param_id"] == "Order"


def test_patch_verifies_the_write_by_reading_the_node_back() -> None:
    reader = _Reader(_action())

    result = edit_live_node(
        profile="mcp-test",
        pointer=["api", "wf-1", "actions", "0"],
        op="patch",
        leaf_pointer=["properties", "to_change", "properties"],
        patch={"param_id": "Order", "param_name": "Order"},
        execute=True,
        reader=reader,
        writer=_writer_that_applies(reader),
    )

    assert result["verified"] is True
    assert result["divergence"] is None
    assert reader.calls == 2


def test_patch_names_the_path_where_a_lossy_write_diverged() -> None:
    reader = _Reader(_action())

    result = edit_live_node(
        profile="mcp-test",
        pointer=["api", "wf-1", "actions", "0"],
        op="patch",
        leaf_pointer=["properties", "to_change", "properties"],
        patch={"param_id": "Order", "param_name": "Order"},
        execute=True,
        reader=reader,
        writer=_writer_that_applies(reader, drop_key="btype_id"),
    )

    assert result["verified"] is False
    assert result["divergence"] == "properties.to_change.properties.btype_id"


def test_patch_refuses_a_leaf_pointer_that_does_not_exist() -> None:
    reader = _Reader(_action())

    result = edit_live_node(
        profile="mcp-test",
        pointer=["api", "wf-1", "actions", "0"],
        op="patch",
        leaf_pointer=["properties", "to_change", "nope"],
        patch={"param_id": "Order"},
        execute=False,
        reader=reader,
        writer=_writer_that_applies(reader),
    )

    assert result["ok"] is False
    assert result["error"] == "pointer_not_resolved"
    assert "nope" in result["message"]


def test_patch_propagates_a_failed_read_untouched() -> None:
    def failing_reader(profile, pointer, **kwargs):  # type: ignore[no-untyped-def]
        return {"ok": False, "error": "pointer_not_found", "pointer": list(pointer)}

    result = edit_live_node(
        profile="mcp-test",
        pointer=["api", "nope"],
        op="patch",
        leaf_pointer=["properties"],
        patch={"a": 1},
        reader=failing_reader,
        writer=lambda *a, **k: None,
    )

    assert result == {"ok": False, "error": "pointer_not_found", "pointer": ["api", "nope"]}


def test_unknown_op_is_refused_before_anything_is_read() -> None:
    reader = _Reader(_action())

    result = edit_live_node(
        profile="mcp-test",
        pointer=["api", "wf-1", "actions", "0"],
        op="rewrite",
        reader=reader,
        writer=_writer_that_applies(reader),
    )

    assert result["ok"] is False
    assert result["error"] == "unknown_op"
    assert reader.calls == 0


def _actions_map() -> dict[str, Any]:
    return {
        "0": {"id": "act-a", "type": "SetCustomState", "properties": {"value": 1}},
        "1": {"id": "act-b", "type": "SetCustomState", "properties": {"value": 2}},
        "2": {"id": "act-c", "type": "SetCustomState", "properties": {"value": 3}},
    }


def _map_writer(reader: _Reader):  # type: ignore[no-untyped-def]
    def write(payload, session, *, dry_run=False, calculate_derived=False):  # type: ignore[no-untyped-def]
        if not dry_run:
            body = copy.deepcopy(payload["changes"][0]["body"])
            reader.node = {
                key: {"id": value["id"], "type": value["%x"], "properties": value["%p"]}
                for key, value in body.items()
            }
        return {"ok": True, "dry_run": dry_run, "request": {"payload": payload}}

    return write


def test_reorder_renumbers_the_map_and_encodes_every_action_root() -> None:
    reader = _Reader(_actions_map())

    result = edit_live_node(
        profile="mcp-test",
        pointer=["api", "wf-1", "actions"],
        op="reorder",
        order=["2", "0", "1"],
        execute=False,
        reader=reader,
        writer=_map_writer(reader),
    )

    body = result["write"]["request"]["payload"]["changes"][0]["body"]
    assert list(body) == ["0", "1", "2"]
    assert [entry["id"] for entry in body.values()] == ["act-c", "act-a", "act-b"]
    assert all("%x" in entry and "%p" in entry for entry in body.values())


def test_reorder_repoints_the_index_at_every_moved_action() -> None:
    reader = _Reader(_actions_map())

    result = edit_live_node(
        profile="mcp-test",
        pointer=["api", "wf-1", "actions"],
        op="reorder",
        order=["2", "0", "1"],
        execute=False,
        reader=reader,
        writer=_map_writer(reader),
    )

    changes = result["write"]["request"]["payload"]["changes"]
    index_changes = [change for change in changes if change["intent"]["name"] == "Update index"]
    assert [(change["path_array"], change["body"]) for change in index_changes] == [
        (["_index", "id_to_path", "act-c"], "api.wf-1.actions.0"),
        (["_index", "id_to_path", "act-a"], "api.wf-1.actions.1"),
        (["_index", "id_to_path", "act-b"], "api.wf-1.actions.2"),
    ]


def test_reorder_verifies_by_reading_the_map_back() -> None:
    reader = _Reader(_actions_map())

    result = edit_live_node(
        profile="mcp-test",
        pointer=["api", "wf-1", "actions"],
        op="reorder",
        order=["2", "0", "1"],
        execute=True,
        reader=reader,
        writer=_map_writer(reader),
    )

    assert result["verified"] is True
    assert result["divergence"] is None


def test_reorder_refuses_an_order_that_would_drop_a_step() -> None:
    reader = _Reader(_actions_map())

    result = edit_live_node(
        profile="mcp-test",
        pointer=["api", "wf-1", "actions"],
        op="reorder",
        order=["2", "0"],
        execute=False,
        reader=reader,
        writer=_map_writer(reader),
    )

    assert result["ok"] is False
    assert result["error"] == "invalid_edit"
    assert "1" in result["message"]


def test_patch_change_carries_a_session_id_and_a_setdata_intent() -> None:
    reader = _Reader(_action())

    result = edit_live_node(
        profile="mcp-test",
        pointer=["api", "wf-1", "actions", "0"],
        op="patch",
        leaf_pointer=["properties", "to_change", "properties"],
        patch={"param_id": "Order", "param_name": "Order"},
        execute=False,
        reader=reader,
        writer=_writer_that_applies(reader),
    )

    change = result["write"]["request"]["payload"]["changes"][0]
    assert change["session_id"]
    assert change["intent"]["name"] == "SetData"
    assert change["intent"]["source_appname"] == ""
    assert isinstance(change["intent"]["id"], int)


def test_reorder_changes_all_share_one_session_id() -> None:
    reader = _Reader(_actions_map())

    result = edit_live_node(
        profile="mcp-test",
        pointer=["api", "wf-1", "actions"],
        op="reorder",
        order=["2", "0", "1"],
        execute=False,
        reader=reader,
        writer=_map_writer(reader),
    )

    changes = result["write"]["request"]["payload"]["changes"]
    session_ids = {change["session_id"] for change in changes}
    assert len(session_ids) == 1
    assert all(session_id for session_id in session_ids)


def test_reorder_update_index_intent_carries_only_a_name() -> None:
    reader = _Reader(_actions_map())

    result = edit_live_node(
        profile="mcp-test",
        pointer=["api", "wf-1", "actions"],
        op="reorder",
        order=["2", "0", "1"],
        execute=False,
        reader=reader,
        writer=_map_writer(reader),
    )

    changes = result["write"]["request"]["payload"]["changes"]
    index_changes = [change for change in changes if change["intent"]["name"] == "Update index"]
    assert index_changes
    for change in index_changes:
        assert change["session_id"]
        assert change["intent"] == {"name": "Update index"}
