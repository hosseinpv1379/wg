#!/usr/bin/env bash
set -Eeuo pipefail

readonly REPOSITORY=${WG_NODE_REPOSITORY:-hosseinpv1379/wg}
readonly NODE_DIR=/etc/wg-node
readonly BINARY=/usr/local/bin/wg-node
TMP_DIR=$(mktemp -d)
NODE_KEY=""
cleanup() { unset NODE_KEY; rm -rf "$TMP_DIR"; }
trap cleanup EXIT
fail() { printf 'ERROR: %s\n' "$1" >&2; exit 1; }
info() { printf '\n==> %s\n' "$1"; }

[[ $EUID -eq 0 ]] || fail 'Run as root: sudo bash install.sh'
[[ -r /dev/tty && -r /etc/os-release ]] || fail 'An interactive Ubuntu/Debian terminal is required.'
. /etc/os-release
[[ ${ID:-} == ubuntu || ${ID:-} == debian ]] || fail 'Supported systems: Ubuntu and Debian.'

read -r -p 'Panel URL (https://...): ' PANEL_URL </dev/tty
PANEL_URL=${PANEL_URL%/}
read -r -p 'NODE_ID from the panel: ' NODE_ID </dev/tty
read -r -s -p 'SERVER_API_KEY for this Node: ' NODE_KEY </dev/tty
printf '\n'
read -r -p 'Public IP or DNS for WireGuard clients: ' WG_PUBLIC_HOST </dev/tty
read -r -p 'WireGuard UDP port [51820]: ' WG_SERVER_PORT </dev/tty
WG_SERVER_PORT=${WG_SERVER_PORT:-51820}
read -r -p 'Client address pool [10.44.0.0/24]: ' WG_CLIENT_POOL </dev/tty
WG_CLIENT_POOL=${WG_CLIENT_POOL:-10.44.0.0/24}

