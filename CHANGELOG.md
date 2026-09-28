# Changelog

## Unreleased

- `bubble_e2e_flow` runs a short browser flow declared inline - `goto`, `click`, `fill`,
  `expect_text` / `expect_no_text`, `wait`, `screenshot` - on one or more versions as an app user,
  and returns the recordings side by side: `compare.html` plays every version's video with one
  button, next to each step's screenshot and status, and `side_by_side.webm` is composed when a
  full ffmpeg is on PATH (Playwright's own build cannot stack videos). Each version gets its own
  impersonated session (Bubble's user cookie is per version), resolved from an email without a
  Data API token. It runs through the suite runner, so URLs, video and step screenshots behave
  as in a suite. `live` is refused: a flow clicks and confirms for real. On the team server the
  agent wrote ~10 Playwright scripts for this, ~40 of an ~80-minute task; on mcp-test-app a
  five-step flow on two versions took 62s end to end.

- Browser tools get past the dev version's preview password page. On the team server E2E and
  visual capture answered 401 on the dev version until the agent supplied the preview login by
  hand. A profile can now hold it (`preview_username` / `preview_password` in
  `bubble_profile_add`; the password is never echoed back), and E2E, `bubble_visual_capture_actual`
  and `bubble_run_as` share one resolver: the call's arguments, the profile, the environment
  (`BUBBLE_PREVIEW_USER` / `BUBBLE_PREVIEW_PW`), the app's export, then Bubble's seeded
  `username` / `password`. Visual capture sent nothing before; E2E read only the export. On
  kaimia-app's dev version a capture went from the 401 page (1 node) to the app's sign-in page
  (45 nodes).

- `bubble_run_as` resolves an email without a Data API token. With no token it failed with
  `no_data_api_config`, and on the team server the agent scripted the editor's Data tab with its
  own Playwright. It now does that itself through the stored editor session: it opens the Data
  tab on the User type, reads the search answers the page receives (plain JSON with `_id` and
  the email; the request itself is encrypted and not reproducible), and types the email into the
  tab's search box when the first page does not hold it. It reads the development database
  (branches share it); live is refused with `live_lookup_unsupported`. About 20s, measured on
  mcp-test-app end to end. A configured token is still used first.

- `bubble_profile_add` updates a profile instead of rebuilding it. An update that named only
  `app_version` reset every other field, and `context_path` was not accepted at all, so on the
  team server a profile lost its context path and it was restored by hand. Updates now change
  only the fields passed (an empty value clears one), `context_path` and `crawler_index_path` are
  accepted, an argument the profile cannot store is an error, and the response says whether the
  profile was created, which fields changed, and everything stored.

- A logged-out session is known before any work is spent on it. `bubble_session_check` asks
  Bubble once (`calculate_derived`, ~0.3s, no browser) whether a profile's stored session is
  still logged in, and every tool that needs the editor (live reads, node edits, clones and
  duplicates, raw writes, branch, savepoint, deploy and log tools, and any executed write) is
  refused up front with `error: session_expired` and a `next_action` that says to stop and ask
  for `bubble_session_login`. A live read on an expired session used to launch a browser and wait
  for the editor before saying `not_logged_in`, and agents retried or tried other tools before
  noticing; on a metered API with a weaker model that was a loop of paid calls. A logged-in
  answer is cached for five minutes per stored session; a logged-out one never is. The server
  instructions tell agents to call the check before Bubble work.
- Session login detects a login by asking Bubble for the app, not by a cookie. The
  `ajs_user_id` cookie proved nothing: Bubble's analytics keep the user id in localStorage and
  write the cookie back on every page load, so an expired login kept it, the login page was
  skipped, and the editor loaded the app for a few seconds before sending the page to bubble.io's
  home (kaimia-app). The login page now runs `/appeditor/get_versions` for the app from inside
  the page (200 with access, 401 without), goes on to the editor when it passes and waits on the
  login page until it does. While the editor session is being validated, a page sent away from
  the app goes back to the login page once, then fails naming login or editor access, instead of
  waiting out the whole budget.
- Session login opens Bubble's login page first. A browser profile that is not logged in used
  to be sent straight to the editor, which shows a signed-out person no login screen, so on the
  team server nobody could sign in without a terminal. Now `bubble_session_login` (and
  `session login`) opens `https://bubble.io/login?mode=login`, detects the login there (Bubble
  stays on the login URL after signing in, so the URL cannot tell; the entry above says how),
  and then opens the editor on the profile's version (`version=<branch>`, omitted for main). A
  profile that is already logged in goes straight on to the editor. One `wait_seconds` budget
  covers both. `login_first=false` (CLI: `--no-login-first`) keeps the old behavior.

- `bubble_context_query` answers the structural questions agents were parsing the `.bubble`
  export for with `python3 -c` (19 times in one session on the team server, 28 in another):
  `kind='element_subtree'` gives an element's children, the workflows it and its children
  trigger and the workflows of the same page or reusable that point at them;
  `kind='workflows'` lists every workflow of a page, a reusable or the backend with trigger,
  actions, touched elements and fields set; `kind='field_writers'` lists every action that
  creates or changes field Y of type Z anywhere in the app, with the target type worked out from
  the expression where it can be (`type_confirmed`), plus the database triggers on Z and whether
  their condition reads the field. It reads the export of the version asked for and downloads it
  first when the cached one is another version's. The context graph that `bubble_context_find`
  searches still has page workflows as bare ids and no reusable workflows.

- `bubble_duplicate_element` copies live elements with their whole subtree and the workflows
  they trigger. Every element id, slot key, event id and action id is reminted against the
  app's whole `_index.id_to_path`, one mapping covers elements and workflows (a copied "show
  popup" step opens the copied popup, a copied GetElement reads the copied input), and the tool
  writes the index itself: `id_to_path` for every new id, `issues_list` for every new element,
  `issues_sub` read-modify-written for the parents and mirrored for nested containers. Pass a
  button and the popup it opens together in `element_ids`; a workflow outside the copy that
  points at a copied element is reported, not copied. Preview by default; an executed copy goes
  to a branch only and every copied node and index entry is read back from it. On the team
  server an agent spent ~20 minutes and ~US$4 rebuilding this by hand, with a hand-made
  `_index` payload. The source is read from the live editor, never from the export.
- `BUBBLE_MCP_TOOLSET=core` makes `tools/list` return about 19 core tools (~8k tokens) plus
  `bubble_tool_schema` (search the catalog, or fetch full schemas by name) and `bubble_call`
  (call any catalog tool by name, through the same checks as a direct call). The full list is
  ~350 tools and ~300k tokens: through OpenRouter, without deferred tool loading, Opus received
  ~432k tokens per request and a 262k-context model could not start. This replaces the external
  gateway the team server ran. `BUBBLE_MCP_CORE_TOOLS` overrides the core set; `full` stays the
  default.

- Main is read-only. No executed write reaches `test` or `live`: `call_tool` refuses any
  mutating tool whose resolved version is main with `main_is_read_only` (before a savepoint is
  taken or a context loaded), and `BubbleEditorClient.write` refuses it again before sending, so
  paths that skip `call_tool` (plan execution, transfers, the HTML and Figma importers) are
  covered too. There is no override. Previews still run. Changes reach main through a branch
  merge.
- `bubble_editor_write` writes to the version it is asked to. The profile's version was
  injected into the arguments and then written over the payload body, so on the team server a
  write whose body said branch `93k8b` went to main. The target is now the top-level
  `app_version` (newly in the schema), else the body's `app_version`/`appVersion`, else the
  profile's; a top-level version that disagrees with the body is an error. `referer` and
  `x-bubble-r` are rebuilt with `version=<target>` instead of copying the URL the session was
  captured on. An executed write is read back from the target version: a divergence returns
  `ok: false` with `write_not_verified`, and the response names `app_version` (sent) and
  `confirmed_app_version` (confirmed by the read-back, or null). `verify=false` skips it.
- Catalog tools resolve names against the version they edit. The cached `.bubble` export is
  one version's, and its `.meta.json` says which; when it is another version's, it is
  downloaded again for the target before use, and a failed download is an error instead of a
  silent fallback (`delete_event` on a branch reported "Workflow not found" from main's export).
  The mutation overlay applies only the entries recorded for the target version.
- Catalog schemas match the runtime signatures. `create_button` without `name` failed as a
  Python `TypeError`; create tools now derive a missing name the way the compiler does
  (`bt_<label>`, returned as `element_name`), and a call that still lacks a runtime argument
  gets a `ValueError` naming it. The schemas that let a runtime-required argument be
  omitted, or did not declare it under any name dispatch reads (`clone_page`,
  `clone_reusable`, `update_reusable_type`, `delete_reusable`, the API token and app text
  tools, `upload_asset`), were fixed. `bubble_catalog_quality` has a
  `runtime_signature_parity` check that keeps them in step.
- `bubble_live_node_read` returns leaf values (`node_kind: "scalar"`): an
  `_index.id_to_path` entry is a string and an `_index.issues_sub` entry a list, and reading
  them failed with `unexpected_node_shape`. `bubble_node_edit` and `bubble_clone_workflow`
  still require a node and say so.

- The autoupdate launcher can keep Chromium in step with Playwright. The browser binaries are
  not a pip dependency and `playwright>=1.45.0` is an open range, so a dependency refresh can
  raise the version and leave the venv driving binaries it no longer matches - with no error,
  until an e2e run stops working. Set `BUBBLE_MCP_SYNC_BROWSERS=1` and the launcher compares the
  Playwright version across an install and runs `playwright install chromium` when it moved.
  Off by default: the download is large, and a laptop that never drives a browser should not
  spend its session-startup budget on it. A failed download is logged and never stops the
  launch, like every other outcome here.

- The run-as storage state is written 0600. It holds live session cookies for an impersonated
  app user, exactly like the editor session file that `sessions/store.py` already protects, but
  it was written with the process umask - 0644 on a stock Ubuntu. Best effort, since the mode is
  meaningless on Windows; the config directory should still be 0700.
- A confirmed scheduled deploy in `test_cli_browser_scheduled_deploy_flow` no longer drives a
  real browser. The test scheduled a deploy for a fixed past timestamp, which arms a
  threading.Timer with a ~0s delay against the module-level Playwright executor. That timer
  fires asynchronously, after the test returns and monkeypatch has restored
  BUBBLE_MCP_CONFIG_DIR: the record was isolated to tmp_path but the DEPLOY WAS NOT, so a plain
  `pytest tests/unit` opened a browser at bubble.io and wrote its history into the developer's
  real config directory. On a machine whose profile names a real app with a live session it
  would have attempted a real deploy. The test now stubs the executor and schedules into the
  future, which is what the equivalent flow in test_mcp_server.py already did.

- End-to-end browser suites are a first-class MCP capability: `bubble_e2e_list`,
  `bubble_e2e_run`, `bubble_e2e_report` and `bubble_e2e_scaffold`. A suite is declared per
  profile under `BUBBLE_MCP_CONFIG_DIR/e2e/<profile>/` and carries environment only - app,
  branch, the `bubble_run_as` user it impersonates, viewport, video and cursor flags; its steps
  are Python case modules written against a stable `ctx` API, because the assertions being
  replaced check computed font weight, the relative order of three strings inside one card and
  a conditional date picker. `bubble_e2e_run` previews with `execute=false`: it resolves branch
  and base URL, verifies the session and the case modules, and reports every blocker with the
  tool that fixes it, without opening a browser. Each case runs in its own browser, so one
  failure does not end the suite, and the structured result names the failing step and points
  at its screenshot and video. `bubble_e2e_report` reads a finished run back by `run_id`
  instead of re-creating its records. A suite may set `cases_root` to a checkout, so an app's
  tests stay in the app's repository rather than being copied into the config directory. The
  three Kaimia cases (KS1-T22, KS1-T23, KS1-T29) were ported to this path and pass through it;
  `kaimia/e2e/` now points at the new route instead of holding standalone scripts. Readiness
  reports Playwright availability and run-as session validity for a profile's suites.

- Canonical raw form of APIEventParameter expressions recovered from live editor memory
  (Playwright + appquery child-node raw()): the parameter only resolves with btype_id +
  event_id + param_id (the parameter KEY, not the internal id) + param_name together, with
  is_slidable: false on every expression node. Frozen as a golden fixture
  (tests/fixtures/expressions/api-event-parameter-golden.json), and bubble_editor_write now
  flags APIEventParameter nodes missing any of the four context fields with the exact fix.
- bubble_editor_write warns on hand-composed expression nodes in workflow actions (the client report
  bug 8): expression encodings (APIEventParameter, Message chains, param ids) are not derivable
  from the .bubble export, and /appeditor/write returns HTTP 200 for any body — results now
  carry `warnings` steering agents to captured editor traffic (bubble_tool_wizard_start),
  add_action, or in-editor Copy/Paste. Tool descriptions and workflow routing notes state that
  a 200 from the endpoint is not success.
- Every created element gets %p.order = max(sibling)+1 stamped in the shared create queue when
  the tool did not set one — batch-created siblings used to tie at no order and render in
  reverse creation order.
- Icon values are validated: dashed library prefixes (ion-checkmark, feather-check) map to the
  canonical "<library> <name>" form, and unknown libraries fail with the accepted formats
  instead of writing a glyph the editor cannot render.
- Aria tool failures now carry a structured `error` field in the MCP result (extracted from the
  runtime failure line) instead of burying the reason in `logs` with a bare ok=false.
- Catalog-wide schema-vs-runtime contract snapshot test: every argument a tool schema
  advertises must reach the runtime (name, alias, or **kwargs). Known gaps for 124 tools are
  frozen in tests/fixtures/schema_runtime_contract_gaps.json; new drops and stale entries both
  fail, so the baseline can only shrink.
- create_shape sizes are honored: the dispatch fixed-size normalizer preferred the builder's
  legacy %w/%h default (100) over explicit min/max_width_css, silently rewriting a 19px dot to
  a 100px square. Explicit CSS lengths now win; %w/%h stay as the legacy fallback.
- create_group normalizes bg_style values (color -> bgcolor) instead of compiling an invalid
  %bas that the editor ignores (transparent group), and rejects junk values with a clear error.
- update_layout accepts the common element properties agents actually need: font_size, font_color,
  font_family, order, rotation_angle, opacity, background style/color (bgcolor), and border
  roundness — previously these failed silently as "unsupported layout property", forcing manual
  payloads. Color values resolve through the app color tokens; sizes/orders coerce from px strings.
- Crawler-only profiles work across the whole runtime: create_from_html no longer hard-requires a
  .bubble export (it falls back to the crawler-index primary source), and the aria dispatch layer
  resolves the profile's default crawler-index artifact automatically instead of requiring an
  explicit crawler_index_path argument on every call. Context-detection failures are non-fatal
  when a previously detected crawler index exists.
- create_reusable_instance now mirrors editor serialization (2026-08-24 the client project bug report #1-#3):
  %p.custom_id uses the definition's inner .id (never the element_definitions dict key), created
  elements get an element-level %nm write and a computed %p.order (max sibling order + 1) so they
  show up in the editor's Elements Tree, and a missing reusable name returns a clear structured
  error instead of a Python TypeError (`source` is now required in the schema and aliases to
  reusable_name). The %nm write applies to every create_* tool via the shared create queue.
- bubble_editor_write lints node bodies for decoded export keys (report #7): a body under
  %el/%wf/actions using type/properties instead of %x/%p is rejected with an explanation (the
  server accepts such nodes but the editor renders "[missing: null]"); allow_decoded_keys=true
  overrides.
- bubble_context_detect returns a compact response by default (report #6); include_details=true
  restores the full summary/attempt payloads.
- bubble_session_login reports the MCP venv's own Python path when Playwright browser binaries
  are missing (report #5), instead of a generic "playwright install" hint.
- PathDiscovery now uses the editor crawler-index as a PRIMARY data source when no .bubble
  export or consolelog is available (the .bubble export endpoint returns 401 on some plans).
  Crawler-only profiles previously failed every aria-runtime tool with "No app data source
  found" even though context detection had succeeded via the crawler.
- The anti-stale workflow guard in add_action/replace_action no longer discards workflows that
  were created via MCP and exist only in the local cache (created after the last .bubble
  download): cache rows newer than the root snapshot (15 min tolerance) are trusted instead of
  auto-creating a duplicate workflow. Cache rows older than the root (deleted/ghost refs) are
  still ignored. The decision lives in BubbleCLI._select_trusted_workflow_rows with unit tests.
- add_action honors the event_ref/event_type/ref_kind arguments its MCP schema always advertised
  (they were silently dropped by the dispatch layer): when given, it delegates to the existing
  add_event_action by-ref path, so actions can be appended to any existing workflow — including
  ConditionTrue, CustomEvent, and DoEvery — without element+event matching, duplicate auto-created
  workflows, or manual /appeditor/write payloads. `to` now aliases to `to_email`, and the
  unsupported query_result_type argument was removed from the add_action schema. A contract test
  guards schema-vs-runtime argument drift.
- Catalog: every exposed tool now has its own description (323 unique / 323 tools, was 194 unique
  with 13 workflow tools, 15 data tools, 41 metadata tools sharing one category blurb). Legacy
  boilerplate ("This is an Aria-compatible Bubble MCP tool. Use it when the user's intent matches...")
  was dropped; per-tool text lives in `server/tool_descriptions.py`.
- API Connector routing: `bubble_agent_guide`, `bubble_task_recipe`, `bubble_task_runbook`, and
  `bubble_tool_search` now route "API call / API Connector call / chamada de API" requests to the
  `create_api_connector_call` extension tool (new `manage_api_connector` route and `api_connector`
  recipe), report whether it is enabled, and explicitly steer away from `create_api_token`
  (Data API tokens) and visual element tools. The `create_api_call` language intent resolves to
  `create_api_connector_call`.
- Tool wizard: generated tool names are flat and MCP-client safe (`^[a-zA-Z0-9_-]{1,64}$`, no dots)
  so clients can expose them as direct callables; generated descriptions state the intent, family
  hint, arguments, and preview contract instead of "Generated candidate tool from session ...".
- API token tools state they manage Data API tokens, not API Connector calls.

## 0.1.0

- Bootstrap package metadata.
- Add local profile CLI.
- Add sensitive-source audit.
- Add minimal read-only MCP stdio server.
- Add compact context summary/search.
- Add deterministic dry-run planner and validator.
- Add basic HTML-to-Bubble dry-run converter.
- Add eval harness.
- Add local Figma bridge skeleton.
