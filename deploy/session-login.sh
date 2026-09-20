#!/usr/bin/env bash
#
# Capture a Bubble editor session ON THIS HOST, by giving the browser a screen nobody else
# can reach.
#
# Why here rather than on a laptop: a captured session is live credentials, and copying it
# between machines leaves one more copy on one more disk every few days. Captured here, the
# cookies are created where they are used and never travel.
#
# The screen is an Xvfb display with x11vnc bound to 127.0.0.1. Nothing listens on a public
# interface - the operator reaches it by forwarding the port over SSH, which is already
# authenticated by key:
#
#     ssh -L 5901:localhost:5901 bubblemcp@<host>
#
# then points any VNC client at localhost:5901. Everything started here is torn down when this
# script exits, however it exits: the display, the window manager and the VNC server exist for
# the duration of one login and no longer.
#
#     bash session-login.sh --profile kaimia --app-id kaimia-app
#
# Only the EDITOR session needs this. A run-as session is two plain HTTP requests derived from
# it (execution/run_as.py), so once this has run, impersonation can be recaptured headlessly
# with no screen and nobody watching.

set -Eeuo pipefail

VENV="${VENV:-/opt/bubble-mcp/venv}"
DISPLAY_NUM="${DISPLAY_NUM:-:99}"
VNC_PORT="${VNC_PORT:-5901}"
GEOMETRY="${GEOMETRY:-1440x900x24}"
WAIT_SECONDS="${WAIT_SECONDS:-600}"

PROFILE=""
APP_ID=""
APP_VERSION=""

usage() {
    cat >&2 <<EOF
usage: $0 --profile NAME --app-id APP [--app-version VERSION] [--wait-seconds N]

Starts a private screen, opens the Bubble editor on it, and captures the session once you
have logged in. Reach the screen with:

    ssh -L ${VNC_PORT}:localhost:${VNC_PORT} $(whoami)@<host>

and connect a VNC client to localhost:${VNC_PORT}.
EOF
    exit 2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --profile) PROFILE="${2:-}"; shift 2 ;;
        --app-id) APP_ID="${2:-}"; shift 2 ;;
        --app-version) APP_VERSION="${2:-}"; shift 2 ;;
        --wait-seconds) WAIT_SECONDS="${2:-}"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "unknown argument: $1" >&2; usage ;;
    esac
done
[[ -n "${PROFILE}" && -n "${APP_ID}" ]] || usage

XVFB_PID=""
OPENBOX_PID=""
X11VNC_PID=""

cleanup() {
    # A screen holding a logged-in Bubble editor must not outlive the capture, so this runs on
    # success, on failure and on Ctrl-C alike.
    local pid
    for pid in "${X11VNC_PID}" "${OPENBOX_PID}" "${XVFB_PID}"; do
        [[ -n "${pid}" ]] && kill "${pid}" 2>/dev/null || true
    done
    rm -f "/tmp/.X${DISPLAY_NUM#:}-lock" 2>/dev/null || true
    printf '\n[session-login] screen torn down\n' >&2
}
trap cleanup EXIT INT TERM

if [[ ! -x "${VENV}/bin/bubble-mcp" ]]; then
    echo "No bubble-mcp in ${VENV}. Run provision.sh first." >&2
    exit 1
fi
if ss -lnt "sport = :${VNC_PORT}" 2>/dev/null | grep -q LISTEN; then
    echo "Port ${VNC_PORT} is already in use - another capture may be running." >&2
    exit 1
fi

printf '[session-login] starting display %s at %s\n' "${DISPLAY_NUM}" "${GEOMETRY}" >&2
Xvfb "${DISPLAY_NUM}" -screen 0 "${GEOMETRY}" -nolisten tcp &
XVFB_PID=$!
sleep 2
kill -0 "${XVFB_PID}" 2>/dev/null || { echo "Xvfb failed to start." >&2; exit 1; }

export DISPLAY="${DISPLAY_NUM}"

# Without a window manager the browser has no way to raise a dialog, which is exactly what a
# two-factor prompt is. openbox is the smallest thing that fixes that.
openbox --sm-disable >/dev/null 2>&1 &
OPENBOX_PID=$!

# -localhost is the security boundary: x11vnc binds 127.0.0.1 only, so the screen is reachable
# through an SSH tunnel and by nothing else. The one-time password is defence in depth against
# anything else already on this host.
VNC_PASS_FILE="$(mktemp)"
chmod 600 "${VNC_PASS_FILE}"
VNC_PASS="$(head -c 9 /dev/urandom | base64 | tr -d '/+=' | head -c 8)"
x11vnc -storepasswd "${VNC_PASS}" "${VNC_PASS_FILE}" >/dev/null 2>&1

x11vnc -display "${DISPLAY_NUM}" -rfbport "${VNC_PORT}" -localhost \
       -rfbauth "${VNC_PASS_FILE}" -forever -shared -quiet >/dev/null 2>&1 &
X11VNC_PID=$!
sleep 1

cat >&2 <<EOF

[session-login] The screen is up. From your own machine:

    ssh -L ${VNC_PORT}:localhost:${VNC_PORT} $(whoami)@$(hostname -f 2>/dev/null || hostname)

  then connect a VNC client to  localhost:${VNC_PORT}
  password: ${VNC_PASS}    (valid only for this capture)

  You have ${WAIT_SECONDS}s to log in, including any two-factor step. The capture ends by
  itself once the session passes a real calculate_derived check.

EOF

ARGS=(--profile "${PROFILE}" --app-id "${APP_ID}" --wait-seconds "${WAIT_SECONDS}")
[[ -n "${APP_VERSION}" ]] && ARGS+=(--app-version "${APP_VERSION}")

set +e
"${VENV}/bin/bubble-mcp" session login "${ARGS[@]}"
STATUS=$?
set -e

rm -f "${VNC_PASS_FILE}"

if [[ ${STATUS} -eq 0 ]]; then
    SESSION_FILE="${HOME}/.config/bubble-mcp/sessions/${PROFILE}.json"
    chmod 600 "${SESSION_FILE}" 2>/dev/null || true
    printf '\n[session-login] captured: %s\n' "${SESSION_FILE}" >&2
    printf '[session-login] a run-as session can now be recaptured headlessly, with no screen.\n' >&2
else
    printf '\n[session-login] capture failed (exit %s). The stored session, if any, is unchanged.\n' "${STATUS}" >&2
fi
exit "${STATUS}"
