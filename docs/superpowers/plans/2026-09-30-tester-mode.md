# Per-profile write policy and tester mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each profile choose its main write policy, and add a tester mode whose three tools set, reuse and restore HTML element ids.

**Architecture:** Two new profile keys are read by `core/config.py`. `execution/version_policy.py` applies `main_write_policy` to ordinary writes, while the editor client gains a narrow `tester_ids` exemption that skips only the main check. A new package `execution/tester_ids/` holds pure element and validation helpers (`elements.py`), an append-only ledger (`ledger.py`) and the plan/apply/restore orchestration (`service.py`) with injected reader and writer. `server/tools.py` wires the tools.

**Tech Stack:** Python 3.11, pytest, the Bubble editor path API (`/appeditor/load_multiple_paths`) and write API (`/appeditor/write`).

**Spec:** `docs/superpowers/specs/2026-09-30-tester-mode-design.md`

## Global Constraints

- `main_write_policy` values: `auto`, `always`, `never`; default `auto`; unknown value behaves as `auto`.
- `tester_mode`: boolean, default `false`.
- No MCP tool writes either key.
- `live` is never written, whatever the configuration or tool.
- HTML id format: `^[a-z][a-z0-9-]*$`.
- `apply` and `restore` default to `execute=false` (preview).
- Ledger path: `<config_dir>/tester/<profile>/id-ledger.jsonl`, append-only.
- The tester exemption from the main lock applies only to `bubble_test_ids_apply` and `bubble_test_ids_restore`.
- Run tests with `.venv/Scripts/python -m pytest`. 14 unit tests already fail on this Windows machine (symlink/chmod/path separator/routing count); compare against `main`, do not fix them here.

## Refinements to the spec found while planning

- **Elements are addressed by pointer, not by bare element id.** The encoded tree nests elements under `%el` maps, and the only unambiguous address for a write is the full path. `plan` returns each element's `pointer`; `apply` and `restore` take pointers. The page is also given as a pointer (`["%p3", <page key>]` or `["%ed", <reusable key>]`), the same way `bubble_live_node_read` takes it; the agent finds the key with `bubble_context_find`.
- **Stored shape confirmed from the exports** (`~/.config/bubble-mcp/contexts/*/*.bubble`): an element's id is `properties.unique_id = {"type": "TextExpression", "entries": {"0": "btn-start"}}`, which encodes as `%p.unique_id = {"%x": "TextExpression", "%e": {"0": "btn-start"}}` - the same body `_apply_global_element_updates` already writes. App setting: `settings.client_safe.advanced_features.expose_id_option`.
- **The invalid-value warning goes in `bubble_session_check`'s `write_policy`** (`setting_warning`) instead of `bubble_readiness_check`, which only sequences other tools and has no per-profile config hook. Same visibility at the first call an agent makes.

## Review Focus

1. Two profiles pointing at the same `app_id` with different `main_write_policy` values: the client only knows the app id, so the strictest value (`never` > `auto` > `always`) must win. Pinned in Task 1.
2. An element whose `unique_id` holds an expression rather than a plain text (`%e` with more than one entry, or a dynamic part): treat it as "has an id" and never overwrite without `replace`, and on restore compare the whole body, not only entry `"0"`. Pinned in Task 3 and Task 5.
3. A restore where the id was cleared by someone else after the tester set it: the current value is absent, not equal to the tester's value, so it is a `conflict`, not a silent re-clear. Pinned in Task 5.
4. A second `apply` on the same element before any restore: restore must return to the value before the FIRST tester change, not to the tester's own earlier id. Pinned in Task 4 (fold) and Task 5.
5. A corrupt or partly written ledger line (process killed mid-append): reading must skip it and keep the rest, not crash every later restore. Pinned in Task 4.

---

### Task 1: Profile keys in the config

**Files:**
- Modify: `src/bubble_mcp/core/config.py`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Produces:
  - `BubbleProfile.main_write_policy: str = "auto"`, `BubbleProfile.tester_mode: bool = False`
  - `MAIN_WRITE_POLICIES = ("auto", "always", "never")`
  - `normalize_main_write_policy(value: object) -> tuple[str, str | None]` returns `(policy, warning)`
  - `app_write_settings(app_id: str, settings: BubbleMcpSettings | None = None) -> dict[str, Any]` returns `{"main_write_policy": str, "tester_mode": bool, "warning": str | None}`; strictest policy across profiles of that app; `tester_mode` true only if every profile of the app has it

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_config.py`)

```python
import json

from bubble_mcp.core.config import app_write_settings, normalize_main_write_policy


def _write_settings(tmp_path: Path, profiles: dict) -> None:
    (tmp_path / "settings.json").write_text(json.dumps({"profiles": profiles}), encoding="utf-8")


def test_profile_write_keys_default_to_auto_and_off(tmp_path: Path) -> None:
    _write_settings(tmp_path, {"p": {"app_id": "a"}})

    profile = load_settings(tmp_path).profiles["p"]

    assert profile.main_write_policy == "auto"
    assert profile.tester_mode is False


def test_profile_write_keys_are_read_and_survive_save(tmp_path: Path) -> None:
    _write_settings(tmp_path, {"p": {"app_id": "a", "main_write_policy": "always", "tester_mode": True}})

    save_settings(load_settings(tmp_path))
    profile = load_settings(tmp_path).profiles["p"]

    assert profile.main_write_policy == "always"
    assert profile.tester_mode is True


def test_an_unknown_policy_behaves_as_auto_with_a_warning() -> None:
    assert normalize_main_write_policy("ALWAYS") == ("always", None)
    assert normalize_main_write_policy(None) == ("auto", None)
    policy, warning = normalize_main_write_policy("sometimes")
    assert policy == "auto"
    assert "sometimes" in str(warning)


def test_the_strictest_profile_of_an_app_wins(tmp_path: Path) -> None:
    _write_settings(
        tmp_path,
        {
            "a1": {"app_id": "shared", "main_write_policy": "always", "tester_mode": True},
            "a2": {"app_id": "shared", "main_write_policy": "never", "tester_mode": False},
            "b": {"app_id": "other", "main_write_policy": "always", "tester_mode": True},
        },
    )
    settings = load_settings(tmp_path)

    assert app_write_settings("shared", settings)["main_write_policy"] == "never"
    assert app_write_settings("shared", settings)["tester_mode"] is False
    assert app_write_settings("other", settings) == {
        "main_write_policy": "always",
        "tester_mode": True,
        "warning": None,
    }
    assert app_write_settings("unknown", settings)["main_write_policy"] == "auto"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/unit/test_config.py -q`
Expected: FAIL with `ImportError: cannot import name 'app_write_settings'`

- [ ] **Step 3: Implement**

In `core/config.py`, add after the imports:

```python
MAIN_WRITE_POLICIES = ("auto", "always", "never")
# Strictest first: when two profiles of one app disagree, the client (which only knows the app
# id) must not pick the looser one.
_POLICY_STRICTNESS = {"never": 0, "auto": 1, "always": 2}


def normalize_main_write_policy(value: object) -> tuple[str, str | None]:
    """Return ``(policy, warning)``; anything unrecognised behaves as ``auto``."""

    text = str(value or "").strip().lower()
    if not text:
        return "auto", None
    if text in MAIN_WRITE_POLICIES:
        return text, None
    return "auto", f"main_write_policy {value!r} is not one of {', '.join(MAIN_WRITE_POLICIES)}; using auto."
```

Add two fields at the end of `BubbleProfile`:

```python
    # Where ordinary writes may go on main (test), and whether the tester tools are on. Chosen by
    # whoever installs the MCP, in settings.json; no tool writes them (see version_policy).
    main_write_policy: str = "auto"
    tester_mode: bool = False
```

In `load_settings`, add to the `BubbleProfile(...)` call:

```python
            main_write_policy=normalize_main_write_policy(raw_profile.get("main_write_policy"))[0],
            tester_mode=raw_profile.get("tester_mode") is True,
