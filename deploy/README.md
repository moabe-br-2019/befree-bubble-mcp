# deploy

Scripts for the host that runs this MCP for a Claude Code sitting beside it. They are meant to
be run **on that host**, by whoever operates it — not driven over SSH from a laptop. Each one is
idempotent, so a re-run after a failure costs time and nothing else.

| Script | What it does | When |
| --- | --- | --- |
| `provision.sh` | Ubuntu 24.04 → service user, swap, venv installed from git, pinned Playwright, Chromium, Claude Code | once, first |
| `session-login.sh` | Captures a Bubble **editor** session on this host, through a private screen | every ~3 days |
| `e2e-artifacts-tunnel.sh` | Publishes E2E run artifacts at a hostname, without opening a port | once, optional |

## Order

```bash
sudo bash provision.sh
# then, as the service user:
bash session-login.sh --profile <name> --app-id <app>
```

`provision.sh` prints the two steps it deliberately leaves to a human: registering the MCP with
Claude Code, and turning on `BUBBLE_MCP_SYNC_BROWSERS`.

## Things worth knowing before they bite

**Install from git, never from a directory.** The autoupdate launcher updates only what pip
recorded as a VCS install. `pip install .` from a clone produces a host that silently never
updates; the only evidence is a `not_a_fork_install` line in `~/.config/bubble-mcp/autoupdate.log`.
`provision.sh` checks for this and refuses to finish without it.

**Playwright is pinned on the host, not in `pyproject.toml`.** The browser binaries are not a pip
dependency, so an open version range lets a dependency refresh move Playwright out from under
binaries that stay where they are. `BUBBLE_MCP_SYNC_BROWSERS=1` makes the launcher re-download
Chromium when that happens.

**Two sessions, two lifetimes, two ways to renew.**

| Session | Renewed by | Needs a screen |
| --- | --- | --- |
| Editor | `session-login.sh` | yes — a human logs in, 2FA included |
| `run_as` | `bubble_run_as` | no — two plain HTTP requests derived from the editor session |

The editor cookie is declared with a three-day life. The `run_as` session has been observed to
stop working **far sooner than its cookie claims** — measured on one host: alive at 22 seconds,
already anonymous at 33 minutes, against a cookie asserting 24 hours. Treat a stored `run_as`
state as stale unless it was just written, and recapture immediately before an E2E run rather
than trusting the file. A run whose first step lands on the app's login page is this, not a
broken app.

**Nothing prunes `runs/`.** Screenshots are small; a recorded case is a few MB.

## Artifacts over a tunnel

`e2e-artifacts-tunnel.sh` serves `~/.config/bubble-mcp/e2e` on loopback and connects it to
Cloudflare from the inside, so no inbound port is opened and no certificate has to be renewed.
It stops partway through and prints the two commands that need a browser and a Cloudflare
account; run it again afterwards to finish.

The hostname it configures must be one **not already in use**. Pointing it at a name whose A
record is how SSH resolves would replace that record with a CNAME and cut off access by
hostname.

Published artifacts are readable by anyone holding a URL. Run ids are unguessable, which is
obscurity rather than access control — put Cloudflare Access in front of the hostname if the
recordings should require a login.
