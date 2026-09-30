"""The tester tools: preview by default, ledger before write, restore only what is still ours."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bubble_mcp.core.config import BubbleMcpSettings, BubbleProfile
from bubble_mcp.execution.tester_ids import ledger, service
from bubble_mcp.execution.tester_ids.elements import EXPOSE_ID_POINTER, html_id_body

ROOT = ["%p3", "pg"]
EMAIL = ["%p3", "pg", "%el", "c"]
START = ["%p3", "pg", "%el", "a"]


class FakeApp:
    """A page and a settings flag held in memory; reads and writes go through it."""

    def __init__(self) -> None:
        self.page: dict[str, Any] = {
            "%x": "Page",
            "%el": {
                "a": {"%x": "Button", "%nm": "Start", "%p": {"unique_id": html_id_body("btn-start")}},
                "c": {"%x": "Input", "%nm": "Email", "%p": {}},
            },
        }
        self.expose = False
        self.writes: list[dict[str, Any]] = []

    def _node(self, pointer: list[str]) -> dict[str, Any]:
        node = self.page
        for part in pointer[2:]:
            node = node[part]
        return node

    def reader(self, profile: str, pointers: Any, **_: Any) -> dict[tuple, Any]:
        out: dict[tuple, Any] = {}
        for pointer in pointers:
            p = list(pointer)
            if p == EXPOSE_ID_POINTER:
                out[tuple(p)] = self.expose
            elif p == ROOT:
                out[tuple(p)] = self.page
            elif p[-2:] == ["%p", "unique_id"]:
                value = self._node(p[:-2]).get("%p", {}).get("unique_id")
                if value is not None:
                    out[tuple(p)] = value
        return out

    def writer(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.writes.append(payload)
        for change in payload["changes"]:
            path = change["path_array"]
            if path == EXPOSE_ID_POINTER:
                self.expose = change["body"]
            else:
                props = self._node(path[:-2]).setdefault("%p", {})
                if change["body"] is None:
                    props.pop("unique_id", None)
                else:
                    props["unique_id"] = change["body"]
        return {"ok": True}


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeApp:
    def settings(tester: bool = True) -> BubbleMcpSettings:
        profile = BubbleProfile(name="p", app_id="app", appname="app", tester_mode=tester)
        return BubbleMcpSettings(config_dir=tmp_path, default_profile="p", profiles={"p": profile})

    monkeypatch.setattr(service, "load_settings", lambda: settings())
    return FakeApp()


def _apply(app: FakeApp, tmp_path: Path, ids: list[dict], execute: bool = True) -> dict:
    return service.apply_ids("p", ROOT, ids, execute=execute, reader=app.reader,
                             writer=app.writer, config_dir=tmp_path)


def _restore(app: FakeApp, tmp_path: Path, **kwargs: Any) -> dict:
    return service.restore_ids("p", execute=True, reader=app.reader, writer=app.writer,
                               config_dir=tmp_path, **kwargs)


def test_plan_splits_reusable_and_missing(app: FakeApp) -> None:
    result = service.plan_ids("p", ROOT, reader=app.reader)

    assert [e["html_id"] for e in result["reusable"]] == ["btn-start"]
    assert [e["name"] for e in result["missing"]] == ["Email"]
    assert result["expose_id_option"] is False


def test_the_tools_refuse_without_tester_mode_and_on_live(
    app: FakeApp, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert service.plan_ids("p", ROOT, app_version="live", reader=app.reader)["error"] == "live_never"
    off = BubbleProfile(name="p", app_id="app", appname="app")
    monkeypatch.setattr(service, "load_settings",
                        lambda: BubbleMcpSettings(config_dir=tmp_path, default_profile="p", profiles={"p": off}))
    assert service.plan_ids("p", ROOT, reader=app.reader)["error"] == "tester_mode_off"


def test_preview_writes_nothing(app: FakeApp, tmp_path: Path) -> None:
    result = _apply(app, tmp_path, [{"pointer": EMAIL, "html_id": "login-email"}], execute=False)

    assert result["ok"] is True and result["executed"] is False
    assert app.writes == []
    assert ledger.read_entries("p", config_dir=tmp_path) == []


def test_apply_writes_only_the_id_turns_expose_on_and_records_both(app: FakeApp, tmp_path: Path) -> None:
    result = _apply(app, tmp_path, [{"pointer": EMAIL, "html_id": "login-email"},
                                    {"pointer": START, "html_id": "ignored"}])

    assert result["ok"] is True and result["batch_id"]
    assert [k["html_id"] for k in result["kept"]] == ["btn-start"]
    paths = [c["path_array"] for c in app.writes[0]["changes"]]
    assert paths == [EXPOSE_ID_POINTER, EMAIL + ["%p", "unique_id"]]
    assert app.page["%el"]["c"]["%p"]["unique_id"] == html_id_body("login-email")
    assert app.expose is True
    assert len(ledger.open_changes("p", config_dir=tmp_path)) == 1
    assert len(ledger.open_expose_flips("p", config_dir=tmp_path)) == 1


def test_an_invalid_batch_writes_nothing(app: FakeApp, tmp_path: Path) -> None:
    result = _apply(app, tmp_path, [{"pointer": EMAIL, "html_id": "btn-start"}])

    assert result["ok"] is False
    assert result["problems"][0]["error"] == "duplicate_html_id"
    assert app.writes == []


def test_a_failed_write_is_recorded_as_failed(app: FakeApp, tmp_path: Path) -> None:
    app.writer = lambda payload: {"ok": False, "error": "boom"}  # type: ignore[method-assign]

    result = _apply(app, tmp_path, [{"pointer": EMAIL, "html_id": "login-email"}])

    assert result["ok"] is False
    assert {e["status"] for e in ledger.read_entries("p", config_dir=tmp_path)} == {"pending", "failed"}
    assert ledger.open_changes("p", config_dir=tmp_path) == []


def test_restore_puts_back_the_first_old_value_and_is_idempotent(app: FakeApp, tmp_path: Path) -> None:
    _apply(app, tmp_path, [{"pointer": START, "html_id": "start-a", "replace": True}])
    _apply(app, tmp_path, [{"pointer": START, "html_id": "start-b", "replace": True}])

    first = _restore(app, tmp_path, pointers=[START])
    second = _restore(app, tmp_path, pointers=[START])

    assert app.page["%el"]["a"]["%p"]["unique_id"] == html_id_body("btn-start")
    assert [r["pointer"] for r in first["restored"]] == [START]
    assert second["restored"] == []


def test_restore_clears_an_id_that_did_not_exist(app: FakeApp, tmp_path: Path) -> None:
    result = _apply(app, tmp_path, [{"pointer": EMAIL, "html_id": "login-email"}])

    _restore(app, tmp_path, batch_id=result["batch_id"])

    assert "unique_id" not in app.page["%el"]["c"]["%p"]


def test_restore_skips_an_id_someone_changed_or_cleared(app: FakeApp, tmp_path: Path) -> None:
    _apply(app, tmp_path, [{"pointer": EMAIL, "html_id": "login-email"},
                           {"pointer": START, "html_id": "start", "replace": True}])
    app.page["%el"]["c"]["%p"]["unique_id"] = html_id_body("human-choice")
    app.page["%el"]["a"]["%p"].pop("unique_id")

    result = _restore(app, tmp_path, restore_all=True)

    assert sorted(c["pointer"][-1] for c in result["conflicts"]) == ["a", "c"]
    assert app.page["%el"]["c"]["%p"]["unique_id"] == html_id_body("human-choice")


def test_expose_goes_back_off_only_with_all(app: FakeApp, tmp_path: Path) -> None:
    result = _apply(app, tmp_path, [{"pointer": EMAIL, "html_id": "login-email"}])

    _restore(app, tmp_path, batch_id=result["batch_id"])
    assert app.expose is True
    _apply(app, tmp_path, [{"pointer": EMAIL, "html_id": "login-email"}])
    _restore(app, tmp_path, restore_all=True)
    assert app.expose is False


def _statuses(tmp_path: Path) -> set[str]:
    return {e["status"] for e in ledger.read_entries("p", config_dir=tmp_path)}


def test_a_writer_that_raises_is_unknown_and_stays_open(app: FakeApp, tmp_path: Path) -> None:
    def boom(payload: dict) -> dict:
        raise TimeoutError("late")

    result = service.apply_ids("p", ROOT, [{"pointer": EMAIL, "html_id": "login-email"}], execute=True,
                               reader=app.reader, writer=boom, config_dir=tmp_path)

    assert result["ok"] is False
    assert _statuses(tmp_path) == {"pending", "unknown"}
    assert len(ledger.open_changes("p", config_dir=tmp_path)) == 1


def test_restore_with_nothing_selected(app: FakeApp, tmp_path: Path) -> None:
    assert _restore(app, tmp_path)["error"] == "nothing_selected"


def test_a_failed_restore_leaves_the_changes_open(app: FakeApp, tmp_path: Path) -> None:
    _apply(app, tmp_path, [{"pointer": EMAIL, "html_id": "login-email"}])

    failing = service.restore_ids("p", restore_all=True, execute=True, reader=app.reader,
                                  writer=lambda payload: {"ok": False}, config_dir=tmp_path)
    again = _restore(app, tmp_path, restore_all=True)

    assert failing["ok"] is False
    assert [r["pointer"] for r in again["restored"]] == [EMAIL]


def test_the_same_pointer_on_two_versions_restores_separately(app: FakeApp, tmp_path: Path) -> None:
    branch = FakeApp()  # a branch has its own copy of the same element keys
    for version, store in (("test", app), ("b1", branch)):
        service.apply_ids("p", ROOT, [{"pointer": EMAIL, "html_id": "login-email"}], app_version=version,
                          execute=True, reader=store.reader, writer=store.writer, config_dir=tmp_path)

    result = _restore(app, tmp_path, restore_all=True)

    assert [r["pointer"] for r in result["restored"]] == [EMAIL]
    [left] = ledger.open_changes("p", config_dir=tmp_path)
    assert left["app_version"] == "b1"
    assert [f["app_version"] for f in ledger.open_expose_flips("p", config_dir=tmp_path)] == ["b1"]


def test_an_expression_id_is_not_replaceable(app: FakeApp, tmp_path: Path) -> None:
    app.page["%el"]["a"]["%p"]["unique_id"] = {"%x": "TextExpression",
                                              "%e": {"0": "row-", "1": {"%x": "X"}}}

    result = _apply(app, tmp_path, [{"pointer": START, "html_id": "new-id", "replace": True}])

    assert result["ok"] is False
    assert result["problems"][0]["error"] == "expression_id_not_replaceable"
    assert app.writes == []


def test_no_session_refuses_before_reading_or_writing(
    app: FakeApp, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(service, "load_session", lambda profile: None)

    assert service.plan_ids("p", ROOT)["error"] == "no_session"
    result = service.apply_ids("p", ROOT, [{"pointer": EMAIL, "html_id": "login-email"}], execute=True,
                               reader=app.reader, config_dir=tmp_path)
    assert result["error"] == "no_session"
    assert ledger.read_entries("p", config_dir=tmp_path) == []


def test_a_reader_that_raises_is_reported(app: FakeApp, tmp_path: Path) -> None:
    def broken(*args: Any, **kwargs: Any) -> dict:
        raise OSError("down")

    result = service.apply_ids("p", ROOT, [{"pointer": EMAIL, "html_id": "x"}], execute=True,
                               reader=broken, writer=app.writer, config_dir=tmp_path)

    assert result["error"] == "read_failed" and "OSError" in result["message"]
    assert ledger.read_entries("p", config_dir=tmp_path) == []
    assert service.plan_ids("p", ROOT, reader=broken)["error"] == "read_failed"
