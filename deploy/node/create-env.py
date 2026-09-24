from pathlib import Path
import base64
import re
import secrets


root = Path(__file__).resolve().parent
example = root / ".env.example"
target = root / ".env"
if target.exists():
    raise SystemExit("deploy/node/.env already exists; it was not changed.")

agent_domain = input("Agent subdomain (for example agent-de.example.com): ").strip().lower()
server_host = input("This Node's public IPv4 or DNS name: ").strip()
server_port_input = input("WireGuard UDP port [51820]: ").strip() or "51820"
if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,63}", agent_domain):
    raise SystemExit("Agent subdomain format is invalid; no file was written.")
if not re.fullmatch(r"[a-zA-Z0-9.-]+", server_host):
    raise SystemExit("Public IP/DNS name format is invalid; no file was written.")
if not server_port_input.isdecimal() or not 1 <= int(server_port_input) <= 65535:
    raise SystemExit("UDP port must be between 1 and 65535; no file was written.")

values = {
    "WG_AGENT_DOMAIN": agent_domain,
    "WG_SERVER_HOST": server_host,
    "WG_SERVER_PORT": server_port_input,
    "WG_AGENT_TOKEN": secrets.token_hex(32),
    "WG_AGENT_STATE_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
}
lines = example.read_text().splitlines()
seen = set()
for index, line in enumerate(lines):
    key = line.partition("=")[0]
    if key in values:
        lines[index] = f"{key}={values[key]}"
        seen.add(key)
if seen != values.keys():
    raise SystemExit(".env.example is missing a required variable; no file was written.")

target.write_text("\n".join(lines) + "\n")
target.chmod(0o600)
print("Created deploy/node/.env with random secrets (mode 600). Secret values were not printed.")
