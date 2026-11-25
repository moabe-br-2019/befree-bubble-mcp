from __future__ import annotations

import contextlib
from typing import Any

import pytest

from bubble_mcp.plugin_authoring import dashboard


class FakeLocator:
    def __init__(self, page: FakePage, label: str) -> None:
        self.page = page
        self.label = label

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self.page, f"{self.label} > {selector}")

    def click(self) -> None:
        self.page.actions.append(("click", self.label))

    def fill(self, value: str) -> None:
        self.page.actions.append(("fill", self.label, value))


class FakePage:
    def __init__(self, pages: list[list[dict[str, str]]], modal_text: str = "") -> None:
        self.pages = pages
        self.index = 0
        self.modal_text = modal_text
        self.actions: list[tuple[Any, ...]] = []
        self.url = "https://bubble.io/home/plugins"

    def evaluate(self, script: str, arg: Any = None) -> Any:
        if script == dashboard._READ_PAGE_JS:
            return {"items": self.pages[self.index], "hasNext": self.index < len(self.pages) - 1}
        if script == dashboard._CLICK_NEXT_JS:
            self.index += 1
            return True
        if script == dashboard._OPEN_DELETE_JS:
            self.actions.append(("open_menu", arg))
            return "menu_opened"
        if script == dashboard._CLICK_DELETE_ITEM_JS:
            return True
        if script == dashboard._MODAL_TEXT_JS:
            return self.modal_text
        raise AssertionError(script)

    def wait_for_timeout(self, ms: int) -> None:
        return None

    def get_by_role(self, role: str, name: str) -> FakeLocator:
        return FakeLocator(self, f"{role}:{name}")

    def get_by_placeholder(self, text: str) -> FakeLocator:
        return FakeLocator(self, f"placeholder:{text}")

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    def goto(self, url: str, timeout: int = 0) -> None:
        self.index = 0


def test_list_follows_pagination_until_last_page() -> None:
    page = FakePage([[{"id": "1x1", "name": "a"}], [{"id": "2x2", "name": "b"}], [{"id": "3x3", "name": "c"}]])
    assert [p["id"] for p in dashboard.list_plugins(page)] == ["1x1", "2x2", "3x3"]


def test_delete_refuses_when_modal_does_not_name_the_plugin() -> None:
    page = FakePage([[{"id": "1x1", "name": "keep"}]], modal_text="Are you sure you want to delete the plugin other?")
    with pytest.raises(dashboard.DashboardError):
        dashboard.delete_plugin(page, "1x1", "keep")
    assert not any(action[0] == "fill" and action[-1] == "DELETE" for action in page.actions)


def test_delete_types_delete_and_confirms_when_modal_matches() -> None:
    page = FakePage([[{"id": "1x1", "name": "old"}]], modal_text="Are you sure you want to delete the plugin old?")
    dashboard.delete_plugin(page, "1x1", "old")
    assert ("fill", "text=Danger zone > xpath=following::input[1]", "DELETE") in page.actions
    assert ("click", "button:Delete plugin") in page.actions


def test_delete_wrapper_requires_confirm() -> None:
    with pytest.raises(ValueError):
        dashboard.delete_plugin_for_profile("p", "1x1", execute=True, confirm=False)


def test_delete_wrapper_dry_run_names_the_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage([[{"id": "1x1", "name": "old"}]])

    @contextlib.contextmanager
    def fake_open(profile: str, *, headless: bool = True) -> Any:
        yield page

    monkeypatch.setattr(dashboard, "open_dashboard", fake_open)
    result = dashboard.delete_plugin_for_profile("p", "1x1")
    assert result["executed"] is False and result["would_delete"]["name"] == "old"
    assert not any(action[0] == "open_menu" for action in page.actions)
    missing = dashboard.delete_plugin_for_profile("p", "9x9")
    assert missing["ok"] is False and missing["reason"] == "not_found"


def test_create_wrapper_previews_by_default() -> None:
    assert dashboard.create_plugin_for_profile("p", "new one")["executed"] is False
