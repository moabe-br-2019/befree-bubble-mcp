# Tester ids on a live editor - findings 2026-09-30 / 2026-10-01

Task 6 of `docs/superpowers/plans/2026-09-30-tester-mode.md`: confirm on a real Bubble app the two facts
the tester tools were built on, which had only been inferred from `.bubble` exports.

1. An element's HTML id is written with a narrow `SetData` at `<element pointer> + ["%p", "unique_id"]`
   and the body `{"%x": "TextExpression", "%e": {"0": "<id>"}}`.
2. A `null` body at that same path clears the id.

Both hold.

## Setup

- Profile `mcp-test`, app `mcp-test-app`, version `test`. Approved by the owner beforehand.
- `"tester_mode": true` was added to the profile in `~/.config/bubble-mcp/settings.json` for the run
  and the file was put back from a copy afterwards.
- Every call went through `bubble_mcp.server.tools.call_tool`, so the main-write guard, the session
  check and the automatic savepoint all ran as they do for an agent.

## Run

| step | call | result |
|---|---|---|
| plan | `bubble_test_ids_plan` on `["%p3", "bTGbC"]` (page `index`) | ok; `expose_id_option` already on; no element had an id |
| read before | path API, leaf of `tx_pricing` (`["%p3","bTGbC","%el","bxJag","%el","bAEHX","%el","bk5xf","%el","b3YLR","%p","unique_id"]`) | absent |
| apply | `bubble_test_ids_apply` `{pointer: <tx_pricing>, html_id: "qa-probe"}`, `execute=true` | ok; savepoint `before bubble_test_ids_apply` created; `not_confirmed: []`; batch `887649cedb8c` |
| read after apply | same leaf | `{"%x": "TextExpression", "%e": {"0": "qa-probe"}}` - exactly `html_id_body("qa-probe")` |
| render check | editor, `tx_pricing` > Interaction > Advanced > ID Attribute | shows `qa-probe` (owner's screenshot) |
| restore | `bubble_test_ids_restore` `batch_id=887649cedb8c`, `execute=true` | ok; savepoint created; `restored: [<tx_pricing>]`, no conflicts, `not_restored: []` |
| read after restore | same leaf | absent - the `null` body cleared the property |
| render check | editor reloaded, same field | empty (confirmed by the owner) |
| restore again | same call | `executed: false`, `restored: []` - nothing left open |

Ledger (`~/.config/bubble-mcp/tester/mcp-test/id-ledger.jsonl`): `pending` then `applied` for the id,
then one `restore` line carrying `app_id` and `app_version`.

## Notes

- The read-back added in the final fix wave matched on the first try: the path API returns the leaf in
  exactly the shape written, so exact equality is the right comparison.
- The editor session captured by `bubble_session_login` expired about a day later (401 from the path
  API), and a new login was needed before the restore. Plan a fresh login before long tester runs.
- That session's cookies opened neither a fresh Playwright context nor the stored browser profile
  in the editor: Bubble bounced the page to `bubble.io/` (the `not_logged_in` case
  `read_live_node` reports). HTTP reads and writes work with them; driving the editor UI does not.
  This also affects `bubble_live_node_read` / `bubble_node_edit` for this profile and is not caused by
  this branch.
