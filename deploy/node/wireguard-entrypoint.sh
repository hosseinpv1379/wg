#!/bin/sh
set -eu

interface="${WG_SERVER_INTERFACE:-wg0}"
port="${WG_SERVER_PORT:-51820}"
address="${WG_SERVER_ADDRESS:-10.45.0.1/24}"
uplink="${WG_UPLINK_INTERFACE:-eth0}"
client_pool="${WG_CLIENT_POOL:-10.44.0.0/24}"
data_dir="${WG_SERVER_STATE_PATH:-/var/lib/wg-node}"
config="/etc/wireguard/${interface}.conf"

case "$interface" in
    *[!a-zA-Z0-9_=+.@-]*|'') echo "Invalid WireGuard interface name" >&2; exit 1 ;;
esac
case "$port" in
    *[!0-9]*|'') echo "Invalid WireGuard listen port" >&2; exit 1 ;;
esac
if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
    echo "WireGuard listen port must be between 1 and 65535" >&2
    exit 1
fi
case "$uplink" in
    *[!a-zA-Z0-9_.:-]*|'') echo "Invalid uplink interface name" >&2; exit 1 ;;
esac
case "$address" in
    *[!0-9./]*|'') echo "Invalid WireGuard server address" >&2; exit 1 ;;
esac
case "$client_pool" in
    *[!0-9./]*|'') echo "Invalid WireGuard client pool" >&2; exit 1 ;;
esac

mkdir -p "$data_dir" /etc/wireguard
chmod 0700 "$data_dir" /etc/wireguard
if [ ! -s "$data_dir/server-private.key" ]; then
    umask 077
    wg genkey > "$data_dir/server-private.key"
fi
if [ ! -s "$data_dir/server-public.key" ]; then
    wg pubkey < "$data_dir/server-private.key" > "$data_dir/server-public.key"
fi
chmod 0600 "$data_dir/server-private.key" "$data_dir/server-public.key"

cat > "$config" <<EOF
[Interface]
Address = ${address}
ListenPort = ${port}
PrivateKey = $(cat "$data_dir/server-private.key")
PostUp = iptables -A FORWARD -i %i -d 10.0.0.0/8 -j REJECT; iptables -A FORWARD -i %i -d 172.16.0.0/12 -j REJECT; iptables -A FORWARD -i %i -d 192.168.0.0/16 -j REJECT; iptables -A FORWARD -i %i -d 169.254.0.0/16 -j REJECT; iptables -A FORWARD -i %i -d 100.64.0.0/10 -j REJECT; iptables -A FORWARD -i %i -o ${uplink} -s ${client_pool} -j ACCEPT; iptables -A FORWARD -i ${uplink} -o %i -d ${client_pool} -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT; iptables -t nat -A POSTROUTING -s ${client_pool} -o ${uplink} -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -d 10.0.0.0/8 -j REJECT; iptables -D FORWARD -i %i -d 172.16.0.0/12 -j REJECT; iptables -D FORWARD -i %i -d 192.168.0.0/16 -j REJECT; iptables -D FORWARD -i %i -d 169.254.0.0/16 -j REJECT; iptables -D FORWARD -i %i -d 100.64.0.0/10 -j REJECT; iptables -D FORWARD -i %i -o ${uplink} -s ${client_pool} -j ACCEPT; iptables -D FORWARD -i ${uplink} -o %i -d ${client_pool} -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT; iptables -t nat -D POSTROUTING -s ${client_pool} -o ${uplink} -j MASQUERADE
EOF
chmod 0600 "$config"

wg-quick up "$config"
shutdown() {
    wg-quick down "$config" || true
    exit 0
}
trap shutdown TERM INT

while :; do
    sleep 3600 &
    wait "$!" || true
done
