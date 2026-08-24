import json
import re
from pathlib import Path

import pytest

from bubble_mcp.extensions.validator import validate_extension_pack
from bubble_mcp.tool_authoring.sessions import (
    active_authoring_session_id,
    append_capture_to_authoring_session,
    create_authoring_session,
    describe_authoring_session,
    finalize_authoring_session,
    generate_authoring_extension_pack,
    set_active_authoring_session,
)


def test_authoring_session_groups_captured_write(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BUBBLE_MCP_CONFIG_DIR", str(tmp_path))

    session = create_authoring_session(
        intent="Create an API Connector call",
        target="api_connector",
        profile="client",
    )
    result = append_capture_to_authoring_session(
        session.id,
        Path("tests/fixtures/tool-authoring/api-connector-write-capture.json"),
    )
    described = describe_authoring_session(session.id)

    assert result["ok"] is True
    assert described["session"]["intent"] == "Create an API Connector call"
    assert described["active"] is True
    assert active_authoring_session_id() == session.id
    assert described["classification"]["change_count"] >= 1
    assert described["next_mcp_calls"] == [
        {"tool": "bubble_tool_wizard_generate", "arguments": {"session_id": session.id}}
    ]


def test_authoring_session_finalize_returns_learned_patterns(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BUBBLE_MCP_CONFIG_DIR", str(tmp_path))

    session = create_authoring_session(
        intent="Create an API Connector call",
        target="api_connector",
        profile="client",
    )
    append_capture_to_authoring_session(
        session.id,
        Path("tests/fixtures/tool-authoring/api-connector-write-capture.json"),
    )

    result = finalize_authoring_session(session.id)

    assert result["ok"] is True
    assert result["status"] == "ready_for_review"
    assert result["active"] is True
    assert result["capture_summary"]["capture_count"] == 1
    assert result["capture_summary"]["intents"] == ["CreateApiConnectorCall"]
    assert result["capture_summary"]["api_connector_ids"]["collections"] == []
    assert result["capture_summary"]["api_connector_ids"]["calls"] == ["call_123"]
    assert any("CreateApiConnectorCall" in item for item in result["understanding"]["learned"])
    assert any("autenticacao" in question for question in result["questions"])
    assert any("execute=false" in step for step in result["testing_guidance"])
    assert result["next_mcp_calls"] == [
        {"tool": "bubble_tool_wizard_generate", "arguments": {"session_id": session.id}}
    ]


def test_authoring_session_finalize_without_captures_requests_capture(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BUBBLE_MCP_CONFIG_DIR", str(tmp_path))

    session = create_authoring_session(
        intent="Create an API Connector call",
        target="api_connector",
        profile="client",
    )

    result = finalize_authoring_session(session.id)

    assert result["ok"] is False
    assert result["status"] == "needs_captures"
    assert result["capture_summary"]["capture_count"] == 0
    assert result["understanding"]["learned"] == ["Nenhuma captura valida foi adicionada a sessao ainda."]
    assert result["next_mcp_calls"] == [
        {"tool": "bubble_tool_wizard_finalize", "arguments": {"session_id": session.id}}
    ]


def test_authoring_session_generate_creates_valid_extension_pack(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BUBBLE_MCP_CONFIG_DIR", str(tmp_path))

    session = create_authoring_session(
        intent="Create an API Connector call",
        target="api_connector",
        profile="client",
    )
    append_capture_to_authoring_session(
        session.id,
        Path("tests/fixtures/tool-authoring/api-connector-write-capture.json"),
    )

    result = generate_authoring_extension_pack(session.id)

    assert result["ok"] is True
    assert result["extension_id"].startswith("local.toolwiz.api_connector.")
    assert result["tool_name"] == "create_an_api_connector_call"
    assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", result["tool_name"])
    pack_path = Path(str(result["pack_path"]))
    assert (pack_path / "extension.json").exists()
    assert Path(str(result["tool_path"])).exists()
    assert Path(str(result["evidence_path"])).exists()
    tool_payload = json.loads(Path(str(result["tool_path"])).read_text(encoding="utf-8"))
    properties = tool_payload["inputSchema"]["properties"]
    for property_name in (
        "collection_id",
        "collection_name",
        "body",
        "body_params",
        "headers",
        "initialize",
        "initialization_values",
    ):
        assert property_name in properties
    assert tool_payload["inputSchema"]["required"] == ["profile", "name", "method", "url"]
    assert tool_payload["template"]["runner"] == "api_connector_resource_v1"
    assert tool_payload["template"]["execution_status"] == "runner_available"
    assert validate_extension_pack(pack_path).ok is True
    assert "bubble_extension_import" in result["next_user_action"]
    assert "bubble_extension_call" in result["catalog_visibility"]
    assert "same extension_id and tool_name" in result["update_guidance"]
    assert result["next_mcp_calls"][0]["tool"] == "bubble_extension_validate"
    assert result["next_mcp_calls"][3]["tool"] == "bubble_extension_call"


def test_authoring_session_generate_without_captures_returns_guidance(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BUBBLE_MCP_CONFIG_DIR", str(tmp_path))

    session = create_authoring_session(
        intent="Create an API Connector call",
        target="api_connector",
        profile="client",
    )

    result = generate_authoring_extension_pack(session.id)

    assert result["ok"] is False
    assert result["status"] == "needs_captures"
    assert result["error"] == "tool_authoring_session_has_no_captures"


def test_authoring_session_can_activate_existing_session(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BUBBLE_MCP_CONFIG_DIR", str(tmp_path))

    first = create_authoring_session(intent="First", target="api_connector", profile="client")
    second = create_authoring_session(intent="Second", target="workflow", profile="client")

    assert active_authoring_session_id() == second.id
    result = set_active_authoring_session(first.id)

    assert result["ok"] is True
    assert result["next_mcp_calls"] == [
        {"tool": "bubble_tool_wizard_finalize", "arguments": {"session_id": first.id}}
    ]
    assert active_authoring_session_id() == first.id
    assert describe_authoring_session(first.id)["active"] is True
    assert describe_authoring_session(second.id)["active"] is False


def test_authoring_session_rejects_unsafe_session_id(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BUBBLE_MCP_CONFIG_DIR", str(tmp_path))

    with pytest.raises(ValueError, match="safe path segment"):
        describe_authoring_session("../outside")


def test_authoring_session_rejects_symlink_capture(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BUBBLE_MCP_CONFIG_DIR", str(tmp_path))
    capture = Path("tests/fixtures/tool-authoring/api-connector-write-capture.json").resolve()
    symlink = tmp_path / "capture-link.json"
    symlink.symlink_to(capture)
    session = create_authoring_session(
        intent="Create an API Connector call",
        target="api_connector",
        profile="client",
    )

    with pytest.raises(ValueError, match="symlink"):
        append_capture_to_authoring_session(session.id, symlink)


def test_authoring_session_rejects_home_expanded_symlink_capture(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BUBBLE_MCP_CONFIG_DIR", str(tmp_path / "config"))
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    capture = Path("tests/fixtures/tool-authoring/api-connector-write-capture.json").resolve()
    symlink = home / "capture-link.json"
    symlink.symlink_to(capture)
    session = create_authoring_session(
        intent="Create an API Connector call",
        target="api_connector",
        profile="client",
    )

    with pytest.raises(ValueError, match="symlink"):
        append_capture_to_authoring_session(session.id, Path("~/capture-link.json"))


def test_authoring_session_rejects_malformed_capture_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BUBBLE_MCP_CONFIG_DIR", str(tmp_path))
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")
    session = create_authoring_session(
        intent="Create an API Connector call",
        target="api_connector",
        profile="client",
    )

    with pytest.raises(ValueError, match="Expecting property name"):
        append_capture_to_authoring_session(session.id, malformed)


def test_authoring_session_rejects_capture_without_write_payload(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BUBBLE_MCP_CONFIG_DIR", str(tmp_path))
    no_payload = tmp_path / "no-payload.json"
    no_payload.write_text("{}", encoding="utf-8")
    session = create_authoring_session(
        intent="Create an API Connector call",
        target="api_connector",
        profile="client",
    )

    with pytest.raises(ValueError, match="does not contain a Bubble editor write body"):
        append_capture_to_authoring_session(session.id, no_payload)


def _api_connector_session(tmp_path, monkeypatch):
    monkeypatch.setenv("BUBBLE_MCP_CONFIG_DIR", str(tmp_path))
    session = create_authoring_session(
        intent="Create an API Connector call",
        target="api_connector",
        profile="client",
    )
    append_capture_to_authoring_session(
        session.id,
        Path("tests/fixtures/tool-authoring/api-connector-write-capture.json"),
    )
    return session


def test_generated_tool_description_explains_capability_not_session(tmp_path, monkeypatch) -> None:
    session = _api_connector_session(tmp_path, monkeypatch)

    result = generate_authoring_extension_pack(session.id)
    tool_payload = json.loads(Path(str(result["tool_path"])).read_text(encoding="utf-8"))
    description = tool_payload["description"]

    # Starts with the human intent so tool search and agents can match by outcome.
    assert description.startswith("Create an API Connector call.")
    assert "API Connector" in description
    # Family-specific disambiguation against the Data API token tools.
    assert "create_api_token" in description
    # Required arguments are listed so agents know what to pass.
    assert "name, method, url" in description
    assert "execute=false" in description
    # Session id stays in the evidence/template, not in the agent-facing description.
    assert session.id not in description
    assert len(description) <= 800


def test_generated_tool_name_is_mcp_client_safe(tmp_path, monkeypatch) -> None:
    session = _api_connector_session(tmp_path, monkeypatch)

    result = generate_authoring_extension_pack(session.id)

    assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", result["tool_name"])
    assert "." not in result["tool_name"]
    assert result["extension_id"].startswith("local.toolwiz.api_connector.")


def test_generate_rejects_tool_name_with_dots_or_too_long(tmp_path, monkeypatch) -> None:
    session = _api_connector_session(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="tool_name"):
        generate_authoring_extension_pack(session.id, tool_name="local.pack.create_call")
    with pytest.raises(ValueError, match="tool_name"):
        generate_authoring_extension_pack(session.id, tool_name="x" * 65)


def test_generate_accepts_explicit_short_tool_name(tmp_path, monkeypatch) -> None:
    session = _api_connector_session(tmp_path, monkeypatch)

    result = generate_authoring_extension_pack(session.id, tool_name="create_api_connector_call")

    assert result["ok"] is True
    assert result["tool_name"] == "create_api_connector_call"
    assert Path(str(result["tool_path"])).name == "create_api_connector_call.tool.json"
    assert result["next_mcp_calls"][3]["arguments"]["tool"] == "create_api_connector_call"
