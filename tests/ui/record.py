"""Open a recording browser against a Bubble app and let Playwright write the test for you.

`playwright codegen` does almost this, but it has no way to answer HTTP Basic - and a Bubble
version with preview password protection challenges with Basic before any page renders, so
codegen alone lands on a 401 and records nothing. This launcher builds the context itself,
with `http_credentials` from the environment, and then calls `page.pause()`, which opens the
same Playwright Inspector codegen uses. Press Record there and drive the app; copy the
generated code into a test beside this file.

It opens the profile's PERSISTENT browser directory - the one `bubble_session_login` already
uses - so the Bubble editor is already logged in. That is what makes "Run as" reachable: open
the editor, click Run as for a user, and Bubble opens the app in a NEW TAB. The recorder
follows it, because the Inspector records the whole browser context rather than a single tab.

Two operational constraints:

* Only one Chromium may hold a user-data-dir at a time. Close any other MCP browser session
  (a running `bubble_session_login`, a live node read) before recording.
* Whatever you type while recording is written into the generated code verbatim - a password
  typed into a login form ends up as a literal in the script. Move it to an environment
  variable before the file goes anywhere near git.

Usage:

    BUBBLE_PREVIEW_USER=... BUBBLE_PREVIEW_PW=... python tests/ui/record.py
    python tests/ui/record.py --editor          # start on the editor, for a Run as recording
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any


# Nothing here names a particular app: this ships with the server, and a default pointing at
# somebody's app would send every other installation at a Bubble app it cannot open.
DEFAULT_VERSION = "version-test"
DEFAULT_PAGE = "index"


def _config_dir() -> Path:
    configured = os.environ.get("BUBBLE_MCP_CONFIG_DIR", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".config" / "bubble-mcp"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", required=True, help="bubble-mcp profile whose browser directory to reuse")
    parser.add_argument("--app-id", required=True, help="Bubble app to open")
    parser.add_argument("--app-version", default=DEFAULT_VERSION, help="empty string targets the live version")
    parser.add_argument("--page", default=DEFAULT_PAGE)
    parser.add_argument(
        "--editor",
        action="store_true",
        help="open the Bubble editor instead of the app, to record a Run as flow into a new tab",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="log in to Bubble in this profile and save nothing else: no Inspector, no recording",
    )
    return parser.parse_args(argv)


def _start_url(args: argparse.Namespace) -> str:
    if args.editor:
        version = args.app_version.replace("version-", "") or "live"
        return f"https://bubble.io/page?id={args.app_id}&name={args.page}&version={version}"
    root = f"https://{args.app_id}.bubbleapps.io"
    return f"{root}/{args.app_version}/{args.page}" if args.app_version else f"{root}/{args.page}"


def main(argv: list[str]) -> int:
    args = _parse_args(argv)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            'Playwright is required. Install with: pip install "befree-bubble-mcp[browser]" '
            "&& python -m playwright install chromium",
            file=sys.stderr,
        )
        return 2

    username = os.environ.get("BUBBLE_PREVIEW_USER", "").strip()
    password = os.environ.get("BUBBLE_PREVIEW_PW", "").strip()
    credentials = {"username": username, "password": password} if username and password else None
    if credentials is None and not args.editor:
        # The editor is reached with the stored session, so it needs no Basic auth; the app's
        # protected version does. Say so now rather than after a blank 401 page.
        print(
            "No BUBBLE_PREVIEW_USER / BUBBLE_PREVIEW_PW in the environment. "
            "If this version has preview password protection, the page will answer 401.",
            file=sys.stderr,
        )

    user_data_dir = _config_dir() / "browser-profiles" / args.profile
    user_data_dir.mkdir(parents=True, exist_ok=True)
    url = _start_url(args)

    # flush=True throughout: this process then blocks in page.pause() for as long as the
    # recording lasts, and a buffered stdout would hold the instructions until it exits -
    # which is exactly when they stop being useful.
    print(f"Recording profile : {args.profile}", flush=True)
    print(f"Browser directory : {user_data_dir}", flush=True)
    print(f"Opening           : {url}", flush=True)
    print("Press Record in the Playwright Inspector, drive the app, then close the browser.", flush=True)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(user_data_dir),
            headless=False,
            http_credentials=credentials,
        )
        page = context.pages[0] if context.pages else context.new_page()

        # Every tab opened from here - "Run as" included - lands in this same context, so the
        # Inspector records it without any per-tab wiring.
        context.on("page", lambda new_page: print(f"new tab: {new_page.url}", flush=True))

        page.goto(url, wait_until="domcontentloaded")
        if args.login:
            # Logging in is deliberately NOT recorded. Bubble's login can bounce through tabs
            # of its own, and page.pause() holds the first page in particular - close that one
            # and the recorder dies mid-login. Worse, the Inspector would write whatever was
            # typed into the generated code as a literal. So this mode has no Inspector at all
            # and simply waits for the window to be closed, by which time the cookies are in
            # the profile directory and every later run starts logged in.
            print("Log in to Bubble - any number of tabs is fine - then close the browser.", flush=True)
            _wait_until_closed(context)
        else:
            page.pause()
        context.close()
    return 0


def _wait_until_closed(context: Any) -> None:
    closed = False

    def mark_closed() -> None:
        nonlocal closed
        closed = True

    context.on("close", lambda _=None: mark_closed())
    while not closed and context.pages:
        # Any page will do as a heartbeat; wait_for_timeout keeps Playwright's event loop
        # running, which a plain sleep would not.
        try:
            context.pages[0].wait_for_timeout(500)
        except Exception:
            break


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
