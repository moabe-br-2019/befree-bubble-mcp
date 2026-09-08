"""Launch the stdio server, bringing the venv up to the fork's HEAD first.

The runtime projects (auton, Orana) do not run from a checkout: each has its own venv holding a
package installed from the fork, so editing a checkout changes nothing about what their agents
execute. ``scripts/update_runtime_installs.py`` does that reinstall, but it has to be
remembered, and a forgotten reinstall is invisible - the agent simply keeps running old code
and nothing says so.

This module removes the remembering. It is what a project's ``.mcp.json`` points at instead of
``bubble_mcp.server.stdio``:

    "args": ["-m", "bubble_mcp.server.autoupdate"]

It updates and only then becomes the server, which is the whole reason it is a launcher rather
than a hook: a SessionStart hook runs alongside a server that has already been spawned, so
whatever it installs reaches the next session, not this one. Here the ordering is not a race -
the update finishes before the server process exists.

The cost of that ordering is the client's patience. A client gives an MCP server a bounded time
to come up, and an install that runs long enough spends it, leaving the session with no Bubble
tools at all. Two things keep the common case far away from that limit:

* Nothing was pushed, which is most restarts. Then the only work is one ``git ls-remote``,
  bounded at REMOTE_TIMEOUT_SEC, and the comparison against ``direct_url.json``. No pip at all.
* Something was pushed. Then the install runs ``--no-deps``, which touches this package and
  nothing else - the slow part of the manual script is reinstalling every dependency, and
  dependencies rarely change. ``pip check`` afterwards is what notices when they did, and only
  then does the full install run.

Everything that can go wrong ends the same way: the server starts. An unreachable remote, a
fork that will not build, a venv that was deliberately set up some other way - each is a reason
to run what is already installed, never a reason to leave the caller without tools. The outcome
is appended to a log instead, because a launcher cannot report to anybody: stdout belongs to
the MCP protocol, and writing anything else there corrupts the stream.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

FORK_URL = "https://github.com/moabe-br-2019/befree-bubble-mcp.git"
DEFAULT_REVISION = "main"
DIST_NAME = "befree_bubble_mcp"

# Setting this to anything non-empty leaves the venv exactly as it is. For a project that should
# track something other than the fork's main, or a debugging session that must not move under
# its own feet.
DISABLE_ENV = "BUBBLE_MCP_NO_AUTOUPDATE"

# The remote lookup happens on every single start, so it gets a short leash. Losing it costs an
# update; waiting on it costs the session its tools.
REMOTE_TIMEOUT_SEC = 5.0
INSTALL_TIMEOUT_SEC = 600.0

# Path files that must not survive a reinstall. pip cleans up the second; the first is written
# by scripts/install_local.py and pip does not know about it, so a leftover copy silently makes
# the venv import a checkout instead of what was just installed.
STALE_PTH_NAMES = ("befree_bubble_mcp_local.pth",)
STALE_PTH_GLOBS = (f"__editable__.{DIST_NAME}-*.pth",)


@dataclass(frozen=True)
class CommandResult:
    """Just the parts of a finished subprocess this module reads."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


Runner = Callable[..., CommandResult]


@dataclass(frozen=True)
class UpdateOutcome:
    """What the update did, for the log. Never a reason to skip launching."""

    action: str
    detail: str