[[ $PANEL_URL == https://* ]] || fail 'Panel URL must use HTTPS.'
[[ $NODE_ID =~ ^[A-Za-z0-9_-]{8,40}$ ]] || fail 'NODE_ID format is invalid.'
[[ $NODE_KEY =~ ^wg_node_[A-Za-z0-9_-]{32,}$ ]] || fail 'SERVER_API_KEY is invalid.'
[[ $WG_PUBLIC_HOST =~ ^[A-Za-z0-9.-]+$ ]] || fail 'Public host format is invalid.'
[[ $WG_SERVER_PORT =~ ^[0-9]+$ ]] || fail 'UDP port must be numeric.'
WG_SERVER_PORT=$((10#$WG_SERVER_PORT))
(( WG_SERVER_PORT >= 1 && WG_SERVER_PORT <= 65535 )) || fail 'UDP port must be 1-65535.'
[[ $WG_CLIENT_POOL =~ ^[0-9./]+$ ]] || fail 'Client pool must be an IPv4 CIDR.'
IFS=./ read -r IP1 IP2 IP3 IP4 PREFIX <<< "$WG_CLIENT_POOL"
[[ $IP1 =~ ^[0-9]+$ && $IP2 =~ ^[0-9]+$ && $IP3 =~ ^[0-9]+$ && $IP4 =~ ^[0-9]+$ && $PREFIX =~ ^[0-9]+$ ]] \
  || fail 'Client pool must be a valid IPv4 CIDR.'
IP1=$((10#$IP1)); IP2=$((10#$IP2)); IP3=$((10#$IP3)); IP4=$((10#$IP4)); PREFIX=$((10#$PREFIX))
(( IP1 <= 255 && IP2 <= 255 && IP3 <= 255 && IP4 <= 255 && PREFIX >= 16 && PREFIX <= 30 )) \
  || fail 'Client pool must be between /16 and /30.'
(( IP1 == 10 || (IP1 == 172 && IP2 >= 16 && IP2 <= 31) || (IP1 == 192 && IP2 == 168) )) \
  || fail 'Client pool must be inside an RFC1918 private IPv4 range.'
IP_NUMBER=$(( (IP1 << 24) + (IP2 << 16) + (IP3 << 8) + IP4 ))
MASK=$(( (0xffffffff << (32 - PREFIX)) & 0xffffffff ))
SERVER_NUMBER=$(( (IP_NUMBER & MASK) + 1 ))
WG_SERVER_ADDRESS="$(( (SERVER_NUMBER >> 24) & 255 )).$(( (SERVER_NUMBER >> 16) & 255 )).$(( (SERVER_NUMBER >> 8) & 255 )).$(( SERVER_NUMBER & 255 ))/$PREFIX"
WG_HOST_ROUTE=${WG_SERVER_ADDRESS%/*}
[[ ! -e $NODE_DIR/node.env ]] || fail "$NODE_DIR/node.env already exists; this Node appears installed."
[[ ! -e /etc/wireguard/wg0.conf ]] || fail '/etc/wireguard/wg0.conf already exists; refusing to overwrite it.'

info 'Checking the central panel and Node key'
curl -fsS "$PANEL_URL/health" >/dev/null || fail 'Panel health check failed.'
curl -fsS -H "X-Node-Key: $NODE_KEY" "$PANEL_URL/api/v1/node-agent/$NODE_ID/commands?wait_seconds=0" >/dev/null \
  || fail 'Node ID and key were rejected by the panel.'

info 'Installing WireGuard tools and the Go Node service'
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl wireguard iptables iproute2 procps
install -d -m 0700 "$NODE_DIR" /var/lib/wg-node /etc/wireguard
ARCH=$(dpkg --print-architecture)
case "$ARCH" in
  amd64) ASSET=wg-node-linux-amd64 ;;
  arm64) ASSET=wg-node-linux-arm64 ;;
  *) fail "Unsupported CPU architecture: $ARCH" ;;
esac
if [[ -n ${WG_NODE_VERSION:-} ]]; then
  [[ $WG_NODE_VERSION =~ ^wg-node-v[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail 'WG_NODE_VERSION must look like wg-node-v0.1.2.'
  RELEASE_BASE="https://github.com/$REPOSITORY/releases/download/$WG_NODE_VERSION"
else
  RELEASE_BASE="https://github.com/$REPOSITORY/releases/latest/download"
fi
curl -fsSL "$RELEASE_BASE/$ASSET" -o "$TMP_DIR/$ASSET"
curl -fsSL "$RELEASE_BASE/SHA256SUMS" -o "$TMP_DIR/SHA256SUMS"
(cd "$TMP_DIR" && grep "  $ASSET$" SHA256SUMS | sha256sum -c -)
install -o root -g root -m 0755 "$TMP_DIR/$ASSET" "$BINARY"

info 'Configuring WireGuard interface and routing'
SERVER_PRIVATE_KEY=$(wg genkey)
WAN_INTERFACE=$(ip -4 route get 1.1.1.1 | awk '{for (i=1;i<=NF;i++) if ($i=="dev") {print $(i+1); exit}}')
[[ -n $WAN_INTERFACE ]] || fail 'Could not determine the public network interface.'
cat > /etc/sysctl.d/99-wg-node.conf <<'EOF'
net.ipv4.ip_forward=1
EOF
sysctl --system >/dev/null
cat > /etc/wireguard/wg0.conf <<EOF
[Interface]
Address = $WG_SERVER_ADDRESS
ListenPort = $WG_SERVER_PORT
PrivateKey = $SERVER_PRIVATE_KEY
SaveConfig = false
PostUp = iptables -C FORWARD -i %i -j ACCEPT 2>/dev/null || iptables -A FORWARD -i %i -j ACCEPT; iptables -C FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || iptables -A FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT; iptables -t nat -C POSTROUTING -s $WG_CLIENT_POOL -o $WAN_INTERFACE -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -s $WG_CLIENT_POOL -o $WAN_INTERFACE -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT 2>/dev/null || true; iptables -D FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || true; iptables -t nat -D POSTROUTING -s $WG_CLIENT_POOL -o $WAN_INTERFACE -j MASQUERADE 2>/dev/null || true
EOF
chmod 600 /etc/wireguard/wg0.conf

cat > "$NODE_DIR/node.env" <<EOF
WG_PANEL_URL=$PANEL_URL
WG_NODE_ID=$NODE_ID
WG_SERVER_API_KEY=$NODE_KEY
WG_PUBLIC_HOST=$WG_PUBLIC_HOST
WG_SERVER_PORT=$WG_SERVER_PORT
WG_INTERFACE=wg0
WG_CLIENT_POOL=$WG_CLIENT_POOL
WG_DNS=1.1.1.1
WG_STATE_PATH=/var/lib/wg-node/peers.json.enc
EOF
chmod 600 "$NODE_DIR/node.env"
unset SERVER_PRIVATE_KEY NODE_KEY

cat > /etc/systemd/system/wg-node.service <<'EOF'
[Unit]
Description=WireGuard Panel Node Agent
After=network-online.target wg-quick@wg0.service
Wants=network-online.target
Requires=wg-quick@wg0.service

[Service]
Type=simple
EnvironmentFile=/etc/wg-node/node.env
ExecStart=/usr/local/bin/wg-node
Restart=always
RestartSec=3
User=root
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/wg-node
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

info 'Starting WireGuard and connecting to the panel'
systemctl daemon-reload
systemctl enable --now "wg-quick@wg0"
systemctl enable --now wg-node
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
  ufw allow "$WG_SERVER_PORT/udp" >/dev/null
  ufw route allow in on wg0 out on "$WAN_INTERFACE" >/dev/null
fi
sleep 2
systemctl --no-pager --full status wg-node || true
printf '\nNode installation finished.\n'
printf 'WireGuard endpoint: %s:%s/udp\n' "$WG_PUBLIC_HOST" "$WG_SERVER_PORT"
printf 'Allow inbound UDP %s in both the VPS firewall and provider firewall.\n' "$WG_SERVER_PORT"
printf 'Node management uses outbound HTTPS; no Node domain or inbound TCP Agent ports are required.\n'
