# Plugin Authoring — Phase 1 (Capture) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record every request the Bubble plugin editor makes, so the save/load format can be documented and Phase 2 (pull/push/create tools) planned against real data.

**Architecture:** The Chrome extension gains a plugin-editor discovery capture (active only on `bubble.io/plugin_editor*`) that forwards every fetch/XHR with request and response to a new companion endpoint, which appends to its own JSONL log. A CLI command summarizes that log and exports redacted samples. The owner then runs a scripted capture session on a throwaway plugin `mcp-test-plugin`, and the findings are written to `docs/capture-plugin-editor.md`.

**Tech Stack:** Python ≥3.11 (stdlib HTTP server, argparse), Chrome MV3 extension (plain JS), pytest, ruff, mypy, `node --test`.

**Spec:** `docs/superpowers/specs/2026-11-25-plugin-authoring-design.md`

## Global Constraints

- Python `>=3.11` (local machine has 3.14; venv at `.venv`). Node `>=20`.
- Extension talks only to `http://127.0.0.1:<port>`; no remote relay, no account auth.
- Existing app-editor capture (`/v1/bubble/crawler/ingest`, `/v1/bubble/crawler/write-ingest`) must behave exactly as before.
- Never send a plugin **publish** request, from code or during the capture session.
- During development, the owner's real plugins are only read; all writes go to `mcp-test-plugin`.
- Capture data stays local; anything committed to `docs/` passes `redact_sensitive` (`src/bubble_mcp/core/redaction.py`).
- Work on branch `feat/plugin-authoring`; tests `pytest`, `ruff check`, `mypy src`, `npm test` stay green.

## Review Focus

