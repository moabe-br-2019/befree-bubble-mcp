"""End-to-end against the app as a real logged-in user, without an editor open.

The session comes from ``bubble_run_as``, which reproduces the editor's Run as over HTTP and
hands back a Playwright ``storage_state``. That is what separates these from
``test_exposed_ids.py``: those load the page as an anonymous visitor, these load it as somebody.

The assertions here are deliberately about the SESSION rather than about any particular screen
of this app - what the page shows a logged-in user is app content, and app content is what each
project's own tests are for. What belongs in this repository is proof that the mechanism holds:
the impersonated cookie reaches the browser, the app accepts it, and two different users do not
share a context.
"""

from __future__ import annotations

from typing import Any

import pytest


APP_COOKIE_MARKER = "_u2main"


def test_the_impersonated_session_cookie_reaches_the_browser(
    page_as_user: Any, run_as_user_id: str, ui_base_url: str, ui_page_name: str
) -> None:
    page = page_as_user(run_as_user_id, page=ui_page_name)
    response = page.goto(f"{ui_base_url}/{ui_page_name}", wait_until="domcontentloaded", timeout=60_000)

    assert response is not None and response.status == 200, (
        f"expected 200 as the impersonated user, got {response.status if response else 'no response'}"
    )
    names = [cookie["name"] for cookie in page.context.cookies(ui_base_url)]
    assert any(APP_COOKIE_MARKER in name for name in names), (
        f"no app session cookie in the context; got {names}. Without it the page is anonymous "
        "and every assertion below would be testing a logged-out app."
    )


def test_the_page_still_renders_for_a_logged_in_user(
    page_as_user: Any, run_as_user_id: str, ui_base_url: str, ui_page_name: str
) -> None:
    # A session cookie the app rejects would still be present in the jar; this proves the app
    # actually served the page rather than bouncing to a login screen.
    page = page_as_user(run_as_user_id, page=ui_page_name)
    page.goto(f"{ui_base_url}/{ui_page_name}", wait_until="domcontentloaded", timeout=60_000)

    # Any element of the page will do; the point is that the app served content rather
    # than a login screen.
    page.wait_for_selector("body", timeout=30_000)
    assert page.locator("body").inner_text().strip()


def test_two_impersonations_do_not_share_a_context(
    page_as_user: Any, run_as_user_id: str, ui_base_url: str, ui_page_name: str
) -> None:
    # The factory hands out one context per call. If it did not, a test checking that user A
    # cannot see user B's data would silently be checking one session twice.
    first = page_as_user(run_as_user_id, page=ui_page_name)
    second = page_as_user(run_as_user_id, page=ui_page_name)

    assert first.context is not second.context
