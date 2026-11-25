"""List, create and delete plugins through the bubble.io dashboard, in a headless browser.

These three operations have no replayable API: the plugin list is an encrypted elasticsearch
search, and create/delete are workflows of Bubble's own app (see
``docs/capture-plugin-editor.md``). So they drive ``bubble.io/home/plugins`` with Playwright, on
the stored browser profile and session cookies. Headless by default, so the user's own browser is
never touched.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

DASHBOARD_URL = "https://bubble.io/home/plugins"
PAGE_WAIT_MS = 2500
NAV_TIMEOUT_MS = 60_000

_READ_PAGE_JS = """() => {
  const items = [];
  for (const a of document.querySelectorAll('a[href*="plugin_editor"]')) {
    const id = new URL(a.href).searchParams.get('id');
    if (id) items.push({id, name: a.textContent.trim()});
  }
  const next = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'arrow_forward');
  return {items, hasNext: Boolean(next && !next.disabled)};
}"""

_CLICK_NEXT_JS = """() => {
  const next = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'arrow_forward');
  if (!next || next.disabled) return false;
  next.click();
  return true;
}"""

# Opens the "..." menu of the card whose link points at the plugin. The card is the smallest
# ancestor of the link that holds only that one plugin link; its menu is the card's only button
# (an icon with no text). Anything else is refused rather than guessed.
_OPEN_DELETE_JS = """(pluginId) => {
  const idOf = a => new URL(a.href).searchParams.get('id');
  const link = [...document.querySelectorAll('a[href*="plugin_editor"]')].find(a => idOf(a) === pluginId);
  if (!link) return 'card_not_found';
  for (let card = link.parentElement, i = 0; card && i < 8; card = card.parentElement, i++) {
    const ids = new Set([...card.querySelectorAll('a[href*="plugin_editor"]')].map(idOf));
    if (ids.size !== 1 || !ids.has(pluginId)) return 'ambiguous_card';
    const buttons = card.querySelectorAll('button');
    if (buttons.length === 1) { buttons[0].click(); return 'menu_opened'; }
    if (buttons.length > 1) return 'ambiguous_card';
  }
  return 'menu_not_found';
}"""

_CLICK_DELETE_ITEM_JS = """() => {
  const item = [...document.querySelectorAll('button')].find(b => b.textContent.trim().toLowerCase() === 'delete');
  if (!item) return false;
  if (item.disabled || item.getAttribute('aria-disabled') === 'true') return 'disabled';
  item.click();
  return true;
}"""

_MODAL_TEXT_JS = """() => {
  const heading = [...document.querySelectorAll('*')].find(el => el.childElementCount === 0 && el.textContent.trim() === 'Danger zone');
  let box = heading;
  for (let i = 0; i < 6 && box && !box.querySelector('input'); i++) box = box.parentElement;
  return box ? box.innerText : '';
}"""


class DashboardError(RuntimeError):
    """The dashboard did not behave as captured (layout change, not logged in, ...)."""


class DeleteDisabled(DashboardError):
    """Bubble greys out Delete for this plugin (seen live on some private plugins)."""


@contextlib.contextmanager
def open_dashboard(profile: str, *, headless: bool = True) -> Iterator[Any]:
    """Yield a Playwright page on ``bubble.io/home/plugins`` logged in as the stored session."""

    from bubble_mcp.core.config import load_settings
    from bubble_mcp.execution.live_node_read import (
        BrowserProfileMissing,
        PlaywrightMissing,
        _session_cookie_header,
        parse_cookie_header,
    )
    from bubble_mcp.sessions.store import load_session

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PlaywrightMissing(
            'Playwright is required for the plugin dashboard. Install with: python -m pip install "befree-bubble-mcp[browser]" && python -m playwright install chromium'
        ) from exc
    user_data_dir = load_settings().config_dir / "browser-profiles" / profile
    if not user_data_dir.exists():
        raise BrowserProfileMissing(f"No browser profile at {user_data_dir}; run bubble-mcp session login --profile {profile} first.")
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(str(user_data_dir), headless=headless)
        try:
            session = load_session(profile)
            if session is not None:
                cookies = parse_cookie_header(_session_cookie_header(session))
                if cookies:
                    context.add_cookies(cookies)  # type: ignore[arg-type]
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(DASHBOARD_URL, timeout=NAV_TIMEOUT_MS)
            page.wait_for_timeout(PAGE_WAIT_MS)
            if "/home/plugins" not in page.url:
                raise DashboardError(f"Not logged in to bubble.io (landed on {page.url}); run bubble-mcp session login --profile {profile}.")
            yield page
        finally:
            context.close()


def list_plugins(page: Any, *, max_pages: int = 50) -> list[dict[str, str]]:
    """Every plugin on the dashboard, following its pagination."""

    plugins: dict[str, dict[str, str]] = {}
    for _ in range(max_pages):
        state = page.evaluate(_READ_PAGE_JS)
        before = len(plugins)
        for item in state.get("items", []):
            plugins.setdefault(item["id"], {"id": item["id"], "name": item["name"]})
        if not state.get("hasNext") or (len(plugins) == before and before > 0):
            break
        if not page.evaluate(_CLICK_NEXT_JS):
            break
        page.wait_for_timeout(PAGE_WAIT_MS)
    return list(plugins.values())


def create_plugin(page: Any, name: str) -> str:
    """Create a plugin named ``name`` and return its id (from the editor URL it opens)."""

    page.get_by_role("button", name="Create a plugin").click()
    page.locator("text=Plugin name").locator("xpath=following::input[1]").fill(name)
    page.get_by_role("button", name="Create plugin").click()
    page.wait_for_url("**/plugin_editor?id=**", timeout=NAV_TIMEOUT_MS)
    plugin_id = str(parse_qs(urlparse(str(page.url)).query).get("id", [""])[0])
    if not plugin_id:
        raise DashboardError(f"Plugin created but no id in {page.url}")
    return plugin_id


def delete_plugin(page: Any, plugin_id: str, name: str) -> None:
    """Delete one plugin through the card menu and the "Danger zone" modal."""

    if not name.strip():
        raise DashboardError(f"Plugin {plugin_id} has no readable name; refusing to delete it blind.")
    page.get_by_placeholder("Search plugins").fill(name)
    page.wait_for_timeout(PAGE_WAIT_MS)
    opened = page.evaluate(_OPEN_DELETE_JS, plugin_id)
    if opened != "menu_opened":
        raise DashboardError(f"Cannot open the menu of plugin {plugin_id}: {opened}")
    page.wait_for_timeout(500)
    clicked = page.evaluate(_CLICK_DELETE_ITEM_JS)
    if clicked == "disabled":
        raise DeleteDisabled(f"Bubble does not allow deleting plugin {name!r} (its Delete item is disabled).")
    if not clicked:
        raise DashboardError("The plugin menu has no Delete item.")
    page.wait_for_timeout(1000)
    modal = str(page.evaluate(_MODAL_TEXT_JS) or "")
    # The dialog reads "...delete the plugin <name>? Apps using...": match the exact name.
    if f"the plugin {name}?" not in modal:
        raise DashboardError(f"The delete dialog does not name {name!r}; refusing to confirm.")
    page.locator("text=Danger zone").locator("xpath=following::input[1]").fill("DELETE")
    page.get_by_role("button", name="Delete plugin").click()
    page.wait_for_timeout(PAGE_WAIT_MS)


# ── profile-level wrappers (what the MCP tools and CLI call) ─────────────────


def list_plugins_for_profile(profile: str, *, headless: bool = True) -> dict[str, Any]:
    with open_dashboard(profile, headless=headless) as page:
        plugins = list_plugins(page)
    return {"ok": True, "profile": profile, "count": len(plugins), "plugins": plugins}


def create_plugin_for_profile(profile: str, name: str, *, execute: bool = False, headless: bool = True) -> dict[str, Any]:
    if not name.strip():
        raise ValueError("A plugin name is required.")
    if not execute:
        return {"ok": True, "executed": False, "would_create": name, "next_action": "Run again with execute=true to create it."}
    with open_dashboard(profile, headless=headless) as page:
        plugin_id = create_plugin(page, name)
    return {"ok": True, "executed": True, "plugin_id": plugin_id, "name": name, "next_action": "Pull it with plugin pull."}


def delete_plugin_for_profile(
    profile: str,
    plugin_id: str,
    *,
    execute: bool = False,
    confirm: bool = False,
    headless: bool = True,
) -> dict[str, Any]:
    if execute and not confirm:
        raise ValueError("Deleting a plugin cannot be undone from the UI; it requires confirm=true when execute=true.")
    with open_dashboard(profile, headless=headless) as page:
        match = next((p for p in list_plugins(page) if p["id"] == plugin_id), None)
        if match is None:
            return {"ok": False, "reason": "not_found", "plugin_id": plugin_id}
        if not execute:
            return {
                "ok": True,
                "executed": False,
                "would_delete": match,
                "next_action": "Bubble marks the plugin deleted; apps already using it keep working, new apps cannot install it, and the UI offers no undo. Run again with execute=true and confirm=true.",
            }
        before = {p["id"] for p in list_plugins(page)}
        page.goto(DASHBOARD_URL, timeout=NAV_TIMEOUT_MS)
        page.wait_for_timeout(PAGE_WAIT_MS)
        try:
            delete_plugin(page, plugin_id, match["name"])
        except DeleteDisabled as exc:
            return {"ok": False, "executed": False, "reason": "delete_disabled", "plugin_id": plugin_id, "message": str(exc)}
        page.goto(DASHBOARD_URL, timeout=NAV_TIMEOUT_MS)
        page.wait_for_timeout(PAGE_WAIT_MS)
        after = {p["id"] for p in list_plugins(page)}
    if plugin_id in after:
        return {"ok": False, "executed": True, "reason": "still_listed", "plugin_id": plugin_id}
    collateral = sorted(before - after - {plugin_id})
    if collateral:
        return {"ok": False, "executed": True, "reason": "other_plugins_missing", "missing": collateral}
    return {"ok": True, "executed": True, "deleted": match}