```

In `save_settings`, add inside the per-profile dict:

```python
                **(
                    {"main_write_policy": profile.main_write_policy}
                    if profile.main_write_policy != "auto"
                    else {}
                ),
                **({"tester_mode": True} if profile.tester_mode else {}),
```

Add at the end of the module:

```python
def app_write_settings(app_id: str, settings: BubbleMcpSettings | None = None) -> dict[str, Any]:
    """Write settings for an app, across every profile that points at it.

    The editor client only knows the app id, so when profiles disagree the strictest policy wins
    and tester mode counts only if every one of them has it on.
    """

    resolved = settings or load_settings()
    raw = load_json_file(get_settings_path(resolved.config_dir)).get("profiles", {})
    matching = [p for p in resolved.profiles.values() if p.app_id == app_id]
    if not matching:
        return {"main_write_policy": "auto", "tester_mode": False, "warning": None}
    policy = min((p.main_write_policy for p in matching), key=_POLICY_STRICTNESS.__getitem__)
    warnings = [
        normalize_main_write_policy((raw.get(p.name) or {}).get("main_write_policy"))[1]
        for p in matching
        if isinstance(raw, dict)
    ]
    return {
        "main_write_policy": policy,
        "tester_mode": all(p.tester_mode for p in matching),
        "warning": next((w for w in warnings if w), None),
    }
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/unit/test_config.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bubble_mcp/core/config.py tests/unit/test_config.py
git commit -m "feat: read main_write_policy and tester_mode from each profile"
```

---

### Task 2: The write policy follows the profile, with a tester exemption

**Files:**
- Modify: `src/bubble_mcp/execution/version_policy.py`
- Modify: `src/bubble_mcp/execution/client.py:276-295`
- Modify: `src/bubble_mcp/server/instructions.py:12`
- Test: `tests/unit/test_version_policy.py`

**Interfaces:**
- Consumes: `app_write_settings(app_id, settings=None)` from Task 1.
- Produces:
  - `write_policy(session, app_id, *, settings: dict | None = None, **kwargs)` result gains `main_write_policy`, `policy_source` (`"app_branches"` | `"profile_setting"`), `tester_mode`, `setting_warning`.
  - `BubbleEditorClient.write(payload, session, *, dry_run=False, calculate_derived=False, tester_ids=False)`: with `tester_ids=True` a write to `test` skips the main policy; `live` is still refused.

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_version_policy.py`)

```python
def _settings(monkeypatch: pytest.MonkeyPatch, **values: Any) -> None:
    answer = {"main_write_policy": "auto", "tester_mode": False, "warning": None, **values}
    monkeypatch.setattr(version_policy, "app_write_settings", lambda app_id: answer)


def test_always_opens_main_even_with_branches_and_skips_the_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _versions(monkeypatch, "93k8b")
    _settings(monkeypatch, main_write_policy="always")

    policy = version_policy.write_policy(SESSION, "team-app")

    assert policy["main_writable"] is True
    assert policy["policy_source"] == "profile_setting"
    assert calls == []
    assert version_policy.main_write_allowed("live", SESSION, "team-app")[0] is False


def test_never_closes_main_even_without_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    _versions(monkeypatch)
    _settings(monkeypatch, main_write_policy="never")

    policy = version_policy.write_policy(SESSION, "solo-app")

    assert policy["main_writable"] is False
    assert policy["policy_source"] == "profile_setting"
    assert "bubble_branch_create" in policy["advice"]


def test_auto_reports_its_source_and_the_tester_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    _versions(monkeypatch)
    _settings(monkeypatch, tester_mode=True, warning="bad value")

    policy = version_policy.write_policy(SESSION, "solo-app")

    assert policy["policy_source"] == "app_branches"
    assert policy["main_write_policy"] == "auto"
    assert policy["tester_mode"] is True
    assert policy["setting_warning"] == "bad value"


def test_tester_mode_does_not_open_main_to_ordinary_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    _versions(monkeypatch, "93k8b")
    _settings(monkeypatch, tester_mode=True)
    sent: list[str] = []

    with pytest.raises(MainVersionReadOnlyError):
        _client(sent).write(_write("test"), SESSION)
    assert sent == []


def test_the_tester_exemption_reaches_test_but_never_live(monkeypatch: pytest.MonkeyPatch) -> None:
    _versions(monkeypatch, "93k8b")
    _settings(monkeypatch, tester_mode=True)
    sent: list[str] = []

    _client(sent).write(_write("test"), SESSION, tester_ids=True)
    with pytest.raises(MainVersionReadOnlyError, match="live"):
        _client(sent).write(_write("live"), SESSION, tester_ids=True)
    assert len(sent) == 1
```

Also add an autouse fixture at the top of the file (after `SESSION`) so existing tests do not read the real settings file:

```python
@pytest.fixture(autouse=True)
def _auto_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        version_policy,
        "app_write_settings",
        lambda app_id: {"main_write_policy": "auto", "tester_mode": False, "warning": None},
    )
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/unit/test_version_policy.py -q`
Expected: FAIL (`AttributeError: ... has no attribute 'app_write_settings'` from the fixture)

- [ ] **Step 3: Implement**

In `version_policy.py`, extend the module docstring with one paragraph:

```
Each profile may override this with ``main_write_policy`` in settings.json: ``always`` opens
main, ``never`` closes it, ``auto`` (the default) is the rule above. ``tester_mode`` does not
change it for ordinary writes; the tester tools pass their own narrow exemption to the client.
```

Add the import `from bubble_mcp.core.config import app_write_settings`, and replace `write_policy` with:

```python
def write_policy(session: BubbleSessionData | None, app_id: str, **kwargs: Any) -> dict[str, Any]:
    """What an agent needs before writing: the branches, whether main takes writes, and why."""

    configured = app_write_settings(app_id)
    extra = {
        "main_write_policy": configured["main_write_policy"],
        "tester_mode": bool(configured["tester_mode"]),
        "setting_warning": configured["warning"],
    }
    if configured["main_write_policy"] == "always":
        return {
            "branches": [],
            "main_writable": True,
            "live_writable": False,
            "policy_source": "profile_setting",
            **extra,
            "advice": (
                "This profile sets main_write_policy=always: writes with app_version='test' are "
                "allowed (after the session's automatic savepoint). live is never written."
            ),
        }
    if configured["main_write_policy"] == "never":
        return {
            "branches": [],
            "main_writable": False,
            "live_writable": False,
            "policy_source": "profile_setting",
            **extra,
            "advice": (
                "This profile sets main_write_policy=never, so main (test) is read-only: create a "
                "development branch with bubble_branch_create (from_app_version='test') and pass "
                "its id as app_version."
            ),
        }
    return {**_branch_policy(session, app_id, **kwargs), "policy_source": "app_branches", **extra}
```

Rename the old body of `write_policy` (everything from `listed = app_branches(...)` to the end) to a private `_branch_policy(session, app_id, **kwargs)` unchanged.

In `client.py`, change the `write` signature and guard:

```python
    def write(
        self,
        payload: dict[str, Any],
        session: BubbleSessionData,
        *,
        dry_run: bool = False,
        calculate_derived: bool = False,
        tester_ids: bool = False,
    ) -> dict[str, Any]:
        normalized = normalize_write_payload(payload, session)
        if not dry_run and is_main_version(normalized.get("app_version")):
            # Nothing below this line may send a write to main unless the profile's policy allows
            # it; live never. tester_ids is the tester tools' narrow exemption for test only - its
            # callers check tester_mode themselves (execution/tester_ids). See version_policy.
            from bubble_mcp.execution.version_policy import LIVE, main_write_allowed

            version = str(normalized.get("app_version") or "test")
            if tester_ids and version.strip().lower() != LIVE:
                allowed, advice = True, ""
            else:
                allowed, advice = main_write_allowed(
                    version, session, str(normalized.get("appname") or session.app_id or "")
                )
            if not allowed:
                raise MainVersionReadOnlyError(version, advice=advice)
```

In `server/instructions.py`, replace the `write_policy:` sentence with:

