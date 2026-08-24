"""Bug 7 (Orana report): bubble_editor_write accepted decoded keys in node bodies.

Bubble's internal app tree uses encoded keys (%x=type, %p=properties, %nm=name,
%dn=default_name). The .bubble export is the DECODED form. Bodies written with
decoded keys at node positions (%el/%wf/actions) get HTTP 200 and even survive
re-export, but the editor renders them as "[missing: null]".
"""

from __future__ import annotations

from bubble_mcp.execution.write_lint import lint_editor_write_changes


def _change(path, body):
    return {"path_array": path, "body": body}


def test_decoded_action_body_is_flagged() -> None:
    issues = lint_editor_write_changes(
        [
            _change(
                ["%p3", "bTVso", "%wf", "bTZRe", "actions", "7"],
                {"id": "b5VD1", "type": "SetCustomState", "properties": {"custom_state": "x"}},
            )
        ]
    )
    assert len(issues) == 1
    assert "type" in issues[0] and "%x" in issues[0]


def test_decoded_element_body_is_flagged() -> None:
    issues = lint_editor_write_changes(
        [
            _change(
                ["%p3", "pg1", "%el", "el1"],
                {"id": "el1", "type": "CustomElement", "default_name": "A", "properties": {"order": 1}},
            )
        ]
    )
    assert len(issues) == 1


def test_encoded_bodies_pass() -> None:
    issues = lint_editor_write_changes(
        [
            _change(
                ["%p3", "bTVso", "%wf", "bTZRe", "actions", "7"],
                {"id": "b5VD1", "%x": "SetCustomState", "%p": {"custom_state": "x"}},
            ),
            _change(["%p3", "pg1", "%el", "el1", "%nm"], "My Name"),
        ]
    )
    assert issues == []


def test_non_node_paths_with_friendly_keys_pass() -> None:
    issues = lint_editor_write_changes(
        [
            _change(
                ["settings", "client_safe", "apiconnector2", "col1", "calls", "call1"],
                {"%nm": "Get Products", "method": "get", "publish_as": "data"},
            ),
            _change(["_index", "id_to_path", "abc"], "%p3.pg1.%el.el1"),
        ]
    )
    assert issues == []


def test_editor_write_tool_rejects_decoded_bodies(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BUBBLE_MCP_CONFIG_DIR", str(tmp_path))
    from bubble_mcp.server import tools as tools_module
    from bubble_mcp.sessions.store import BubbleSessionData

    monkeypatch.setattr(
        tools_module,
        "load_session",
        lambda profile: BubbleSessionData(app_id="app-x", url="https://bubble.io/page?id=app-x", method="POST", headers={}, cookies="k=v", app_version="test", captured_at="2026-08-24T00:00:00Z", source="test"),
    )

    result = tools_module.call_tool(
        "bubble_editor_write",
        {
            "profile": "p1",
            "execute": False,
            "payload": {
                "appname": "app-x",
                "changes": [
                    {
                        "path_array": ["%p3", "bTVso", "%wf", "bTZRe", "actions", "7"],
                        "body": {"id": "b5VD1", "type": "SetCustomState", "properties": {}},
                        "intent": {"name": "SetData"},
                    }
                ],
            },
        },
    )
    assert result["ok"] is False
    assert result["error"] == "decoded_keys_in_node_body"
    assert result["issues"]
    assert "allow_decoded_keys" in result["message"]
