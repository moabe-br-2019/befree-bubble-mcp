import json
from pathlib import Path

from bubble_mcp.core.config import (
    BubbleMcpSettings,
    BubbleProfile,
    app_write_settings,
    load_settings,
    normalize_main_write_policy,
    resolve_config_artifact_path,
    resolve_profile,
    save_settings,
    with_profile,
)


def test_resolve_config_artifact_path_uses_settings_directory(tmp_path: Path) -> None:
    assert resolve_config_artifact_path(tmp_path, "contexts/sample.bubble") == (
        tmp_path / "contexts/sample.bubble"
    )
    absolute = tmp_path / "absolute.bubble"
    assert resolve_config_artifact_path(tmp_path / "ignored", str(absolute)) == absolute
    assert resolve_config_artifact_path(tmp_path, None) is None


def test_save_load_and_resolve_profile(tmp_path: Path) -> None:
    settings = BubbleMcpSettings(config_dir=tmp_path, default_profile=None, profiles={})
    updated = with_profile(
        settings,
        BubbleProfile(
            name="cli-test",
            app_id="sample-app",
            appname="sample-app",
            app_version="test",
            app_json_path="src/app.bubble",
            consolelog_json_path="src/consolelog-app.json",
        ),
    )

    save_settings(updated)
    loaded = load_settings(tmp_path)

    assert loaded.default_profile == "cli-test"
    assert resolve_profile(loaded, "cli_test") is not None
    assert resolve_profile(loaded, "cli_test").app_id == "sample-app"  # type: ignore[union-attr]
    assert resolve_profile(loaded, "cli_test").app_version == "test"  # type: ignore[union-attr]
    assert resolve_profile(loaded, "cli_test").app_json_path == "src/app.bubble"  # type: ignore[union-attr]
    assert resolve_profile(loaded, "cli_test").consolelog_json_path == "src/consolelog-app.json"  # type: ignore[union-attr]


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
