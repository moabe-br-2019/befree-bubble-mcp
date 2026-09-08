"""The launcher that updates the MCP before becoming it.

Every branch here is about one question: does the server still start? The launcher exists to
keep the runtime projects current without a human remembering to reinstall, but a session with
no Bubble tools is worse than a session with slightly old ones - so network trouble, a broken
fork and a failed install all have to end the same way, with the server running.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from bubble_mcp.server import autoupdate
from bubble_mcp.server.autoupdate import CommandResult, update_before_launch

INSTALLED_SHA = "1111111111111111111111111111111111111111"
REMOTE_SHA = "2222222222222222222222222222222222222222"


@dataclass
class FakeRunner:
    """Answers each command by prefix, and records what it was asked to run."""

    replies: dict[str, CommandResult] = field(default_factory=dict)
    calls: list[list[str]] = field(default_factory=list)

    def __call__(self, command: list[str], timeout: float | None = None) -> CommandResult:
        self.calls.append(list(command))
        for marker, reply in self.replies.items():
            if any(marker in part for part in command):
                return reply
        return CommandResult(returncode=0)

    def ran(self, marker: str) -> bool:
        return any(any(marker in part for part in call) for call in self.calls)


def _site_packages(tmp_path: Path, commit: str | None = INSTALLED_SHA) -> Path:
    site = tmp_path / "site-packages"
    site.mkdir()
    if commit is not None:
        dist_info = site / "befree_bubble_mcp-0.1.2.dist-info"
        dist_info.mkdir()
        (dist_info / "direct_url.json").write_text(
            json.dumps(
                {
                    "url": autoupdate.FORK_URL,
                    "vcs_info": {
                        "vcs": "git",
                        "requested_revision": "main",
                        "commit_id": commit,
                    },
                }
            ),
            encoding="utf-8",
        )
    return site


def _ls_remote(sha: str) -> CommandResult:
    return CommandResult(returncode=0, stdout=f"{sha}\trefs/heads/main\n")


def test_matching_commit_installs_nothing(tmp_path: Path) -> None:
    # The common restart: nothing was pushed, so the only cost should be the remote lookup.
    runner = FakeRunner(replies={"ls-remote": _ls_remote(INSTALLED_SHA)})

    outcome = update_before_launch(
        Path("python"), _site_packages(tmp_path), runner=runner, log_path=tmp_path / "log"
    )

    assert outcome.action == "up_to_date"
    assert not runner.ran("pip")


def test_a_newer_commit_is_installed(tmp_path: Path) -> None:
    runner = FakeRunner(replies={"ls-remote": _ls_remote(REMOTE_SHA)})

    outcome = update_before_launch(
        Path("python"), _site_packages(tmp_path), runner=runner, log_path=tmp_path / "log"
    )

    assert outcome.action == "installed"
    assert runner.ran("pip")
    assert runner.ran(f"@{REMOTE_SHA}")


def test_the_install_skips_dependencies_until_pip_check_complains(tmp_path: Path) -> None:
    # Reinstalling every dependency is what makes the manual script slow, and this runs while a
    # session waits for its tools. Dependencies change rarely, so pay for them only when pip
    # says they are actually broken.
    runner = FakeRunner(replies={"ls-remote": _ls_remote(REMOTE_SHA)})

    update_before_launch(
        Path("python"), _site_packages(tmp_path), runner=runner, log_path=tmp_path / "log"
    )

    installs = [call for call in runner.calls if "install" in call]
    assert len(installs) == 1
    assert "--no-deps" in installs[0]


def test_a_broken_dependency_set_triggers_the_full_install(tmp_path: Path) -> None:
    runner = FakeRunner(
        replies={
            "ls-remote": _ls_remote(REMOTE_SHA),
            "check": CommandResult(
                returncode=1, stdout="befree-bubble-mcp requires missing-dep"
            ),
        }
    )

    update_before_launch(
        Path("python"), _site_packages(tmp_path), runner=runner, log_path=tmp_path / "log"
    )

    installs = [call for call in runner.calls if "install" in call]
    assert len(installs) == 2
    assert "--no-deps" not in installs[1]


def test_stale_path_files_are_removed_before_installing(tmp_path: Path) -> None:
    # pip never removes the one scripts/install_local.py writes, and leaving it means the venv
    # keeps importing a checkout instead of what was just installed - silently.
    site = _site_packages(tmp_path)
    local_pth = site / "befree_bubble_mcp_local.pth"
    local_pth.write_text("C:/somewhere/src\n", encoding="utf-8")
    editable_pth = site / "__editable__.befree_bubble_mcp-0.1.2.pth"
    editable_pth.write_text("C:/elsewhere/src\n", encoding="utf-8")
    runner = FakeRunner(replies={"ls-remote": _ls_remote(REMOTE_SHA)})

    update_before_launch(Path("python"), site, runner=runner, log_path=tmp_path / "log")

    assert not local_pth.exists()
    assert not editable_pth.exists()


def test_an_unreachable_remote_is_not_an_error(tmp_path: Path) -> None:
    # No network, GitHub down, a proxy in the way: none of that should cost the caller its
    # tools. The installed copy is old, not broken.
    runner = FakeRunner(
        replies={"ls-remote": CommandResult(returncode=128, stderr="could not resolve host")}
    )

    outcome = update_before_launch(
        Path("python"), _site_packages(tmp_path), runner=runner, log_path=tmp_path / "log"
    )

    assert outcome.action == "remote_unreachable"
    assert not runner.ran("pip")


def test_a_failed_install_is_reported_but_not_fatal(tmp_path: Path) -> None:
    runner = FakeRunner(
        replies={
            "ls-remote": _ls_remote(REMOTE_SHA),
            "install": CommandResult(returncode=1, stderr="build failed"),
        }
    )

    outcome = update_before_launch(
        Path("python"), _site_packages(tmp_path), runner=runner, log_path=tmp_path / "log"
    )

    assert outcome.action == "install_failed"


def test_the_environment_can_switch_updating_off(tmp_path: Path) -> None:
    runner = FakeRunner()

    outcome = update_before_launch(
        Path("python"),
        _site_packages(tmp_path),
        runner=runner,
        log_path=tmp_path / "log",
        env={autoupdate.DISABLE_ENV: "1"},
    )

    assert outcome.action == "disabled"
    assert runner.calls == []


def test_nothing_recorded_about_the_install_means_leave_it_alone(tmp_path: Path) -> None:
    # A venv holding an editable or a wheel install has no direct_url.json commit to compare
    # against. Reinstalling from the fork would silently replace a deliberately local setup.
    runner = FakeRunner(replies={"ls-remote": _ls_remote(REMOTE_SHA)})

    outcome = update_before_launch(
        Path("python"),
        _site_packages(tmp_path, commit=None),
        runner=runner,
        log_path=tmp_path / "log",
    )

    assert outcome.action == "not_a_fork_install"
    assert not runner.ran("pip")


def test_every_outcome_still_writes_a_log(tmp_path: Path) -> None:
    log_path = tmp_path / "nested" / "autoupdate.log"
    runner = FakeRunner(replies={"ls-remote": _ls_remote(REMOTE_SHA)})

    update_before_launch(
        Path("python"), _site_packages(tmp_path), runner=runner, log_path=log_path
    )

    assert "installed" in log_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "outcome_action",
    ["up_to_date", "installed", "remote_unreachable", "install_failed", "disabled"],
)
def test_the_server_is_launched_whatever_the_update_did(
    monkeypatch: pytest.MonkeyPatch, outcome_action: str
) -> None:
    launched: list[list[str]] = []

    def fake_launch(command: list[str]) -> int:
        launched.append(command)
        return 0

    monkeypatch.setattr(
        autoupdate,
        "update_before_launch",
        lambda *args, **kwargs: autoupdate.UpdateOutcome(action=outcome_action, detail=""),
    )
    monkeypatch.setattr(autoupdate, "_launch_server", fake_launch)

    assert autoupdate.main([]) == 0
    assert launched and launched[0][1:] == ["-m", "bubble_mcp.server.stdio"]
