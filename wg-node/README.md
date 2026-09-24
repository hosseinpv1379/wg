# WG Node

Standalone Go daemon for one WireGuard server interface. It makes authenticated outbound HTTPS requests to the central WG Panel, registers its public endpoint, polls durable commands, and applies peer changes with `wg`.

## Requirements

- Linux with `wg` from `wireguard-tools` and an active interface (normally `wg0`).
- Root privileges for `wg set` and reading the interface public key.
- A Node ID and the one-time Node API key created in the panel.

The production installer configures WireGuard and systemd. The Node does not listen on a management TCP port; allow inbound WireGuard UDP and outbound HTTPS to the panel.

## Configuration

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `WG_PANEL_URL` | Yes | - | HTTPS base URL of the central panel |
| `WG_NODE_ID` | Yes | - | Node ID created in the panel |
| `WG_SERVER_API_KEY` | Yes | - | One-time Node key; stored in the root-only systemd environment file |
| `WG_PUBLIC_HOST` | Yes | - | Public address clients use for this server |
| `WG_SERVER_PORT` | No | `51820` | WireGuard UDP port |
| `WG_INTERFACE` | No | `wg0` | Existing WireGuard interface name |
| `WG_CLIENT_POOL` | No | `10.44.0.0/24` | IPv4 client pool registered with the panel |
| `WG_DNS` | No | `1.1.1.1` | DNS included in generated client configs |
| `WG_STATE_PATH` | No | `/var/lib/wg-node/peers.json.enc` | Encrypted local peer state |

Peer private keys in local state are encrypted with AES-GCM using a key derived from the Node API key. Back up the state file and the API key securely; losing either prevents restoration of existing peer configs.

## Build and test

~~~sh
go test ./...
CGO_ENABLED=0 go build -trimpath -o wg-node .
~~~

Pushing a tag such as `wg-node-v1.0.0` runs the repository release workflow, which tests and publishes Linux amd64 and arm64 binaries with SHA-256 checksums.
