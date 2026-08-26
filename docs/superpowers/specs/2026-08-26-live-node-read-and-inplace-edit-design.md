# Live Node Read and In-Place Edit Design

**Date:** 2026-08-26
**Status:** Approved

## Context

Editing an action inside an existing workflow is unsupported today. Every action tool
composes the node from scratch out of the `.bubble` export, and the export is a decoded
projection of what the editor actually stores: node keys, param ids, and Message tokens are
all translated on the way out, and `src/bubble_mcp/vendor/bubble_modules.py` only splits the export, so
there is no local map to invert. The Orana report measured the consequence — four composed
`ChangeThing` actions, all accepted with HTTP 200, all rendered `[missing: null]` in the
editor. `/appeditor/write` performs no semantic validation, so a 200 is not evidence of
anything.

`docs/session-findings-2026-08-24.md` recovered the canonical raw form by reading the
running editor's own memory through Playwright:
`window.appquery.app().json._child('api')._child('<wf_id>').raw()`. That read is the only
known source of truth for the raw encoding, and it is currently a manual procedure.

Commit `127203a` landed the pure layer for the other route — read, patch one leaf, write
back — as `src/bubble_mcp/execution/raw_node_edit.py`, deliberately unexposed: without a
live read, an edit tool could only operate on a node the agent assembled, which is the path
this work exists to replace. This design closes that gap and ships the tool surface.

## Goals

- Expose the live editor read as `bubble_live_node_read`, so the raw form of any node can be
  obtained on demand without a human in the editor and without the Chrome extension.
- Expose one in-place edit tool, `bubble_node_edit`, that performs the whole
  read → modify → write → re-read → compare cycle in a single call, with `execute=false` as
  the default.
- Make the re-read mandatory and structural: the tool reports where a write diverged from
  intent, never a bare success.
- Keep `raw_node_edit.py` internal; the two tools are the only new public surface.
- Cover the pure layers (key translation, script construction, orchestration) with tests that
  run in CI without a browser and without a Bubble session.

## Non-goals

- Reconstructing expression encodings from the export. This design exists because that is
  not possible; nothing here parses or rebuilds an expression interior.
- Editing elements (`%el`) or any node whose root carries a `name` key. v1 addresses
  workflow action nodes only; the ambiguous cases fail loudly rather than guess.
- Replacing `add_action`, `bubble_editor_write`, or the capture-based tool wizard. Those
  remain the paths for creating new actions.
- Deleting actions. `reorder` refuses an order that would drop a step precisely so deletion
  cannot arrive disguised as a reorder; a real delete stays out of scope.
- Running the browser read in CI. The default evaluator needs Playwright and a stored
  session; tests inject a fake.

## Chosen Architecture

Three new modules under `src/bubble_mcp/execution/`, each depending only downward. None of
them imports the server package.

### `node_keys.py` — key-space translation

The editor's in-memory tree and the write endpoint disagree on spelling. A read returns
decoded keys (`type`, `properties`); `/appeditor/write` requires encoded keys (`%x`, `%p`)
at the node root, with the same interior. `lint_editor_write_changes` already refuses the
decoded form for exactly this reason.

- `encode_node_root(node)` maps **root keys only**: `type` → `%x`, `properties` → `%p`,
  `default_name` → `%dn`. It does not recurse. The interior — the expression chain, its
  `Message` nodes, their `name` fields — is copied verbatim, because the interior is the
  part nobody can reconstruct.
- A root that carries `name` raises `ValueError` instead of translating. `name` is `%nm` on
  an element and an internal field name on a `Message`; the mapping in
  `write_lint._DECODED_TO_ENCODED` deliberately omits it. v1 targets action nodes, where
  `name` does not appear at the root, so encountering one means the caller is addressing a
  node shape this design does not cover.
- `decode_node_root(node)` is the inverse, used to close the round trip in tests.

### `live_node_read.py` — the read

- `build_appquery_script(pointer)` is pure: `["api", "<wf_id>"]` becomes
  `window.appquery.app().json._child('api')._child('<wf_id>').raw()`. An empty pointer
  raises — `app.raw()` on the root is blocked by Bubble itself "for performance reasons".
  Pointer segments are JSON-escaped into the script.
