"""create_page must register the page name; delete_page must drop it.

Bubble resolves pages through _index.page_name_to_id / page_name_to_path. A page
created without those entries exists under %p3 and answers path reads, but the
editor opens it empty and the runtime serves a blank shell. Creating a second
page with a name that already exists produces two nodes for one name, and only
one of them is reachable.
"""

from types import SimpleNamespace

from bubble_mcp.aria_runtime.bubble_cli import BubbleCLI


def _change_by_path(payload: dict, path: list[str]) -> dict:
    return next(change for change in payload["changes"] if change.get("path_array") == path)


def test_create_page_registers_name_maps_in_dispatched_payload() -> None:
    cli = object.__new__(BubbleCLI)
    cli.appname = "test-app"
    cli.discovery = SimpleNamespace(
        find_page=lambda _name: None,
        inject_element=lambda **_kwargs: None,
    )
    payloads: list[dict] = []
    cli._dispatch_payload = lambda builder, **_kwargs: payloads.append(builder.build())
    cli._cache_context_alias = lambda *_args, **_kwargs: None

    page_id = BubbleCLI.create_page(cli, "reports")

    assert page_id
    assert len(payloads) == 1
    payload = payloads[0]
    create_change = next(change for change in payload["changes"] if change["intent"]["name"] == "CreateElement")
    page_slot = create_change["path_array"][1]
    assert create_change["body"]["%nm"] == "reports"
    assert _change_by_path(payload, ["_index", "page_name_to_id", "reports"])["body"] == page_id
    assert _change_by_path(payload, ["_index", "page_name_to_path", "reports"])["body"] == f"%p3.{page_slot}"


def test_create_page_refuses_duplicate_name_before_dispatch() -> None:
    cli = object.__new__(BubbleCLI)
    cli.appname = "test-app"
    cli.discovery = SimpleNamespace(find_page=lambda _name: "existing-page-id")
    cli._dispatch_payload = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("duplicate page must not dispatch")
    )

    assert BubbleCLI.create_page(cli, "reports") is False


def test_delete_page_clears_name_maps_in_dispatched_payload() -> None:
    cli = object.__new__(BubbleCLI)
    cli.appname = "test-app"
    cli._find_context = lambda _name: ("page-slot", "page")
    cli._resolve_context_object_id_from_index = lambda *_args: "page-object-id"
    cli._lookup_cached_context_object_id = lambda *_args: None
    cli._collect_context_object_ids_from_index = lambda *_args: ["page-object-id"]
    cli._remove_cached_context_aliases = lambda **_kwargs: None
    cli._remove_context_scoped_cache_entries = lambda *_args: None
    cli._remove_context_from_discovery_cache = lambda *_args: None
    payloads: list[dict] = []
    cli._dispatch_payload = lambda builder, **_kwargs: payloads.append(builder.build())

    assert BubbleCLI.delete_page(cli, "reports") is True

    assert len(payloads) == 1
    payload = payloads[0]
    assert _change_by_path(payload, ["_index", "page_name_to_id", "reports"])["body"] is None
    assert _change_by_path(payload, ["_index", "page_name_to_path", "reports"])["body"] is None
