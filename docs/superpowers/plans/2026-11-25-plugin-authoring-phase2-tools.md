# Plugin Authoring — Phase 2 (Tools) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mirror a Bubble plugin into a local folder and back (`pull`/`push`), and list, create and delete plugins, as MCP tools and CLI commands.

**Architecture:**
- A plugin is one JSON document. It is read with `GET /appeditor/get_plugin?id=` and written whole with `POST /appeditor/save_plugin` (see `docs/capture-plugin-editor.md`).
- `plugin_authoring/folder.py` splits that document into code files plus JSON and rebuilds it losslessly. Secrets go to a git-ignored side file.
- `plugin_authoring/client.py` makes the HTTP calls with the stored session.
- `plugin_authoring/sync.py` does pull and push. Push has a dry-run diff, a conflict check against the last pull, the editor lock, and a read-back check after saving.
- The dashboard-only operations (list, create, delete) run in a headless Playwright page on the stored browser profile (`plugin_authoring/dashboard.py`), so they never touch the user's own Chrome.

**Tech Stack:** Python ≥3.11 (stdlib `urllib`, `json`, `hashlib`), Playwright (existing `browser` extra), pytest, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-11-25-plugin-authoring-design.md`. **Wire format:** `docs/capture-plugin-editor.md`. **Corpus findings (owner's 119 plugins):** summarized in Global Constraints and Review Focus below.

## Global Constraints

- **Never publish.** Never call "Submit a new version", GitHub sync, `set_plugin_price` or `set_authorized_apps`.
- **Writes go only to the plugin the user names.** Development and live tests use only `mcp_test_plugin` (`1795610633201x773816577862139900`). The owner's real plugins are only ever pulled, never written.
- **No closed schema.** Every key the tool does not understand passes through untouched. The corpus has legacy and undocumented keys: `doc`, `long_text`, `in_style`, `field_list`, `field_type_restriction`, `visibility`, `allow_blank`, `wrap_error`, `get_headers`, `doc_link`, `body`, `body_params`, `params`, `alpha_opt_in`, `designed_for`, `last_published_bv`, OAuth/basic auth fields, and stray `"0"`/`"1"`/`"2"` keys inside calls.
- **The round trip is identity on parsed objects.** `assemble(explode(doc)) == doc` must hold exactly:
  - list vs index-keyed object for `meta_data.categories`;
  - `null`s, empty `{}`;
  - numeric strings vs ints vs floats;
  - code text with no added or stripped trailing newline, tabs kept.

  Byte comparison against the raw GET is not a goal. The canonical on-disk JSON is `json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n"`.
- **Ids.** Three letters in base-52 over `A-Z` then `a-z` (`AAA`=0). `editor_counter` is the **last id issued**. The next id is `encode(max(editor_counter, max decoded id in doc) + 1)`, and `editor_counter` is set to that value. Ids are only unique per plugin among elements, fields, states, events, actions and calls. `return_value` and call-param ids may repeat, so identity is always the full path.
- **Secrets never go to git.** Values under these keys move to `<folder>/.bubble-plugin/secrets.json`, which is git-ignored, and are reinjected on push:
  - `plugin_api.private_key`, `private_key_test`, `appsecret`, `password`, `username`;
  - every param/header object `value` whose `private` is true or whose `visibility` is `"secret"`.
- **Preview first.** Write tools take `execute: bool` (default false = dry run). Delete also takes `confirm: bool`, required when `execute=true`. Result keys follow `ok`, `executed`, `next_action`.
- **Lint baseline.** The repo already has lint errors (ruff 502, mypy 30) and 12 failing unit tests. The gate is: no new errors in touched files, and no new failing tests. Run tests with `.venv/bin/python -m pytest`.
- **Branch:** `feat/plugin-authoring`.

## Review Focus

1. **Lossless round trip on real plugins.** The corpus round-trip test (Task 2) runs only when `BUBBLE_MCP_PLUGIN_CORPUS` points at a directory of `get_plugin` JSON files. The reviewer runs it against `scratchpad/plugin-corpus` (119 docs). Every doc must round-trip.
2. **Code files.** A `fn` with no trailing newline must not gain one, and a file must not lose one. `package.fn` may be `""` or invalid JSON and is kept as text. → Task 2 tests.
3. **Unsafe names.** Element and action `display` may contain `/`, non-ASCII or padding spaces, and an element and an action may share a name. Folder names are `<id>-<slug>` under separate `elements/` and `actions/` dirs. → Task 2 test.
4. **Push races.**
   - Remote changed since pull → refuse unless `force`.
   - Editor open (`plugin_editor_lock` → `held: false`) → refuse.
   - Read-back after save differs → `ok: false` with the diff.

   → Task 4 tests.