- `read_live_node(profile, pointer, *, evaluator=None, app_id=None, headless=True,
  timeout_sec=...)` returns `{"ok": True, "pointer": [...], "node": {...}}`.
- The default evaluator uses `playwright.sync_api.sync_playwright` with
  `launch_persistent_context(settings.config_dir / "browser-profiles" / profile)`, the same
  pattern already in production in `browser_automation/scheduled_deploy.py`. Readiness is
  gated on `typeof window.appquery !== 'undefined'`, not on the deploy button.
- The evaluator is a parameter so tests supply a fake and never launch a browser.

### `node_edit.py` — the cycle

`edit_live_node(...)` runs six steps:

1. read the node at `pointer` (decoded key space);
2. apply exactly one operation, delegating to the untouched primitives in `raw_node_edit.py`
   — `patch_expression_leaf` for `patch`, `reorder_actions` for `reorder`;
3. `encode_node_root` on each body about to be written;
4. build the `changes` entries, intent `SetData`, matching the shape
   `PayloadBuilder.add_change` already produces;
5. `BubbleEditorClient().write(payload, session, dry_run=not execute)`;
6. when `execute` is true, re-read the same pointer and call `first_divergence(intended,
   actual)`.

**The write granularity is the whole node at `pointer`, never the patched leaf.** Writing the
leaf directly would put an expression interior in the body at a path containing `actions`,
which `_is_node_position` classifies as a node position, so `lint_editor_write_changes` would
reject the decoded interior — correctly by its own rules, and wrongly for this case. Sending
the whole node keeps exactly one translation point (the root) and leaves the interior where
the lint does not inspect it. For `patch`, `pointer` therefore addresses the action node
(`["api", "<wf_id>", "actions", "3"]`) and `leaf_pointer` is relative to it. For `reorder`,
`pointer` addresses the actions map (`["api", "<wf_id>", "actions"]`) and every action body
in that map is encoded at its own root.

**A container pointer is refused for `patch`, and every written body is walked.**
`encode_node_root` translates exactly one root, so a `patch` whose `pointer` addresses a
container — the actions map `["api", "<wf>", "actions"]`, or the workflow root
`["api", "<wf>"]` — would write every action underneath it with decoded keys while the
re-read compared decoded-to-decoded and reported `verified: true`: the exact
`[missing: null]` corruption, certified as success. Two defences:

- `_apply` refuses `op == "patch"` unless the node it read is a node (`type` or `%x` at its
  root), raising `patch requires a pointer to a node, not a container`.
- `_assert_encoded_node_roots` walks every assembled change body before it is sent, from
  both `build_patch_changes` and `build_reorder_changes`, descending only through the
  container keys `actions` / `%el` / `%wf`, and refuses any dict carrying `type` or
  `properties` without `%x` or `%p`. It never descends into a node's `%p`, because the
  expression interior is legitimately spelled with decoded-looking keys (`"type": "Message"`)
  and is the one part nothing here may inspect.

These two catch different halves of the container-pointer case: the actions map has no root
`type`, so `_apply`'s root-type check catches a `patch` aimed at it directly. A workflow root
does have a root `type`, so it passes that check; it is the recursive `_assert_encoded_node_roots`
guard, descending into `actions`, that catches the WORKFLOW-root case instead.

`lint_editor_write_changes` cannot stand in for either: it is path-shaped, and
`_is_node_position(["api", "<wf>", "actions"])` is false, so the whole container write passes
it unexamined.

**A reorder also rewrites the index.** Renumbering moves each action to a new path while
`_index.id_to_path` still points at the old one. Alongside the `SetData` change, `reorder`
emits one `Update index` change per action — `path_array` `["_index", "id_to_path",
"<action_id>"]`, body the dotted new path (`"api.<wf_id>.actions.1"`) — the same pairing
`bubble_cli` already uses when it writes an action. An action with no `id` raises: skipping
its index entry silently would leave `_index.id_to_path` pointing at whatever now occupies
its old position, which is the same class of quiet damage `reorder_actions` refuses when an
order would drop a step.