def run_command(command: list[str], timeout: float | None = None) -> CommandResult:
    """The real runner. Never raises: a timeout or a missing executable is an outcome."""

    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(returncode=124, stderr=f"timed out after {timeout}s")
    except OSError as exc:
        return CommandResult(returncode=127, stderr=str(exc))
    return CommandResult(
        returncode=int(completed.returncode),
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def site_packages_for(prefix: Path) -> Path:
    """The venv's site-packages, on either layout.

    Windows puts it at ``Lib/site-packages``; POSIX at ``lib/pythonX.Y/site-packages``. Asking
    ``sysconfig`` would answer for the interpreter running THIS code, which is the right one
    here - but only because the launcher runs inside the venv it updates.
    """

    windows = prefix / "Lib" / "site-packages"
    if windows.is_dir():
        return windows
    candidates = sorted((prefix / "lib").glob("python*/site-packages"))
    return candidates[0] if candidates else windows


def stale_path_files(site_packages: Path) -> list[Path]:
    """Path files that would outlive a reinstall and quietly win over it."""

    found = [
        site_packages / name for name in STALE_PTH_NAMES if (site_packages / name).exists()
    ]
    for pattern in STALE_PTH_GLOBS:
        found.extend(sorted(site_packages.glob(pattern)))
    return found


def installed_revision(site_packages: Path) -> dict[str, object] | None:
    """What the venv actually holds, straight from pip's own record.

    ``None`` means pip recorded no VCS install - an editable, a wheel, a checkout on the path.
    That is deliberately not something to overwrite.
    """

    for dist_info in sorted(site_packages.glob(f"{DIST_NAME}-*.dist-info")):
        record = dist_info / "direct_url.json"
        if not record.exists():
            continue
        try:
            payload = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        info = payload.get("vcs_info") or {}
        if not info.get("commit_id"):
            continue
        return {
            "url": payload.get("url"),
            "requested": info.get("requested_revision"),
            "commit": info.get("commit_id"),
        }
    return None


def remote_head(runner: Runner, *, url: str, revision: str) -> str:
    """The commit ``revision`` points at on the fork, or "" when the remote cannot be reached.

    ``git ls-remote`` answers ``<sha>\\t<ref>``. It is one round trip and needs no clone, which
    is why the fast path can afford to run on every start.
    """

    result = runner(["git", "ls-remote", url, revision], timeout=REMOTE_TIMEOUT_SEC)
    if result.returncode != 0:
        return ""
    first_line = (result.stdout or "").strip().splitlines()
    if not first_line:
        return ""
    return first_line[0].split()[0].strip()


def _append_log(log_path: Path, outcome: UpdateOutcome) -> None:
    """Record the outcome. Failing to log is never worth failing a launch over."""

    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {outcome.action} {outcome.detail}\n".rstrip() + "\n")
    except OSError:
        pass


def _install(
    python: Path, site_packages: Path, runner: Runner, *, url: str, commit: str
) -> UpdateOutcome:
    for path in stale_path_files(site_packages):
        try:
            path.unlink()
        except OSError:
            pass

    # Pinned to the resolved commit rather than to the branch name, so what gets installed is
    # exactly what was compared against a moment ago. A push landing in between would otherwise
    # leave direct_url.json recording a commit this run never checked.
    spec = f"befree-bubble-mcp @ git+{url}@{commit}"
    fast = runner(
        [str(python), "-m", "pip", "install", "--force-reinstall", "--no-deps", spec],
        timeout=INSTALL_TIMEOUT_SEC,
    )
    if fast.returncode != 0:
        return UpdateOutcome("install_failed", (fast.stderr or fast.stdout)[-500:].strip())

    checked = runner([str(python), "-m", "pip", "check"], timeout=REMOTE_TIMEOUT_SEC * 4)
    if checked.returncode == 0:
        return UpdateOutcome("installed", commit)

    # Dependencies moved. Pay for the full resolve now rather than starting a server whose
    # imports will fail.
    full = runner(
        [str(python), "-m", "pip", "install", "--force-reinstall", spec],
        timeout=INSTALL_TIMEOUT_SEC,
    )
    if full.returncode != 0:
        return UpdateOutcome("install_failed", (full.stderr or full.stdout)[-500:].strip())
    return UpdateOutcome("installed", f"{commit} (dependencies refreshed)")


def update_before_launch(
    python: Path,
    site_packages: Path,
    *,
    runner: Runner = run_command,
    url: str = FORK_URL,
    revision: str = DEFAULT_REVISION,
    env: Mapping[str, str] | None = None,
    log_path: Path,
) -> UpdateOutcome:
    """Bring ``site_packages`` up to the fork's ``revision``, and say what happened.

    Returns rather than raises for every failure mode: the caller launches the server either
    way, so there is nothing an exception here could usefully mean.
    """

    environment = os.environ if env is None else env
    if str(environment.get(DISABLE_ENV, "")).strip():
        outcome = UpdateOutcome("disabled", f"{DISABLE_ENV} is set")
        _append_log(log_path, outcome)
        return outcome

    current = installed_revision(site_packages)
    if current is None:
        outcome = UpdateOutcome(
            "not_a_fork_install", "no VCS install recorded; leaving the venv alone"
        )
        _append_log(log_path, outcome)
        return outcome

    head = remote_head(runner, url=url, revision=revision)
    if not head:
        outcome = UpdateOutcome("remote_unreachable", f"could not read {revision} from {url}")
        _append_log(log_path, outcome)
        return outcome

    if head == current.get("commit"):
        outcome = UpdateOutcome("up_to_date", str(head))
        _append_log(log_path, outcome)
        return outcome

    outcome = _install(python, site_packages, runner, url=url, commit=head)
    _append_log(log_path, outcome)
    return outcome


def _launch_server(command: list[str]) -> int:
    """Run the real server as a child, passing this process's stdio straight through.

    ``os.execv`` would be the tidier shape, but on Windows it is spawn-and-exit rather than a
    true replacement: the process the MCP client is holding a handle to terminates, and the
    client reads that as a server that died on startup. A child with inherited handles costs one
    idle parent and behaves the same on both platforms.
    """

    try:
        return int(subprocess.run(command, check=False).returncode)
    except OSError as exc:  # the interpreter vanished mid-launch; nothing left to try
        print(f"befree-bubble-mcp: could not start the server: {exc}", file=sys.stderr)
        return 1


def default_log_path() -> Path:
    from bubble_mcp.core.config import get_config_dir

    return get_config_dir() / "autoupdate.log"


def main(argv: list[str] | None = None) -> int:
    """Update this venv, then run the stdio server in it."""

    python = Path(sys.executable)
    prefix = Path(sys.prefix)

    try:
        update_before_launch(
            python,
            site_packages_for(prefix),
            log_path=default_log_path(),
        )
    except Exception as exc:  # noqa: BLE001 - an update must never cost the caller its tools
        _append_log(default_log_path(), UpdateOutcome("update_crashed", str(exc)))

    return _launch_server([str(python), "-m", "bubble_mcp.server.stdio", *(argv or [])])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
