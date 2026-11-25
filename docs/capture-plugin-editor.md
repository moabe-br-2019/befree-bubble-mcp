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
| upload file | `POST /fileupload` `{appname: "meta", contents: <base64>, name, size, type, ...}` returns a CDN url |
| mobile deps check | `POST /appeditor/validate_mobile_dep_npm` `{"mobile_dep_str"}` returns `{"invalid_packages": [...]}` |
| install in app (test) | app editor `/appeditor/write`: `settings.client_safe.plugins.<plugin_id>_current = "current"` |
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
  "dependencies": {"plugin_api_version": 4, "use_jquery": true,
                   "mobile_dep": "{\"dayjs\": \"^1.11.0\"}"},       # extra mobile libraries (JSON text)
  "editor_counter": 23,
  "human": "mcp_test_plugin",            # original/internal name; NOT changed by renaming the public name
  "meta_data": MetaData,
  "published_bv": 33,
  "html_header": {"snippet": "<script>...</script>",
                  "params": {"<id>": {"key": "MCP_TOKEN"}}},   # one per _*_NAME_*_ found in the snippet
  "shared_keys": {"<id>": {"caption": "MCP_EXTRA", "type": "secure"}},  # Additional keys; Private=secure
  "assets": {"<id>": {"name": "mcp_logo", "url": "//meta-q.cdn.bubble.io/f.../file.png"}},
  "plugin_elements": {"<id>": Element, ...},
  "plugin_actions":  {"<id>": Action, ...}
}
```

Full sample after every section below was filled: `capture-plugin-editor-save-plugin-full.json`.

### General settings (`meta_data`)

```
name                  public name (rename here; "human" keeps the original)
image                 logo url, from /fileupload
description, link (Service URL), plugin_instructions, demo_page
platforms_new         "web" | "mobile" | "both"
categories            {"0": "analytics", "1": "internationalization"}   # object keyed by index, max 5
sa_node_version       22
```

GET also returns `license`, `data_tracked`, `data_tracked_description` (null here). License and
pricing live under "Versions and license", which was not captured (it is tied to publishing).

### Shared technical settings

- Bubble plugin API version and "Include jQuery 3" are `dependencies.plugin_api_version` /
  `use_jquery`.
- The HTML header is `html_header.snippet`. Every `_*_NAME_*_` the editor finds in it becomes an entry
  in `html_header.params` (its own id) and is exposed as a key in apps' Plugins tab. A writer that
  edits the snippet must recompute `params` the same way.
- Additional keys are `shared_keys`, with type Public or Private (`secure`).
- Shared assets are `assets`: upload with `/fileupload`, then add `{name, url}` under a new id.
- Mobile libraries (shown once a mobile item exists) are `dependencies.mobile_dep`, a JSON string.
  The editor checks it with `validate_mobile_dep_npm` while typing, and a partial version such as
  `^1.` is reported invalid. iOS plist and Android manifest entries exist in the UI but were not
  captured.

### Upload

`POST /fileupload` with JSON `{"app_version": "live", "appname": "meta", "contents": <base64>,
"name", "size", "type", "lastModified", "lastModifiedDate", "webkitRelativePath": ""}` returns the
protocol-relative CDN url as a JSON string. The same call serves the logo (`meta_data.image`) and
shared assets. Sample: `capture-plugin-editor-fileupload.json`.

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
display, category ("visual elements" | "input forms"), icon, platform_type ("web" | "mobile"),
default_dim {height, width},
# Bubble properties (General tab), keys present only when checked:
property_applications {resizable, MinWidth (= "Responsive"), Background, Border, BoxShadow,
                       Font, Padding: true},
can_fit_width, can_fit_height, add_hover_pressed, add_is_visible (standard visible),
add_large_file_upload_properties, support_autobinding, autobinding_value: <type>, (is_list),
headers {snippet: "<html>"},                       # element header
code {initialize|update|preview|reset: {fn: "function(...) {...}"}},
fields  {<id>: Field},
states  {<id>: {name, caption, value: <type>, is_list, initialization_code {fn}}},
events  {<id>: {name, caption}},
actions {<id>: {caption, code {fn}}}               # element actions
```

A **mobile** element (`platform_type: "mobile"`, created via New > Mobile once the plugin targets
mobile) has no initialize/update. Its code is `code.rn_component.fn` (a React Native functional
component in JSX, `function(props) {...}`) plus `code.preview` and, when "Add a reset function" is
checked, `has_reset_fn: true` with `code.reset`. Its Bubble properties are the same set (applied to
the outer `view`). The UI labels mobile elements MOBILE and web elements WEB.

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

Action types: Client side - Web `client_side_web`, Client side - Mobile `client_side_mobile`, Client
side - Both `client_side_both`, Server side `server_side`. Client actions of every kind keep both
`code.client` and `code.server` stubs.

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

## Install in an app (test)

The plugin editor's "Test app" box (any app name, then "Open test app") opens the app editor at
`bubble.io/page?id=<app>&test_plugin=<plugin_id>...` and the app editor writes through the normal
`/appeditor/write` (`intent: ChangeAppSetting`):

```
path_array: ["settings", "client_safe", "plugins", "<plugin_id>_current"]   body: "current"
```

This is the unpublished, in-editor version. `bubble_plugin_install` already writes
`settings.client_safe.plugins.<key>`, so installing the test version is that tool with key
`<plugin_id>_current` and value `"current"`. Installing a published version uses the version
string, see `capture-plugin-install.md`. Sample: `capture-plugin-editor-test-app-install.json`.

## Other calls on editor load

`get_plugin_editors` (collaborators, `[]`), `get_git_status` (`{"type": "no_repo"}`, since plugins
can sync to GitHub), `update_plugin_meta_object` with `sync_pv_from_s3`, and
`doapicallfromserver get_plugins_tags` (category list).

## Not captured yet

- Dynamic value types other than `text`; iOS plist / Android manifest entries.
- API calls tab; Versions and license (publishing side).
- Whether `save_plugin` rejects a stale `editor_counter` or a non-holder.

## Security note

The raw discovery log (`~/.config/bubble-mcp/extension-companion/plugin-editor-discovery.jsonl`)
also captured dashboard traffic such as `/api/1.1/init/data`, which returns the account's linked
GitHub access token, email and billing data. Keep that log local and delete it after use. The
committed samples were exported through the redacting exporter and scrubbed of the account's user
id.
