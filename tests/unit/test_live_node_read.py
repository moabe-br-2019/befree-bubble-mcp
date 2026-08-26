"""Reading a node out of the running editor, with the browser replaced by a fake."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from typing import Any

import pytest

from bubble_mcp.execution.live_node_read import (
    build_appquery_script,
    read_live_node,
)


def test_build_appquery_script_chains_one_child_per_pointer_segment() -> None:
    script = build_appquery_script(["api", "wf-1", "actions", "3"])

    assert script == (
        '() => window.appquery.app().json._child("api")._child("wf-1")'
        '._child("actions")._child("3").raw()'
    )


def test_build_appquery_script_refuses_an_empty_pointer() -> None:
    with pytest.raises(ValueError, match="at least one child"):
        build_appquery_script([])


def test_build_appquery_script_refuses_an_empty_segment() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        build_appquery_script(["api", ""])


def test_build_appquery_script_escapes_a_segment_with_a_quote() -> None:
    script = build_appquery_script(['we"ird'])

    assert '._child("we\\"ird")' in script


def test_read_live_node_returns_the_node_the_page_produced() -> None:
    captured: dict[str, Any] = {}

    def fake_evaluator(script: str) -> Any:
        captured["script"] = script
        return {"id": "act-1", "type": "ChangeThing", "properties": {}}

    result = read_live_node(
        "mcp-test", ["api", "wf-1"], evaluator=fake_evaluator, app_id="mcp-test-app"
    )

    assert result["ok"] is True
    assert result["node"]["id"] == "act-1"
    assert result["pointer"] == ["api", "wf-1"]
    assert result["app_id"] == "mcp-test-app"
    assert captured["script"] == build_appquery_script(["api", "wf-1"])


def test_read_live_node_reports_a_pointer_that_did_not_resolve() -> None:
    result = read_live_node(
        "mcp-test", ["api", "nope"], evaluator=lambda _script: None, app_id="mcp-test-app"
    )

    assert result == {
        "ok": False,
        "error": "pointer_not_found",
        "pointer": ["api", "nope"],
        "message": (
            "window.appquery returned nothing for pointer 'api.nope'. Check the pointer against "
            "the app tree; a wrong segment reads as an absent node, not as an error."
        ),
    }


def test_read_live_node_rejects_a_page_value_that_is_not_a_node() -> None:
    result = read_live_node(
        "mcp-test", ["api"], evaluator=lambda _script: "a string", app_id="mcp-test-app"
    )

    assert result["ok"] is False
    assert result["error"] == "unexpected_node_shape"


def test_read_live_node_reports_an_evaluator_failure_as_a_structured_result() -> None:
    def raising_evaluator(_script: str) -> Any:
        raise RuntimeError("page closed")

    result = read_live_node(
        "mcp-test", ["api", "nope", "x"], evaluator=raising_evaluator, app_id="mcp-test-app"
    )

    assert result["ok"] is False
    assert result["error"] == "evaluator_failed"
    assert result["pointer"] == ["api", "nope", "x"]
    assert result["message"] == "RuntimeError: page closed"


def _fake_playwright(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Install an importable playwright whose launcher fails the test if it is ever called."""

    package = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")

    def _must_not_launch():  # type: ignore[no-untyped-def]
        raise AssertionError("no browser may be launched without a browser profile")

    sync_api.sync_playwright = _must_not_launch  # type: ignore[attr-defined]
    package.sync_api = sync_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)


def test_read_live_node_refuses_a_profile_with_no_browser_profile_directory(
    monkeypatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    """bubble_session_import stores a session without ever creating this directory."""

    _fake_playwright(monkeypatch)
    monkeypatch.setattr(
        "bubble_mcp.execution.live_node_read.load_settings",
        lambda: SimpleNamespace(config_dir=tmp_path),
    )

    result = read_live_node("imported-only", ["api", "wf-1"], app_id="mcp-test-app")

    assert result["ok"] is False
    assert result["error"] == "browser_profile_missing"
    assert "bubble_session_login" in result["message"]
    assert result["pointer"] == ["api", "wf-1"]


def test_read_live_node_gets_past_the_browser_profile_guard_once_it_exists(
    monkeypatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    _fake_playwright(monkeypatch)
    (tmp_path / "browser-profiles" / "logged-in").mkdir(parents=True)
    monkeypatch.setattr(
        "bubble_mcp.execution.live_node_read.load_settings",
        lambda: SimpleNamespace(config_dir=tmp_path),
    )

    result = read_live_node("logged-in", ["api", "wf-1"], app_id="mcp-test-app")

    # The guard passed, so the launcher was reached and refused: a different, later failure.
    assert result["ok"] is False
    assert result["error"] == "evaluator_failed"
