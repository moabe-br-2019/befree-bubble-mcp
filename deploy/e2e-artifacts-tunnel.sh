#!/usr/bin/env bash
#
# Publish the E2E run artifacts at https://e2e.mowebstudio.com, without opening a port.
#
# Two pieces, both talking only to localhost:
#
#   caddy       serves ~/.config/bubble-mcp/e2e read-only on 127.0.0.1:8080
#   cloudflared holds an outbound tunnel from this host to Cloudflare's edge
#
# Nothing listens on a public interface. The connection is established FROM here, so no
# inbound port, no firewall rule, and no TLS certificate to renew - the edge terminates TLS.
#
# Why caddy rather than nginx or python -m http.server:
#
#   * The artifacts live under a 0700 directory owned by the service user. nginx runs as
#     www-data and would need either a relaxed directory or a global user change; caddy runs
#     here as the owner, so the permissions stay as they are.
#   * Video seeking needs HTTP Range. Python's SimpleHTTPRequestHandler does not implement it,
#     so a .webm served by it plays from the start and refuses to scrub.
#
#     sudo bash e2e-artifacts-tunnel.sh
#
# Two steps cannot be scripted, because they need a browser and a Cloudflare account. The
# script stops and prints them.

set -Eeuo pipefail

SERVICE_USER="${SERVICE_USER:-bubblemcp}"
HOSTNAME_FQDN="${HOSTNAME_FQDN:-e2e.mowebstudio.com}"
TUNNEL_NAME="${TUNNEL_NAME:-bubblemcp-e2e}"
LISTEN_PORT="${LISTEN_PORT:-8080}"

log() { printf '\n== %s\n' "$*"; }
skip() { printf '   (already done) %s\n' "$*"; }

[[ "${EUID}" -eq 0 ]] || { echo "Run as root: sudo bash $0" >&2; exit 1; }
id -u "${SERVICE_USER}" >/dev/null 2>&1 || { echo "No user ${SERVICE_USER}. Run provision.sh first." >&2; exit 1; }

