import copy
import json

import pytest

from bubble_mcp.aria_runtime.bubble_cli import BubbleCLI


def make_cli(
    tmp_path, monkeypatch, context_type, body, suffix=(), intent=None, wire_context_id="ctx1"
):
    monkeypatch.setenv("BUBBLE_MCP_CONFIG_DIR", str(tmp_path / "config"))
    bucket, prefix = ("pages", "%p3") if context_type == "page" else ("element_definitions", "%ed")
    old = {
        "id": "wf1",
        "%x": "ButtonClicked",
        "%p": {"%ei": "btn1"},
        "actions": {"0": {"id": "action1", "%x": "OldAction"}},
    }
    app = {
        bucket: {"ctx1": {"id": "ctx1", "name": "Context", "workflows": {"wf1": old}}},
        "_index": {"id_to_path": {"ctx1": f"{prefix}.{wire_context_id}"}},
    }
    app_path = tmp_path / "app.json"
    app_path.write_text(json.dumps(app))
    change = {"path_array": [prefix, wire_context_id, "%wf", "wf1", *suffix], "body": body}
    if intent:
        change["intent"] = {"name": intent}
    overlay_path = tmp_path / "overlay.json"
    overlay_path.write_text(json.dumps({"entries": [{"changes": [change]}]}))
    return BubbleCLI(
        app_json_path=str(app_path), mutation_overlay_path=str(overlay_path), appname="review"
    )


@pytest.mark.parametrize("context_type", ["page", "reusable"])
@pytest.mark.parametrize("wire_context_id", ["ctx1", "wireCtx"])
def test_overlay_actions_remain_authoritative(tmp_path, monkeypatch, context_type, wire_context_id):
    new = {"0": {"id": "action1", "%x": "NewAction"}}
    cli = make_cli(
        tmp_path,
        monkeypatch,
        context_type,
        new,
        suffix=("actions",),
        wire_context_id=wire_context_id,
    )
    rows = cli._list_context_workflows("ctx1", context_type)
    assert rows[0]["workflow"]["actions"] == new


@pytest.mark.parametrize("context_type", ["page", "reusable"])
@pytest.mark.parametrize("wire_context_id", ["ctx1", "wireCtx"])
def test_listing_does_not_mutate_overlay(tmp_path, monkeypatch, context_type, wire_context_id):
    new = {"0": {"id": "action1", "%x": "NewAction"}}
    cli = make_cli(
        tmp_path,
        monkeypatch,
        context_type,
        new,
        suffix=("actions",),
        wire_context_id=wire_context_id,
    )
    before = copy.deepcopy(cli.discovery.data)
    cli._list_context_workflows("ctx1", context_type)
    assert cli.discovery.data == before


@pytest.mark.parametrize("context_type", ["page", "reusable"])
@pytest.mark.parametrize("wire_context_id", ["ctx1", "wireCtx"])
def test_removed_event_stays_removed_after_overlay_reload(
    tmp_path, monkeypatch, context_type, wire_context_id
):
    cli = make_cli(
        tmp_path,
        monkeypatch,
        context_type,
        None,
        intent="RemoveEvent",
        wire_context_id=wire_context_id,
    )
    assert cli._list_context_workflows("ctx1", context_type) == []


@pytest.mark.parametrize("context_type", ["page", "reusable"])
@pytest.mark.parametrize("wire_context_id", ["ctx1", "wireCtx"])
def test_removed_actions_stay_removed(tmp_path, monkeypatch, context_type, wire_context_id):
    cli = make_cli(
        tmp_path,
        monkeypatch,
        context_type,
        {},
        suffix=("actions",),
        wire_context_id=wire_context_id,
    )
    rows = cli._list_context_workflows("ctx1", context_type)
    assert rows[0]["workflow"]["actions"] == {}