**The comparison lives in the decoded key space.** The write encodes the root because the
endpoint demands it; the re-read comes back decoded, and the intent is compared as it stood
before the encode. Encoding both sides would measure the write with the same ruler that
produced it, so an encoding error would cancel itself out and the tool would certify a
broken node.

## Tool Surface

### `bubble_live_node_read`

| arg | required | meaning |
|---|---|---|
| `profile` | yes | stored profile whose session and browser profile are used |
| `pointer` | yes | array of child keys from the app tree root, e.g. `["api", "<wf_id>"]` |
| `app_id` | no | overrides the profile's app id |
| `headless` | no | default `true` |
| `read_timeout_sec` | no | editor-ready and evaluate timeout |

Returns `{ok, pointer, node, app_id, read_at}`. Read-only: `readOnlyHint` true,
`openWorldHint` true (it drives a browser).

### `bubble_node_edit`

| arg | required | meaning |
|---|---|---|
| `profile` | yes | as above |
| `pointer` | yes | for `patch`, the action node; for `reorder`, the actions map holding it |
| `op` | yes | `"patch"` or `"reorder"` |
| `leaf_pointer` | for `patch` | address of the dict to patch, relative to the node |
| `patch` | for `patch` | object merged into that dict; a value may be a whole subtree |
| `order` | for `reorder` | every existing action key, exactly once, in the new order |
| `execute` | no | default `false` — previews the write and stops |
| `app_id` | no | as above |

Returns `{ok, execute, verified, render_unverified, verified_meaning, divergence, pointer,
before, intended, write, after}`. `verified` is only `true` after a successful re-read whose
`first_divergence` is `None`; with `execute=false` it is absent, never `true`. A failed
re-read reports the read result under `after_read`, never under `after`, which always holds
the node itself.

**`verified: true` is a byte check, not a render check.** `/appeditor/write` stores any body
and `.raw()` returns what is stored, so a body whose interior was wrongly decoded round-trips
byte-identically and `first_divergence` returns `None`. Every executed result therefore also
carries `render_unverified: true` and a `verified_meaning` string saying so; only step 4 of
Manual Validation clears it. Reporting `verified: true` alone would be exactly the bare
success this design promised never to report. Destructive-ish: `readOnlyHint` false,
`openWorldHint` true, and it joins the write-annotated set in `agent_catalog.py`.

`app_version` is threaded through both reads and into the write payload. Without it the read
would hard-default to `?version=test` while the write went to the session's version, so a
branch-configured profile would read `test`, write the branch, and verify against `test`.

## Error Taxonomy

Every failure is a structured result, not a stack trace:

| condition | signal |
|---|---|
| Playwright not installed | `ok: false`, `error: "playwright_missing"`, with the `pip install "befree-bubble-mcp[browser]"` instruction already used by scheduled deploy |
| no stored session for profile | `ValueError`, matching `bubble_editor_write` |
| profile has no `browser-profiles/<profile>` directory | `ok: false`, `error: "browser_profile_missing"`, naming `bubble_session_login`. A session stored by `bubble_session_import` is valid for writes but never creates this directory; without the check, Playwright creates an empty one, loads a logged-out bubble.io, and fails 90s later as `editor_not_ready` |
| editor never becomes ready | `ok: false`, `error: "editor_not_ready"` |
| pointer does not resolve in the live tree | `ok: false`, `error: "pointer_not_found"`, naming the deepest segment that did resolve |
| `leaf_pointer` addresses a missing key | `KeyError` from `patch_expression_leaf` — creating the key is how a node ends up rendering `[missing: null]` |
| `order` drops or invents an action | `ValueError` from `reorder_actions` |
| node root carries `name` (or `%nm` on decode) | `ValueError` from `encode_node_root` / `decode_node_root`, checked before the already-encoded passthrough |
| node root mixes decoded and encoded members of the mapping | `ValueError` from `encode_node_root` / `decode_node_root`: a half-translated root is either an uncovered node shape or a hand-assembled body |
| `patch` pointer addresses a container | `ok: false`, `error: "invalid_edit"`, `patch requires a pointer to a node, not a container` |
| any body carries an untranslated node root | `ok: false`, `error: "invalid_edit"`, naming the dotted position inside the body |
| `read_timeout_sec` is not numeric | `ok: false`, `error: "invalid_read_timeout_sec"` |
| write returns 401/403 | the existing `auth_blocked` result from `BubbleEditorClient.write` |
| re-read differs from intent | `ok: true`, `verified: false`, `divergence: "<dotted.path>"` |

