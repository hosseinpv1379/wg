#!/usr/bin/env bash
set -Eeuo pipefail

readonly INSTALL_DIR=/opt/wg-node
readonly REPOSITORY=${WG_NODE_REPOSITORY:-https://github.com/hosseinpv1379/wg.git}
TMP_DIR=$(mktemp -d)
CURL_CONFIG="$TMP_DIR/curl.conf"
SERVER_API_KEY=""

cleanup() {
  unset SERVER_API_KEY
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

fail() { printf 'ERROR: %s\n' "$1" >&2; exit 1; }
info() { printf '\n==> %s\n' "$1"; }

[[ $EUID -eq 0 ]] || fail "Run this installer as root: sudo bash install.sh"
[[ -r /dev/tty ]] || fail "An interactive terminal is required to enter the setup values safely."
[[ -r /etc/os-release ]] || fail "This installer requires Ubuntu or Debian."
. /etc/os-release
[[ ${ID:-} == ubuntu || ${ID:-} == debian ]] || fail "Supported operating systems: Ubuntu or Debian."
[[ ! -e $INSTALL_DIR ]] || fail "$INSTALL_DIR already exists. Back it up or remove it before reinstalling."
if ! command -v curl >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1 || ! command -v git >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ca-certificates curl python3 git
fi

read -r -p "Panel URL (for example https://api.example.com): " PANEL_URL </dev/tty
PANEL_URL=${PANEL_URL%/}
[[ $PANEL_URL == https://* ]] || fail "Panel URL must start with https://"
read -r -s -p "server_api_key from the panel: " SERVER_API_KEY </dev/tty
printf '\n'
[[ $SERVER_API_KEY =~ ^[A-Za-z0-9_-]{32,}$ ]] || fail "The server_api_key looks incomplete or invalid."
read -r -p "Node name (for example Germany-1): " NODE_NAME </dev/tty
read -r -p "Country code (DE, TR, US, RU, CN): " NODE_COUNTRY </dev/tty
NODE_COUNTRY=${NODE_COUNTRY^^}
read -r -p "Node domain with DNS pointing here (for example agent-de.example.com): " NODE_DOMAIN </dev/tty
read -r -p "Public IPv4 or DNS for WireGuard clients: " NODE_PUBLIC_HOST </dev/tty
read -r -p "WireGuard UDP port [51820]: " WG_SERVER_PORT </dev/tty
WG_SERVER_PORT=${WG_SERVER_PORT:-51820}

[[ -n $NODE_NAME && ${#NODE_NAME} -le 120 ]] || fail "Node name is required (maximum 120 characters)."
[[ $NODE_COUNTRY =~ ^[A-Z]{2}$ ]] || fail "Country must be a two-letter code such as DE."
[[ $NODE_DOMAIN =~ ^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || fail "Node domain format is invalid."
[[ $NODE_PUBLIC_HOST =~ ^[A-Za-z0-9.-]+$ ]] || fail "Public IP or DNS format is invalid."
[[ $WG_SERVER_PORT =~ ^[0-9]+$ ]] && (( WG_SERVER_PORT >= 1 && WG_SERVER_PORT <= 65535 )) || fail "UDP port must be between 1 and 65535."

cat > "$CURL_CONFIG" <<EOF
silent
show-error
header = "X-API-Key: $SERVER_API_KEY"
header = "Content-Type: application/json"
EOF
chmod 600 "$CURL_CONFIG"
export CURL_CONFIG

info "Checking panel and setup key"
curl -fsS "$PANEL_URL/health" >/dev/null || fail "Cannot reach panel health endpoint."
curl -fsS --config "$CURL_CONFIG" "$PANEL_URL/api/v1/nodes" >/dev/null || fail "Setup key is invalid or lacks Node permissions."

info "Installing Docker"
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ca-certificates curl
  curl -fsSL https://get.docker.com -o "$TMP_DIR/get-docker.sh"
  sh "$TMP_DIR/get-docker.sh"
  systemctl enable --now docker
fi

info "Downloading WireGuard Node"
git clone --depth 1 --branch main "$REPOSITORY" "$INSTALL_DIR"
cd "$INSTALL_DIR"
printf '%s\n' "$NODE_DOMAIN" "$NODE_PUBLIC_HOST" "$WG_SERVER_PORT" | python3 deploy/node/create-env.py

info "Starting WireGuard and secure Agent"
docker compose --env-file deploy/node/.env -f deploy/node/compose.yml up -d --build
PUBLIC_KEY=$(docker compose --env-file deploy/node/.env -f deploy/node/compose.yml \
  exec -T wireguard cat /var/lib/wg-node/server-public.key)
AGENT_TOKEN=$(sed -n 's/^WG_AGENT_TOKEN=//p' deploy/node/.env)
[[ -n $PUBLIC_KEY && -n $AGENT_TOKEN ]] || fail "Node did not produce its WireGuard key or Agent token."

info "Registering Node in the panel"
export PANEL_URL SERVER_API_KEY NODE_NAME NODE_COUNTRY NODE_DOMAIN NODE_PUBLIC_HOST WG_SERVER_PORT AGENT_TOKEN PUBLIC_KEY
python3 - <<'PY'
import json
import os
import subprocess
import sys

def post(path, body):
    result = subprocess.run(
        ["curl", "-fsS", "--config", os.environ["CURL_CONFIG"], "-X", "POST",
         "-d", json.dumps(body), os.environ["PANEL_URL"] + path],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)["data"]

try:
    node = post("/api/v1/nodes", {
        "name": os.environ["NODE_NAME"],
        "country": os.environ["NODE_COUNTRY"],
        "agent_url": "https://" + os.environ["NODE_DOMAIN"],
        "agent_secret": os.environ["AGENT_TOKEN"],
    })
    post("/api/v1/servers", {
        "node_id": node["id"],
        "interface_name": "wg0",
        "name": os.environ["NODE_NAME"],
        "country": os.environ["NODE_COUNTRY"],
        "endpoint": os.environ["NODE_PUBLIC_HOST"] + ":" + os.environ["WG_SERVER_PORT"],
        "public_key": os.environ["PUBLIC_KEY"],
        "address_pool": "10.44.0.0/24",
        "dns": "1.1.1.1",
    })
except (subprocess.CalledProcessError, KeyError, ValueError) as exc:
    print("Node services are running, but registration failed. Check the setup key and panel logs, then retry registration.", file=sys.stderr)
    if isinstance(exc, subprocess.CalledProcessError):
        print(exc.stderr.strip(), file=sys.stderr)
    sys.exit(1)
PY

unset SERVER_API_KEY AGENT_TOKEN
rm -f "$CURL_CONFIG"
info "Installation complete"
printf 'Node: %s (%s)\n' "$NODE_NAME" "$NODE_COUNTRY"
printf 'Agent URL: https://%s\n' "$NODE_DOMAIN"
printf 'WireGuard endpoint: %s:%s/udp\n' "$NODE_PUBLIC_HOST" "$WG_SERVER_PORT"
printf 'Open the panel and run Test Connection for this Node. Allow TCP 80/443 and UDP %s in the VPS firewall.\n' "$WG_SERVER_PORT"