```python
    "write_policy: whether main (test) takes writes is set per profile (main_write_policy auto/always/never; "
    "auto = read-only when the app has branches) - write to a branch, or create one with "
```

(keep the rest of that string as it is.)

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/unit/test_version_policy.py tests/unit/test_config.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bubble_mcp/execution/version_policy.py src/bubble_mcp/execution/client.py src/bubble_mcp/server/instructions.py tests/unit/test_version_policy.py
git commit -m "feat: let each profile set its main write policy"
```

---

### Task 3: Element helpers for tester ids

**Files:**
- Create: `src/bubble_mcp/execution/tester_ids/__init__.py` (empty docstring only)
- Create: `src/bubble_mcp/execution/tester_ids/elements.py`
- Test: `tests/unit/test_tester_ids_elements.py`

**Interfaces:**
- Produces:
  - `HTML_ID_PATTERN` (compiled `^[a-z][a-z0-9-]*$`)
  - `EXPOSE_ID_POINTER = ["settings", "client_safe", "advanced_features", "expose_id_option"]`
  - `html_id_body(value: str) -> dict` returns `{"%x": "TextExpression", "%e": {"0": value}}`
  - `html_id_text(body: object) -> str | None` returns the plain id, or `None` when absent; for a non-plain expression returns `json.dumps(body, sort_keys=True)`
  - `collect_elements(context_node: dict, context_pointer: list[str]) -> list[dict]`; each item `{"pointer": [...], "name": str, "type": str, "html_id": str | None, "html_id_body": object}`
  - `validate_batch(items: list[dict], elements: list[dict]) -> dict` returning `{"ok": bool, "problems": [...], "write": [...], "kept": [...]}`; `items` are `{"pointer": [...], "html_id": str, "replace": bool}`

- [ ] **Step 1: Write the failing tests**

```python
"""Pure helpers behind the tester id tools: read ids out of an encoded tree, validate a batch."""

from __future__ import annotations

from bubble_mcp.execution.tester_ids.elements import (
    collect_elements,
    html_id_body,
    html_id_text,
    validate_batch,
)

PAGE = {
    "%x": "Page",
    "%nm": "index",
    "%el": {
        "a": {"%x": "Button", "%nm": "Start", "%p": {"unique_id": html_id_body("btn-start")}},
        "b": {
            "%x": "Group",
            "%nm": "Form",
            "%p": {},
            "%el": {"c": {"%x": "Input", "%nm": "Email", "%p": {}}},
        },
        "d": {
            "%x": "Text",
            "%nm": "Dyn",
            "%p": {"unique_id": {"%x": "TextExpression", "%e": {"0": "row-", "1": {"%x": "X"}}}},
        },
    },
}
ROOT = ["%p3", "bTpage"]


def _by_name(name: str) -> dict:
    return next(e for e in collect_elements(PAGE, ROOT) if e["name"] == name)


def test_collect_walks_nested_elements_with_their_pointers() -> None:
    assert _by_name("Email")["pointer"] == ["%p3", "bTpage", "%el", "b", "%el", "c"]
    assert _by_name("Start")["html_id"] == "btn-start"
    assert _by_name("Email")["html_id"] is None


def test_an_expression_id_counts_as_an_id() -> None:
    assert _by_name("Dyn")["html_id"] is not None
    assert html_id_text(None) is None


def _batch(*items: dict) -> dict:
    return validate_batch(list(items), collect_elements(PAGE, ROOT))


def test_a_missing_id_is_written_and_an_existing_one_is_kept() -> None:
    result = _batch(
        {"pointer": _by_name("Email")["pointer"], "html_id": "login-email"},
        {"pointer": _by_name("Start")["pointer"], "html_id": "other"},
    )

    assert result["ok"] is True
    assert [w["html_id"] for w in result["write"]] == ["login-email"]
    assert [k["html_id"] for k in result["kept"]] == ["btn-start"]


def test_replace_overwrites_an_existing_id() -> None:
    result = _batch({"pointer": _by_name("Start")["pointer"], "html_id": "start", "replace": True})

    assert result["write"][0]["old_body"] == html_id_body("btn-start")


def test_every_problem_is_listed_and_the_batch_refused() -> None:
    result = _batch(
        {"pointer": _by_name("Email")["pointer"], "html_id": "Bad_Id"},
        {"pointer": _by_name("Form")["pointer"], "html_id": "btn-start"},
        {"pointer": ROOT + ["%el", "zzz"], "html_id": "ghost"},
    )

    assert result["ok"] is False
    assert sorted(p["error"] for p in result["problems"]) == [
        "duplicate_html_id",
        "element_not_found",
        "invalid_html_id",
    ]


def test_two_items_in_one_batch_cannot_share_an_id() -> None:
    result = _batch(
        {"pointer": _by_name("Email")["pointer"], "html_id": "same"},
        {"pointer": _by_name("Form")["pointer"], "html_id": "same"},
    )

    assert [p["error"] for p in result["problems"]] == ["duplicate_html_id"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/unit/test_tester_ids_elements.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'bubble_mcp.execution.tester_ids'`

- [ ] **Step 3: Implement**

`tester_ids/__init__.py`:

```python
"""Tester mode: set, reuse and restore HTML element ids so an app can be driven by tests."""
```

`tester_ids/elements.py`:

```python
"""Read HTML ids out of an encoded page tree and check a batch of new ones.

An element's id is ``%p.unique_id``, a TextExpression - ``{"%x": "TextExpression", "%e":
{"0": "btn-start"}}`` - the encoded form of ``properties.unique_id`` in the .bubble export.
Elements nest under ``%el`` maps, so the only unambiguous address is the full pointer.
"""

from __future__ import annotations

import json
import re
from typing import Any

HTML_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
EXPOSE_ID_POINTER = ["settings", "client_safe", "advanced_features", "expose_id_option"]


def html_id_body(value: str) -> dict[str, Any]:
    return {"%x": "TextExpression", "%e": {"0": value}}


def html_id_text(body: object) -> str | None:
    """The plain id, or a stable text for an expression id, or None when there is no id."""

    if not isinstance(body, dict):
        return None
    entries = body.get("%e")
    if not isinstance(entries, dict) or not entries:
        return None
    if list(entries) == ["0"] and isinstance(entries["0"], str):
        return entries["0"] or None
    return json.dumps(body, sort_keys=True)


def collect_elements(context_node: dict[str, Any], context_pointer: list[str]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], pointer: list[str]) -> None:
        children = node.get("%el")
        if not isinstance(children, dict):
            return
        for key, child in children.items():
            if not isinstance(child, dict):
                continue
            child_pointer = [*pointer, "%el", str(key)]
            props = child.get("%p") if isinstance(child.get("%p"), dict) else {}
            body = props.get("unique_id")
            found.append(
                {
                    "pointer": child_pointer,
                    "name": str(child.get("%nm") or ""),
                    "type": str(child.get("%x") or ""),
                    "html_id": html_id_text(body),
                    "html_id_body": body,
                }
            )
            walk(child, child_pointer)

    walk(context_node, [str(part) for part in context_pointer])
    return found


