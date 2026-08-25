"""Created siblings must keep strictly increasing %p.order.

Bubble renders tied orders in reverse creation order. Two ways the order was lost:
builders that hardcoded a default (button=100, link/alert/map=1, text=0) skipped the
central stamping, and a cold discovery cache made _next_child_order return 0 for
every child of a freshly created parent.
"""

from bubble_mcp.aria_runtime.bubble_cli import BubbleCLI
from bubble_mcp.aria_runtime.bubble_sdk import ElementBuilder


def _bare_cli() -> BubbleCLI:
    return BubbleCLI.__new__(BubbleCLI)


def test_advance_child_order_is_monotonic_per_parent() -> None:
    cli = _bare_cli()
    parent = "%p3.page.%el.hero"

    # Bubble reassigns %p.order = 0 server-side, so stamping starts at 1.
    assert cli._advance_child_order(parent, 0) == 1
    assert cli._advance_child_order(parent, 0) == 2
    assert cli._advance_child_order(parent, 0) == 3
    # A warm cache that already counted the siblings still wins.
    assert cli._advance_child_order(parent, 7) == 7
    assert cli._advance_child_order(parent, 3) == 8


def test_advance_child_order_is_isolated_per_parent() -> None:
    cli = _bare_cli()

    assert cli._advance_child_order("%p3.page.%el.nav", 0) == 1
    assert cli._advance_child_order("%p3.page.%el.hero", 0) == 1
    assert cli._advance_child_order("%p3.page.%el.nav", 0) == 2


def test_builders_leave_order_unset_for_central_stamping() -> None:
    builder = ElementBuilder()

    button = builder.button(name="btn", label="Go")
    text = builder.text("tx", "Hello")

    assert button["%p"].get("order") is None
    assert text["%p"].get("order") is None
    assert builder.button(name="btn2", label="Go", order=4)["%p"]["order"] == 4


class _FakeDiscovery:
    def __init__(self, issues_sub: dict) -> None:
        self.data = {"_index": {"issues_sub": issues_sub}}

    def _get_context_root(self, context_id, context_type):  # pragma: no cover - not used here
        return None


def test_next_child_order_falls_back_to_editor_index() -> None:
    cli = _bare_cli()
    cli.discovery = _FakeDiscovery({"bHero": '["bTitle", "bSub", "bCta"]'})

    # Parent resolved from the alias cache: known id, but no cached children.
    parent_result = {"id": "bHero", "element": {"id": "bHero", "%el": {}}}

    assert cli._next_child_order("bPage", "page", parent_result) == 3


def test_next_child_order_uses_cached_children_when_present() -> None:
    cli = _bare_cli()
    cli.discovery = _FakeDiscovery({})
    parent_result = {
        "id": "bHero",
        "element": {"id": "bHero", "%el": {"a": {"%p": {"order": 0}}, "b": {"%p": {"order": 4}}}},
    }

    assert cli._next_child_order("bPage", "page", parent_result) == 5
