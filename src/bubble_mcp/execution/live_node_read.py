"""Read a node from the running Bubble editor's own memory.

The raw encoding of Bubble expressions is not derivable from the .bubble export - the export
is the decoded projection and the decoding happens server-side - so the only source of truth
is the tree the editor holds in the page. ``window.appquery`` exposes it. Everything here is
built so the page call is one injectable function: tests pass a fake and never open a browser.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Sequence

from bubble_mcp.core.config import load_settings
from bubble_mcp.sessions.store import load_session


DEFAULT_READ_TIMEOUT_SEC = 90
EDITOR_URL_TEMPLATE = "https://bubble.io/page?name=index&id={app_id}&version={app_version}"
APPQUERY_READY_SCRIPT = "() => typeof window.appquery !== 'undefined'"

Evaluator = Callable[[str], Any]


class PlaywrightMissing(RuntimeError):
    """Raised when the browser extra is not installed."""


class EditorNotReady(RuntimeError):
    """Raised when the editor page never exposes window.appquery."""


class BrowserProfileMissing(RuntimeError):
    """Raised when the profile has no persistent browser profile directory to drive."""


def build_appquery_script(pointer: Sequence[str]) -> str:
    """Return the page script that reads the node at ``pointer`` out of editor memory."""

    segments = [str(part) for part in pointer]
    if not segments:
        raise ValueError(
            "pointer must name at least one child: app.raw() on the root is refused by Bubble "
            "'for performance reasons'"
        )
    if any(not part for part in segments):
        raise ValueError("pointer segments must be non-empty")
    chain = "".join(f"._child({json.dumps(part)})" for part in segments)
    return f"() => window.appquery.app().json{chain}.raw()"


def _resolve_app_id(profile: str, app_id: str | None) -> str:
    explicit = str(app_id or "").strip()
    if explicit:
        return explicit
    session = load_session(profile)
    from_session = str(getattr(session, "app_id", "") or "").strip()
    if from_session:
        return from_session
    raise ValueError(f"No app id for profile '{profile}'; pass app_id explicitly.")


def _playwright_evaluator(
    *, profile: str, app_id: str, app_version: str, headless: bool, timeout_sec: int
) -> Evaluator:
    """Return an evaluator that runs one script in the editor page for this app."""

    def evaluate(script: str) -> Any:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # noqa: BLE001 - re-raised as a typed, reportable failure
            raise PlaywrightMissing(
                "Playwright is required to read the live editor. Install with: "
                'python -m pip install "befree-bubble-mcp[browser]" '
                "&& python -m playwright install chromium"
            ) from exc

        settings = load_settings()
        user_data_dir = settings.config_dir / "browser-profiles" / profile
        if not user_data_dir.exists():
            # launch_persistent_context would happily create an empty profile, load a logged-out
            # bubble.io, and only fail 90s later as editor_not_ready. bubble_session_import stores
            # a valid session without ever populating this directory.
            raise BrowserProfileMissing(
                f"No browser profile at {user_data_dir}. The live editor read drives a real "
                f"browser session, which only bubble_session_login creates; run "
                f"bubble_session_login for profile '{profile}' first (an imported session is not "
                "enough)."
            )
        url = EDITOR_URL_TEMPLATE.format(app_id=app_id, app_version=app_version)
        timeout_ms = timeout_sec * 1000
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(user_data_dir), headless=headless
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    page.wait_for_function(APPQUERY_READY_SCRIPT, timeout=timeout_ms)
                except Exception as exc:  # noqa: BLE001 - Playwright raises its own timeout type
                    raise EditorNotReady(
                        f"window.appquery never appeared on {url} within {timeout_sec}s"
                    ) from exc
                return page.evaluate(script)
            finally:
                context.close()

    return evaluate


def read_live_node(
    profile: str,
    pointer: Sequence[str],
    *,
    evaluator: Evaluator | None = None,
    app_id: str | None = None,
    app_version: str = "test",
    headless: bool = True,
    timeout_sec: int = DEFAULT_READ_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Read the node at ``pointer`` from the live editor, as a structured result."""

    segments = [str(part) for part in pointer]
    script = build_appquery_script(segments)
    resolved_app_id = _resolve_app_id(profile, app_id)
    run = evaluator or _playwright_evaluator(
        profile=profile,
        app_id=resolved_app_id,
        app_version=app_version,
        headless=headless,
        timeout_sec=timeout_sec,
    )
    try:
        node = run(script)
    except PlaywrightMissing as exc:
        return {"ok": False, "error": "playwright_missing", "pointer": segments, "message": str(exc)}
    except EditorNotReady as exc:
        return {"ok": False, "error": "editor_not_ready", "pointer": segments, "message": str(exc)}
    except BrowserProfileMissing as exc:
        return {
            "ok": False,
            "error": "browser_profile_missing",
            "pointer": segments,
            "message": str(exc),
        }
    except Exception as exc:  # noqa: BLE001 - any browser failure must reach the caller as a result
        return {
            "ok": False,
            "error": "evaluator_failed",
            "pointer": segments,
            "message": f"{type(exc).__name__}: {exc}",
        }

    if node is None:
        return {
            "ok": False,
            "error": "pointer_not_found",
            "pointer": segments,
            "message": (
                f"window.appquery returned nothing for pointer '{'.'.join(segments)}'. Check the "
                "pointer against the app tree; a wrong segment reads as an absent node, not as an "
                "error."
            ),
        }
    if not isinstance(node, dict):
        return {
            "ok": False,
            "error": "unexpected_node_shape",
            "pointer": segments,
            "message": f"Expected a node object at '{'.'.join(segments)}', got {type(node).__name__}.",
        }
    return {"ok": True, "pointer": segments, "node": node, "app_id": resolved_app_id}
