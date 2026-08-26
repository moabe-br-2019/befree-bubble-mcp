# Session findings, 2026-08-26: the action write encoding, measured

Ground truth captured from live editor traffic on `mcp-test-app`. Fixture:
`tests/fixtures/expressions/action-encoding-pair-golden.json` holds the decoded and encoded
halves of the *same* action, which is what makes it usable.

## 0. The finding that supersedes the rest of this document

`window.appquery` exposes the node in BOTH forms:

- `node.raw()` returns the DECODED form (`type`, `properties`, `entries`, `next`, `name`, …)
- `node._raw()` returns the ENCODED form (`%x`, `%p`, `%e`, `%n`, `%nm`, `%ei`, …)

Measured on the same action, `_raw()` is **byte-identical to the body the editor itself POSTs**
to `/appeditor/write` (compared against the captured `CreateAction` change: equal).

So no encoder is needed and no key table has to be completed. Read with `_raw()`, patch the
leaf, write it back. The entire translation layer this branch built — `node_keys.py`, the
root-only rule, the three failed encoding hypotheses below — exists only because the read used
`raw()` instead of `_raw()`.

The sections below are kept because they document how the wrong path failed, and because the
key mapping is still useful for reading a `.bubble` export, which only has the decoded form.

## 1. The encoding is far wider than `%x`/`%p`

`docs/session-findings-2026-08-24.md` concluded: "Editor memory reads expose nodes with decoded
`type`/`properties` keys; write payloads use `%x`/`%p` at the node level with this same interior."
The second half is wrong, and the design built on it
(`docs/superpowers/specs/2026-08-26-live-node-read-and-inplace-edit-design.md`) is wrong with it.

Measured mapping, from a captured `CreateAction` body against the same action read back through
`window.appquery....raw()`:

| decoded (`raw()`) | encoded (write payload) |
|---|---|
| `type` | `%x` |
| `properties` | `%p` |
| `entries` | `%e` |
| `next` | `%n` |
| `name` | `%nm` |
| `element_id` | `%ei` |

`is_slidable` and `id` are unchanged. `new_password` / `new_password_again` are unchanged, while
`element_id` becomes `%ei` — so *some* property names encode and others do not. The
property-name half of the table is per-action-type and one capture cannot complete it.

The real encoded body:

```json
{"0": {"id": "bTHDN", "%x": "ResetPassword", "%p": {
  "new_password": {"%x": "TextExpression", "%e": {"0": {
     "%n": {"%nm": "get_data", "%x": "Message", "is_slidable": false},
     "%x": "GetElement", "%p": {"%ei": "bTGyp0"}, "is_slidable": false}}}}}}
```

## 2. Three inferred encodings, three failures

All three were written to a real app and judged by the Bubble editor, not by a re-read:

| form written | action type renders | property values |
|---|---|---|
| fully decoded, as `raw()` returns it | `[missing: null]` | destroyed |
| root only encoded (`%x`/`%p` at the node root) | correct | empty; issue checker: "Password should be text but right now it is a empty" |
| `type`/`properties` encoded recursively | correct | empty; no issue raised, still blank |

The middle row is what the spec mandated. The pattern explains itself against the table above:
the root-only form gets the node header right and leaves `%e`/`%n`/`%nm`/`%ei` decoded, so the
expression is unreadable; the recursive form fixes `%x`/`%p` everywhere and still leaves the
other four wrong.

This is the concrete evidence for the claim the 2026-08-24 session already made on other grounds:
the encoding is not derivable from a read. It has to be captured.

## 3. Two intents, two granularities

The editor does not use one write shape for everything:

- **`CreateAction`**, `path_array` `[..., "%wf", "<wf_id>", "actions"]`, body = the WHOLE actions
  map keyed `"0"`, `"1"`, … Used when creating or replacing actions.
- **`SetData`**, `path_array` `[..., "%wf", "<wf_id>", "actions", "0", "%p", "%ei"]`, body = the
  bare value (`"bS35G"`). Used when editing one property of one action.

Also observed: `ReorderActions` and `RemoveAction`, both addressing the `actions` container.

The `SetData` row matters most for this project: it is exactly the "patch one leaf" model
`patch_expression_leaf` was built for. What the implementation got wrong was not the idea but two
concrete things — the pointer must be expressed in ENCODED key space, and the body is the leaf
value alone, not the enclosing dict.

Every `SetData` write is accompanied by `Update index` changes for the touched ids and an
`_index.issues_list.<wf_id>` update.

## 4. The read path had four defects of its own

Found while driving the tool against the real editor; all fixed in this branch:

- The browser was seeded from the `browser-profiles/<profile>` directory rather than the stored
  session. That directory held cookies for Bubble's own `meta` app in `live` but not the app
  session, so the editor loaded the requested app and redirected to `https://bubble.io/` after
  ~3s, where `appquery` serves Bubble's own product. Reads then returned Bubble's app —
  128 pages, 46 workflows — and a verification once reported `verified: true` against it.
  Seeding the context from the stored session's `Cookie` header removes the redirect entirely.
- `read_live_node` never asserted that the tree it read belonged to the app requested.
  `root.appname()` / `root.app_version()` were available all along. The check now runs in the
  same evaluation as the read, because the redirect is on a timer and would slip between two.
- Readiness was treated as one app-wide moment. The tree loads lazily per subtree, and
  `window.appquery` is a getter that exists before it works.
- `NotReadyError` is not an `Error`: `name` and `message` are `null`, `String(e)` is
  `"[object Object]"`, `e instanceof Error` is false. It is identifiable only by its own
  `not_ready_key` property or `constructor.name`. A text match on `name`/`message` — the obvious
  implementation — can never fire.

## 5. Method note

The render check was scheduled as the last task of the plan. It should have been the first.
It cost one write and one screenshot and it invalidated the premise the whole design rested on.
Also: no BEFORE screenshot was taken, so the first breakage could not be distinguished from
pre-existing state without falling back to an untouched-action control and the issue checker.