5. **Secrets.** A pulled folder must contain no secret value outside `.bubble-plugin/secrets.json`, and the folder's `.gitignore` must list `.bubble-plugin/secrets.json`. → Task 2 test.

---

### Task 1: Ids

**Files:**
- Create: `src/bubble_mcp/plugin_authoring/ids.py`
- Modify: `docs/capture-plugin-editor.md` (Ids section: the counter is the last id issued, the alphabet, and repeats in `return_value` and call params)
- Test: `tests/unit/test_plugin_ids.py`

**Interfaces — Produces:**
- `ALPHABET = "ABC…Zabc…z"` (52 chars).
- `encode_id(n: int) -> str` (3 chars; raises above 52³−1).
- `decode_id(s: str) -> int | None` (None for anything that is not 3 alphabet chars).
- `max_id_in(doc: dict) -> int` (walks every dict key anywhere in the doc).
- `allocate_id(doc: dict) -> str` (mutates `doc["editor_counter"]` to the new value and returns the id).

- [ ] Step 1: Tests.
  - `encode_id(0)=="AAA"`, `encode_id(11)=="AAL"`, `encode_id(26)=="AAa"`, `encode_id(52)=="ABA"`, `decode_id("AAb")==27`, `decode_id("0") is None`.
  - `allocate_id({"editor_counter":18,"plugin_elements":{"AAC":{}}})=="AAT"` and the counter becomes 19.
  - A doc with counter 7 and a `return_value` key `"AAQ"` (=16) yields `"AAR"`.
  - A doc without `editor_counter` and no ids yields `"AAB"`. Rule: start from max(−1, …)+1, but never return `AAA`, because Bubble reserves it. Assert ≥ 1.
- [ ] Step 2: Run, FAIL. Step 3: Implement. Step 4: PASS plus ruff/mypy on the new files. Step 5: Fix the doc's Ids section. Commit `feat: plugin id allocation matching the editor counter`.

---

### Task 2: Folder mirror (explode / assemble / secrets)

**Files:**
- Create: `src/bubble_mcp/plugin_authoring/folder.py`
- Test: `tests/unit/test_plugin_folder.py`, fixture `tests/fixtures/plugins/mcp_test_plugin.json`. The fixture is the redacted `raw` from `docs/capture-plugin-editor-save-plugin-full.json` with `[REDACTED]` values kept.

**Interfaces — Produces:**
- `explode(doc: dict) -> tuple[dict[str, str], dict]` returns `(files, secrets)`.
  - `files` maps relative POSIX path → text.
  - `secrets` is the side-file content as a JSON-able dict mapping a JSON-pointer path → value.
- `assemble(files: dict[str, str], secrets: dict) -> dict`.
- `write_folder(folder: Path, doc: dict) -> list[Path]` explodes, writes files, writes `.bubble-plugin/secrets.json` and a `.gitignore` containing it, and removes stale mirrored files from a previous pull. It never deletes files outside the mirrored set.
- `read_folder(folder: Path) -> dict` returns the assembled doc.
- `canonical_json(obj) -> str`.

**Layout** (the split rule set lives in one table in `folder.py`; everything not matched stays in the owning JSON):

```
plugin.json                                 # doc minus plugin_elements, plugin_actions, html_header.snippet
shared/header.html                          # html_header.snippet
elements/<id>-<slug>/element.json           # element minus code fns, headers.snippet, actions.*.code.fn
elements/<id>-<slug>/header.html            # headers.snippet
elements/<id>-<slug>/<initialize|update|preview|reset|rn_component>.js   # code.<k>.fn
elements/<id>-<slug>/states/<stateId>.init.js                          # states.<id>.initialization_code.fn
elements/<id>-<slug>/actions/<actId>-<slug>.js                         # actions.<id>.code.fn
actions/<id>-<slug>/action.json
actions/<id>-<slug>/<client|server>.js     # code.client.fn / code.server.fn
actions/<id>-<slug>/package.json           # code.package.fn, raw text (may be "" or invalid)
```