HOME_DIR="$(getent passwd "${SERVICE_USER}" | cut -d: -f6)"
ARTIFACTS="${HOME_DIR}/.config/bubble-mcp/e2e"
[[ -d "${ARTIFACTS}" ]] || { echo "No artifacts directory at ${ARTIFACTS}." >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive

# ---------------------------------------------------------------------------- caddy

log "caddy"
if command -v caddy >/dev/null 2>&1; then
    skip "caddy $(caddy version | head -1)"
else
    apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl >/dev/null
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    apt-get update -qq
    apt-get install -y -qq caddy >/dev/null
    # The packaged unit runs a full web server on :80/:443 as its own user. This host wants
    # neither, so it is stopped and masked in favour of the localhost-only unit below.
    systemctl disable --now caddy >/dev/null 2>&1 || true
fi

log "Static file service on 127.0.0.1:${LISTEN_PORT}"
install -d -m 755 /etc/bubble-mcp
cat > /etc/bubble-mcp/Caddyfile <<EOF
# Loopback only: the tunnel is the sole way in, so binding anywhere else would just be an
# unprotected copy of the same files.
http://127.0.0.1:${LISTEN_PORT} {
	root * ${ARTIFACTS}
	file_server browse
	encode gzip

	# Artifacts are immutable once a run finishes, so a long cache costs nothing and stops a
	# re-watch from pulling the whole video again.
	header /runs/* Cache-Control "public, max-age=86400"

	log {
		output file /var/log/bubble-mcp-e2e-access.log
		format console
	}
}
EOF

cat > /etc/systemd/system/bubble-mcp-e2e-files.service <<EOF
[Unit]
Description=Serve Bubble MCP E2E artifacts on loopback
After=network.target

[Service]
# Runs as the owner of the 0700 artifacts directory, so its permissions never have to change.
User=${SERVICE_USER}
Group=${SERVICE_USER}
ExecStart=/usr/bin/caddy run --config /etc/bubble-mcp/Caddyfile --adapter caddyfile
Restart=on-failure
RestartSec=5
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/var/log
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now bubble-mcp-e2e-files >/dev/null
sleep 1
if curl -fsS -o /dev/null "http://127.0.0.1:${LISTEN_PORT}/"; then
    printf '   serving %s\n' "${ARTIFACTS}"
else
    echo "The file service did not answer. Check: journalctl -u bubble-mcp-e2e-files -n 30" >&2
    exit 1
fi

# ---------------------------------------------------------------------------- cloudflared

log "cloudflared"
if command -v cloudflared >/dev/null 2>&1; then
    skip "cloudflared $(cloudflared --version 2>/dev/null | head -1)"
else
    mkdir -p --mode=0755 /usr/share/keyrings
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
        | tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" \
        > /etc/apt/sources.list.d/cloudflared.list
    apt-get update -qq
    apt-get install -y -qq cloudflared >/dev/null
fi

CF_DIR="/etc/cloudflared"
install -d -m 700 "${CF_DIR}"

if [[ -f "${CF_DIR}/cert.pem" ]] && cloudflared tunnel --origincert "${CF_DIR}/cert.pem" list 2>/dev/null | grep -q "${TUNNEL_NAME}"; then
    skip "tunnel ${TUNNEL_NAME} already exists"
    TUNNEL_ID="$(cloudflared tunnel --origincert "${CF_DIR}/cert.pem" list 2>/dev/null | awk -v n="${TUNNEL_NAME}" '$2==n{print $1}')"
else
    cat >&2 <<EOF

== Two steps need you, because they need a browser and your Cloudflare account.

1. Authorise this host against the zone (opens a URL to paste into a browser):

     sudo cloudflared tunnel --origincert ${CF_DIR}/cert.pem login

2. Create the tunnel and point the hostname at it:

     sudo cloudflared tunnel --origincert ${CF_DIR}/cert.pem create ${TUNNEL_NAME}
     sudo cloudflared tunnel --origincert ${CF_DIR}/cert.pem route dns ${TUNNEL_NAME} ${HOSTNAME_FQDN}

   'route dns' writes a CNAME. It must be a NAME NOT ALREADY IN USE - do not point it at
   bubblemcp.mowebstudio.com, whose A record is what SSH resolves.

Then run this script again; it will finish the configuration and start the tunnel.

EOF
    exit 0
fi

log "Tunnel configuration"
cat > "${CF_DIR}/config.yml" <<EOF
tunnel: ${TUNNEL_ID}
credentials-file: ${CF_DIR}/${TUNNEL_ID}.json

ingress:
  - hostname: ${HOSTNAME_FQDN}
    service: http://127.0.0.1:${LISTEN_PORT}
  # Anything that reaches the tunnel without matching above is refused rather than passed on.
  - service: http_status:404
EOF
chmod 600 "${CF_DIR}/config.yml"

cloudflared --config "${CF_DIR}/config.yml" tunnel ingress validate
cloudflared service install >/dev/null 2>&1 || true
systemctl enable --now cloudflared >/dev/null
sleep 3

cat <<EOF

== Published.

   https://${HOSTNAME_FQDN}/runs/

   Nothing listens on a public interface here: caddy is on 127.0.0.1:${LISTEN_PORT} and the
   tunnel is an outbound connection.

   Artifacts are readable by anyone who knows a URL. Run ids are unguessable
   (20260921124837_2e61e7), which is obscurity, not access control. To require a login, put
   Cloudflare Access in front of ${HOSTNAME_FQDN} in the dashboard - no change here.

   Logs:    journalctl -u cloudflared -n 30
            journalctl -u bubble-mcp-e2e-files -n 30
            /var/log/bubble-mcp-e2e-access.log

   Nothing prunes runs/. Videos accumulate at a few MB per recorded case.

EOF
