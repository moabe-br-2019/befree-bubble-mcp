# Plugin Authoring (create and edit Bubble plugins) — Design

Status: draft for approval · 2026-11-25

## Intent

Today this MCP edits Bubble **apps**. The owner also builds Bubble **plugins** (visual elements,
actions) and does it by hand in `bubble.io/plugin_editor`: every property is added through the UI,
and code is pasted into text boxes. The goal is to let an agent:

1. create a new plugin from scratch, and
2. edit the owner's existing plugins,

with the plugin's source kept as files on disk (versioned with git, diffable, editable in an IDE),
synced to Bubble by the MCP.

## Goals

- Mirror a plugin into a local folder (`pull`) and write local changes back (`push`).
- Create a new plugin in the owner's account from a local template (`create`).
- List the owner's plugins (`list`).
- Cover visual elements and actions (client-side and server-side), including properties/fields,
  header, initialize/update/preview code, exposed states, events, and action code.
- Preview every write (dry-run diff) before applying it, as the rest of this MCP does.

## Non-goals (for now)

- **Publishing** a plugin version (the editor's "Publish" button). Publishing reaches every app that
  uses the plugin; the owner keeps doing it in the UI.
- The plugin's own **API calls** (the plugin-level API connector tab). Later phase.
- Marketplace metadata (pricing, description pages, images), contributors, plugin settings shared
  keys. Later, only if the capture shows they are cheap.
- UI automation of the plugin editor (Playwright clicking). Rejected, see Approaches.

## Anti-goals

- Silently overwriting work done in the Bubble UI. A push must never clobber a remote change that
  the local folder has not seen.
- Writing a payload the editor then refuses to load (the `plugin_install` "Invalid plugin
  version: true" class of bug, see `docs/capture-plugin-install.md`). Every push is followed by a
  read-back that proves the editor accepts the result.
- Storing plugin source inside this MCP repository.

## Constraints

- The save format of the plugin editor is **unknown**. Nothing in `src/` references
  `plugin_editor`. Known pieces: `GET /appeditor/get_raw_plugin?plugin_id=<id>&version=<version>`
  returns a plugin definition (needs a version), and the catalogue call
  `apiservice/doapicallfromserver` (`get_edit_mode_plugins_list`) used by `execution/plugins.py`.
  Whether the editor saves through `/appeditor/write` with the plugin id as `appname`, or through a
  separate endpoint, must be captured first.
- Auth reuses the existing stored Bubble session (`session login` / `session import`) — the plugin
  editor lives on `bubble.io` with the same cookies. Today a profile is bound to an app id; plugin
  calls take a plugin id instead, so plugin tools must accept a profile for the session and a
  plugin id as an explicit argument rather than reusing `_resolve_app_id`.
- The Chrome extension (`chrome-extension/content.js`) only intercepts `load_multiple_paths`,
  `load_single_path` and mutating `/appeditor/*` calls, and drops write payloads that have no
  `changes` array. It must be widened for discovery.
- Follows repo conventions: Python 3.11, tools registered in `server/tools.py` with schemas in
  `server/schemas.py`, execution code under `execution/`, pytest + ruff + mypy, capture findings
  written as `docs/capture-*.md` with redacted JSON samples.

## Approaches

| | Approach | Trade-off |
|---|---|---|
| **A (chosen)** | Capture the editor's real requests, then write through HTTP with the stored session; plugin mirrored to a local folder via pull/push | Fast, precise, diffable, matches the rest of the MCP. Depends on reverse engineering; a Bubble format change breaks it (mitigated by read-back verification). |
| B | Playwright drives the plugin editor UI (click "Add a new property", paste code) | No reverse engineering, but slow, fragile to layout changes, bad at bulk edits and diffs. |
| C | Like A, but MCP tools edit fields directly in Bubble, no local folder | No git history or diff; easy to overwrite work; loses the "edit real JS files" workflow. |

The owner picked **A**, explicitly valuing having the code saved locally.

## Design

### Phase 1 — Capture (discovery)

- Extension: add a **plugin editor discovery mode**. When the page is `bubble.io/plugin_editor*`
  (or the popup toggle is on), intercept *every* fetch/XHR to `bubble.io` (all methods), recording
  URL, method, request body and response body, regardless of `changes` presence. Plugin id comes
  from the `?id=` URL param. Send to the companion as a new message type / endpoint
  (e.g. `POST /v1/bubble/crawler/discovery-ingest`) appended to its own JSONL file under the
  companion dir. Normal app-editor capture is unchanged.
- Capture script (driven with the owner): open a throwaway test plugin and, in order: create the
  plugin; add an element; add each property type (text, number, yes/no, dynamic, list, option
  dropdown, file, color, app type…); edit header, initialize, update, preview; add exposed state and
  event; add a client action and a server action with fields and code; rename and delete each kind
  of item; reload the editor (to capture the load call).
- Output: `docs/capture-plugin-editor.md` (+ redacted `docs/capture-plugin-editor-*.json`)
  documenting the load call, the save call(s), the payload shape per item kind, id generation, and
  how the editor detects a stale version (if it does).

Who runs it (designer's call): the owner, in their own Chrome with the extension, since that is
how they asked to work. If the manual pass leaves gaps, the builder may fill them by driving the
editor with Playwright on the stored session (as `capture-plugin-install.md` did) — Playwright is
rejected only for the final tool, not for discovery. The capture must also record what happens
when an open editor tab holds stale state and the plugin is changed elsewhere (see Safety).

**Gate:** the implementation plan stops after Phase 1. Phase 2 is planned in detail only once the
capture doc exists. The shapes below are the target, not a guess at the wire format.

### Phase 2 — Tools

New module `src/bubble_mcp/execution/plugin_authoring.py` (plus a small `plugin_folder.py` for the
file mapping), reusing `BubbleEditorApiClient` / session headers from `editor_api.py`.

| tool | does |
|---|---|
| `bubble_plugin_list` | Plugins owned by the session's user. The owner's plugins all live in their personal Bubble account (no organization/team owner), so no owner argument. |
| `bubble_plugin_pull` | Read the plugin definition, write it to a local folder, record a base snapshot. |
| `bubble_plugin_push` | Diff local folder against the base snapshot and against current remote; dry-run by default (shows diff); with approval, writes, then reads back and verifies. |
| `bubble_plugin_create` | Create a new plugin in the account, then push a local template/folder into it. |

All four also exposed as CLI subcommands (`bubble-mcp plugin list|pull|push|create`).

#### Local folder layout

Default root `~/Projects/bubble-plugins/<plugin-slug>/` (overridable per call / config), its own
git repo, outside this repository.

```
<plugin-slug>/
  plugin.json            # id, name, metadata the tool understands
  .bubble-plugin/base.json   # last pulled remote snapshot (for conflict detection)
  elements/<element-slug>/
    element.json         # name, category, settings, states, events, ids
    properties.json      # ordered fields
    header.html
    initialize.js
    update.js
    preview.js
  actions/<action-slug>/
    action.json          # name, client/server, fields, return values, ids
    code.js              # client action body or server action body
    package.json         # server actions only: npm dependencies (Bubble builds them remotely)
  raw/                   # anything captured but not yet mapped, round-tripped verbatim
```

Rules: every remote object keeps its Bubble id in the JSON files so renames do not become
delete+create; unknown keys are preserved (round-trip), never dropped; code lives in plain `.js` /
`.html` files, not escaped inside JSON.

#### Safety

- **Conflict check:** push compares current remote against `base.json`. If remote changed since
  the last pull, push refuses and lists what changed, unless `force: true`.
- **Dry-run first:** push without approval returns the diff and the exact requests it would send.
- **Read-back verification:** after writing, re-read the plugin and assert it matches the local
  folder; report any mismatch.
- **Deletes** of elements/actions/properties require explicit listing in the approval, never
  implied by a missing file alone in the same call without confirmation.
- No publish call is ever sent.
- **npm dependencies** (owner asked for them): server actions may carry a `package.json`. When it
  changes, push sends it the way the editor does and then waits for / polls Bubble's remote build
  status, reporting a build failure as a push failure. The capture must include adding a dependency
  to a server action and the build-status calls that follow.
- **Open editor tabs** (designer's call): push warns that any open `plugin_editor` tab for the
  plugin must be reloaded or closed, since it may save stale state over the push. If the capture
  shows the editor detects concurrent changes, document that instead; if it shows a cheap way to
  detect an active edit session, the builder may add it.

#### Create from scratch

`bubble_plugin_create` takes a name and an optional local folder (or a built-in minimal template:
one element with one property and stub initialize/update). It creates the plugin, pulls the fresh
plugin to establish ids/base, then pushes the template content.

### Test plugin

The owner authorized creating a throwaway plugin `mcp-test-plugin` in their personal account
(it may not be deletable). It is used for the Phase 1 capture and for live integration tests of
create/pull/push/delete, read-back verification, the conflict check and npm builds. Live tests
against it are part of acceptance; unit tests run on redacted capture fixtures. No tool ever
touches the owner's real plugins during development except a read-only `pull`.

## Left to the builder

- Exact endpoints, payload shapes, id formats, and versioning — from Phase 1 capture.
- Whether `pull` uses `get_raw_plugin` or the editor's own load call (whichever the capture shows
  returns the editable, not published, state).
- Slug rules, JSON key ordering, how option sets / app types referenced by properties are stored.
- Test fixtures built from the redacted capture samples.
