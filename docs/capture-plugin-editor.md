# Captured: how the plugin editor loads, saves and creates plugins

Captured 2026-11-25 against the throwaway plugins `mcp_test_plugin` (`1795610633201x773816577862139900`)
and `mcp_test_plugin_2`, with the Chrome extension's plugin-editor discovery mode
(`bubble-mcp plugin discovery summary|export`). The editor was driven by the owner and by Claude in
Chrome. Nothing was published. Redacted samples sit next to this file as
`docs/capture-plugin-editor-*.json`.

## The short version

**A plugin is one JSON document. The editor reads it whole and writes it whole.**

| step | call |
|---|---|
| load | `GET /appeditor/get_plugin?id=<plugin_id>` returns the document |
| save | `POST /appeditor/save_plugin` `{"id": <plugin_id>, "raw": <the whole document>}` returns `null` |
| after save | `POST /appeditor/update_plugin_meta_object` `{"method": "update_git_modified_status", "plugin_id"}` |
| edit lock | `POST /appeditor/plugin_editor_lock` `{"holder", "op": "acquire", "plugin_id"}` returns `{"held": bool}` |
| npm build | `POST /appeditor/create_action_s3_package` `{"node_version": 22, "package_json_string", "plugin_id"}` |
| create | `POST /workflow/start`: a workflow of Bubble's own `meta` app, not an `/appeditor` call |
| delete plugin | `POST /workflow/start`: dashboard "Danger zone" workflow, sets `deleted: true` |
| list | `POST /elasticsearch/msearch`: encrypted `{x, y, z}` body, not reproducible as-is |

So `pull` is one GET. `push` is one POST of the full document, and every save the editor makes is a
full replace. There is no diff/changes format like the app editor's `/appeditor/write`. The document
the GET returns equals what the last save sent (`dependencies`, `editor_counter`, `human`,
`plugin_actions`, `plugin_elements` and `published_bv` were identical). `meta_data` on the GET also
carries null-valued keys the save omitted (`categories`, `description`, `image`, `license`,
`link`, ...).

## The document

```
{
  "dependencies": {"plugin_api_version": 4, "use_jquery": true},
  "editor_counter": 18,
  "human": "mcp_test_plugin",
  "meta_data": {"name": "...", "platforms_new": "web", ...},
  "published_bv": 33,
  "plugin_elements": {"<id>": Element, ...},
  "plugin_actions":  {"<id>": Action, ...}
}
```

### Ids

Every element, field, state, event, element action, plugin action and return value gets a key
drawn from one plugin-wide counter, `editor_counter`, written as three letters A-Z (`AAC`, `AAD`, ...
`AAL` = 11). Creating anything takes the next value and bumps the counter. A writer must do the same:
use `editor_counter`, then increment it, so ids never collide with what the editor will generate
next.

Renaming changes `display` (elements, actions) or `name`/`caption` (fields, states). The key stays,
so local files can keep ids and renames never become delete plus create.

### Element (`plugin_elements.<id>`)

```
display, category ("visual elements"), icon, platform_type ("web"), add_is_visible,
default_dim {height, width},
headers {snippet: "<html>"},                       # element header
code {initialize|update|preview|reset: {fn: "function(...) {...}"}},
fields  {<id>: Field},
states  {<id>: {name, caption, value: <type>, is_list, initialization_code {fn}}},
events  {<id>: {name, caption}},
actions {<id>: {caption, code {fn}}}               # element actions
```

### Field (element fields and action fields)

`name`, `caption`, `editor`, `rank` (order), plus the following by editor:

| UI editor | `editor` | extra keys seen |
|---|---|---|
| Static text | `StaticText` | `default_val` |
| Static number | `StaticNumber` | |
| Checkbox | `Checkbox` | |
| Color | `Color` | |
| Dropdown | `Dropdown` | `options: "a,b,c"` |
| Static Image | `Image` | |
| Dynamic value | `DynamicValue` | `value: <type>` (`text`, `number`, `date`, `user`, ...), `is_list` |
| App Type | `custom_type` | |
| Information | `Label` | |

`optional: true` appears only when it is checked. Dynamic value types offered: text, number,
numeric range, yes / no, image, file, geographic address, date, date range, User, any thing, any
thing with fields. Only `text` was captured as a wire value. The others need a capture before a
writer uses them.

### Plugin action (`plugin_actions.<id>`)

```
display, category ("plugins"), type ("client_side_web" | "server_side" | ...),
fields {<id>: Field},
return_value {<id>: {name, caption}},              # server actions
code {
  client: {fn}, server: {fn},
  package: {fn: "<package.json text>"},            # npm, server actions
  package_used: true, package_hash, package_status: "up_to_date",
  automatically_added_packages: "{\"lodash\":\"latest\"}"
}
```

