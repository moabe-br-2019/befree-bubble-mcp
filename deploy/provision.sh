#!/usr/bin/env bash
#
# Provision a Ubuntu 24.04 host to run befree-bubble-mcp over stdio for a local Claude Code.
#
# Idempotent: every step checks before it acts, so re-running after a failure - or after a
# change to this file - costs time and nothing else.
#
# Nothing here exposes a port. The MCP speaks stdio to a Claude Code running on the same host,
# inside tmux. The only thing reachable from outside is SSH.
#
# Run as root (or via sudo) the first time:
#
#     sudo bash provision.sh
#
# What it deliberately does NOT do:
#
#   * Install a Bubble session. Sessions are captured interactively and expire in days; they
#     are copied in afterwards, by hand, with 0600. See deploy/README.md.
#   * Register the MCP with Claude Code. That writes to the operator's own config and wants
#     their eyes on it: the command is printed at the end instead.
#   * Open a firewall port. There is nothing here to reach.

set -Eeuo pipefail

SERVICE_USER="${SERVICE_USER:-bubblemcp}"
FORK_URL="${FORK_URL:-https://github.com/moabe-br-2019/befree-bubble-mcp.git}"
FORK_REVISION="${FORK_REVISION:-main}"
PLAYWRIGHT_PIN="${PLAYWRIGHT_PIN:-1.62.0}"
SWAP_GB="${SWAP_GB:-4}"
VENV_DIR="/opt/bubble-mcp/venv"
CONSTRAINTS="/etc/bubble-mcp/constraints.txt"

log() { printf '\n== %s\n' "$*"; }
skip() { printf '   (already done) %s\n' "$*"; }

trap 'printf "\n!! failed at line %s. Nothing here is half-applied that a re-run will not fix.\n" "$LINENO" >&2' ERR

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run this as root: sudo bash $0" >&2
    exit 1
fi

# ---------------------------------------------------------------------------- sanity

log "Checking the host"
ARCH="$(uname -m)"
if [[ "${ARCH}" != "x86_64" ]]; then
    # Playwright ships prebuilt browsers for x86_64 and arm64 only, and --with-deps knows the
    # package names for a subset of distributions. Stopping here beats failing three steps in.
    echo "Unsupported architecture: ${ARCH}. Playwright's Chromium build expects x86_64." >&2
    exit 1
fi
. /etc/os-release
if [[ "${VERSION_ID:-}" != "24.04" ]]; then
    echo "Warning: this script was written for Ubuntu 24.04, found ${PRETTY_NAME:-unknown}." >&2
fi
printf '   %s on %s\n' "${PRETTY_NAME:-unknown}" "${ARCH}"

# ---------------------------------------------------------------------------- swap

log "Swap"
if swapon --show --noheadings | grep -q .; then
    skip "swap is already active: $(swapon --show=NAME,SIZE --noheadings | tr '\n' ' ')"
else
    # Chromium plus a pip resolve in 6 GB of RAM is tight, and the failure mode is the OOM
    # killer taking the browser mid-test rather than anything legible.
    fallocate -l "${SWAP_GB}G" /swapfile
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    printf '   %s GB of swap added and persisted in /etc/fstab\n' "${SWAP_GB}"
fi

# ---------------------------------------------------------------------------- packages

log "System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
    python3 python3-venv python3-pip \
    git curl ca-certificates \
    tmux \
    xdg-utils
printf '   python3 is %s\n' "$(python3 --version)"

# ---------------------------------------------------------------------------- user

log "Service user: ${SERVICE_USER}"
if id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    skip "user exists"
else
    adduser --disabled-password --gecos "" "${SERVICE_USER}"
fi
HOME_DIR="$(getent passwd "${SERVICE_USER}" | cut -d: -f6)"

# Every file this user creates is theirs alone. The MCP chmods the files that hold cookies,
# but defence in depth is cheaper here than auditing every writer.
if grep -q '^umask 077$' "${HOME_DIR}/.profile" 2>/dev/null; then
    skip "umask 077 already set"
else
    echo 'umask 077' >> "${HOME_DIR}/.profile"
    printf '   umask 077 appended to .profile\n'
fi

# Whoever can already reach root over SSH gets in as the service user too. Without this the
# only way in is root, which is exactly the login that should be turned off once this host is
# settled - and turning it off while it is the sole key would lock the operator out.
if [[ -s /root/.ssh/authorized_keys ]]; then
    install -d -m 700 -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${HOME_DIR}/.ssh"
    touch "${HOME_DIR}/.ssh/authorized_keys"
    while read -r key; do
        [[ -n "${key}" ]] || continue
        grep -qxF "${key}" "${HOME_DIR}/.ssh/authorized_keys" || echo "${key}" >> "${HOME_DIR}/.ssh/authorized_keys"
    done < /root/.ssh/authorized_keys
    chown "${SERVICE_USER}:${SERVICE_USER}" "${HOME_DIR}/.ssh/authorized_keys"
    chmod 600 "${HOME_DIR}/.ssh/authorized_keys"
    printf '   %s authorized key(s) copied from root\n' "$(wc -l < "${HOME_DIR}/.ssh/authorized_keys")"
else
    echo "Warning: /root/.ssh/authorized_keys is empty, so ${SERVICE_USER} gets no SSH key." >&2
fi

# ---------------------------------------------------------------------------- config dir

log "MCP config directory"
CONFIG_DIR="${HOME_DIR}/.config/bubble-mcp"
install -d -m 700 -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${HOME_DIR}/.config"
install -d -m 700 -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${CONFIG_DIR}"
# It holds live Bubble session cookies for every profile. 700 is the wall; the 600 the MCP
# puts on individual files is the second one.
printf '   %s is 0700\n' "${CONFIG_DIR}"

