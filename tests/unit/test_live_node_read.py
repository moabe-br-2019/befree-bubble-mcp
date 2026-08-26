"""Reading a node out of the running editor, with the browser replaced by a fake."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from typing import Any

import pytest

from bubble_mcp.execution.live_node_read import (
    APPQUERY_READY_SCRIPT,
    NotLoggedIn,
    PointerNotReady,
    build_appquery_script,
    build_pointer_ready_script,
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


def test_appquery_ready_script_is_a_functional_probe_not_a_typeof_race() -> None:
    """Regression pin: window.appquery is a getter that exists before it works.

    A bare `typeof window.appquery !== 'undefined'` check passes the instant the getter is
    defined, well before the editor can actually serve `.app().json`, so the very next
    page.evaluate throws "The variable appquery is not fully initialized yet". The probe must
    call all the way through to something usable, inside a try/catch so the getter's exception
    never propagates out of wait_for_function (which would fail the wait instead of polling).
    """

    assert "typeof window.appquery !== 'undefined'" not in APPQUERY_READY_SCRIPT
    assert "try" in APPQUERY_READY_SCRIPT
    assert "catch" in APPQUERY_READY_SCRIPT
    assert "window.appquery" in APPQUERY_READY_SCRIPT


def test_build_pointer_ready_script_chains_one_child_per_segment_and_calls_raw() -> None:
    script = build_pointer_ready_script(["api", "wf-1", "actions", "3"])

    assert '._child("api")' in script
    assert '._child("wf-1")' in script
    assert '._child("actions")' in script
    assert '._child("3")' in script
    assert "node.raw();" in script


def test_build_pointer_ready_script_primes_the_walk_with_ensure_loading() -> None:
    script = build_pointer_ready_script(["api", "wf-1"])

    # Each segment's walk step both checks for and calls ensure_loading.
    assert script.count("ensure_loading") == 2 * 2


def test_build_pointer_ready_script_returns_false_only_for_not_ready() -> None:
    """Regression pin: any other error must return true, or a broken pointer hangs to timeout."""

    script = build_pointer_ready_script(["api"])

    assert "NotReady" in script
    assert "isNotReady" in script
    assert "return !isNotReady;" in script


def test_build_pointer_ready_script_refuses_an_empty_pointer() -> None:
    with pytest.raises(ValueError, match="at least one child"):
        build_pointer_ready_script([])


def test_build_pointer_ready_script_refuses_an_empty_segment() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        build_pointer_ready_script(["api", ""])


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


def test_read_live_node_reports_a_logged_out_profile_as_a_structured_result() -> None:
    """A profile directory can exist but never have logged in (Chrome creates the shell on

    first launch). That case must not surface as a bare evaluator_failed or a getter
    exception from https://bubble.io/ - it needs its own actionable error.
    """

    def raising_evaluator(_script: str) -> Any:
        raise NotLoggedIn(
            "Browser profile 'stale' landed on https://bubble.io/ instead of the editor for "
            "app 'mcp-test-app'. The profile directory exists but appears never to have "
            "logged in to Bubble; run bubble_session_login for profile 'stale', then retry."
        )

    result = read_live_node(
        "stale", ["api", "wf-1"], evaluator=raising_evaluator, app_id="mcp-test-app"
    )

    assert result["ok"] is False
    assert result["error"] == "not_logged_in"
    assert result["pointer"] == ["api", "wf-1"]
    assert "bubble_session_login" in result["message"]
    assert "stale" in result["message"]


def test_read_live_node_reports_a_pointer_that_never_became_ready_as_a_structured_result() -> None:
    """A NotReadyError that never clears is neither editor_not_ready (editor is up) nor

    pointer_not_found (nothing was read) - it needs its own actionable error.
    """

    def raising_evaluator(_script: str) -> Any:
        raise PointerNotReady(
            "pointer subtree never finished loading on https://bubble.io/page?... within 90s"
        )

    result = read_live_node(
        "mcp-test", ["api", "wf-1"], evaluator=raising_evaluator, app_id="mcp-test-app"
    )

    assert result["ok"] is False
    assert result["error"] == "pointer_not_ready"
    assert result["pointer"] == ["api", "wf-1"]
    assert "api.wf-1" in result["message"]
    assert "90" in result["message"]


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