def validate_batch(items: list[dict[str, Any]], elements: list[dict[str, Any]]) -> dict[str, Any]:
    by_pointer = {tuple(e["pointer"]): e for e in elements}
    taken = {e["html_id"]: tuple(e["pointer"]) for e in elements if e["html_id"]}
    problems: list[dict[str, Any]] = []
    write: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    claimed: dict[str, tuple[str, ...]] = {}
    for item in items:
        pointer = tuple(str(part) for part in item.get("pointer") or [])
        new_id = str(item.get("html_id") or "")
        element = by_pointer.get(pointer)
        if element is None:
            problems.append({"error": "element_not_found", "pointer": list(pointer)})
            continue
        if element["html_id"] and not item.get("replace"):
            kept.append({"pointer": list(pointer), "html_id": element["html_id"]})
            continue
        if not HTML_ID_PATTERN.match(new_id):
            problems.append({"error": "invalid_html_id", "pointer": list(pointer), "html_id": new_id})
            continue
        owner = taken.get(new_id)
        if (owner is not None and owner != pointer) or new_id in claimed:
            problems.append({"error": "duplicate_html_id", "pointer": list(pointer), "html_id": new_id})
            continue
        claimed[new_id] = pointer
        write.append(
            {"pointer": list(pointer), "html_id": new_id, "old_body": element["html_id_body"]}
        )
    return {"ok": not problems, "problems": problems, "write": write, "kept": kept}
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/unit/test_tester_ids_elements.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bubble_mcp/execution/tester_ids tests/unit/test_tester_ids_elements.py
git commit -m "feat: read and validate tester html ids from an encoded page"
```

---

### Task 4: The id ledger

**Files:**
- Create: `src/bubble_mcp/execution/tester_ids/ledger.py`
- Test: `tests/unit/test_tester_ids_ledger.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `ledger_path(profile: str, config_dir: Path | None = None) -> Path`
  - `append(profile, entry: dict, config_dir=None) -> None` (adds `at` timestamp)
  - `read_entries(profile, config_dir=None) -> list[dict]` (skips unparseable lines)
  - `open_changes(profile, *, batch_id=None, pointers=None, config_dir=None) -> list[dict]`: per pointer, the applied, not-yet-restored state: `{"pointer", "app_id", "app_version", "old_body", "new_body", "batch_ids"}` where `old_body` is the body before the FIRST open tester change and `new_body` the latest one
  - `open_expose_flips(profile, config_dir=None) -> list[dict]`: applied, unrestored `expose_id` entries
  - Entry kinds: `{"kind": "id", "batch_id", "app_id", "app_version", "pointer", "old_body", "new_body", "status": "pending"|"applied"|"failed"}`, `{"kind": "expose_id", "batch_id", "app_id", "app_version", "old": bool, "new": True, "status"}`, `{"kind": "restore", "pointer" | "expose_id": True, "batch_id"}`

- [ ] **Step 1: Write the failing tests**

```python
"""The ledger is append-only; the open state of each element is the fold of its lines."""

from __future__ import annotations

from pathlib import Path

from bubble_mcp.execution.tester_ids import ledger

P = ["%p3", "pg", "%el", "a"]


def _id(batch: str, old: object, new: object, status: str = "applied") -> dict:
    return {"kind": "id", "batch_id": batch, "app_id": "app", "app_version": "test",
            "pointer": P, "old_body": old, "new_body": new, "status": status}


def test_entries_are_appended_and_read_back(tmp_path: Path) -> None:
    ledger.append("p", _id("b1", None, "x"), config_dir=tmp_path)

    entries = ledger.read_entries("p", config_dir=tmp_path)

    assert entries[0]["batch_id"] == "b1"
    assert "at" in entries[0]
    assert ledger.ledger_path("p", tmp_path) == tmp_path / "tester" / "p" / "id-ledger.jsonl"


def test_a_torn_line_is_skipped(tmp_path: Path) -> None:
    ledger.append("p", _id("b1", None, "x"), config_dir=tmp_path)
    with ledger.ledger_path("p", tmp_path).open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "id", "batch')

    assert len(ledger.read_entries("p", config_dir=tmp_path)) == 1


def test_two_changes_restore_to_the_first_old_value(tmp_path: Path) -> None:
    ledger.append("p", _id("b1", None, "x"), config_dir=tmp_path)
    ledger.append("p", _id("b2", "x", "y"), config_dir=tmp_path)

    [change] = ledger.open_changes("p", config_dir=tmp_path)

    assert change["old_body"] is None
    assert change["new_body"] == "y"
    assert change["batch_ids"] == ["b1", "b2"]


def test_failed_and_pending_lines_are_not_open(tmp_path: Path) -> None:
    ledger.append("p", _id("b1", None, "x", status="pending"), config_dir=tmp_path)
    ledger.append("p", _id("b2", None, "x", status="failed"), config_dir=tmp_path)

    assert ledger.open_changes("p", config_dir=tmp_path) == []


def test_a_restored_pointer_is_closed_and_filters_work(tmp_path: Path) -> None:
    ledger.append("p", _id("b1", None, "x"), config_dir=tmp_path)
    assert ledger.open_changes("p", batch_id="other", config_dir=tmp_path) == []
    assert len(ledger.open_changes("p", pointers=[P], config_dir=tmp_path)) == 1

    ledger.append("p", {"kind": "restore", "pointer": P, "batch_id": "b1"}, config_dir=tmp_path)

    assert ledger.open_changes("p", config_dir=tmp_path) == []


def test_expose_flips_open_until_restored(tmp_path: Path) -> None:
    flip = {"kind": "expose_id", "batch_id": "b1", "app_id": "app", "app_version": "test",
            "old": False, "new": True, "status": "applied"}
    ledger.append("p", flip, config_dir=tmp_path)
    assert len(ledger.open_expose_flips("p", config_dir=tmp_path)) == 1

    ledger.append("p", {"kind": "restore", "expose_id": True, "batch_id": "b1"}, config_dir=tmp_path)

    assert ledger.open_expose_flips("p", config_dir=tmp_path) == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/unit/test_tester_ids_ledger.py -q`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement**

`tester_ids/ledger.py`:

```python
"""Append-only record of every id the tester set, so each can be put back.

One JSON object per line. Nothing is edited in place: a restore appends a ``restore`` line. The
open state of an element is the fold of its lines since its last restore, so a second change
before a restore still returns to the value before the first one.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bubble_mcp.core.config import get_config_dir


def ledger_path(profile: str, config_dir: Path | None = None) -> Path:
    return (config_dir or get_config_dir()) / "tester" / profile / "id-ledger.jsonl"


def append(profile: str, entry: dict[str, Any], config_dir: Path | None = None) -> None:
    path = ledger_path(profile, config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {**entry, "at": datetime.now(timezone.utc).isoformat()}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, sort_keys=True) + "\n")


def read_entries(profile: str, config_dir: Path | None = None) -> list[dict[str, Any]]:
    path = ledger_path(profile, config_dir)
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue  # a line torn by a killed process; the rest stays usable
        if isinstance(value, dict):
            entries.append(value)
    return entries


def open_changes(
    profile: str,
    *,
    batch_id: str | None = None,
    pointers: list[list[str]] | None = None,
    config_dir: Path | None = None,
) -> list[dict[str, Any]]:
    state: dict[tuple[str, ...], dict[str, Any]] = {}
    for entry in read_entries(profile, config_dir):
        key = tuple(str(part) for part in entry.get("pointer") or [])
        if entry.get("kind") == "restore" and key:
            state.pop(key, None)
        elif entry.get("kind") == "id" and entry.get("status") == "applied" and key:
            current = state.get(key)
            if current is None:
                state[key] = {
                    "pointer": list(key),
                    "app_id": entry.get("app_id"),
                    "app_version": entry.get("app_version"),
                    "old_body": entry.get("old_body"),
                    "new_body": entry.get("new_body"),
                    "batch_ids": [entry.get("batch_id")],
                }
            else:
                current["new_body"] = entry.get("new_body")
                current["batch_ids"].append(entry.get("batch_id"))
    wanted = {tuple(str(p) for p in pointer) for pointer in pointers} if pointers else None
    return [
        change
        for key, change in state.items()
        if (batch_id is None or batch_id in change["batch_ids"]) and (wanted is None or key in wanted)
    ]


def open_expose_flips(profile: str, config_dir: Path | None = None) -> list[dict[str, Any]]:
    flips: dict[str, dict[str, Any]] = {}
    for entry in read_entries(profile, config_dir):
        if entry.get("kind") == "expose_id" and entry.get("status") == "applied":
            flips[str(entry.get("batch_id"))] = entry
        elif entry.get("kind") == "restore" and entry.get("expose_id"):
            flips.clear()
    return list(flips.values())
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/unit/test_tester_ids_ledger.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bubble_mcp/execution/tester_ids/ledger.py tests/unit/test_tester_ids_ledger.py
git commit -m "feat: keep an append-only ledger of tester id changes"
```

---

### Task 5: plan, apply and restore

