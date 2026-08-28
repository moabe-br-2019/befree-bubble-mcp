# Captured: how a Bubble global expression is stored

Captured 2026-08-28 against `mcp-test-app` (version `test`). Two nodes were compared:

| node | how it was made | file |
|------|-----------------|------|
| `bTGOw0` "User email" | authored by hand in the Bubble editor | `capture-global-expression-editor-authored.json` |
| `bpOVa` "mcp-probe-user-email" | written by the four MCP tools | `capture-global-expression-mcp-write.json` |

Both were read back with `bubble_live_node_read`, which goes through `window.appquery` and is the
only source of the **raw** encoding. The `.bubble` export is decoded and cannot be inverted - it is
what makes the two key spaces below diverge.

## The node

```json
{
  "id": "bTGOw0",
  "%nm": "User email",
  "btype_id": "text",
  "is_list": false,
  "parameters": {
    "bTGPA0": {"param_id": "bTGPA0", "param_name": "user", "btype_id": "user", "is_list": false}
  },
  "expression": {
    "%x": "GlobalExpressionParameter",
    "%p": {"global_expression_id": "bTGOw0", "param_id": "bTGPA0"},
    "%n": {"%x": "Message", "%nm": "email", "is_slidable": false},
    "is_slidable": false
  }
}
```

The hand-authored node and the MCP-written node are structurally identical: same keys, same nesting,
same `%x` constructors. Nothing in the editor's own encoding is missing from what the tools write.

`folder_id` appears on global expressions that live inside an editor folder and is **absent** on
both nodes here, so it is optional and a create does not have to supply it.

## Two key spaces for the same field

The name is `%nm` in the wire/live encoding and `name` in the decoded `.bubble` export. A reader that
only knows one of them silently falls back to the id. `list_global_expressions` accepts both; so does
name-based resolution in `GlobalExpressionService`.

The parameter map has no such split: `param_id`, `param_name`, `btype_id` and `is_list` carry the
same names in both spaces.

## Creation writes three changes

```
_index.id_to_path.<id>     -> "global_expressions.<id>"          Update index
global_expressions.<id>    -> {%nm, btype_id, id, is_list}       CreateGlobalExpression
_index.issues_list.<id>    -> [{message, node}] as a JSON string  Update index
```

The `issues_list` entry is what keeps the editor's issue checker reporting
`Global expression: remember to fill out expression` until a body is set. It carries a
`constructor_name` of `GlobalExpression` and one `json` arg holding the node's own path.

Parameters and the body are separate `ModifyGlobalExpression` writes against
`global_expressions.<id>.parameters.<param_id>` and `global_expressions.<id>.expression`. So an
expression is only usable after its body is set - creation alone leaves it flagged.

## Still unverified

`write_verify` reads every written path back and compares bytes, and all three writes passed. That is
not a render check: `render_unverified` stays true until a human opens the editor and confirms the
expression displays as `user's email`.
