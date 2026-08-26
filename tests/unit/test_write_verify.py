"""verify_changes: generic post-write verification, with a fake reader and no browser."""

from __future__ import annotations

from typing import Any

from bubble_mcp.execution.write_verify import verify_changes


def _set_data(path_array: list[str], body: Any) -> dict[str, Any]:
    return {
        "intent": {"name": "SetData", "id": 7, "source_appname": ""},
        "path_array": path_array,
        "body": body,
        "version_control_api_version": 4,
        "changelog_data": [],
        "session_id": "sess-1",
    }


def _create_action(path_array: list[str], body: dict[str, Any]) -> dict[str, Any]:
    return {
        "intent": {"name": "CreateAction"},
        "path_array": path_array,
        "body": body,
        "version_control_api_version": 4,
        "changelog_data": [],
        "session_id": "sess-1",
    }


def _delete(path_array: list[str]) -> dict[str, Any]:
    return {
        "intent": {"name": "Delete"},
        "path_array": path_array,
        "body": None,
        "version_control_api_version": 4,
        "changelog_data": [],
        "session_id": "sess-1",
    }


def _update_index(path_array: list[str], body: Any) -> dict[str, Any]:
    return {
        "intent": {"name": "Update index"},
        "path_array": path_array,
        "body": body,
        "version_control_api_version": 4,
        "changelog_data": [],
        "session_id": "sess-1",
    }


class FakeReader:
    """A reader with the read_live_nodes signature, backed by a fixed pointer -> result map.

    Records every call so tests can assert pointers are read exactly once each.
    """

    def __init__(self, table: dict[tuple[str, ...], dict[str, Any]]) -> None:
        self.table = table
        self.calls: list[list[tuple[str, ...]]] = []

    def __call__(
        self,
        profile: str,
        pointers: list[tuple[str, ...]],
        *,
        app_id: str | None = None,
        app_version: str = "test",
    ) -> dict[tuple[str, ...], dict[str, Any]]:
        self.calls.append(list(pointers))
        return {pointer: self.table[pointer] for pointer in pointers}


def _ok(node: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "pointer": [], "node": node, "app_id": "mcp-test-app"}


def _not_found(pointer: tuple[str, ...]) -> dict[str, Any]:
    return {
        "ok": False,
        "error": "pointer_not_found",
        "pointer": list(pointer),
        "message": "gone",
    }