**Files:**
- Create: `src/bubble_mcp/execution/tester_ids/service.py`
- Test: `tests/unit/test_tester_ids_service.py`

**Interfaces:**
- Consumes: Task 1 `load_settings`, `resolve_profile`; Task 3 helpers; Task 4 ledger; `node_edit._change(path_array, body, *, session_id)`; `compiler.payload.bubble_session_id()`; `deploy_preview.read_nodes_over_http(profile, pointers, *, app_id, app_version)`; `BubbleEditorClient.write(..., tester_ids=True)` from Task 2.
- Produces (all return a JSON-serializable dict with `ok`):
  - `plan_ids(profile, pointer, *, app_version="test", reader=None) -> dict`
  - `apply_ids(profile, pointer, ids, *, app_version="test", execute=False, reader=None, writer=None, config_dir=None) -> dict`
  - `restore_ids(profile, *, batch_id=None, pointers=None, restore_all=False, app_version="test", execute=False, reader=None, writer=None, config_dir=None) -> dict`
  - `reader(profile, pointers, *, app_id, app_version) -> dict[tuple, Any]` (same shape as `read_nodes_over_http`)
  - `writer(payload: dict) -> dict` (defaults to `BubbleEditorClient().write(payload, session, tester_ids=True)`)
  - Refusals: `{"ok": False, "error": "tester_mode_off" | "live_never" | "no_session", "message": str}`

- [ ] **Step 1: Write the failing tests**

```python
"""The tester tools: preview by default, ledger before write, restore only what is still ours."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bubble_mcp.core.config import BubbleMcpSettings, BubbleProfile
from bubble_mcp.execution.tester_ids import ledger, service
from bubble_mcp.execution.tester_ids.elements import EXPOSE_ID_POINTER, html_id_body

ROOT = ["%p3", "pg"]
EMAIL = ["%p3", "pg", "%el", "c"]
START = ["%p3", "pg", "%el", "a"]


class FakeApp:
    """A page and a settings flag held in memory; reads and writes go through it."""

    def __init__(self) -> None:
        self.page: dict[str, Any] = {
            "%x": "Page",
            "%el": {
                "a": {"%x": "Button", "%nm": "Start", "%p": {"unique_id": html_id_body("btn-start")}},
                "c": {"%x": "Input", "%nm": "Email", "%p": {}},
            },
        }
        self.expose = False
        self.writes: list[dict[str, Any]] = []

    def _node(self, pointer: list[str]) -> dict[str, Any]:
        node = self.page
        for part in pointer[2:]:
            node = node[part]
        return node

    def reader(self, profile: str, pointers: Any, **_: Any) -> dict[tuple, Any]:
        out: dict[tuple, Any] = {}
        for pointer in pointers:
            p = list(pointer)
            if p == EXPOSE_ID_POINTER:
                out[tuple(p)] = self.expose
            elif p == ROOT:
                out[tuple(p)] = self.page
            elif p[-2:] == ["%p", "unique_id"]:
                value = self._node(p[:-2]).get("%p", {}).get("unique_id")
                if value is not None:
                    out[tuple(p)] = value
        return out

    def writer(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.writes.append(payload)
        for change in payload["changes"]:
            path = change["path_array"]
            if path == EXPOSE_ID_POINTER:
                self.expose = change["body"]
            else:
                props = self._node(path[:-2]).setdefault("%p", {})
                if change["body"] is None:
                    props.pop("unique_id", None)
                else:
                    props["unique_id"] = change["body"]
        return {"ok": True}


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeApp:
    def settings(tester: bool = True) -> BubbleMcpSettings:
        profile = BubbleProfile(name="p", app_id="app", appname="app", tester_mode=tester)
        return BubbleMcpSettings(config_dir=tmp_path, default_profile="p", profiles={"p": profile})

    monkeypatch.setattr(service, "load_settings", lambda: settings())
    return FakeApp()


def _apply(app: FakeApp, tmp_path: Path, ids: list[dict], execute: bool = True) -> dict:
    return service.apply_ids("p", ROOT, ids, execute=execute, reader=app.reader,
                             writer=app.writer, config_dir=tmp_path)


def _restore(app: FakeApp, tmp_path: Path, **kwargs: Any) -> dict:
    return service.restore_ids("p", execute=True, reader=app.reader, writer=app.writer,
                               config_dir=tmp_path, **kwargs)


def test_plan_splits_reusable_and_missing(app: FakeApp) -> None:
    result = service.plan_ids("p", ROOT, reader=app.reader)

    assert [e["html_id"] for e in result["reusable"]] == ["btn-start"]
    assert [e["name"] for e in result["missing"]] == ["Email"]
    assert result["expose_id_option"] is False


def test_the_tools_refuse_without_tester_mode_and_on_live(
    app: FakeApp, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert service.plan_ids("p", ROOT, app_version="live", reader=app.reader)["error"] == "live_never"
    off = BubbleProfile(name="p", app_id="app", appname="app")
    monkeypatch.setattr(service, "load_settings",
                        lambda: BubbleMcpSettings(config_dir=tmp_path, default_profile="p", profiles={"p": off}))
    assert service.plan_ids("p", ROOT, reader=app.reader)["error"] == "tester_mode_off"


def test_preview_writes_nothing(app: FakeApp, tmp_path: Path) -> None:
    result = _apply(app, tmp_path, [{"pointer": EMAIL, "html_id": "login-email"}], execute=False)

    assert result["ok"] is True and result["executed"] is False
    assert app.writes == []
    assert ledger.read_entries("p", config_dir=tmp_path) == []


def test_apply_writes_only_the_id_turns_expose_on_and_records_both(app: FakeApp, tmp_path: Path) -> None:
    result = _apply(app, tmp_path, [{"pointer": EMAIL, "html_id": "login-email"},
                                    {"pointer": START, "html_id": "ignored"}])

    assert result["ok"] is True and result["batch_id"]
    assert [k["html_id"] for k in result["kept"]] == ["btn-start"]
    paths = [c["path_array"] for c in app.writes[0]["changes"]]
    assert paths == [EXPOSE_ID_POINTER, EMAIL + ["%p", "unique_id"]]
    assert app.page["%el"]["c"]["%p"]["unique_id"] == html_id_body("login-email")
    assert app.expose is True
    assert len(ledger.open_changes("p", config_dir=tmp_path)) == 1
    assert len(ledger.open_expose_flips("p", config_dir=tmp_path)) == 1


def test_an_invalid_batch_writes_nothing(app: FakeApp, tmp_path: Path) -> None:
    result = _apply(app, tmp_path, [{"pointer": EMAIL, "html_id": "btn-start"}])

    assert result["ok"] is False
    assert result["problems"][0]["error"] == "duplicate_html_id"
    assert app.writes == []


def test_a_failed_write_is_recorded_as_failed(app: FakeApp, tmp_path: Path) -> None:
    app.writer = lambda payload: {"ok": False, "error": "boom"}  # type: ignore[method-assign]

    result = _apply(app, tmp_path, [{"pointer": EMAIL, "html_id": "login-email"}])

    assert result["ok"] is False
    assert {e["status"] for e in ledger.read_entries("p", config_dir=tmp_path)} >= {"pending", "failed"}
    assert ledger.open_changes("p", config_dir=tmp_path) == []


def test_restore_puts_back_the_first_old_value_and_is_idempotent(app: FakeApp, tmp_path: Path) -> None:
    _apply(app, tmp_path, [{"pointer": START, "html_id": "start-a", "replace": True}])
    _apply(app, tmp_path, [{"pointer": START, "html_id": "start-b", "replace": True}])

    first = _restore(app, tmp_path, pointers=[START])
    second = _restore(app, tmp_path, pointers=[START])

    assert app.page["%el"]["a"]["%p"]["unique_id"] == html_id_body("btn-start")
    assert [r["pointer"] for r in first["restored"]] == [START]
    assert second["restored"] == []


def test_restore_clears_an_id_that_did_not_exist(app: FakeApp, tmp_path: Path) -> None:
    result = _apply(app, tmp_path, [{"pointer": EMAIL, "html_id": "login-email"}])

    _restore(app, tmp_path, batch_id=result["batch_id"])

    assert "unique_id" not in app.page["%el"]["c"]["%p"]


def test_restore_skips_an_id_someone_changed_or_cleared(app: FakeApp, tmp_path: Path) -> None:
    _apply(app, tmp_path, [{"pointer": EMAIL, "html_id": "login-email"},
                           {"pointer": START, "html_id": "start", "replace": True}])
    app.page["%el"]["c"]["%p"]["unique_id"] = html_id_body("human-choice")
    app.page["%el"]["a"]["%p"].pop("unique_id")

    result = _restore(app, tmp_path, restore_all=True)

    assert sorted(c["pointer"][-1] for c in result["conflicts"]) == ["a", "c"]
    assert app.page["%el"]["c"]["%p"]["unique_id"] == html_id_body("human-choice")


def test_expose_goes_back_off_only_with_all(app: FakeApp, tmp_path: Path) -> None:
    result = _apply(app, tmp_path, [{"pointer": EMAIL, "html_id": "login-email"}])

    _restore(app, tmp_path, batch_id=result["batch_id"])
    assert app.expose is True
    _apply(app, tmp_path, [{"pointer": EMAIL, "html_id": "login-email"}])
    _restore(app, tmp_path, restore_all=True)
    assert app.expose is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/unit/test_tester_ids_service.py -q`
