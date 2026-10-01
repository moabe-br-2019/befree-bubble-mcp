"""Pure helpers behind the tester id tools: read ids out of an encoded tree, validate a batch."""

from __future__ import annotations

from bubble_mcp.execution.tester_ids.elements import (
    collect_elements,
    html_id_body,
    html_id_text,
    validate_batch,
)

PAGE = {
    "%x": "Page",
    "%nm": "index",
    "%el": {
        "a": {"%x": "Button", "%nm": "Start", "%p": {"unique_id": html_id_body("btn-start")}},
        "b": {
            "%x": "Group",
            "%nm": "Form",
            "%p": {},
            "%el": {"c": {"%x": "Input", "%nm": "Email", "%p": {}}},
        },
        "d": {
            "%x": "Text",
            "%nm": "Dyn",
            "%p": {"unique_id": {"%x": "TextExpression", "%e": {"0": "row-", "1": {"%x": "X"}}}},
        },
    },
}
ROOT = ["%p3", "bTpage"]


def _by_name(name: str) -> dict:
    return next(e for e in collect_elements(PAGE, ROOT) if e["name"] == name)


def test_collect_walks_nested_elements_with_their_pointers() -> None:
    assert _by_name("Email")["pointer"] == ["%p3", "bTpage", "%el", "b", "%el", "c"]
    assert _by_name("Start")["html_id"] == "btn-start"
    assert _by_name("Email")["html_id"] is None


def test_an_expression_id_counts_as_an_id() -> None:
    assert _by_name("Dyn")["html_id"] is not None
    assert html_id_text(None) is None


def _batch(*items: dict) -> dict:
    return validate_batch(list(items), collect_elements(PAGE, ROOT))


def test_a_missing_id_is_written_and_an_existing_one_is_kept() -> None:
    result = _batch(
        {"pointer": _by_name("Email")["pointer"], "html_id": "login-email"},
        {"pointer": _by_name("Start")["pointer"], "html_id": "other"},
    )

    assert result["ok"] is True
    assert [w["html_id"] for w in result["write"]] == ["login-email"]
    assert [k["html_id"] for k in result["kept"]] == ["btn-start"]


def test_replace_overwrites_an_existing_id() -> None:
    result = _batch({"pointer": _by_name("Start")["pointer"], "html_id": "start", "replace": True})

    assert result["write"][0]["old_body"] == html_id_body("btn-start")


def test_every_problem_is_listed_and_the_batch_refused() -> None:
    result = _batch(
        {"pointer": _by_name("Email")["pointer"], "html_id": "Bad_Id"},
        {"pointer": _by_name("Form")["pointer"], "html_id": "btn-start"},
        {"pointer": ROOT + ["%el", "zzz"], "html_id": "ghost"},
    )

    assert result["ok"] is False
    assert sorted(p["error"] for p in result["problems"]) == [
        "duplicate_html_id",
        "element_not_found",
        "invalid_html_id",
    ]


def test_two_items_in_one_batch_cannot_share_an_id() -> None:
    result = _batch(
        {"pointer": _by_name("Email")["pointer"], "html_id": "same"},
        {"pointer": _by_name("Form")["pointer"], "html_id": "same"},
    )

    assert [p["error"] for p in result["problems"]] == ["duplicate_html_id"]


def test_trailing_newline_in_id_is_rejected() -> None:
    result = _batch({"pointer": _by_name("Email")["pointer"], "html_id": "ok\n"})

    assert result["ok"] is False
    assert result["problems"][0]["error"] == "invalid_html_id"


def test_expression_id_given_new_html_id_without_replace_is_kept() -> None:
    result = _batch({"pointer": _by_name("Dyn")["pointer"], "html_id": "new-id"})

    assert result["ok"] is True
    assert [k["html_id"] for k in result["kept"]] == [_by_name("Dyn")["html_id"]]
    assert result["write"] == []


def test_expression_id_with_replace_includes_full_old_body() -> None:
    result = _batch({"pointer": _by_name("Dyn")["pointer"], "html_id": "replace-expr", "replace": True})

    assert result["ok"] is True
    assert result["write"][0]["old_body"] == {"%x": "TextExpression", "%e": {"0": "row-", "1": {"%x": "X"}}}
    assert result["write"][0]["html_id"] == "replace-expr"
