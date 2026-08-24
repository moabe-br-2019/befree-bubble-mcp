import json
from types import SimpleNamespace

from bubble_mcp.aria_dispatch import (
    _method_kwargs,
    _requires_calculate_derived,
    dispatch_aria_runtime_tool,
)
from bubble_mcp.core.config import BubbleMcpSettings, BubbleProfile, save_settings


def test_method_kwargs_maps_public_schema_aliases_to_aria_runtime_args() -> None:
    def create_data_field(
        data_type_key: str,
        field_name: str,
        field_type: str,
        dry_run: bool = False,
    ) -> bool:
        return True

    kwargs = _method_kwargs(
        create_data_field,
        {
            "profile": "smoke",
            "data_type_ref": "user",
            "name": "company",
            "type": "text",
            "execute": False,
        },
        execute=False,
    )

    assert kwargs == {
        "data_type_key": "user",
        "field_name": "company",
        "field_type": "text",
        "dry_run": True,
    }


def test_method_kwargs_maps_delete_data_field_name_to_field_key() -> None:
    def delete_data_field(
        data_type_key: str,
        field_key: str,
        dry_run: bool = False,
    ) -> bool:
        return True

    kwargs = _method_kwargs(
        delete_data_field,
        {
            "profile": "smoke",
            "data_type_ref": "user",
            "name": "campo_novo_text",
            "execute": False,
        },
        execute=False,
    )

    assert kwargs == {
        "data_type_key": "user",
        "field_key": "campo_novo_text",
        "dry_run": True,
    }


def test_delete_data_field_requires_calculate_derived_refresh() -> None:
    assert _requires_calculate_derived("delete_data_field") is True
    assert _requires_calculate_derived("create_privacy_rule") is True
    assert _requires_calculate_derived("set_privacy_rule_field_visibility") is True
    assert _requires_calculate_derived("delete_privacy_rule") is True
    assert _requires_calculate_derived("create_data_field") is False


def test_method_kwargs_maps_style_condition_aliases() -> None:
    def add_style_condition(
        style_name: str,
        condition: str,
        dry_run: bool = False,
    ) -> bool:
        return True

    def reorder_style_states(
        style_name: str,
        order_list: str,
        dry_run: bool = False,
        prune_missing: bool = False,
    ) -> bool:
        return True

    condition_kwargs = _method_kwargs(
        add_style_condition,
        {"name": "HTML Button Primary", "condition": "hover"},
        execute=True,
    )
    reorder_kwargs = _method_kwargs(
        reorder_style_states,
        {"name": "HTML Button Primary", "order": "hover,focus", "prune_missing": True},
        execute=True,
    )

    assert condition_kwargs == {
        "style_name": "HTML Button Primary",
        "condition": "hover",
        "dry_run": False,
    }
    assert reorder_kwargs == {
        "style_name": "HTML Button Primary",
        "order_list": "hover,focus",
        "dry_run": False,
        "prune_missing": True,
    }


def test_method_kwargs_maps_visual_and_workflow_aliases() -> None:
    def create_image(context_name: str, parent_name: str, name: str, source: str, dry_run: bool = False) -> bool:
        return True

    image_kwargs = _method_kwargs(
        create_image,
        {
            "context": "index",
            "parent": "root",
            "name": "im_logo",
            "image_url": "https://example.com/logo.png",
            "execute": False,
        },
        execute=False,
    )

    assert image_kwargs == {
        "context_name": "index",
        "parent_name": "root",
        "name": "im_logo",
        "source": "https://example.com/logo.png",
        "dry_run": True,
    }

    def create_workflow(context_name: str, element_name: str, event_type: str = "click", dry_run: bool = False) -> bool:
        return True

    workflow_kwargs = _method_kwargs(
        create_workflow,
        {
            "context": "index",
            "element_name": "Page",
            "event": "PageLoaded",
            "execute": False,
        },
        execute=False,
    )

    assert workflow_kwargs == {
        "context_name": "index",
        "element_name": "Page",
        "event_type": "PageLoaded",
        "dry_run": True,
    }

    def add_action(
        context_name: str,
        element_name: str,
        action_type: str,
        action_param: str | None = None,
        dry_run: bool = False,
    ) -> bool:
        return True

    action_kwargs = _method_kwargs(
        add_action,
        {
            "context": "index",
            "element_name": "bt_save",
            "action_type": "hide",
            "param": "bt_save",
            "execute": True,
        },
        execute=True,
    )

    assert action_kwargs == {
        "context_name": "index",
        "element_name": "bt_save",
        "action_type": "hide",
        "action_param": "bt_save",
        "dry_run": False,
    }