Expected: FAIL with `ImportError: cannot import name 'service'`

- [ ] **Step 3: Implement**

`tester_ids/service.py`:

```python
"""plan / apply / restore for tester ids. Reader and writer are injected so tests need no Bubble.

Only the element's ``%p.unique_id`` leaf and the app's ``expose_id_option`` are ever written.
Every write goes through the ledger first (pending), then Bubble, then the outcome.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable

from bubble_mcp.core.config import load_settings, resolve_profile
from bubble_mcp.execution.tester_ids import ledger
from bubble_mcp.execution.tester_ids.elements import (
    EXPOSE_ID_POINTER,
    collect_elements,
    html_id_body,
    validate_batch,
)

LIVE = "live"
Reader = Callable[..., dict[tuple, Any]]
Writer = Callable[[dict[str, Any]], dict[str, Any]]


def _refusal(error: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": error, "message": message}


def _gate(profile: str, app_version: str) -> tuple[Any, dict[str, Any] | None]:
    if str(app_version).strip().lower() == LIVE:
        return None, _refusal("live_never", "live is the deployed app and is never written by this MCP.")
    resolved = resolve_profile(load_settings(), profile)
    if resolved is None or not resolved.tester_mode:
        return None, _refusal(
            "tester_mode_off",
            f"Profile {profile!r} does not have tester_mode: true in settings.json. Only whoever "
            "installs the MCP can turn it on.",
        )
    return resolved, None


def _default_reader() -> Reader:
    from bubble_mcp.execution.deploy_preview import read_nodes_over_http

    return read_nodes_over_http


def _default_writer(profile: str) -> Writer:
    from bubble_mcp.execution.client import BubbleEditorClient
    from bubble_mcp.sessions.store import load_session

    session = load_session(profile)
    if session is None:
        raise RuntimeError("no_session")
    client = BubbleEditorClient()
    return lambda payload: client.write(payload, session, tester_ids=True)


def _read(reader: Reader, profile: str, app_id: str, app_version: str, pointers: list[list[str]]) -> dict[tuple, Any]:
    return reader(profile, [tuple(p) for p in pointers], app_id=app_id, app_version=app_version)


def _payload(app_id: str, app_version: str, changes: list[tuple[list[str], Any]]) -> dict[str, Any]:
    from bubble_mcp.compiler.payload import bubble_session_id
    from bubble_mcp.execution.node_edit import _change

    session_id = bubble_session_id()
    return {
        "appname": app_id,
        "app_version": app_version,
        "changes": [_change(path, body, session_id=session_id) for path, body in changes],
    }


def _id_leaf(pointer: list[str]) -> list[str]:
    return [*pointer, "%p", "unique_id"]


def plan_ids(profile: str, pointer: list[str], *, app_version: str = "test", reader: Reader | None = None) -> dict[str, Any]:
    resolved, refusal = _gate(profile, app_version)
    if refusal:
        return refusal
    nodes = _read(reader or _default_reader(), profile, resolved.app_id, app_version, [pointer, EXPOSE_ID_POINTER])
    context = nodes.get(tuple(pointer))
    if not isinstance(context, dict):
        return _refusal("element_not_found", f"Nothing at {pointer} in {app_version}.")
    elements = collect_elements(context, pointer)
    public = [{k: v for k, v in e.items() if k != "html_id_body"} for e in elements]
    return {
        "ok": True,
        "pointer": pointer,
        "app_version": app_version,
        "expose_id_option": bool(nodes.get(tuple(EXPOSE_ID_POINTER))),
        "reusable": [e for e in public if e["html_id"]],
        "missing": [e for e in public if not e["html_id"]],
    }


def apply_ids(
    profile: str,
    pointer: list[str],
    ids: list[dict[str, Any]],
    *,
    app_version: str = "test",
    execute: bool = False,
    reader: Reader | None = None,
    writer: Writer | None = None,
    config_dir: Path | None = None,
) -> dict[str, Any]:
    resolved, refusal = _gate(profile, app_version)
    if refusal:
        return refusal
    read = reader or _default_reader()
    nodes = _read(read, profile, resolved.app_id, app_version, [pointer, EXPOSE_ID_POINTER])
    context = nodes.get(tuple(pointer))
    if not isinstance(context, dict):
        return _refusal("element_not_found", f"Nothing at {pointer} in {app_version}.")
    checked = validate_batch(ids, collect_elements(context, pointer))
    if not checked["ok"]:
        return {"ok": False, "executed": False, "problems": checked["problems"], "kept": checked["kept"]}
    expose_on = bool(nodes.get(tuple(EXPOSE_ID_POINTER)))
    changes: list[tuple[list[str], Any]] = []
    if not expose_on and checked["write"]:
        changes.append((EXPOSE_ID_POINTER, True))
    changes += [(_id_leaf(w["pointer"]), html_id_body(w["html_id"])) for w in checked["write"]]
    preview = {
        "write": [{"pointer": w["pointer"], "html_id": w["html_id"]} for w in checked["write"]],
        "kept": checked["kept"],
        "turns_expose_id_on": not expose_on and bool(checked["write"]),
    }
    if not execute or not changes:
        return {"ok": True, "executed": False, **preview}

    batch_id = uuid.uuid4().hex[:12]
    base = {"batch_id": batch_id, "app_id": resolved.app_id, "app_version": app_version}
    lines: list[dict[str, Any]] = []
    if preview["turns_expose_id_on"]:
        lines.append({**base, "kind": "expose_id", "old": False, "new": True})
    lines += [
        {**base, "kind": "id", "pointer": w["pointer"], "old_body": w["old_body"],
         "new_body": html_id_body(w["html_id"])}
        for w in checked["write"]
    ]
    for line in lines:
        ledger.append(profile, {**line, "status": "pending"}, config_dir)
    try:
        result = (writer or _default_writer(profile))(_payload(resolved.app_id, app_version, changes))
    except Exception as error:  # noqa: BLE001 - recorded, then reported
        result = {"ok": False, "error": f"{type(error).__name__}: {error}"}
    status = "applied" if result.get("ok") else "failed"
    for line in lines:
        ledger.append(profile, {**line, "status": status}, config_dir)
    return {"ok": status == "applied", "executed": True, "batch_id": batch_id, **preview,
            "write_result": result}


def restore_ids(
    profile: str,
    *,
    batch_id: str | None = None,
    pointers: list[list[str]] | None = None,
    restore_all: bool = False,
    app_version: str = "test",
    execute: bool = False,
    reader: Reader | None = None,
    writer: Writer | None = None,
    config_dir: Path | None = None,
) -> dict[str, Any]:
    resolved, refusal = _gate(profile, app_version)
    if refusal:
        return refusal
    if not (batch_id or pointers or restore_all):
        return _refusal("nothing_selected", "Pass batch_id, element pointers, or all=true.")
    open_changes = [
        c for c in ledger.open_changes(profile, batch_id=batch_id, pointers=pointers, config_dir=config_dir)
        if c["app_version"] == app_version and c["app_id"] == resolved.app_id
    ]
    current = _read(reader or _default_reader(), profile, resolved.app_id, app_version,
                    [_id_leaf(c["pointer"]) for c in open_changes]) if open_changes else {}
    restorable, conflicts = [], []
    for change in open_changes:
        now = current.get(tuple(_id_leaf(change["pointer"])))
        if now == change["new_body"]:
            restorable.append(change)
        else:
            conflicts.append({"pointer": change["pointer"], "current": now, "expected": change["new_body"]})
    flips = ledger.open_expose_flips(profile, config_dir) if restore_all else []
    changes: list[tuple[list[str], Any]] = [(_id_leaf(c["pointer"]), c["old_body"]) for c in restorable]
    if flips:
        changes.append((EXPOSE_ID_POINTER, False))
    summary = {
        "restored": [{"pointer": c["pointer"]} for c in restorable],
        "conflicts": conflicts,
        "turns_expose_id_off": bool(flips),
    }
    if not execute or not changes:
        return {"ok": True, "executed": False, **summary}
    try:
        result = (writer or _default_writer(profile))(_payload(resolved.app_id, app_version, changes))
    except Exception as error:  # noqa: BLE001 - reported; nothing is marked restored
        result = {"ok": False, "error": f"{type(error).__name__}: {error}"}
    if not result.get("ok"):
        return {"ok": False, "executed": True, **summary, "restored": [], "write_result": result}
    for change in restorable:
        ledger.append(profile, {"kind": "restore", "pointer": change["pointer"],
                                "batch_id": change["batch_ids"][-1]}, config_dir)
    if flips:
        ledger.append(profile, {"kind": "restore", "expose_id": True, "batch_id": flips[-1]["batch_id"]},
                      config_dir)
    return {"ok": True, "executed": True, **summary, "write_result": result}
```

