"""create_page must register the page name; delete_page must drop it.

Bubble resolves pages through _index.page_name_to_id / page_name_to_path. A page
created without those entries exists under %p3 and answers path reads, but the
editor opens it empty and the runtime serves a blank shell. Creating a second
page with a name that already exists produces two nodes for one name, and only
one of them is reachable.
"""

import inspect

from bubble_mcp.aria_runtime.bubble_cli import BubbleCLI


def test_create_page_registers_name_maps() -> None:
    source = inspect.getsource(BubbleCLI.create_page)

    assert '["_index", "page_name_to_id", name]' in source
    assert '["_index", "page_name_to_path", name]' in source


def test_create_page_refuses_duplicate_name() -> None:
    source = inspect.getsource(BubbleCLI.create_page)

    assert "self.discovery.find_page(name)" in source
    assert "already exists" in source


def test_delete_page_clears_name_maps() -> None:
    source = inspect.getsource(BubbleCLI.delete_page)

    assert '["_index", "page_name_to_id", page_display_name]' in source
    assert '["_index", "page_name_to_path", page_display_name]' in source