The action type dropdown offers Client side - Web / Mobile / Both and Server side. Only
`client_side_web` and `server_side` were captured.

### npm

Checking "This action uses node modules" makes the editor fill `package.json` from the `require`s
it sees. Clicking "rebuild" sends `create_action_s3_package` (about 1 s, response
`{"success": true, "npm_output": {...}}`). The next save then carries `package_status:
"up_to_date"` and a `package_hash`. Where the hash comes from (client side or a response not
captured) is still open. A writer that changes dependencies must rebuild and must not mark
`up_to_date` by itself.

## Concurrent editing: the editor lock

Each editor window holds a random `holder` id and keeps calling `plugin_editor_lock` with
`op: "acquire"`. The holder of the lock gets `{"held": true}`. A second window got
`{"held": false}` and turned **read-only** with "Multi-editing not allowed... Changes made in this
window will not be saved". Once the first window closed, the second acquired the lock.

For `push`: acquire the lock with a fresh holder before `save_plugin`, and refuse if `held` is
false, because someone has the editor open. This covers the spec's stale-tab concern with Bubble's
own mechanism. Whether `save_plugin` itself rejects a non-holder was not tested, so do not rely on
it. A release op (if any) was not observed.

## Create

Creating a plugin is not an `/appeditor` call. The dashboard (`bubble.io/home/plugins`, "Create a
plugin") runs a workflow of Bubble's own app through `POST /workflow/start`. The plugin name travels
as the value of an input element in `client_state.element_state`, and the response returns the new
`custom.plugins` thing id (= plugin id). The call depends on element ids of Bubble's dashboard
(`element_id: cmunT0`, `item_id: cmunZ0`, input `cmunR0`, page `AFw`), so it is fragile.
`bubble_plugin_create` should prefer driving the dashboard UI (Playwright on the stored session)
over replaying this, or replay it with a UI fallback. Right after creation the editor saves a
minimal document (`dependencies`, `meta_data.name`, `platforms_new`).

## Delete

**Inside a plugin** (field, state, event, action, element), deleting means removing the key from
the document, and the next `save_plugin` simply omits it. Deleting field `p_info` (`AAL`) left the
other fields untouched, `editor_counter` stayed at 18 (ids are never reused), and the remaining
`rank`s were not renumbered (0-7 here only because the deleted field was last).

**A whole plugin** is deleted from the dashboard: "⋯" > Delete, then a "Danger zone" modal that
asks to type `DELETE`. It is another `POST /workflow/start` of Bubble's `meta` app (`element_id:
cnWhv1`, `item_id: cnWic1`, the typed `DELETE` in `element_state`, the plugin as `group_data` of
the list cell). The response shows it sets `deleted: true` on the `custom.plugins` thing, so it is a
soft delete server side, but the UI offers no undo. The modal says apps already using the plugin
keep working, and new apps can no longer install it. Sample: `capture-plugin-editor-delete-plugin.json`.
Same fragility as create: a bulk-cleanup tool should drive this modal (Playwright), one plugin at a
time, with an explicit list confirmed by the owner.

## List

The plugin list on the dashboard comes from `POST /elasticsearch/msearch` with an encrypted
`{x, y, z}` body, the same scheme the Bubble runtime uses for searches. It is not replayable
without implementing that encryption. Options for `bubble_plugin_list`: read the dashboard page
(Playwright), or keep a local registry of pulled plugin ids. `GET /appeditor/get_plugin_count?id=`
returns how many apps use a plugin (0 here).

## Other calls on editor load

`get_plugin_editors` (collaborators, `[]`), `get_git_status` (`{"type": "no_repo"}`, since plugins
can sync to GitHub), `update_plugin_meta_object` with `sync_pv_from_s3`, and
`doapicallfromserver get_plugins_tags` (category list).

## Not captured yet

- Client side Mobile / Both action types; Dynamic value types other than `text`.
- Plugin-level shared header, additional keys, API calls tab.
- Whether `save_plugin` rejects a stale `editor_counter` or a non-holder.

## Security note

The raw discovery log (`~/.config/bubble-mcp/extension-companion/plugin-editor-discovery.jsonl`)
also captured dashboard traffic such as `/api/1.1/init/data`, which returns the account's linked
GitHub access token, email and billing data. Keep that log local and delete it after use. The
committed samples were exported through the redacting exporter and scrubbed of the account's user
id.