## Testing

Automated, in CI, with no browser and no Bubble session:

- `node_keys`: root-only translation; interior left byte-identical (asserted against
  `tests/fixtures/expressions/api-event-parameter-golden.json`, a real editor capture);
  `encode`/`decode` round trip; `ValueError` on a root `name`.
- `live_node_read`: `build_appquery_script` output for a nested pointer; refusal of an empty
  pointer; segment escaping.
- `node_edit`: the full cycle against a fake evaluator and a fake write transport — one case
  where the re-read matches (`verified: true`), and one where the fake write drops a byte,
  asserting the exact dotted path in `divergence` rather than merely that it failed;
  `execute=false` produces a preview and performs no re-read.
- `reorder` through the tool: the refusal that would drop a step, and the `Update index`
  change emitted per action with its new dotted path.
- the container-pointer hazard: a `patch` at the actions map and at a workflow root are both
  refused, the recursive guard refuses a decoded action nested under an encoded node, and the
  guard leaves an expression interior spelled `"type": "APIEventParameter"` alone.
- `app_version` reaches both reads and `payload["app_version"]`; an executed result carries
  `render_unverified`; a preview carries neither `verified` nor `render_unverified`.
- `browser_profile_missing` is returned before any browser is launched.
- Existing `tests/unit/test_raw_node_edit.py` is untouched.

This machine has 14 unit tests that fail on Windows regardless of this change
(symlink/chmod/path separator/routing count). Verification compares against the pre-change
baseline on the same machine, not an absolute pass count.

## Manual Validation

Against profile `mcp-test` (`mcp-test-app`) only — never `orana` (`~/.bubble-mcp`) and never
`auto-on`:

1. `bubble_live_node_read` on a workflow that already exists and works; keep the raw node.
2. `bubble_node_edit` with `execute=false`, confirm the previewed payload.
3. Re-run with `execute=true` on one small, reversible change; the tool re-reads and reports
   `verified`.
4. A human opens the Bubble editor and confirms the step renders correctly.

Step 4 cannot be automated and cannot be skipped: identical bytes can still render broken,
and that is the exact failure class this work exists to eliminate. A failure at step 4
invalidates the encoding contract in `node_keys.py`, which is cheaper to learn now than
after the tools ship.

## Registration Checklist

- `server/schema_families.py`: new `FIELD_LIBRARY` entries (`pointer`, `leaf_pointer`,
  `patch`, `order`, `op`, `headless`, `read_timeout_sec`) and two `tool_schema` entries.
- `server/tools.py`: two handlers alongside the `bubble_editor_write` branch. The edit handler
  records a mutation overlay after a successful executed write, exactly as `bubble_editor_write`
  does, so `bubble_context_summary` does not keep serving pre-edit state. The overlay call stays
  in the server layer; `node_edit.py` must not import the context package.
- `server/tool_descriptions.py` and `server/agent_catalog.py`: descriptions, plus
  `bubble_node_edit` in the write-annotated and `openWorldHint` sets.
- `runtime_coverage.py`: both names in `NATIVE_SPECIAL_TOOLS`.
- `server/agent_guide.py`: point the workflow-editing note at `bubble_node_edit` for editing
  an existing action, keeping `add_action` for creating one.

## Risks

- **The encoding contract is unproven.** Root-only translation is inferred from one capture
  and one lint. Step 4 of manual validation is the only thing that confirms it.
- **Browser cost per read.** Each `bubble_live_node_read` launches a persistent context.
  Acceptable for an edit workflow; a session-reuse cache is deliberately deferred until
  there is evidence the cost matters.
- **Editor internals are unversioned.** `window.appquery` is Bubble's private surface and can
  change without notice. The failure is loud (`editor_not_ready` / `pointer_not_found`), not
  silent.