def test_aria_runtime_payload_builder_inherits_profile_app_version(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("BUBBLE_MCP_CONFIG_DIR", str(tmp_path))
    bubble_file = tmp_path / "app.bubble"
    bubble_file.write_text("{}", encoding="utf-8")
    save_settings(
        BubbleMcpSettings(
            config_dir=tmp_path,
            default_profile="branch-profile",
            profiles={
                "branch-profile": BubbleProfile(
                    name="branch-profile",
                    app_id="synthetic-app",
                    appname="synthetic-app",
                    app_version="feature-branch",
                    app_json_path=str(bubble_file),
                )
            },
        )
    )

    class FakePayloadBuilder:
        def __init__(self, appname="synthetic-page", app_version="test", metadata=None):  # type: ignore[no-untyped-def]
            self.appname = appname
            self.app_version = app_version
            self.metadata = metadata or {}

        def build(self):  # type: ignore[no-untyped-def]
            return {
                "appname": self.appname,
                "app_version": self.app_version,
                "changes": [
                    {
                        "intent": {"name": "CreateElement"},
                        "body": {
                            "%p": {
                                "%w": 320,
                                "%h": 180,
                                "fixed_width": True,
                                "fixed_height": True,
                            }
                        },
                    }
                ],
            }

        def send_to_webhook(self, _url=""):  # type: ignore[no-untyped-def]
            return {"ok": True}

        def to_json(self, indent=2):  # type: ignore[no-untyped-def]
            return json.dumps(self.build(), indent=indent)

    fake_sdk = SimpleNamespace(PayloadBuilder=FakePayloadBuilder)

    class FakeBubbleCLI:
        def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
            self.appname = kwargs["appname"]

        def create_text(self, dry_run=False):  # type: ignore[no-untyped-def]
            builder = fake_sdk.PayloadBuilder(appname=self.appname)
            return builder.to_json()

    fake_cli = SimpleNamespace(BubbleCLI=FakeBubbleCLI)
    monkeypatch.setattr("bubble_mcp.aria_dispatch._load_aria_runtime_modules", lambda: (fake_cli, fake_sdk))

    result = dispatch_aria_runtime_tool("create_text", {"profile": "branch-profile"})

    assert result is not None
    assert result["ok"] is True
    assert result["app_version"] == "feature-branch"
    payload = result["results"][0]["payload"]
    assert payload["app_version"] == "feature-branch"
    properties = payload["changes"][0]["body"]["%p"]
    assert properties["min_width_css"] == "320px"
    assert properties["max_width_css"] == "320px"
    assert properties["min_height_css"] == "180px"
    assert properties["max_height_css"] == "180px"


def test_aria_runtime_applies_project_default_styles_to_created_elements(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("BUBBLE_MCP_CONFIG_DIR", str(tmp_path))
    bubble_file = tmp_path / "app.bubble"
    bubble_file.write_text(
        json.dumps(
            {
                "app": {
                    "settings": {
                        "client_safe": {
                            "default_styles": {
                                "Group": "Group_runtime_default",
                                "Text": "Text_runtime_default",
                                "Button": "Button_runtime_default",
                                "Input": "Input_runtime_default",
                                "RadioButtons": "Radio_runtime_default",
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    save_settings(
        BubbleMcpSettings(
            config_dir=tmp_path,
            default_profile="runtime-profile",
            profiles={
                "runtime-profile": BubbleProfile(
                    name="runtime-profile",
                    app_id="synthetic-app",
                    appname="synthetic-app",
                    app_version="test",
                    app_json_path=str(bubble_file),
                )
            },
        )
    )

    class FakePayloadBuilder:
        def __init__(self, appname="synthetic-page", app_version="test", metadata=None):  # type: ignore[no-untyped-def]
            self.appname = appname
            self.app_version = app_version
            self.metadata = metadata or {}

        def build(self):  # type: ignore[no-untyped-def]
            return {
                "appname": self.appname,
                "app_version": self.app_version,
                "changes": [
                    {
                        "intent": {"name": "CreateElement"},
                        "body": {
                            "%x": "Group",
                            "%p": {},
                        },
                    },
                    {
                        "intent": {"name": "CreateElement"},
                        "body": {
                            "%x": "Text",
                            "%p": {},
                        },
                    },
                    {
                        "intent": {"name": "CreateElement"},
                        "body": {
                            "%x": "Button",
                            "%p": {
                                "fit_height": True,
                                "fit_width": True,
                                "single_width": False,
                            },
                        },
                    },
                    {
                        "intent": {"name": "CreateElement"},
                        "body": {
                            "%x": "Input",
                            "%s1": "Input_std_dash_",
                            "%p": {
                                "%h": 44,
                            },
                        },
                    },
                    {
                        "intent": {"name": "CreateElement"},
                        "body": {
                            "%x": "RadioButtons",
                            "%p": {
                                "fit_height": True,
                            },
                        },
                    }
                ],
            }

        def send_to_webhook(self, _url=""):  # type: ignore[no-untyped-def]
            return {"ok": True}

        def to_json(self, indent=2):  # type: ignore[no-untyped-def]
            return json.dumps(self.build(), indent=indent)

    fake_sdk = SimpleNamespace(PayloadBuilder=FakePayloadBuilder)

    class FakeBubbleCLI:
        def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
            self.appname = kwargs["appname"]

        def create_button(self, dry_run=False):  # type: ignore[no-untyped-def]
            builder = fake_sdk.PayloadBuilder(appname=self.appname)
            return builder.to_json()

    fake_cli = SimpleNamespace(BubbleCLI=FakeBubbleCLI)
    monkeypatch.setattr("bubble_mcp.aria_dispatch._load_aria_runtime_modules", lambda: (fake_cli, fake_sdk))

    result = dispatch_aria_runtime_tool("create_button", {"profile": "runtime-profile"})

    assert result is not None
    payload = result["results"][0]["payload"]
    group_body = payload["changes"][0]["body"]
    text_body = payload["changes"][1]["body"]
    body = payload["changes"][2]["body"]
    properties = body["%p"]
    assert group_body["%s1"] == "Group_runtime_default"
    assert text_body["%s1"] == "Text_runtime_default"
    assert body["%s1"] == "Button_runtime_default"
    assert properties["fit_height"] is True
    assert properties["fit_width"] is True
    input_body = payload["changes"][3]["body"]
    radio_body = payload["changes"][4]["body"]
    assert input_body["%s1"] == "Input_runtime_default"
    assert radio_body["%s1"] == "Radio_runtime_default"


def test_add_action_schema_args_are_accepted_by_runtime_signature() -> None:
    """Every arg the MCP schema advertises for add_action must reach BubbleCLI.add_action.

    Regression: the schema advertised event_ref/event_type/ref_kind but the runtime
    signature lacked them, so _method_kwargs silently dropped the workflow reference
    and add_action fell back to element/event matching (auto-creating duplicates).
    """

    import inspect

    from bubble_mcp.aria_runtime.bubble_cli import BubbleCLI
    from bubble_mcp.server.agent_catalog import _legacy_fields_for_name
    from bubble_mcp.aria_dispatch import ARG_ALIASES, CONTROL_ARG_KEYS

    fields = _legacy_fields_for_name("add_action")
    assert fields is not None
    required, optional = fields
    signature = inspect.signature(BubbleCLI.add_action)
    accepted = set(signature.parameters)
    alias_targets = {alias: param for param, aliases in ARG_ALIASES.items() for alias in aliases}
    ignorable = set(CONTROL_ARG_KEYS) | {"profile", "context", "dry_run", "settings_path"}

    missing = []
    for field in (*required, *optional):
        if field in ignorable:
            continue
        if field in accepted:
            continue
        if alias_targets.get(field) in accepted:
            continue
        missing.append(field)
    assert missing == [], f"schema args dropped by BubbleCLI.add_action: {missing}"


def test_add_action_delegates_to_add_event_action_for_event_ref() -> None:
    from bubble_mcp.aria_runtime.bubble_cli import BubbleCLI

    cli = object.__new__(BubbleCLI)
    captured: dict = {}

    def fake_add_event_action(**kwargs):
        captured.update(kwargs)
        return True

    cli.add_event_action = fake_add_event_action

    ok = BubbleCLI.add_action(
        cli,
        "dashboard",
        None,
        "show_alert",
        event_ref="bTYJT0",
        ref_kind="key",
        message="hello",
        dry_run=True,
    )

    assert ok is True
    assert captured["context_name"] == "dashboard"
    assert captured["event_ref"] == "bTYJT0"
    assert captured["ref_kind"] == "key"
    assert captured["action_type"] == "show_alert"
    assert captured["message"] == "hello"
    assert captured["dry_run"] is True


def test_add_action_without_element_or_ref_fails_with_guidance(capsys) -> None:
    from bubble_mcp.aria_runtime.bubble_cli import BubbleCLI

    cli = object.__new__(BubbleCLI)
    ok = BubbleCLI.add_action(cli, "dashboard", None, "show_alert", message="x", dry_run=True)

    assert ok is False
    out = capsys.readouterr().out
    assert "event_ref" in out


def test_select_trusted_workflow_rows_prefers_noncache_then_root_then_recent_cache() -> None:
    """Guard regression: a workflow created via MCP lives only in the local cache until the
    .bubble export is re-downloaded. The old guard discarded such rows and auto-created a
    duplicate workflow. Cached rows newer than (root mtime - tolerance) must be trusted."""

    from bubble_mcp.aria_runtime.bubble_cli import BubbleCLI

    noncache = {"key": "a", "from_cache": False, "updated_at": 10}
    cached_in_root = {"key": "b", "from_cache": True, "updated_at": 20}
    cached_recent = {"key": "c", "from_cache": True, "updated_at": 1_000_000}
    cached_stale = {"key": "d", "from_cache": True, "updated_at": 100}

    # 1. Non-cache rows always win.
    pool = BubbleCLI._select_trusted_workflow_rows(
        [noncache, cached_recent], exists_in_root=lambda row: False, root_source_mtime_ms=2_000_000
    )
    assert pool == [noncache]

    # 2. Cache-only rows that the root confirms are kept.
    pool = BubbleCLI._select_trusted_workflow_rows(
        [cached_in_root, cached_stale], exists_in_root=lambda row: row is cached_in_root, root_source_mtime_ms=2_000_000
    )
    assert pool == [cached_in_root]

    # 3. Cache-only rows newer than the root snapshot (minus tolerance) are trusted even when
    #    the root does not (yet) contain them — the root cannot refute what it predates.
    tolerance = BubbleCLI._WORKFLOW_CACHE_ROOT_TOLERANCE_MS
    pool = BubbleCLI._select_trusted_workflow_rows(
        [cached_recent, cached_stale],
        exists_in_root=lambda row: False,
        root_source_mtime_ms=1_000_000 + tolerance - 1,
    )
    assert pool == [cached_recent]

    # 4. Cache-only rows older than the root snapshot stay untrusted (deleted/ghost refs).
    pool = BubbleCLI._select_trusted_workflow_rows(
        [cached_stale], exists_in_root=lambda row: False, root_source_mtime_ms=10_000_000
    )
    assert pool == []

    # 5. Unknown root mtime: fall back to trusting recent-cache rows rather than duplicating.
    pool = BubbleCLI._select_trusted_workflow_rows(
        [cached_recent], exists_in_root=lambda row: False, root_source_mtime_ms=None
    )
    assert pool == [cached_recent]