Rules:
- A missing key and an extracted key are different. Only keys present in the doc produce files.
- Siblings of `fn` (`add_debugger`, `invalid`, `invalid_package`) stay in the JSON. The JSON omits the `fn` key, and `assemble` re-adds it only when the file exists.
- Slug: NFKD, ASCII only, `[^A-Za-z0-9._-]+` → `-`, trimmed, at most 40 chars, `"x"` if empty.

Tests:
- `test_round_trip_fixture`: `assemble(*explode(doc)) == doc`.
- `test_code_trailing_newline_preserved`: one `fn` without and one with a trailing `\n`, plus a `package.fn` of `""`.
- `test_unsafe_names`: element display `"a/b é "` and an action with the same display → separate dirs `elements/AAC-a-b-e/` and `actions/AAD-a-b-e/`.
- `test_categories_list_and_object_preserved`.
- `test_secrets_split`:
  - after `write_folder`, grep every non-secrets file → no `"hunter2"`;
  - `.gitignore` contains `.bubble-plugin/secrets.json`;
  - `read_folder` restores the values.
- `test_unknown_keys_pass_through`: keys `wrap_error`, `"0"` in a call, and `visibility`.
- `test_corpus_round_trip`: skipped unless `BUBBLE_MCP_PLUGIN_CORPUS` is set. Every file round-trips through `write_folder` + `read_folder` in a tmp dir.

- [ ] Steps: tests → FAIL → implement → PASS. Then run `BUBBLE_MCP_PLUGIN_CORPUS=<scratchpad>/plugin-corpus .venv/bin/python -m pytest tests/unit/test_plugin_folder.py -q` → all 119 pass. Commit `feat: lossless local folder mirror for plugin documents`.

---

### Task 3: Plugin editor HTTP client

**Files:**
- Create: `src/bubble_mcp/plugin_authoring/client.py`
- Test: `tests/unit/test_plugin_client.py`

**Interfaces:**
- Consumes: `BubbleSessionData`, `build_editor_write_headers` (`execution/client.py:182`), and `HttpResponse` / `HttpTransport` (`execution/client.py:29,35`).
- Produces `class PluginEditorClient(transport: HttpTransport = default_http_transport, get_transport: GetTransport = default_get_transport, timeout: float = 80.0)` with these methods:
  - `get_plugin(plugin_id, session) -> dict`. Calls `GET https://bubble.io/appeditor/get_plugin?id=`. Raises `PluginEditorError` on a non-2xx status or an HTML body.
  - `save_plugin(plugin_id, raw, session) -> dict`. Calls `POST /appeditor/save_plugin` with `{"id", "raw"}`.
  - `mark_git_modified(plugin_id, session) -> dict`. Calls `update_plugin_meta_object` with `update_git_modified_status`.
  - `acquire_lock(plugin_id, holder, session) -> bool`. Calls `plugin_editor_lock` with `op: "acquire"` and returns `held`.
  - `build_package(plugin_id, package_json_string, session, node_version=22) -> dict`. Calls `create_action_s3_package`.

  `GetTransport = Callable[[str, dict[str, str], float], HttpResponse]`. Headers come from `build_editor_write_headers(session, {"appname": session.app_id})`; for GET, `content-type` is removed.

- [ ] Tests use fake transports that record `(url, body, headers)`:
  - each method hits the exact URL and body from the capture doc;
  - `get_plugin` raises on 401 and on an `<html` body;
  - `acquire_lock` returns False for `{"held": false}`;
  - the session cookie is present in the headers.
- [ ] FAIL → implement → PASS → commit `feat: plugin editor HTTP client`.

---

### Task 4: Pull and push

