"""Structural questions answered from the decoded .bubble export, without a browser.

The export below mirrors the shapes of a real one (kaimia-app, 2026-09): slot-keyed maps with a
``length`` entry mixed in, workflows under pages and reusables, backend workflows and database
triggers under ``api``, and field writes as ``changes`` / ``initial_values`` entries keyed by
the field's key.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bubble_mcp.context.export_queries import (
    container_workflows,
    element_subtree,
    field_writers,
    run_context_query,
)


def _status_change(action_id: str, to_change: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": action_id,
        "type": "ChangeThing",
        "properties": {
            "changes": {"0": {"key": "status_option_status", "value": {"type": "OneOptionValue"}}},
            "to_change": to_change,
        },
    }


def _export() -> dict[str, Any]:
    return {
        "user_types": {
            "client": {
                "display": "Client",
                "fields": {
                    "status_option_status": {"display": "Client Status", "value": "option.status"},
                    "main_booking_custom_booking": {"display": "Main booking", "value": "custom.booking"},
                },
            },
            "booking": {
                "display": "Booking",
                "fields": {
                    "status_option_status": {"display": "Status", "value": "option.status"},
                    "client_custom_client": {"display": "Client", "value": "custom.client"},
                },
            },
        },
        "pages": {
            "length": 1,
            "pg1": {
                "id": "bPage",
                "name": "clients",
                "type": "Page",
                "elements": {
                    "s1": {
                        "id": "bGrp",
                        "name": "Client card",
                        "type": "Group",
                        "properties": {"group_type": "custom.client"},
                        "elements": {
                            "s2": {"id": "bBtn", "name": "Mark inactive", "type": "Button"},
                            "s3": {"id": "bTxt", "name": "Status text", "type": "Text"},
                        },
                    },
                    "s4": {"id": "bOther", "name": "Other", "type": "Button"},
                },
                "workflows": {
                    "length": 2,
                    "w1": {
                        "id": "bE1",
                        "type": "ButtonClicked",
                        "properties": {"element_id": "bBtn"},
                        "actions": {
                            "0": _status_change(
                                "bA1",
                                {"type": "GetElement", "properties": {"element_id": "bGrp"}},
                            ),
                            "1": {"id": "bA2", "type": "HideElement", "properties": {"element_id": "bTxt"}},
                        },
                    },
                    "w2": {
                        "id": "bE2",
                        "type": "ButtonClicked",
                        "properties": {"element_id": "bOther"},
                        "actions": {"0": {"id": "bA3", "type": "ShowElement", "properties": {"element_id": "bTxt"}}},
                    },
                },
            },
        },
        "element_definitions": {
            "bRpSlot": {
                "id": "bRp",
                "name": "R Client Overview",
                "type": "CustomDefinition",
                "elements": {"s9": {"id": "bRpBtn", "name": "Deceased", "type": "Button"}},
                "workflows": {
                    "w9": {
                        "id": "bE9",
                        "type": "ButtonClicked",
                        "properties": {"element_id": "bRpBtn"},
                        # The type of the reusable's thing is not derivable: unknown, not wrong.
                        "actions": {"0": _status_change("bA9", {"type": "GetElement", "properties": {"element_id": "bRp"}})},
                    }
                },
            }
        },
        "api": {
            "b1": {
                "id": "bApi",
                "type": "APIEvent",
                "properties": {
                    "wf_name": "close_client",
                    "parameters": {"0": {"key": "client", "value": "custom.client"}},
                },
                "actions": {
                    "0": _status_change(
                        "bA10",
                        {"type": "APIEventParameter", "properties": {"btype_id": "custom.client", "event_id": "bApi"}},
                    ),
                    # Same field key on a booking reached through the client: not a client write.
                    "1": _status_change(
                        "bA11",
                        {
                            "type": "APIEventParameter",
                            "properties": {"btype_id": "custom.client"},
                            "next": {"type": "Message", "name": "main_booking_custom_booking"},
                        },
                    ),
                },
            },
            "b2": {
                "id": "bTrig",
                "type": "DatabaseTriggerEvent",
                "properties": {
                    "event_name": "Client deceased",
                    "data_trigger_type": "custom.client",
                    "condition": {
                        "type": "CurrentDataItem",
                        "next": {"type": "Message", "name": "status_option_status"},
                    },
                },
                "actions": {
                    "0": {
                        "id": "bA12",
                        "type": "ChangeListOfThings",
                        "properties": {
                            "type_to_change": "custom.booking",
                            "changes": {"0": {"key": "status_option_status"}},
                        },
                    }
                },
            },
            "b3": {
                "id": "bNew",
                "type": "APIEvent",
                "properties": {"wf_name": "create_client"},
                "actions": {
                    "0": {
                        "id": "bA13",
                        "type": "NewThing",
                        "properties": {
                            "thing_type": "custom.client",
                            "initial_values": {"0": {"key": "status_option_status"}},
                        },
                    }
                },
            },
        },
    }


def test_element_subtree_returns_children_and_the_workflows_tied_to_them() -> None:
    result = element_subtree(_export(), "Client card")

    assert result["container"] == {"kind": "page", "id": "bPage", "name": "clients"}
    assert result["pointer"] == ["pages", "pg1", "elements", "s1"]
    tree = result["element"]
    assert tree["content_type"] == "custom.client"
    assert [child["id"] for child in tree["children"]] == ["bBtn", "bTxt"]
    assert [wf["id"] for wf in result["workflows_triggered"]] == ["bE1"]
    triggered = result["workflows_triggered"][0]
    assert triggered["trigger_element"] == {"id": "bBtn", "name": "Mark inactive"}
    assert triggered["actions"][0]["fields_set"] == ["status_option_status"]
    assert [wf["id"] for wf in result["workflows_referencing"]] == ["bE2"]
    assert result["workflows_referencing"][0]["references"] == ["bTxt"]


def test_element_subtree_depth_limits_the_tree_but_not_the_workflows() -> None:
    result = element_subtree(_export(), "bGrp", depth=0)

    assert result["element"]["children_omitted"] == 2
    assert [wf["id"] for wf in result["workflows_triggered"]] == ["bE1"]


def test_an_ambiguous_or_unknown_element_is_an_error_with_candidates() -> None:
    export = _export()
    export["element_definitions"]["bRpSlot"]["elements"]["s8"] = {"id": "bDup", "name": "Other", "type": "Button"}

    for query, error in (("Other", "ambiguous_element"), ("nothing", "element_not_found")):
        try:
            element_subtree(export, query)
        except ValueError as raised:
            assert raised.payload["error"] == error  # type: ignore[attr-defined]
        else:
            raise AssertionError(query)


def test_reusable_workflows_are_listed() -> None:
    result = container_workflows(_export(), "R Client Overview")

    assert result["container"]["kind"] == "reusable"
    assert [wf["id"] for wf in result["workflows"]] == ["bE9"]
    assert result["workflows"][0]["pointer"] == ["element_definitions", "bRpSlot", "workflows", "w9"]
    assert result["workflows"][0]["actions"][0]["elements"] == [{"id": "bRp", "name": "R Client Overview"}]


def test_backend_workflows_are_listed_with_their_names_and_trigger_types() -> None:
    result = container_workflows(_export(), "backend")

    by_id = {wf["id"]: wf for wf in result["workflows"]}
    assert by_id["bApi"]["name"] == "close_client"
    assert by_id["bApi"]["pointer"] == ["api", "b1"]
    assert by_id["bTrig"]["data_trigger_type"] == "custom.client"
    assert by_id["bTrig"]["actions"][0]["thing_type"] == "custom.booking"


def test_field_writers_finds_every_write_of_the_field_on_the_type() -> None:
    result = field_writers(_export(), "Client", "Client Status")

    assert result["field"]["key"] == "status_option_status"
    found = {(writer["action"]["id"], writer["type_confirmed"]) for writer in result["writers"]}
    assert found == {("bA1", True), ("bA9", False), ("bA10", True), ("bA13", True)}


def test_field_writers_excludes_a_write_to_the_same_key_on_another_type() -> None:
    result = field_writers(_export(), "client", "status_option_status")

    ids = {writer["action"]["id"] for writer in result["writers"]}
    assert "bA11" not in ids  # client -> main booking -> status: a booking's status
    assert "bA12" not in ids  # ChangeListOfThings on bookings

    bookings = field_writers(_export(), "booking", "status_option_status")
    assert {writer["action"]["id"] for writer in bookings["writers"]} >= {"bA11", "bA12"}


def test_field_writers_lists_the_database_triggers_on_the_type() -> None:
    result = field_writers(_export(), "client", "Client Status")

    triggers = result["triggers_on_type"]
    assert [trigger["id"] for trigger in triggers] == ["bTrig"]
    assert triggers[0]["condition_reads_field"] is True
    assert triggers[0]["actions"][0]["fields_set"] == ["status_option_status"]


def test_an_unknown_field_lists_the_fields_the_type_has() -> None:
    try:
        field_writers(_export(), "client", "deceased")
    except ValueError as raised:
        payload = raised.payload  # type: ignore[attr-defined]
    else:
        raise AssertionError("an unknown field must not answer with an empty list")

    assert payload["error"] == "field_not_found"
    assert "status_option_status (Client Status)" in payload["candidates"]


def _write_export(tmp_path: Path, version: str | None) -> Path:
    path = tmp_path / "app.bubble"
    path.write_text(json.dumps(_export()), encoding="utf-8")
    if version is not None:
        (tmp_path / "app.bubble.meta.json").write_text(
            json.dumps({"app_version": version, "fetched_at": "2026-09-27T10:00:00Z"}), encoding="utf-8"
        )
    return path


def test_run_reads_an_explicit_file_and_reports_its_version(tmp_path: Path) -> None:
    path = _write_export(tmp_path, "93k8b")

    result = run_context_query(kind="workflows", file=str(path), container="clients")

    assert result["ok"] is True
    assert result["export"]["app_version"] == "93k8b"
    assert result["export"]["fetched_at"] == "2026-09-27T10:00:00Z"
    assert [wf["id"] for wf in result["workflows"]] == ["bE1", "bE2"]


def test_run_downloads_the_target_version_when_the_cache_is_another(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    cached = _write_export(tmp_path, "test")
    monkeypatch.setattr("bubble_mcp.context.detector.default_bubble_export_path", lambda profile, app_id: cached)
    branch_dir = tmp_path / "branch"
    branch_dir.mkdir()
    branch_export = _write_export(branch_dir, "93k8b")
    calls: list[dict[str, Any]] = []

    def refresh(**kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        return branch_export

    result = run_context_query(
        kind="workflows", profile="kaimia", app_id="kaimia-app", app_version="93k8b", container="backend", refresh=refresh
    )

    assert calls == [{"profile": "kaimia", "app_id": "kaimia-app", "app_version": "93k8b"}]
    assert result["export"]["app_version"] == "93k8b"
    assert result["export"]["downloaded_now"] is True


def test_run_refuses_when_the_target_version_cannot_be_downloaded(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    cached = _write_export(tmp_path, "test")
    monkeypatch.setattr("bubble_mcp.context.detector.default_bubble_export_path", lambda profile, app_id: cached)

    def refresh(**kwargs):  # type: ignore[no-untyped-def]
        raise ValueError("not logged in")

    result = run_context_query(
        kind="workflows", profile="kaimia", app_id="kaimia-app", app_version="93k8b", container="backend", refresh=refresh
    )

    assert result["ok"] is False
    assert result["error"] == "export_version_mismatch"
    assert result["cached_version"] == "test"


def test_run_uses_the_cache_when_it_is_the_target_version(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    cached = _write_export(tmp_path, "93k8b")
    monkeypatch.setattr("bubble_mcp.context.detector.default_bubble_export_path", lambda profile, app_id: cached)

    def refresh(**kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("a cache of the target version must not be downloaded again")

    result = run_context_query(
        kind="field_writers",
        profile="kaimia",
        app_id="kaimia-app",
        app_version="93k8b",
        data_type="client",
        field="Client Status",
        refresh=refresh,
    )

    assert result["ok"] is True
    assert result["export"]["downloaded_now"] is False


def test_run_names_the_missing_argument_for_the_kind(tmp_path: Path) -> None:
    path = _write_export(tmp_path, None)

    result = run_context_query(kind="field_writers", file=str(path), data_type="client")

    assert result["ok"] is False
    assert result["error"] == "missing_argument"
    assert "field" in result["message"]


# --- the MCP tool -------------------------------------------------------------------------


def test_the_tool_is_exposed_and_read_only() -> None:
    from bubble_mcp.server.agent_catalog import tool_annotations
    from bubble_mcp.server.schemas import list_tool_schemas

    schema = {tool["name"]: tool for tool in list_tool_schemas()}["bubble_context_query"]

    assert schema["inputSchema"]["required"] == ["kind"]
    assert schema["inputSchema"]["properties"]["kind"]["enum"] == ["element_subtree", "workflows", "field_writers"]
    assert tool_annotations("bubble_context_query")["readOnlyHint"] is True


def test_the_tool_answers_through_call_tool(tmp_path: Path) -> None:
    from bubble_mcp.server.tools import call_tool

    path = _write_export(tmp_path, "93k8b")

    result = call_tool(
        "bubble_context_query",
        {"kind": "field_writers", "file": str(path), "data_type": "Client", "field": "Client Status"},
    )

    assert result["ok"] is True
    assert {writer["action"]["id"] for writer in result["writers"]} == {"bA1", "bA9", "bA10", "bA13"}


def test_tool_search_finds_it_for_the_questions_agents_asked() -> None:
    from bubble_mcp.server.agent_guide import search_tool_catalog

    for query in (
        "which workflows change field status of type client",
        "list a reusable's workflows",
        "element subtree with children and workflows",
    ):
        names = [match["name"] for match in search_tool_catalog(query, limit=5)["matches"]]
        assert "bubble_context_query" in names, (query, names)


def test_a_data_type_key_matches_whatever_its_case() -> None:
    export = _export()
    export["user_types"]["client"]["display"] = "System - Client"

    result = field_writers(export, "Client", "Client Status")

    assert result["data_type"]["type"] == "custom.client"


def test_an_unknown_data_type_suggests_the_types_that_contain_the_word() -> None:
    try:
        field_writers(_export(), "clien", "status")
    except ValueError as raised:
        payload = raised.payload  # type: ignore[attr-defined]
    else:
        raise AssertionError("an unknown type must not answer")

    assert payload["candidates"] == ["client (Client)"]