def _read_error(pointer: tuple[str, ...], error: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": error, "pointer": list(pointer), "message": message}


def test_matching_node_change_verifies() -> None:
    pointer = ("api", "wf-1", "actions", "0")
    node = {"id": "act-1", "%x": "ChangeThing", "%p": {"a": "1"}}
    reader = FakeReader({pointer: _ok(node)})

    changes = [_create_action(list(pointer), node)]
    result = verify_changes("mcp-test", changes, app_id="mcp-test-app", reader=reader)

    assert result["ok"] is True
    assert result["checked"] == 1
    assert result["verified"] is True
    assert result["divergences"] == []
    assert result["unverified"] == []
    assert result["render_unverified"] is True
    assert "verified_meaning" in result


def test_diverging_change_reports_the_exact_dotted_divergence_path() -> None:
    pointer = ("api", "wf-1", "actions", "0")
    written = {"id": "act-1", "%x": "ChangeThing", "%p": {"%ei": "bS35G"}}
    read_back = {"id": "act-1", "%x": "ChangeThing", "%p": {"%ei": "in_email"}}
    reader = FakeReader({pointer: _ok(read_back)})

    changes = [_create_action(list(pointer), written)]
    result = verify_changes("mcp-test", changes, app_id="mcp-test-app", reader=reader)

    assert result["ok"] is True
    assert result["verified"] is False
    assert len(result["divergences"]) == 1
    divergence = result["divergences"][0]
    assert divergence["path"] == "api.wf-1.actions.0"
    assert divergence["divergence"] == "%p.%ei"
    assert divergence["expected"] == written
    assert divergence["actual"] == read_back


def test_index_changes_are_skipped_and_not_counted() -> None:
    leaf_path = ["api", "wf-1", "actions", "0", "%p", "%ei"]
    parent_pointer = ("api", "wf-1", "actions", "0", "%p")
    reader = FakeReader({parent_pointer: _ok({"%ei": "bS35G"})})

    changes = [
        _set_data(leaf_path, "bS35G"),
        _update_index(["_index", "id_to_path", "act-1"], "api.wf-1.actions.0"),
        _update_index(["_index", "issues_list", "wf-1"], {"anything": True}),
    ]
    result = verify_changes("mcp-test", changes, app_id="mcp-test-app", reader=reader)

    assert result["checked"] == 1  # only the SetData counted; both _index changes skipped
    assert result["verified"] is True


def test_null_body_change_is_skipped_unless_it_is_a_delete() -> None:
    reader = FakeReader({})

    changes = [_set_data(["api", "wf-1", "actions", "0", "%p", "%ei"], None)]
    result = verify_changes("mcp-test", changes, app_id="mcp-test-app", reader=reader)

    assert result["checked"] == 0
    assert reader.calls == []


def test_delete_whose_path_still_reads_back_is_a_divergence() -> None:
    pointer = ("api", "wf-1", "actions", "0")
    reader = FakeReader({pointer: _ok({"id": "act-1", "%x": "ChangeThing"})})

    changes = [_delete(list(pointer))]
    result = verify_changes("mcp-test", changes, app_id="mcp-test-app", reader=reader)

    assert result["checked"] == 1
    assert result["verified"] is False
    assert len(result["divergences"]) == 1
    assert result["divergences"][0]["path"] == "api.wf-1.actions.0"
    assert result["divergences"][0]["expected"] == "absent"
    assert result["unverified"] == []


def test_delete_whose_path_reads_pointer_not_found_passes() -> None:
    pointer = ("api", "wf-1", "actions", "0")
    reader = FakeReader({pointer: _not_found(pointer)})

    changes = [_delete(list(pointer))]
    result = verify_changes("mcp-test", changes, app_id="mcp-test-app", reader=reader)

    assert result["checked"] == 1
    assert result["verified"] is True
    assert result["divergences"] == []
    assert result["unverified"] == []


def test_a_read_error_lands_in_unverified_not_divergences_and_ok_stays_true() -> None:
    pointer = ("api", "wf-1", "actions", "0")
    reader = FakeReader({pointer: _read_error(pointer, "editor_unstable", "kept reinitializing")})

    changes = [_create_action(list(pointer), {"id": "act-1", "%x": "ChangeThing"})]
    result = verify_changes("mcp-test", changes, app_id="mcp-test-app", reader=reader)

    assert result["ok"] is True
    assert result["divergences"] == []
    assert len(result["unverified"]) == 1
    assert result["unverified"][0]["path"] == "api.wf-1.actions.0"
    assert result["unverified"][0]["error"] == "editor_unstable"
    assert result["unverified"][0]["message"] == "kept reinitializing"
    assert result["verified"] is False  # we don't know, so it cannot be claimed verified


def test_a_delete_whose_read_fails_for_an_unrelated_reason_lands_in_unverified() -> None:
    pointer = ("api", "wf-1", "actions", "0")
    reader = FakeReader({pointer: _read_error(pointer, "not_logged_in", "no session")})

    changes = [_delete(list(pointer))]
    result = verify_changes("mcp-test", changes, app_id="mcp-test-app", reader=reader)

    assert result["divergences"] == []
    assert len(result["unverified"]) == 1
    assert result["unverified"][0]["error"] == "not_logged_in"


def test_duplicate_pointers_are_read_once() -> None:
    pointer = ("api", "wf-1", "actions", "0", "%p")
    node = {"%ei": "bS35G", "other": "x"}
    reader = FakeReader({pointer: _ok(node)})

    changes = [
        _set_data([*pointer, "%ei"], "bS35G"),
        _set_data([*pointer, "other"], "x"),
    ]
    result = verify_changes("mcp-test", changes, app_id="mcp-test-app", reader=reader)

    assert result["checked"] == 2
    assert result["verified"] is True
    assert reader.calls == [[pointer]]  # one read call, one distinct pointer


def test_leaf_setdata_compares_against_the_parent_nodes_value_at_the_final_key() -> None:
    """A SetData body is a bare scalar addressing a leaf under a parent node. The parent - not

    the leaf - is what gets read (see write_verify's docstring on why), so the comparison must
    reach through to the parent's value at the final path segment.
    """

    parent_pointer = ("api", "wf-1", "actions", "0", "%p")
    node = {"%ei": "bS35G"}
    reader = FakeReader({parent_pointer: _ok(node)})

    changes = [_set_data([*parent_pointer, "%ei"], "bS35G")]
    result = verify_changes("mcp-test", changes, app_id="mcp-test-app", reader=reader)

    assert result["verified"] is True
    assert reader.calls == [[parent_pointer]]


def test_leaf_setdata_diverging_from_the_parents_value_is_reported() -> None:
    parent_pointer = ("api", "wf-1", "actions", "0", "%p")
    node = {"%ei": "in_email"}
    reader = FakeReader({parent_pointer: _ok(node)})

    changes = [_set_data([*parent_pointer, "%ei"], "bS35G")]
    result = verify_changes("mcp-test", changes, app_id="mcp-test-app", reader=reader)

    assert result["verified"] is False
    assert len(result["divergences"]) == 1
    divergence = result["divergences"][0]
    assert divergence["expected"] == "bS35G"
    assert divergence["actual"] == "in_email"


def test_render_unverified_is_always_present() -> None:
    reader = FakeReader({})
    result = verify_changes("mcp-test", [], app_id="mcp-test-app", reader=reader)

    assert result["render_unverified"] is True
    assert result["checked"] == 0
    assert result["verified"] is True  # nothing to check, vacuously true
