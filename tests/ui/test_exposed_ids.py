"""The contract every other UI test rests on: IDs written by the MCP reach the rendered page.

`html_id` on an element tool writes `%p.unique_id` into the app tree, and the app-level
`expose_id_option` setting is what makes Bubble emit it as a real `id` attribute in run mode.
Both are writes into Bubble, so both can silently stop being true - a restored savepoint, a
branch merge, someone toggling the setting back. When that happens every selector in this
directory breaks at once, and the failure looks like "the button moved" rather than "the app
stopped exposing IDs".

This test names that cause directly, so it fails first and explains the rest.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture
def loaded_page(ui_page: Any, ui_base_url: str, ui_page_name: str) -> Any:
    response = ui_page.goto(
        f"{ui_base_url}/{ui_page_name}", wait_until="domcontentloaded", timeout=60_000
    )
    assert response is not None, "no response from the Bubble app"
    assert response.status == 200, (
        f"expected 200 from {response.url}, got {response.status}. "
        "A 401 means the preview password protection rejected the credentials in the environment."
    )
    return ui_page


def test_every_exposed_id_reaches_the_dom(
    loaded_page: Any, expected_element_ids: tuple[str, ...]
) -> None:
    # Bubble renders client-side, so the first ID has to be waited for; once one element is
    # painted the rest of the page's initial tree is there too.
    loaded_page.wait_for_selector(f"#{expected_element_ids[0]}", timeout=30_000)

    missing = loaded_page.evaluate(
        "ids => ids.filter(id => document.getElementById(id) === null)",
        list(expected_element_ids),
    )
    assert missing == [], (
        f"these IDs never reached the DOM: {missing}. Check that the app still has "
        "expose_id_option ON (project setting 'advanced-expose-id-option') and that the "
        "elements still carry their ID attribute."
    )


def test_the_first_expected_id_addresses_something_visible(
    loaded_page: Any, expected_element_ids: tuple[str, ...]
) -> None:
    # Existing in the DOM is not the same as being the element people see. This checks the
    # first configured ID actually resolves to a rendered node.
    first = loaded_page.locator(f"#{expected_element_ids[0]}")
    first.wait_for(timeout=30_000)
    assert first.is_visible(), f"#{expected_element_ids[0]} exists but is not visible"