@pytest.mark.parametrize("context_type", ["page", "reusable"])
def test_cached_event_cannot_resurrect_overlay_tombstone(tmp_path, monkeypatch, context_type):
    cli = make_cli(tmp_path, monkeypatch, context_type, None, intent="RemoveEvent")
    cli._cache_workflow_event(
        "ctx1",
        context_type,
        "wf1",
        {"id": "wf1", "%x": "ButtonClicked", "actions": {}, "%p": {"%ei": "btn1"}},
    )
    assert cli._list_context_workflows("ctx1", context_type) == []


@pytest.mark.parametrize("context_type", ["page", "reusable"])
def test_cached_actions_cannot_restore_replaced_action_map(tmp_path, monkeypatch, context_type):
    cli = make_cli(tmp_path, monkeypatch, context_type, {}, suffix=("actions",))
    cli._cache_workflow_event(
        "ctx1",
        context_type,
        "wf1",
        {
            "id": "wf1",
            "%x": "ButtonClicked",
            "actions": {"0": {"id": "action1", "%x": "OldAction"}},
            "%p": {"%ei": "btn1"},
        },
    )
    before = copy.deepcopy(cli.discovery.data)
    rows = cli._list_context_workflows("ctx1", context_type)
    assert rows[0]["workflow"]["actions"] == {}
    assert cli.discovery.data == before


@pytest.mark.parametrize("context_type", ["page", "reusable"])
@pytest.mark.parametrize("map_key", ["parameters", "return_types"])
def test_cached_structural_maps_cannot_restore_removed_entries(
    tmp_path, monkeypatch, context_type, map_key
):
    cli = make_cli(tmp_path, monkeypatch, context_type, {}, suffix=("%p", map_key))
    cli._cache_workflow_event(
        "ctx1",
        context_type,
        "wf1",
        {"id": "wf1", "%x": "ButtonClicked", "%p": {map_key: {"removed": {"name": "old"}}}},
    )
    rows = cli._list_context_workflows("ctx1", context_type)
    assert rows[0]["workflow"]["%p"][map_key] == {}


@pytest.mark.parametrize("context_type", ["page", "reusable"])
def test_explicitly_cleared_binding_is_not_filled_from_older_root(
    tmp_path, monkeypatch, context_type
):
    cli = make_cli(
        tmp_path, monkeypatch, context_type, None, suffix=("%p", "%ei"), wire_context_id="wireCtx"
    )
    rows = cli._list_context_workflows("ctx1", context_type)
    assert rows[0]["workflow"]["%p"]["%ei"] is None


@pytest.mark.parametrize("context_type", ["page", "reusable"])
def test_newer_workflow_wins_over_older_tombstone(tmp_path, monkeypatch, context_type):
    new = {"id": "wf1", "%x": "PageLoaded", "actions": {}}
    cli = make_cli(tmp_path, monkeypatch, context_type, new, wire_context_id="wireCtx")
    bucket = "pages" if context_type == "page" else "element_definitions"
    cli.discovery.data[bucket]["ctx1"]["%wf"]["wf1"] = None
    rows = cli._list_context_workflows("ctx1", context_type)
    assert len(rows) == 1
    assert rows[0]["type"] == "PageLoaded"


@pytest.mark.parametrize("context_type", ["page", "reusable"])
def test_listing_result_is_detached_from_sources_and_cache(tmp_path, monkeypatch, context_type):
    cli = make_cli(tmp_path, monkeypatch, context_type, {}, suffix=("actions",))
    cli._cache_workflow_event(
        "ctx1", context_type, "wf1", {"id": "wf1", "%p": {"%en": "cached name"}}
    )
    before_data = copy.deepcopy(cli.discovery.data)
    before_cache = copy.deepcopy(cli._schema_events_cache())
    row = cli._list_context_workflows("ctx1", context_type)[0]
    row["workflow"]["%p"]["%en"] = "caller edit"
    row["workflow"]["actions"]["0"] = {"%x": "CallerAction"}
    assert cli.discovery.data == before_data
    assert cli._schema_events_cache() == before_cache