**Files:**
- Create: `src/bubble_mcp/plugin_authoring/sync.py`
- Test: `tests/unit/test_plugin_sync.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces:
  - `DEFAULT_PLUGINS_ROOT = Path.home()/"Projects"/"bubble-plugins"`; overridden by the env var `BUBBLE_MCP_PLUGINS_DIR`.
  - `pull_plugin(*, profile, plugin_id, folder: Path | None = None, client=None, session=None) -> dict`.
    - Default folder: `<root>/<slug(meta_data.name)>-<plugin_id[-6:]>`.
    - Writes the folder and `.bubble-plugin/base.json` (the canonical remote doc **with** secrets; the file is git-ignored too).
    - Returns `{ok, plugin_id, folder, files, element_count, action_count}`.
    - Refuses to overwrite local edits: if the folder exists and `read_folder(folder) != base.json`, it returns `ok: false, reason: "local_changes"` unless `force=True`.
  - `push_plugin(*, profile, folder: Path, execute=False, force=False, client=None, session=None) -> dict`:
    1. Load `base.json` and `local = read_folder(folder)`.
    2. Fetch `remote = get_plugin`. If `remote != base` and not `force`, return `ok: false, reason: "remote_changed", remote_diff`.
    3. Compute `changes = diff_documents(base, local)`. The list holds `{path, op: add|remove|change}`; code paths show line counts, not full text. No changes → `ok: true, changed: false`.
    4. Validate: warn (don't block) on `custom_data_<id>` / `custom_field_<id>` refs to missing ids. New elements and actions are added by creating a dir named `NEW-<slug>` (copied from an existing one). `read_folder` gives each one a fresh id from `allocate_id`, and push renames the dir to `<id>-<slug>` after a successful save. New fields, states and events inside a JSON file use the key `"NEW"`, `"NEW2"`, … and get ids the same way.
    5. `execute=False`: return `{ok: true, executed: false, changes, would_build_packages: [...]}`.
    6. `execute=True`, in order:
       - `acquire_lock(fresh holder = f"{int(time.time()*1000)}x{random 18 digits}")`; if `held` is false → `ok: false, reason: "editor_open"`;
       - for each action whose `package.fn` changed → `build_package`; on failure, abort before saving;
       - `save_plugin(local)`, then `mark_git_modified`;
       - `readback = get_plugin`;
       - if `readback != local` (ignoring `meta_data` keys whose local value is absent and remote is `None`) → `ok: false, reason: "readback_mismatch", diff`;
       - else write `readback` as the new `base.json` and return `ok: true, executed: true`.
  - `diff_documents(a: dict, b: dict) -> list[dict]`.

- **npm ruling:** the capture did not reveal where `package_hash` comes from.
  - Push keeps `package_hash` from the folder, sets `package_status: "out_of_date"` on changed packages, and does **not** fabricate a hash.
  - After `build_package` succeeds, it sets `package_status: "up_to_date"` only if the build response says `success: true`, keeping the old hash.
  - It reports `next_action: "open the action in the plugin editor and confirm the deployment package is up to date"`.
  - Task 7 verifies this live; if the editor needs a real hash, the open question goes back to the owner.
- [ ] Tests (fake client):
  - pull writes the folder and `base.json`; pulling over local edits is refused;
  - push with no changes → `changed: false`;
  - push dry-run lists a changed `update.js`;
  - `remote_changed` refusal, and `force` bypasses it;
  - `editor_open` refusal (lock false) does not call `save_plugin`;
  - a package change calls `build_package` before `save_plugin`; a build failure → no save;
  - a readback mismatch → `ok: false`;
  - a `NEW-widget` dir gets `allocate_id` and is renamed;
  - the dangling `custom_data_AEn` ref produces a warning only.
- [ ] FAIL → implement → PASS → commit `feat: pull and push plugins with conflict check, lock and read-back`.

---

### Task 5: Dashboard operations (headless Playwright)

**Files:**
- Create: `src/bubble_mcp/plugin_authoring/dashboard.py`
- Test: `tests/unit/test_plugin_dashboard.py`

**Interfaces:**
- Consumes: the stored browser profile `<config>/browser-profiles/<profile>`, plus `parse_cookie_header` and `_session_cookie_header` (`execution/live_node_read.py:298,329`). Follow the `_open_editor_page` steps 1–4, then call `page.goto` directly.
- Produces:
  - `open_dashboard(profile, *, headless=True) -> ContextManager[Page]`.
  - `list_plugins(page) -> list[dict]` returns `[{id, name, uses, updated}]`. It reads `a[href*="plugin_editor"]` per page and clicks the button whose text is `arrow_forward` until it is disabled or missing, as the capture did.
  - `create_plugin(page, name) -> str`. Clicks "Create a plugin", types the name, clicks "Create plugin", waits for the URL `plugin_editor?id=`, and returns the id.
  - `delete_plugin(page, plugin_id, name) -> None`. Opens the "⋯" menu of the card whose link has `id=<plugin_id>`, clicks Delete, types `DELETE`, and clicks "Delete plugin". It verifies the modal text contains `name` before confirming, then waits for the card to disappear.
  - Module-level wrappers `list_plugins_for_profile`, `create_plugin_for_profile`, `delete_plugin_for_profile(profile, plugin_id, *, execute, confirm)`. The delete wrapper requires `confirm` when `execute`; dry-run returns the plugin it would delete after checking that it exists in the list.
- [ ] Unit tests use a fake `Page` object (methods `goto`, `locator`, `click`, `fill`, `wait_for_url`, `url`) to assert the call sequence:
  - pagination stops when `arrow_forward` is disabled;
  - delete refuses when the modal text lacks the name;
  - `delete_plugin_for_profile(execute=True, confirm=False)` raises.

  Real behavior is verified in Task 7.
- [ ] FAIL → implement → PASS → commit `feat: list, create and delete plugins through the dashboard`.

---

### Task 6: MCP tools and CLI

**Files (from the wiring survey):**
- `server/schema_families.py`: five `tool_schema(...)` entries next to `bubble_plugin_install` (line ~1955). New `FIELD_LIBRARY` props: `plugin_id`, `plugin_folder`, `plugin_name`, `force`. `execute` and `confirm` already exist.
- `server/agent_catalog.py`:
  - `NATIVE_TOOL_DESCRIPTIONS` entries: unique, at least 80 chars, and non-generic. Say "never publishes".
  - `agent_read_only` += `bubble_plugin_list`.
  - `destructive` += `bubble_plugin_delete`.
  - `openWorldHint` += all five.
- `server/tools.py`:
  - import the four entry points at module level;
  - dispatch branches modeled on `bubble_branch_delete` (line ~2687);
  - add all five to `EDITOR_SESSION_TOOLS`.
  - Keep them **out of `_is_mutating`**, so the app savepoint and main-branch gates don't apply. Plugins have no app version.
- `runtime_coverage.py`: `NATIVE_SPECIAL_TOOLS` += five.
- `cli/main.py`: `plugin list|pull|push|create|delete`, each with its own handler (`--profile` required; `--plugin-id`, `--folder`, `--name`, `--execute`, `--confirm`, `--force`).
- `cli_leaf_inventory.py`: five `_direct(...)` entries.
- Count updates: catalog 356→361 and mcp_only 151→156 in `test_catalog_selection.py`, `test_catalog_quality.py`, `test_catalog_audit.py`, `test_catalog_inventory.py` (357→362), and `test_mcp_server.py:3114`. CLI leaves 107→112, direct 99→104 in `test_cli_leaf_inventory.py`.
- Docs: `docs/cli-reference.md` (plugin section), a `docs/mcp-clients.md` bullet list, `CHANGELOG.md`.
- Test: `tests/unit/test_mcp_server.py`. Add `test_plugin_tools_listed_and_dispatch`, following the `bubble_plugin_install` test at line ~4219:
  - `tools/list` shows the 5 tools;
  - `bubble_plugin_delete` has `destructiveHint`;
  - `bubble_plugin_list` has `readOnlyHint`;
  - `tools/call` for `bubble_plugin_push` reaches a monkeypatched `push_plugin` with `execute=False` by default.

- [ ] Steps: tests → FAIL → wire → run the full unit suite. Expected: the same 12 baseline failures and nothing new. Commit `feat: expose plugin list/pull/push/create/delete as MCP tools and CLI`.

---

### Task 7: Live verification (owner present)

Needs a stored session on this machine. The owner runs `! .venv/bin/bubble-mcp session login --profile plugins --app-id mcp-test-app` and completes the login and 2FA in the browser that opens.

- [ ] `plugin pull --plugin-id 1795610633201x773816577862139900` → the folder is created; `git init` and commit inside it.
- [ ] Edit `elements/AAC-test_element/update.js`, then run `plugin push` (dry-run). The diff shows that file only.
- [ ] `plugin push --execute`. Then open the plugin editor and confirm the code is there and the page is not read-only.
- [ ] Keep an editor tab open and run `plugin push --execute` again with a change → `editor_open` refusal.
- [ ] Change the server action's `package.json` (add a dependency), push with execute, then check the editor's deployment-package status. Record the outcome in the capture doc's npm section and settle the npm ruling.
- [ ] `plugin list` → 119 or more entries.
- [ ] Ask the owner before each irreversible step: `plugin create --name mcp_test_plugin_3 --execute`, then `plugin delete --plugin-id <new> --execute --confirm`.
- [ ] Pull 3 of the owner's real plugins (read-only) into a tmp dir and run `plugin push` dry-run on each → `changed: false`. This proves the round trip on live data.
- [ ] Commit the doc updates: `docs: record live verification of plugin tools`.