Note on the restore write for "no id before": `old_body` is `None`, so the change body is `null`. Task 6 confirms Bubble clears the property with a `null` body; if it does not, change only the body built here.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/unit/test_tester_ids_service.py tests/unit/test_tester_ids_ledger.py tests/unit/test_tester_ids_elements.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bubble_mcp/execution/tester_ids/service.py tests/unit/test_tester_ids_service.py
git commit -m "feat: plan, apply and restore tester ids"
```

---

### Task 6: Confirm the narrow write on a real app (needs the owner's ok)

**Files:**
- Create: `docs/tester-ids-findings-2026-09-30.md`

This task writes to a real Bubble app. Ask Moabe before running it and use the `mcp-test` profile (`mcp-test-app`), `app_version` of a branch or `test` only if that app allows it. Do not use `auto-on`, `kaimia` or `orana`.

- [ ] **Step 1: Ask for approval** naming the profile, version, page and element that will be touched.
- [ ] **Step 2: Temporarily set `"tester_mode": true` on `mcp-test`** in `~/.config/bubble-mcp/settings.json` (note the original file content first).
- [ ] **Step 3: Run from a scratch script:** `plan_ids` on one page; `apply_ids(..., execute=True)` on one element without an id; read the leaf back with `read_nodes_over_http`; `restore_ids(batch_id=..., execute=True)`; read the leaf back again.
- [ ] **Step 4: Check** that the id appeared after apply (read back equals `html_id_body(...)`) and that the leaf is absent after restore. If the `null` body did not clear it, capture what the editor sends when a human clears an id field (same method as `docs/capture-version-control-*.json`) and change the restore body in `service.py` to match, with a test.
- [ ] **Step 5: Open the element in the editor** and confirm the ID attribute field shows the id (render check), then that it is empty after restore.
- [ ] **Step 6: Put `settings.json` back** as it was.
- [ ] **Step 7: Record the result** in `docs/tester-ids-findings-2026-09-30.md` (path used, bodies sent, read-backs, render check) and commit:

```bash
git add docs/tester-ids-findings-2026-09-30.md
git commit -m "docs: confirm the tester id write and clear on a live editor"
```

---

### Task 7: Expose the three tools

**Files:**
- Modify: `src/bubble_mcp/server/tools.py` (`EDITOR_SESSION_TOOLS` ~line 911, `main_write_refusal` ~line 1066, dispatch in `_call_tool` next to the savepoint tools ~line 2347)
- Modify: `src/bubble_mcp/server/agent_catalog.py` (descriptions dict ~line 841, read-only set ~line 2080, mutating set ~line 2330)
- Modify: `src/bubble_mcp/server/schema_families.py` (next to `bubble_savepoint_*` ~line 2214)
- Modify: `src/bubble_mcp/runtime_coverage.py:55-70`
- Modify: `src/bubble_mcp/server/instructions.py`
- Test: `tests/unit/test_tester_ids_tools.py`

**Interfaces:**
- Consumes: Task 5 `plan_ids`, `apply_ids`, `restore_ids`.
- Produces: MCP tools `bubble_test_ids_plan(profile, pointer, app_version?)`, `bubble_test_ids_apply(profile, pointer, ids, app_version?, execute?)`, `bubble_test_ids_restore(profile, batch_id?, element_pointers?, all?, app_version?, execute?)`.

- [ ] **Step 1: Write the failing tests**

```python
"""The tester tools are wired, pass their arguments through, and are not blocked by the main lock."""

from __future__ import annotations

from typing import Any

import pytest

from bubble_mcp.server import tools
from bubble_mcp.server.agent_catalog import tool_annotations
from bubble_mcp.server.schemas import list_tool_schemas

TESTER_TOOLS = ("bubble_test_ids_plan", "bubble_test_ids_apply", "bubble_test_ids_restore")


def test_the_tools_are_listed_with_schemas() -> None:
    names = {tool["name"] for tool in list_tool_schemas()}

    assert set(TESTER_TOOLS) <= names
    assert tool_annotations("bubble_test_ids_plan")["readOnlyHint"] is True
    assert tool_annotations("bubble_test_ids_apply")["readOnlyHint"] is False


def test_apply_passes_its_arguments_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_apply(profile: str, pointer: list[str], ids: list[dict], **kwargs: Any) -> dict:
        seen.update(profile=profile, pointer=pointer, ids=ids, **kwargs)
        return {"ok": True}

    monkeypatch.setattr(tools, "apply_tester_ids", fake_apply)

    tools._call_tool("bubble_test_ids_apply", {
        "profile": "p", "pointer": ["%p3", "pg"], "execute": True,
        "ids": [{"pointer": ["%p3", "pg", "%el", "c"], "html_id": "x"}],
    })

    assert seen["pointer"] == ["%p3", "pg"] and seen["execute"] is True
    assert seen["ids"][0]["html_id"] == "x"