# ---------------------------------------------------------------------------- constraints

log "Pinning Playwright"
install -d -m 755 /etc/bubble-mcp
if [[ -f "${CONSTRAINTS}" ]] && grep -q "playwright==${PLAYWRIGHT_PIN}" "${CONSTRAINTS}"; then
    skip "already pinned to ${PLAYWRIGHT_PIN}"
else
    # The package declares playwright>=1.45.0. The browser binaries are NOT a pip dependency -
    # `playwright install` fetches them, keyed to the version - so an open range lets a
    # dependency refresh move Playwright out from under binaries that stay put, and e2e then
    # fails for reasons that look like the app. Pinning here rather than in pyproject.toml
    # keeps the constraint on the host that needs it instead of on every developer.
    printf 'playwright==%s\n' "${PLAYWRIGHT_PIN}" > "${CONSTRAINTS}"
    printf '   %s pins playwright==%s\n' "${CONSTRAINTS}" "${PLAYWRIGHT_PIN}"
fi

# ---------------------------------------------------------------------------- venv

log "Virtualenv and package"
install -d -m 755 -o "${SERVICE_USER}" -g "${SERVICE_USER}" /opt/bubble-mcp
if [[ -x "${VENV_DIR}/bin/python" ]]; then
    skip "venv exists at ${VENV_DIR}"
else
    sudo -u "${SERVICE_USER}" python3 -m venv "${VENV_DIR}"
fi

# Installed FROM GIT, never from a checkout and never editable. The autoupdate launcher reads
# pip's direct_url.json and updates only when it finds vcs_info.commit_id there; a local or
# editable install records no such thing, and the launcher then - correctly - refuses to touch
# the venv. `pip install .` from a clone would silently produce a host that never updates.
sudo -u "${SERVICE_USER}" "${VENV_DIR}/bin/pip" install --quiet --upgrade pip
sudo -u "${SERVICE_USER}" "${VENV_DIR}/bin/pip" install --quiet \
    -c "${CONSTRAINTS}" \
    "befree-bubble-mcp[browser] @ git+${FORK_URL}@${FORK_REVISION}"

INSTALLED_COMMIT="$(sudo -u "${SERVICE_USER}" "${VENV_DIR}/bin/python" - <<'PY'
import json, sys
from pathlib import Path
site = next(Path(sys.prefix, "lib").glob("python*/site-packages"), None)
for info in sorted((site or Path()).glob("befree_bubble_mcp-*.dist-info/direct_url.json")):
    vcs = json.loads(info.read_text()).get("vcs_info") or {}
    if vcs.get("commit_id"):
        print(vcs["commit_id"])
        break
else:
    print("")
PY
)"
if [[ -z "${INSTALLED_COMMIT}" ]]; then
    echo "The install recorded no VCS commit. The autoupdate launcher will refuse to update" >&2
    echo "this venv. Reinstall with the git+ URL above rather than from a directory." >&2
    exit 1
fi
printf '   installed %s at %s\n' "${FORK_REVISION}" "${INSTALLED_COMMIT:0:10}"

# ---------------------------------------------------------------------------- browsers

log "Chromium for Playwright"
# --with-deps installs the shared libraries Chromium needs, which is an apt operation and so
# runs as root; the download itself belongs to the service user, because the binaries live
# under its ~/.cache/ms-playwright and that is where the venv will look for them.
"${VENV_DIR}/bin/playwright" install-deps chromium
sudo -u "${SERVICE_USER}" env HOME="${HOME_DIR}" "${VENV_DIR}/bin/playwright" install chromium
printf '   chromium present under %s/.cache/ms-playwright\n' "${HOME_DIR}"

# ---------------------------------------------------------------------------- claude code

log "Claude Code"
if sudo -u "${SERVICE_USER}" env HOME="${HOME_DIR}" bash -lc 'command -v claude' >/dev/null 2>&1; then
    skip "claude is on the service user's PATH"
else
    sudo -u "${SERVICE_USER}" env HOME="${HOME_DIR}" bash -lc \
        'curl -fsSL https://claude.ai/install.sh | bash'
fi

# ---------------------------------------------------------------------------- done

cat <<EOF

== Provisioned.

Three things are left, and each one needs a human.

1. Register the MCP, as ${SERVICE_USER}, at USER scope so it works from any directory:

     claude mcp add -s user befree-bubble-mcp \\
       ${VENV_DIR}/bin/python -- -m bubble_mcp.server.autoupdate

   The launcher updates the venv from ${FORK_REVISION} and only then becomes the server, so
   the update can never land in the middle of a working session.

2. Turn on the browser sync for this host, so a dependency refresh that moves Playwright
   re-downloads Chromium instead of leaving e2e broken and silent:

     echo 'export BUBBLE_MCP_SYNC_BROWSERS=1' >> ${HOME_DIR}/.profile

3. Bring a Bubble session over. Sessions are captured interactively and cannot be created
   here; the editor session lasts about 3 days and the run-as session about 2, so this is a
   recurring chore rather than a one-off:

     scp ~/.config/bubble-mcp/sessions/<profile>.json ${SERVICE_USER}@<host>:${CONFIG_DIR}/sessions/
     ssh ${SERVICE_USER}@<host> 'chmod 600 ${CONFIG_DIR}/sessions/*.json'

   Then check it from the VPS with bubble_profile_status - and remember that it reads the file
   without asking Bubble anything, so a green answer there is not proof the session is live.

EOF