1. **Huge responses** (the plugin catalogue call is ~42 MB) — capture must truncate the response body above 4 MB and mark `responseTruncated: true`, never exceed the companion's 10 MB request limit or freeze the tab. → Task 2 test `discovery truncates large bodies`.
2. **Non-JSON bodies** (FormData, URLSearchParams, plain text, Blob) — recorded as entries / raw text / `"[binary]"`, never throws. → Task 2 test `discovery serializes non-JSON bodies`.
3. **Interceptor breaking the editor** — every discovery code path is wrapped so an exception never rejects the editor's own fetch or changes its response. → Task 2 test `discovery never alters the editor request`.
4. **Companion offline** — editor keeps working; background counts an error and logs `local_offline`. Covered by existing `postLocal`; Task 2 reuses it (asserted in Task 2's static test).
5. **App-editor noise** — discovery must not fire on `bubble.io/page?...` (the app editor). → Task 2 test `discovery only on plugin_editor`.

---

### Task 1: Companion discovery endpoint

**Files:**
- Modify: `src/bubble_mcp/extension_companion.py`
- Test: `tests/unit/test_extension_companion.py`
- Also commit: `docs/superpowers/specs/2026-11-25-plugin-authoring-design.md`, this plan

**Interfaces:**
- Produces:
  - Endpoint `POST /v1/bubble/plugin-editor/discovery-ingest` (same capture-key check, same `_ping` handling as existing ingest paths).
  - `default_discovery_log_path() -> Path` = `extension_companion_dir() / "plugin-editor-discovery.jsonl"`.
  - `ExtensionCompanionConfig.discovery_log_path: Path | None = None`.
  - `record_extension_companion_event("discovery", payload, config)` appends one line to the discovery log (not `events.jsonl`) with keys: `received_at`, `kind="discovery"`, `plugin_id` (from `payload["pluginId"]`), `method`, `endpoint` (from `payload["url"]`), `status`, `payload`. Tool-authoring routing is skipped for this kind.
  - `/health` and `companion_status_payload` include `"discovery_log": str(path)`.

- [ ] **Step 1: Setup.** `git checkout -b feat/plugin-authoring`; `python3 -m venv .venv && .venv/bin/python scripts/install_local.py --extras browser,dev`; run `.venv/bin/pytest tests/unit/test_extension_companion.py -q` → PASS (baseline).

- [ ] **Step 2: Write failing test** `test_extension_companion_records_plugin_editor_discovery`:

```python
status, body = _json_request(base_url, "/v1/bubble/plugin-editor/discovery-ingest", method="POST",
    headers={CAPTURE_KEY_HEADER: "dev-key"},
    body={"pluginId": "1700000000000x1", "url": "https://bubble.io/appeditor/write",
          "method": "POST", "status": 200, "requestBody": {"a": 1}, "responseData": {"ok": True}})
assert status == HTTPStatus.OK and body["kind"] == "discovery"
lines = discovery_log.read_text().splitlines()
saved = json.loads(lines[0])
assert saved["kind"] == "discovery"
assert saved["plugin_id"] == "1700000000000x1"
assert saved["method"] == "POST" and saved["status"] == 200
assert saved["endpoint"] == "https://bubble.io/appeditor/write"
assert not event_log.exists()   # app event log untouched
```
Plus: same POST without the key → `403 capture_key_rejected`; `/health` contains `discovery_log`.

- [ ] **Step 3: Run** `.venv/bin/pytest tests/unit/test_extension_companion.py -q` → new test FAILS (404).
- [ ] **Step 4: Implement** the interfaces above; `kind` chosen by path: `discovery-ingest` → `"discovery"`.
- [ ] **Step 5: Run** the test file, then `.venv/bin/ruff check src tests && .venv/bin/mypy src` → PASS.
- [ ] **Step 6: Commit** `feat: companion endpoint for plugin editor discovery captures` (include spec + plan).

---

### Task 2: Extension plugin-editor discovery capture

**Files:**
- Modify: `chrome-extension/content.js`, `chrome-extension/bridge.js`, `chrome-extension/background.js`, `chrome-extension/popup.js` (stats line only), `chrome-extension/README.md`
- Test: `test-node/chrome-extension-discovery.test.mjs`

**Interfaces:**
- Consumes: Task 1 endpoint.
- Produces:
  - content.js message type `__befree_bubble_mcp_plugin_discovery__`; bridge relays it as `BUBBLE_PLUGIN_DISCOVERY_CAPTURED`; background `handleCaptured('discovery', payload)` → `postLocal(discoveryIngestUrl, payload)`, `stats.discovery += 1`.
  - Payload: `{ pluginId, pageUrl, url, method, status, requestBody, responseData, responseTruncated, capturedAt }`.
  - Pure helpers in content.js, exported for tests via `globalThis.__befreeDiscovery = { isPluginEditorPage, serializeBody, truncateBody }` **only** when `globalThis.__BEFREE_TEST__` is set:
    - `isPluginEditorPage(href: string) -> boolean`: `new URL(href).hostname === 'bubble.io'` (or `*.bubble.io`) and `pathname.startsWith('/plugin_editor')`.
    - `serializeBody(body) -> any`: string → `JSON.parse` or raw string; `FormData`/`URLSearchParams` → `Object.fromEntries`; `Blob`/`ArrayBuffer` → `"[binary]"`; null → null.
    - `truncateBody(text: string, limit = 4_000_000) -> { value, truncated }`: value is parsed JSON when under limit and parseable, else raw text cut to `limit`.

Behavior: when `isPluginEditorPage(location.href)`, the fetch and XHR wrappers record **every** request (any method, any host except `127.0.0.1`/`localhost`), skipping GETs whose path ends in `.js|.css|.png|.jpg|.svg|.woff2?|.ico`. Existing structure/write interception runs unchanged alongside. Discovery uses `response.clone().text()` and swallows all errors.

- [ ] **Step 1: Write failing tests** in `test-node/chrome-extension-discovery.test.mjs` — load `content.js` text into `node:vm` with a stub `window`/`document`/`XMLHttpRequest`/`fetch` and `__BEFREE_TEST__ = true`:
  - `discovery only on plugin_editor`: `isPluginEditorPage('https://bubble.io/plugin_editor?id=x')` true; `'https://bubble.io/page?id=app'` false; `'https://evil.com/plugin_editor'` false.
  - `discovery serializes non-JSON bodies`: `serializeBody('{"a":1}')` deep-equals `{a:1}`; `serializeBody('x=1')` === `'x=1'`; URLSearchParams `a=1` → `{a:'1'}`; `new Blob(['z'])` → `'[binary]'`.
  - `discovery truncates large bodies`: `truncateBody('x'.repeat(10), 4)` → `{ value: 'xxxx', truncated: true }`; `truncateBody('{"a":1}')` → `{ value: {a:1}, truncated: false }`.
  - `discovery never alters the editor request` (static): content.js discovery code sits inside `try {` / `.catch(() => {})` and the fetch wrapper `return response;` is unconditional — assert with regexes as the existing security test does.
  - Static: bridge.js allow-list contains `__befree_bubble_mcp_plugin_discovery__` and still checks `event.origin !== window.location.origin`; background.js contains `/v1/bubble/plugin-editor/discovery-ingest` and routes it through `postLocal`.
- [ ] **Step 2: Run** `npm test` → new tests FAIL.
- [ ] **Step 3: Implement** the interfaces above. Popup shows `Discovery: N` from `stats.discovery`. README gets a "Plugin editor discovery" section: what is captured, where the log lives, that it is on only on `plugin_editor` pages.
- [ ] **Step 4: Run** `npm test` → PASS (old security tests included).
- [ ] **Step 5: Commit** `feat: capture every plugin editor request in discovery mode`.

---

### Task 3: `bubble-mcp plugin discovery` summarize/export

**Files:**
- Create: `src/bubble_mcp/plugin_authoring/__init__.py`, `src/bubble_mcp/plugin_authoring/discovery.py`
- Modify: `src/bubble_mcp/cli/main.py` (subcommand under existing `plugin` parser, ~line 2271)
- Test: `tests/unit/test_plugin_discovery.py`

**Interfaces:**
- Consumes: discovery log format from Task 1.
- Produces:
  - `endpoint_signature(method: str, url: str) -> str` — `"POST /appeditor/write"`; path segments that look like ids (`^\d{10,}x\d+$`, hex ≥16, pure digits ≥6) become `{id}`; query string dropped.
  - `summarize_discovery_log(path: Path, *, plugin_id: str | None = None, since: str | None = None) -> list[dict]` — one row per signature: `signature`, `count`, `statuses` (sorted unique), `first_seen`, `last_seen`, `request_keys` (union of top-level keys), `response_keys`.
  - `export_discovery_samples(path: Path, out_dir: Path, **filters) -> list[Path]` — per signature, writes `out_dir/<slug>.json` holding up to 5 events, each passed through `redact_sensitive`; returns written paths.
  - CLI: `bubble-mcp plugin discovery summary [--log PATH] [--plugin-id ID] [--since ISO]` prints JSON rows; `bubble-mcp plugin discovery export --out DIR [same filters]`. `--log` defaults to `default_discovery_log_path()`.

- [ ] **Step 1: Write failing tests:**
  - `endpoint_signature("POST", "https://bubble.io/appeditor/load_single_path/1700000000000x123/test/abc?x=1") == "POST /appeditor/load_single_path/{id}/test/abc"`.
  - Summary over a 3-line fixture log (two `POST /appeditor/write` with statuses 200/400, one `GET /appeditor/get_raw_plugin`) → 2 rows; write row `count == 2`, `statuses == [200, 400]`; `plugin_id` filter drops non-matching lines.
  - Export: event with `requestBody: {"cookie": "abc", "code": "x"}` → written file has `"[REDACTED]"` for `cookie`, keeps `code`; at most 5 samples per signature.
  - CLI: `main(["plugin", "discovery", "summary", "--log", str(log)])` exits 0 and prints JSON list.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/unit/test_plugin_discovery.py -q` → FAIL (import error).
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** the test file, `ruff check`, `mypy src` → PASS.
- [ ] **Step 5: Commit** `feat: summarize and export plugin editor discovery captures`.

---

### Task 4: Capture session and findings doc (owner + agent)

**Files:**
- Create: `docs/capture-plugin-editor.md`, `docs/capture-plugin-editor-*.json` (redacted exports)

This task is driven with the owner; the agent prepares, watches the log, and writes the doc.

- [ ] **Step 1: Prepare.** Owner loads `chrome-extension/` unpacked in Chrome (`chrome://extensions` → Developer mode → Load unpacked; reload if already loaded). Agent starts `.venv/bin/bubble-mcp extension companion serve --port 3847` in background and confirms the popup shows connected.
- [ ] **Step 2: Scripted session** on a new plugin `mcp-test-plugin` (owner clicks; agent checks `plugin discovery summary` grows after each block):
  1. Create the plugin from the account's plugin list. Open it.
  2. Add element `TestElement`; add one property of each type the dropdown offers (text, number, yes/no, dynamic value, list, dropdown/options, file, image, color, app type, …).
  3. Edit header, initialize, update, preview; add an exposed state and an event.
  4. Add client action `TestClientAction` with 2 fields and code.
  5. Add server action `TestServerAction` with a field, a return value, code, and an npm dependency (e.g. `lodash`); wait for the build to finish.
  6. Rename one element, one property, one action; then delete one of each.
  7. Reload the editor (load call). Open a second tab on the same plugin, edit in tab A, then edit in stale tab B (stale-tab behavior).
  8. Owner opens one of their real plugins read-only (load call only; no edits).
  **Do not press Publish.**
- [ ] **Step 3: Fill gaps.** For anything missing from the summary, the agent may drive the editor with Playwright on the stored session (no session on this machine yet → owner runs `! .venv/bin/bubble-mcp session login --profile plugins --app-id <any app id>` first; see `docs/capture-plugin-install.md` for the pattern), on `mcp-test-plugin` only.
- [ ] **Step 4: Export** `bubble-mcp plugin discovery export --plugin-id <test id> --out <scratch>`; review; copy representative redacted samples to `docs/capture-plugin-editor-*.json`.
- [ ] **Step 5: Write `docs/capture-plugin-editor.md`** answering, with sample references: plugin list call; create call; load call (editable state vs `get_raw_plugin`); save call(s) and payload per item kind (element, property, code, state, event, client action, server action, npm deps); id generation; rename/delete shape; npm build trigger and status polling; how (or whether) the editor detects stale tabs/concurrent edits.
- [ ] **Step 6: Commit** `docs: capture how the plugin editor loads and saves plugins`.

**Gate:** stop here. Phase 2 (`bubble_plugin_list/pull/push/create`, local folder layout, safety checks) gets its own plan written from `docs/capture-plugin-editor.md`.
