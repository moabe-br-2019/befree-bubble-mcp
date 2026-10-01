# Per-profile write policy and tester mode

Two capabilities chosen by whoever installs the MCP, per app profile, never by the agent:

1. **A configurable main write policy.** Today the rule is fixed: main (`test`) takes writes only
   in an app without branches, and `live` is never written (`execution/version_policy.py`). Some
   installations want main always writable, some want it never writable.
2. **A tester mode.** An agent whose only job is preparing an app for automated tests may set
   HTML element ids - reusing the ones already there - and can put the old ids back afterwards.

Decided 2026-09-30 with Moabe in a brainstorming session.

## Configuration

Both keys live inside each profile in the config dir's `settings.json`. No MCP tool writes either
key; changing them means editing the file.

```json
"profiles": {
  "orana": {
    "app_id": "...",
    "main_write_policy": "auto",
    "tester_mode": true
  }
}
```

| key | values | default | meaning |
|---|---|---|---|
| `main_write_policy` | `auto`, `always`, `never` | `auto` | ordinary writes to main: `auto` is today's rule (writable only without branches), `always` writable, `never` read-only |
| `tester_mode` | `true`, `false` | `false` | enables the three `bubble_test_ids_*` tools for this profile |

- A missing or unrecognised `main_write_policy` value is treated as `auto`; an unrecognised value
  is also reported as a warning by `bubble_readiness_check`.
- `live` is never written, whatever the configuration.
- `BubbleProfile` gains both fields; `load_settings` reads them and `save_settings` writes them
  back. Today `save_settings` rebuilds the file from known fields and would silently drop them.
- `bubble_session_check` reports both in its `write_policy` block, together with a
  `policy_source` (`app_branches` for `auto`, `profile_setting` for `always`/`never`), so the
  agent knows up front what it may do.

### Scope of the tester exemption

Tester mode does **not** filter other write tools. Ordinary tools (`update_*`, `batch`,
`bubble_node_edit`, `bubble_editor_write`, ...) keep following `main_write_policy`. The tester
tools alone may write to main in an app with branches. Without that scoping, turning tester mode
on would open main to every write, which is exactly what the main lock exists to prevent.

Implementation: `main_write_allowed` keeps its current signature for ordinary writes. The tester
tools call the editor client with an explicit, narrow exemption (`purpose="tester_ids"`) that the
client honours only when the profile has `tester_mode: true` and the target is not `live`.

## Tools

All three refuse with `tester_mode_off` unless the profile has `tester_mode: true`, and refuse
with `live_never` when the target version is `live`. `app_version` defaults to `test`.

### `bubble_test_ids_plan(profile, page, app_version?)` - read only

- Reads the element tree of a page or reusable in the chosen version.
- Returns every element with `element_id`, name, type, tree path and current `html_id`, split into
  `reusable` (already has an id) and `missing` (no id).
- Reports whether the app's `expose_id_option` is on.
- Suggests no names: the agent chooses them.

### `bubble_test_ids_apply(profile, page, ids, app_version?, execute=false)`

`ids` is a list of `{element_id, html_id, replace?}`.

- `execute=false` returns the preview: what would be written, kept and refused.
- Validation, which refuses the whole batch when any item fails:
  - `invalid_html_id`: format must match `^[a-z][a-z0-9-]*$`;
  - `duplicate_html_id`: unique within the page, counting existing ids and the batch itself;
  - `element_not_found`.
- Reuse: an element that already has an id is skipped and reported as `kept`; it is replaced only
  when that item carries `replace: true`.
- When `expose_id_option` is off, the batch turns it on and records that it was off.
- Writes only each element's `unique_id` property, after the session's automatic savepoint.
- Records old and new values in the ledger before writing, and returns a `batch_id`.

### `bubble_test_ids_restore(profile, batch_id? | element_ids? | all=true, execute=false)`

- Puts each element back to its recorded old value, or clears the id when there was none.
- Restores an element only if its current id is still the one the tester set. Otherwise it is
  skipped as `conflict`, with its current value, and nothing is overwritten.
- Turns `expose_id_option` back off only with `all=true`, and only if the tester turned it on.
- Restored entries are marked in the ledger, so restoring twice is a no-op.

## Ledger

- One file per profile: `<config_dir>/tester/<profile>/id-ledger.jsonl`.
- One line per change: `batch_id`, timestamp, app, version, page, `element_id`, `old`, `new`,
  `status`. One line per batch for `expose_id_option`.
- Append-only: a restore appends a restore line instead of editing the original, so history stays
  complete. Current state per element is the fold of its lines.
- Order per batch: record intent (`pending`), write to Bubble, record the outcome (`applied` or
  `failed`). Restore ignores `failed` and `pending` entries.
- The ledger is local to the installation that made the changes; another installation does not
  see it. Accepted limitation.

## Errors

| error | when |
|---|---|
| `tester_mode_off` | profile does not have `tester_mode: true` |
| `live_never` | target version is `live` |
| `invalid_html_id`, `duplicate_html_id`, `element_not_found` | validation; whole batch refused, every problem listed |
| `session_expired` | existing session behaviour |

A failure part-way through a write reports exactly what was written and what was not, and the
ledger reflects the same.

## Open fact to confirm before `apply`

The `path_array` Bubble uses for an element's `unique_id` change has not been captured yet. The
existing builders set it inside the whole-properties update
(`aria_runtime/bubble_cli.py` `_apply_global_element_updates`). Before implementing `apply`, one
real id change is captured on a test app, with the owner's approval, and the narrow write is
built from that capture.

## Testing (TDD)

- Config: both keys read, defaults applied, unknown policy treated as `auto`, `save_settings`
  preserves both.
- `version_policy`: `always` and `never` override branch detection; with tester mode on, ordinary
  writes to main in an app with branches are still refused.
- `apply`: each validation; existing id kept; `replace`; `expose_id_option` turned on and
  recorded; ledger holds old and new; preview writes nothing.
- `restore`: by batch, by element, all; conflict when the id changed after the tester; restoring
  twice is a no-op; `expose_id_option` restored only with `all=true`.
- Tester tools refuse with `tester_mode_off` and `live_never`.

## Out of scope

- Filtering or hiding other write tools in tester mode.
- An installation-wide default for either key.
- Generated or prefixed id names.
- Sharing the ledger across installations.
