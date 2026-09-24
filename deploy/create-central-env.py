from pathlib import Path
import base64
import secrets


root = Path(__file__).resolve().parent.parent
example = root / ".env.example"
target = root / ".env"
if target.exists():
    raise SystemExit(".env already exists; it was not changed.")

values = {
    "WG_BOOTSTRAP_API_KEY": secrets.token_hex(32),
    "WG_CONFIG_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    "WG_AGENT_STATE_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    "POSTGRES_PASSWORD": secrets.token_urlsafe(36),
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
print("Created .env with random secrets (mode 600). Secret values were not printed.")