def test_restore_maps_all_and_element_pointers(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(tools, "restore_tester_ids", lambda profile, **kw: seen.update(kw) or {"ok": True})

    tools._call_tool("bubble_test_ids_restore", {"profile": "p", "all": True,
                                                 "element_pointers": [["%p3", "pg", "%el", "c"]]})

    assert seen["restore_all"] is True
    assert seen["pointers"] == [["%p3", "pg", "%el", "c"]]


def test_the_main_lock_leaves_the_tester_tools_to_their_own_gate() -> None:
    for name in ("bubble_test_ids_apply", "bubble_test_ids_restore"):
        assert tools.main_write_refusal(name, {"profile": "p", "execute": True, "app_version": "test"}) is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/unit/test_tester_ids_tools.py -q`
Expected: FAIL (tools not listed / `AttributeError: apply_tester_ids`)

- [ ] **Step 3: Implement**

`tools.py` imports:

```python
from bubble_mcp.execution.tester_ids.service import (
    apply_ids as apply_tester_ids,
    plan_ids as plan_tester_ids,
    restore_ids as restore_tester_ids,
)
```

Add the three names to `EDITOR_SESSION_TOOLS`. Add near `MAIN_GUARDED_EXTRA_TOOLS`:

```python
# The tester tools write to test even in an app with branches; execution/tester_ids gates them on
# the profile's tester_mode and refuses live itself.
TESTER_ID_TOOLS = frozenset({"bubble_test_ids_apply", "bubble_test_ids_restore"})
```

In `main_write_refusal`, next to the `MAIN_GUARD_LOCAL_ONLY_TOOLS` check:

```python
    if name in MAIN_GUARD_LOCAL_ONLY_TOOLS or name in TESTER_ID_TOOLS:
        return None
```

Dispatch, before the savepoint block in `_call_tool`:

```python
    if name in {"bubble_test_ids_plan", "bubble_test_ids_apply", "bubble_test_ids_restore"}:
        args = arguments or {}
        profile = str(args.get("profile") or "").strip()
        if not profile:
            raise ValueError(f"{name} requires a profile.")
        app_version = str(args.get("app_version") or "test")
        if name == "bubble_test_ids_restore":
            raw_pointers = args.get("element_pointers")
            return restore_tester_ids(
                profile,
                batch_id=str(args.get("batch_id") or "") or None,
                pointers=[[str(p) for p in ptr] for ptr in raw_pointers] if isinstance(raw_pointers, list) else None,
                restore_all=bool(args.get("all")),
                app_version=app_version,
                execute=bool(args.get("execute")),
            )
        pointer = args.get("pointer")
        if not isinstance(pointer, list) or len(pointer) < 2:
            raise ValueError(f"{name} requires a pointer like ['%p3', '<page key>'].")
        pointer = [str(part) for part in pointer]
        if name == "bubble_test_ids_plan":
            return plan_tester_ids(profile, pointer, app_version=app_version)
        ids = args.get("ids")
        if not isinstance(ids, list) or not ids:
            raise ValueError("bubble_test_ids_apply requires a non-empty ids array.")
        return apply_tester_ids(profile, pointer, ids, app_version=app_version, execute=bool(args.get("execute")))
```

`agent_catalog.py` descriptions dict:

```python
    "bubble_test_ids_plan": (
        "Tester mode: list a page's elements with the HTML id each already has (reuse these) and "
        "the ones without one. Read-only. Needs tester_mode: true on the profile."
    ),
    "bubble_test_ids_apply": (
        "Tester mode: give elements HTML ids for tests. Elements that already have one keep it "
        "unless replace=true. Records the old value so it can be restored. execute=false previews."
    ),
    "bubble_test_ids_restore": (
        "Tester mode: put back the HTML ids the tester changed - by batch_id, element pointers or "
        "all. Skips any element whose id someone changed since. execute=false previews."
    ),
```

Add `"bubble_test_ids_plan"` to the `agent_read_only` set, and `"bubble_test_ids_apply"`, `"bubble_test_ids_restore"` to the explicit set in `_is_mutating` (so the session savepoint is taken before an executed call).

`schema_families.py`, next to the savepoint schemas:

```python
        tool_schema(
            "bubble_test_ids_plan",
            "Tester mode (profile tester_mode: true). Read a page or reusable - pointer "
            "['%p3', <page key>] or ['%ed', <reusable key>], found with bubble_context_find - and "
            "list its elements: 'reusable' already have an HTML id, 'missing' do not. Read-only.",
            ["profile", "app_version"],
            required=["profile", "pointer"],
            field_overrides={"pointer": {"type": "array", "items": {"type": "string"}, "minItems": 2}},
        ),
        tool_schema(
            "bubble_test_ids_apply",
            "Tester mode. Set HTML ids on elements of the page at pointer. Each ids item is "
            "{pointer, html_id, replace?}; html_id must match ^[a-z][a-z0-9-]*$ and be unique on the "
            "page. Elements that already have an id keep it unless replace=true. Turns the app's "
            "expose-id option on if it is off. Writes to test even when the app has branches; never "
            "live. Returns a batch_id for bubble_test_ids_restore. execute=false previews.",
            ["profile", "app_version", "execute"],
            required=["profile", "pointer", "ids"],
            field_overrides={
                "pointer": {"type": "array", "items": {"type": "string"}, "minItems": 2},
                "ids": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "pointer": {"type": "array", "items": {"type": "string"}},
                            "html_id": {"type": "string"},
                            "replace": {"type": "boolean", "default": False},
                        },
                        "required": ["pointer", "html_id"],
                        "additionalProperties": False,
                    },
                },
            },
        ),
        tool_schema(
            "bubble_test_ids_restore",
            "Tester mode. Put back the HTML ids the tester changed, to the value before its first "
            "change. Select with batch_id, element_pointers, or all=true (all also turns the "
            "expose-id option back off if the tester turned it on). An element whose id someone "
            "changed since is skipped as a conflict. execute=false previews.",
            ["profile", "app_version", "execute"],
            required=["profile"],
            field_overrides={
                "batch_id": {"type": "string"},
                "element_pointers": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                "all": {"type": "boolean", "default": False},
            },
        ),
```

`runtime_coverage.py`: add the three names to the list that holds `bubble_savepoint_*`.

`instructions.py`: append one sentence to the write_policy paragraph:

```python
    "A profile with tester_mode: true may set and restore HTML element ids with bubble_test_ids_plan/"
    "apply/restore, also on test in an app with branches; those tools change nothing else. "
```

- [ ] **Step 4: Run the new tests and the catalog gates**

Run: `.venv/Scripts/python -m pytest tests/unit/test_tester_ids_tools.py -q`
Expected: PASS

Run: `.venv/Scripts/python -m pytest tests/unit -q -p no:cacheprovider 2>&1 | grep -E "^FAILED" | sort > /tmp/branch-failures.txt`, then the same command in a `main` worktree (`git worktree add ../bfm-main main`) into `/tmp/main-failures.txt`, and `diff` the two. Expected: no failure on the branch that is not also on `main` (the 14 known Windows failures appear in both). Catalog/coverage tests that count tools (e.g. `test_agent_catalog*`, `test_runtime_coverage*`) must pass; if one pins a tool count, update the count in that test.

- [ ] **Step 5: Commit**

```bash
git add src/bubble_mcp/server src/bubble_mcp/runtime_coverage.py tests/unit/test_tester_ids_tools.py
git commit -m "feat: expose the tester id tools"
```

---

### Task 8: Session check shows the settings

**Files:**
- Modify: `src/bubble_mcp/server/tools.py` (session check block ~line 1836)
- Test: `tests/unit/test_version_policy.py`

**Interfaces:**
- Consumes: Task 2 `write_policy` fields.

The session check already calls `write_policy`, so `main_write_policy`, `policy_source`, `tester_mode` and `setting_warning` reach the agent with no change. This task only pins it.

- [ ] **Step 1: Write the failing-or-pinning test** (append to `tests/unit/test_version_policy.py`)

```python
def test_session_check_reports_the_profile_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from bubble_mcp.server import tools

    _versions(monkeypatch)
    _settings(monkeypatch, main_write_policy="never", tester_mode=True)
    monkeypatch.setattr(tools, "check_session", lambda profile, use_cache=False: {"ok": True, "logged_in": True, "app_id": "solo-app"})
    monkeypatch.setattr(tools, "load_session", lambda profile: SESSION)

    checked = tools._call_tool(tools.SESSION_CHECK_TOOL, {"profile": "p"})

    assert checked["write_policy"]["main_write_policy"] == "never"
    assert checked["write_policy"]["tester_mode"] is True
    assert checked["write_policy"]["policy_source"] == "profile_setting"
```

- [ ] **Step 2: Run it**

Run: `.venv/Scripts/python -m pytest tests/unit/test_version_policy.py -q`
Expected: PASS (if it fails, the block at ~line 1845 is not passing through `write_policy`'s result; fix it there).

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_version_policy.py
git commit -m "test: pin the profile write settings in the session check"
```
