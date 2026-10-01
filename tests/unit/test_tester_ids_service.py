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

    assert result["ok"] is False and result["error"] == "invalid_batch"
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


def _not_landing(app: FakeApp) -> Any:
    """A writer Bubble answers ok for but that changes nothing."""

    def writer(payload: dict[str, Any]) -> dict[str, Any]:
        app.writes.append(payload)
        return {"ok": True}

    return writer


def test_a_loose_profile_spelling_shares_the_canonical_ledger(
    app: FakeApp, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = BubbleProfile(name="orana", app_id="app", appname="app", tester_mode=True)
    monkeypatch.setattr(service, "load_settings", lambda: BubbleMcpSettings(
        config_dir=tmp_path, default_profile="orana", profiles={"orana": profile}))

    for spelling in ("O-rana", "../orana"):
        service.apply_ids(spelling, ROOT, [{"pointer": EMAIL, "html_id": "login-email"}], execute=True,
                          reader=app.reader, writer=app.writer, config_dir=tmp_path)
    result = service.restore_ids("orana", pointers=[EMAIL], execute=True, reader=app.reader,
                                 writer=app.writer, config_dir=tmp_path)

    assert [r["pointer"] for r in result["restored"]] == [EMAIL]
    assert [p.name for p in (tmp_path / "tester").iterdir()] == ["orana"]
    assert not (tmp_path / "orana").exists()


def test_a_restore_bubble_did_not_store_is_reported_and_stays_open(app: FakeApp, tmp_path: Path) -> None:
    _apply(app, tmp_path, [{"pointer": EMAIL, "html_id": "login-email"}])

    result = service.restore_ids("p", pointers=[EMAIL], execute=True, reader=app.reader,
                                 writer=_not_landing(app), config_dir=tmp_path)
    again = _restore(app, tmp_path, pointers=[EMAIL])

    assert result["ok"] is False and result["restored"] == []
    assert result["not_restored"] == [{"pointer": EMAIL, "current": html_id_body("login-email")}]
    assert [r["pointer"] for r in again["restored"]] == [EMAIL]
    assert "unique_id" not in app.page["%el"]["c"]["%p"]


def test_an_apply_bubble_did_not_store_is_unknown_and_open(app: FakeApp, tmp_path: Path) -> None:
    result = service.apply_ids("p", ROOT, [{"pointer": EMAIL, "html_id": "login-email"}], execute=True,
                               reader=app.reader, writer=_not_landing(app), config_dir=tmp_path)

    assert result["ok"] is False
    assert {"pointer": EMAIL, "current": None} in result["not_confirmed"]
    assert {"expose_id": True, "current": False} in result["not_confirmed"]
    [change] = ledger.open_changes("p", config_dir=tmp_path)
    assert change["pointer"] == EMAIL
    assert _statuses(tmp_path) == {"pending", "unknown"}
    assert len(ledger.open_expose_flips("p", config_dir=tmp_path)) == 1


def test_a_read_back_that_fails_after_an_ok_write_is_unknown(app: FakeApp, tmp_path: Path) -> None:
    calls = {"n": 0}

    def flaky(*args: Any, **kwargs: Any) -> dict:
        calls["n"] += 1
        if calls["n"] > 1:
            raise OSError("down")
        return app.reader(*args, **kwargs)

    result = service.apply_ids("p", ROOT, [{"pointer": EMAIL, "html_id": "login-email"}], execute=True,
                               reader=flaky, writer=app.writer, config_dir=tmp_path)

    assert result["ok"] is False and result["executed"] is True
    assert [n["pointer"] for n in result["not_confirmed"] if "pointer" in n] == [EMAIL]
    assert _statuses(tmp_path) == {"pending", "unknown"}
    assert len(ledger.open_changes("p", config_dir=tmp_path)) == 1


def test_a_change_already_at_its_old_value_is_closed_without_writing(app: FakeApp, tmp_path: Path) -> None:
    def boom(payload: dict) -> dict:
        raise TimeoutError("late")

    applied = service.apply_ids("p", ROOT, [{"pointer": EMAIL, "html_id": "login-email"}], execute=True,
                                reader=app.reader, writer=boom, config_dir=tmp_path)
    first = _restore(app, tmp_path, batch_id=applied["batch_id"])
    second = _restore(app, tmp_path, batch_id=applied["batch_id"])

    assert first["already_restored"] == [{"pointer": EMAIL}]
    assert first["conflicts"] == [] and first["restored"] == []
    assert app.writes == []
    assert (second["restored"], second["already_restored"], second["conflicts"]) == ([], [], [])
    assert any(e.get("note") == "already_at_old" for e in ledger.read_entries("p", config_dir=tmp_path))


def test_all_is_exclusive_with_other_selections(app: FakeApp, tmp_path: Path) -> None:
    def broken(*args: Any, **kwargs: Any) -> dict:
        raise AssertionError("must refuse before reading")

    for extra in ({"batch_id": "x"}, {"pointers": [EMAIL]}):
        result = service.restore_ids("p", restore_all=True, execute=True, reader=broken, writer=app.writer,
                                     config_dir=tmp_path, **extra)
        assert result["ok"] is False and result["error"] == "conflicting_selection"
    assert app.writes == []


@pytest.mark.parametrize("pointer", [["%p3"], ["%p3", "pg", "%el", "a"], ["%x", "pg"], ["pg", "%p3"]])
def test_plan_and_apply_take_only_a_whole_page_or_reusable(app: FakeApp, tmp_path: Path, pointer: list) -> None:
    assert service.plan_ids("p", pointer, reader=app.reader)["error"] == "invalid_pointer"
    result = service.apply_ids("p", pointer, [{"pointer": EMAIL, "html_id": "x"}], execute=True,
                               reader=app.reader, writer=app.writer, config_dir=tmp_path)
    assert result["error"] == "invalid_pointer"
    assert app.writes == []


def test_a_reusable_pointer_is_accepted(app: FakeApp) -> None:
    assert service.plan_ids("p", ["%ed", "r1"], reader=lambda *a, **k: {})["error"] == "element_not_found"


def test_replacing_an_id_a_human_changed_after_the_tester_is_refused(app: FakeApp, tmp_path: Path) -> None:
    _apply(app, tmp_path, [{"pointer": START, "html_id": "start-a", "replace": True}])
    app.page["%el"]["a"]["%p"]["unique_id"] = html_id_body("human-id")
    writes = len(app.writes)

    result = _apply(app, tmp_path, [{"pointer": START, "html_id": "start-b", "replace": True}])

    assert result["ok"] is False and result["error"] == "invalid_batch"
    assert result["problems"] == [{"error": "changed_since_tester", "pointer": START}]
    assert len(app.writes) == writes


def test_an_expose_flip_that_landed_with_an_unknown_answer_is_restorable(app: FakeApp, tmp_path: Path) -> None:
    def lands_then_raises(payload: dict) -> dict:
        app.writer(payload)
        raise TimeoutError("late")

    service.apply_ids("p", ROOT, [{"pointer": EMAIL, "html_id": "login-email"}], execute=True,
                      reader=app.reader, writer=lands_then_raises, config_dir=tmp_path)
    assert app.expose is True and len(ledger.open_expose_flips("p", config_dir=tmp_path)) == 1

    result = _restore(app, tmp_path, restore_all=True)

    assert result["ok"] is True
    assert app.expose is False
    assert ledger.open_expose_flips("p", config_dir=tmp_path) == []


def test_an_apply_that_only_keeps_ids_still_turns_expose_on(app: FakeApp, tmp_path: Path) -> None:
    preview = _apply(app, tmp_path, [{"pointer": START, "html_id": "ignored"}], execute=False)
    result = _apply(app, tmp_path, [{"pointer": START, "html_id": "ignored"}])

    assert preview["turns_expose_id_on"] is True
    assert result["ok"] is True and app.expose is True
    assert [c["path_array"] for c in app.writes[0]["changes"]] == [EXPOSE_ID_POINTER]
    assert len(ledger.open_expose_flips("p", config_dir=tmp_path)) == 1
