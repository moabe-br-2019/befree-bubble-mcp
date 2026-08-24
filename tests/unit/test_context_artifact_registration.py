"""Detection must leave a pointer on the profile, and dispatch must honour it.

The profile stored no path to the artifact detection produced, so
_resolve_runtime_environment saw "no local artifact" on every tool call and
re-ran detection (failing .bubble download + browser crawl) before each write.
"""

import json
from pathlib import Path

from bubble_mcp.core.config import BubbleMcpSettings, BubbleProfile, load_settings, save_settings


def _settings_with_profile(tmp_path: Path) -> BubbleMcpSettings:
    return BubbleMcpSettings(
        config_dir=tmp_path,
        default_profile="demo",
        profiles={"demo": BubbleProfile(name="demo", app_id="demo-app", appname="demo-app")},
    )


def test_profile_round_trips_context_paths(tmp_path: Path) -> None:
    settings = _settings_with_profile(tmp_path)
    profile = settings.profiles["demo"]
    updated = BubbleMcpSettings(
        config_dir=tmp_path,
        default_profile="demo",
        profiles={
            "demo": BubbleProfile(
                name=profile.name,
                app_id=profile.app_id,
                appname=profile.appname,
                context_path=str(tmp_path / "ctx.json"),
                crawler_index_path=str(tmp_path / "crawler.json"),
            )
        },
    )
    save_settings(updated)

    reloaded = load_settings(tmp_path).profiles["demo"]

    assert reloaded.context_path == str(tmp_path / "ctx.json")
    assert reloaded.crawler_index_path == str(tmp_path / "crawler.json")
    payload = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert payload["profiles"]["demo"]["crawler_index_path"].endswith("crawler.json")


def test_register_detected_artifacts_updates_profile(tmp_path: Path, monkeypatch) -> None:
    from bubble_mcp.context import detector

    save_settings(_settings_with_profile(tmp_path))
    monkeypatch.setattr(detector, "load_settings", lambda: load_settings(tmp_path))

    context_file = tmp_path / "ctx.json"
    crawler_file = tmp_path / "crawler.json"
    assert detector.register_detected_artifacts(
        "demo", context_path=context_file, crawler_index_path=crawler_file
    )

    stored = load_settings(tmp_path).profiles["demo"]
    assert stored.context_path == str(context_file)
    assert stored.crawler_index_path == str(crawler_file)
    # Re-registering the same paths is a no-op.
    assert not detector.register_detected_artifacts(
        "demo", context_path=context_file, crawler_index_path=crawler_file
    )


def test_dispatch_skips_detection_when_crawler_index_exists(tmp_path: Path, monkeypatch) -> None:
    from bubble_mcp import aria_dispatch

    crawler_file = tmp_path / "crawler.json"
    crawler_file.write_text("{}", encoding="utf-8")
    profile = BubbleProfile(
        name="demo",
        app_id="demo-app",
        appname="demo-app",
        crawler_index_path=str(crawler_file),
    )
    settings = BubbleMcpSettings(config_dir=tmp_path, default_profile="demo", profiles={"demo": profile})

    monkeypatch.setattr(aria_dispatch, "load_settings", lambda: settings)
    monkeypatch.setattr(aria_dispatch, "load_session", lambda _profile: None)

    def _boom(**_kwargs):  # pragma: no cover - must not run
        raise AssertionError("detection must not run when a local artifact is registered")

    monkeypatch.setattr(aria_dispatch, "detect_project_context", _boom)

    env = aria_dispatch._resolve_runtime_environment({"profile": "demo"})

    assert env.crawler_index_path == str(crawler_file)
