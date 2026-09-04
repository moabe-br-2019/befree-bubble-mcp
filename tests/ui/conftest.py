"""Fixtures for UI tests that drive a real Bubble app in a browser.

These tests are not part of the unit suite's contract: they need the network, a running Bubble
app, and the preview credentials. They skip themselves - rather than fail - whenever any of
those is missing, so `pytest` with no configuration still runs green on a laptop with no
credentials and no Playwright install.

Two things about the target app decide whether a UI test can address an element at all:

* the app must have "expose the option to add an ID attribute" ON
  (`settings.client_safe.advanced_features.expose_id_option`), and
* each element under test must carry an ID attribute (`%p.unique_id` in the app tree, written
  by the MCP as `html_id`).

Without both, Playwright falls back to text and auto-generated class names, which Bubble
regenerates on deploy - the test passes once and rots. `bubble_mcp` writes both, so the setup
is a tool call, not manual editor work.

A Bubble version with preview password protection answers HTTP Basic (401
`WWW-Authenticate: Basic`), which is why the context carries `http_credentials` rather than
filling a login form. Credentials come from the environment and are never written to a file
in the repository.

That protection is ON BY DEFAULT for apps on Bubble's AGENCY plan, and an owner on any plan
may switch it on to keep strangers off a work-in-progress version. Bubble seeds it with the
literal pair `username` / `password`, which owners frequently leave alone - it is a wall
against passers-by rather than a secret. So a 401 here usually means the environment is unset
rather than that the pair is unknown: try the defaults, or ask the app owner. It gates the
preview version, not the app's own user accounts.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from typing import Any

import pytest


APP_ID_ENV = "BUBBLE_UI_APP_ID"
VERSION_ENV = "BUBBLE_UI_APP_VERSION"
USER_ENV = "BUBBLE_PREVIEW_USER"
PASSWORD_ENV = "BUBBLE_PREVIEW_PW"
PROFILE_ENV = "BUBBLE_UI_PROFILE"
RUN_AS_USER_ENV = "BUBBLE_UI_RUN_AS_USER_ID"

PAGE_ENV = "BUBBLE_UI_PAGE"
IDS_ENV = "BUBBLE_UI_ELEMENT_IDS"

# No app, profile or page is hardcoded. This suite ships with the server, so a default naming
# somebody's app would point every other installation at a Bubble app its owner cannot reach -
# and the skip message would read as a broken test rather than as unconfigured.
DEFAULT_VERSION = "version-test"


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


@pytest.fixture(scope="session")
def ui_base_url() -> str:
    """Root URL of the Bubble version under test, without a trailing slash.

    ``version-test`` is a path segment on the app's own domain; the live version has no such
    segment, so an empty ``BUBBLE_UI_APP_VERSION`` addresses live.
    """

    app_id = _env(APP_ID_ENV)
    if not app_id:
        pytest.skip(f"Set {APP_ID_ENV} to the Bubble app these UI tests should drive.")
    version = _env(VERSION_ENV, DEFAULT_VERSION)
    root = f"https://{app_id}.bubbleapps.io"
    return f"{root}/{version}" if version else root


@pytest.fixture(scope="session")
def preview_credentials() -> dict[str, str]:
    """HTTP Basic credentials for a password-protected Bubble version.

    Skips the test when unset: a UI run without them gets a 401 and an empty DOM, which reads
    as a broken page rather than as missing configuration.
    """

    username = _env(USER_ENV)
    password = _env(PASSWORD_ENV)
    if not username or not password:
        pytest.skip(f"Set {USER_ENV} and {PASSWORD_ENV} to run the Bubble UI tests.")
    return {"username": username, "password": password}


@pytest.fixture(scope="session")
def _playwright() -> Iterator[Any]:
    playwright_api = pytest.importorskip(
        "playwright.sync_api",
        reason='Install with: pip install "befree-bubble-mcp[browser]" && playwright install chromium',
    )
    with playwright_api.sync_playwright() as playwright:
        yield playwright


@pytest.fixture(scope="session")
def _browser(_playwright: Any) -> Iterator[Any]:
    headed = _env("BUBBLE_UI_HEADED").lower() in {"1", "true", "yes"}
    try:
        browser = _playwright.chromium.launch(headless=not headed)
    except Exception as exc:  # playwright.Error has no stable import path here
        # The package installs without the browser binaries, and they are per-interpreter: a
        # `playwright install` run against a different Python does not help this one. That is
        # missing setup, not a failing app, so skip with the command that fixes it.
        message = str(exc)
        if "Executable doesn't exist" in message or "playwright install" in message:
            pytest.skip(f"Chromium is not installed for {sys.executable}: run '{sys.executable}' -m playwright install chromium")
        raise
    try:
        yield browser
    finally:
        browser.close()


@pytest.fixture
def ui_page(_browser: Any, preview_credentials: dict[str, str]) -> Iterator[Any]:
    """A fresh ANONYMOUS page whose context answers the preview's Basic auth challenge.

    One context per test, so no test inherits another's cookies or logged-in user - the whole
    point of these tests is often WHICH user is logged in. For a logged-in one, use
    ``page_as_user``.
    """

    context = _browser.new_context(http_credentials=preview_credentials)
    page = context.new_page()
    try:
        yield page
    finally:
        context.close()


@pytest.fixture
def page_as_user(
    _browser: Any, preview_credentials: dict[str, str], ui_profile: str
) -> Iterator[Any]:
    """Factory: ``page_as_user(user_id)`` returns a page logged in as that app user.

    This is the half that makes an E2E test possible rather than a public-page smoke test.
    ``bubble_run_as`` establishes the session the way the editor's Run as does and writes a
    Playwright ``storage_state``; here that state is loaded into a fresh context, so the app
    treats the page as that user with no editor open and nothing to click.

    The session is established once per call and the contexts are closed at teardown. A test
    may ask for several users to check that they see different things.
    """

    from bubble_mcp.execution.run_as import run_as_user

    contexts: list[Any] = []

    def _open(user_id: str, *, page: str = "index", app_version: str | None = None) -> Any:
        result = run_as_user(
            ui_profile,
            user_id,
            page=page,
            app_id=_env(APP_ID_ENV),
            app_version=(app_version or _env(VERSION_ENV, DEFAULT_VERSION)).replace("version-", "")
            or "test",
            preview_username=preview_credentials["username"],
            preview_password=preview_credentials["password"],
        )
        if not result.get("ok"):
            # A missing editor session or a rejected preview password is setup, not a failing
            # app: skipping says so without a red suite that hides real regressions.
            if result.get("error") in {"not_logged_in", "preview_password_required"}:
                pytest.skip(str(result.get("message")))
            pytest.fail(f"bubble_run_as could not impersonate {user_id}: {result.get('message')}")

        context = _browser.new_context(
            http_credentials=preview_credentials,
            storage_state=result["storage_state_path"],
        )
        contexts.append(context)
        return context.new_page()

    try:
        yield _open
    finally:
        for context in contexts:
            context.close()


@pytest.fixture(scope="session")
def ui_profile() -> str:
    """The bubble-mcp profile whose stored editor session mints the impersonation."""

    profile = _env(PROFILE_ENV)
    if not profile:
        pytest.skip(f"Set {PROFILE_ENV} to the bubble-mcp profile whose editor session to use.")
    return profile


@pytest.fixture(scope="session")
def run_as_user_id() -> str:
    """The app user an E2E test logs in as, as a Bubble unique id.

    There is no lookup from an email yet, so the id is configuration. Tests that need a
    logged-in user skip without it rather than silently testing the anonymous page.
    """

    user_id = _env(RUN_AS_USER_ENV)
    if not user_id:
        pytest.skip(
            f"Set {RUN_AS_USER_ENV} to the Bubble unique id of the user these tests log in as."
        )
    return user_id


@pytest.fixture(scope="session")
def ui_page_name() -> str:
    """Which page of the app under test these tests load."""

    page = _env(PAGE_ENV)
    if not page:
        pytest.skip(f"Set {PAGE_ENV} to the page these UI tests should open.")
    return page


@pytest.fixture(scope="session")
def expected_element_ids() -> tuple[str, ...]:
    """The ID attributes that must reach the DOM, comma-separated in the environment.

    Which elements matter is a property of the app under test, not of this server, so the list
    is configuration. Without it there is nothing to assert and the test says so.
    """

    raw = _env(IDS_ENV)
    ids = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not ids:
        pytest.skip(
            f"Set {IDS_ENV} to a comma-separated list of ID attributes expected on the page."
        )
    return ids
