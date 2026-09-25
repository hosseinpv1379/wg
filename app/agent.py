import hashlib
import hmac
import sqlite3
import subprocess
from contextlib import contextmanager
from pathlib import Path
from ipaddress import ip_interface

from cryptography.fernet import Fernet
from fastapi import FastAPI, Header, HTTPException, Query

from app.config import settings
from app.schemas import AgentPeerCreate, AgentPeerOut

app = FastAPI(title="WireGuard Server Agent", version="1.0.0", docs_url=None, redoc_url=None)


def state_encrypt(value: str) -> str:
    if not settings.agent_state_key:
        raise RuntimeError("WG_AGENT_STATE_KEY must be configured")
    return Fernet(settings.agent_state_key.encode()).encrypt(value.encode()).decode()


def state_decrypt(value: str) -> str:
    if not settings.agent_state_key:
        raise RuntimeError("WG_AGENT_STATE_KEY must be configured")
    return Fernet(settings.agent_state_key.encode()).decrypt(value.encode()).decode()


def authorize(authorization: str | None) -> None:
    if not settings.agent_token:
        raise HTTPException(503, "Agent token is not configured")
    if not settings.agent_state_key:
        raise HTTPException(503, "Agent state encryption key is not configured")
    expected = hashlib.sha256(settings.agent_token.encode()).hexdigest()
    supplied = (authorization or "").removeprefix("Bearer ")
    actual = hashlib.sha256(supplied.encode()).hexdigest()
    if not hmac.compare_digest(expected, actual):
        raise HTTPException(401, "Invalid agent token")


def wg(*args: str) -> str:
    try:
        result = subprocess.run(["wg", *args], check=True, capture_output=True, text=True, timeout=15)
        return result.stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise HTTPException(502, "WireGuard command failed") from exc


@contextmanager
def state_db():
    path = Path(settings.agent_state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.execute("CREATE TABLE IF NOT EXISTS peers (peer_id TEXT PRIMARY KEY, public_key TEXT NOT NULL, config TEXT NOT NULL, interface_name TEXT NOT NULL DEFAULT 'wg0')")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(peers)")}
    if "interface_name" not in columns:
        connection.execute("ALTER TABLE peers ADD COLUMN interface_name TEXT NOT NULL DEFAULT 'wg0'")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


@app.get("/internal/health")
def health(authorization: str | None = Header(default=None),
           interface_name: str = Query(default="wg0", pattern=r"^[a-zA-Z0-9_=+.@-]{1,15}$")):
    authorize(authorization)
    wg("show", interface_name)
    return {"status": "ok", "interface": interface_name}


@app.post("/internal/peers", response_model=AgentPeerOut)
def create_peer(data: AgentPeerCreate, authorization: str | None = Header(default=None)):
    authorize(authorization)
    with state_db() as db:
        saved = db.execute("SELECT public_key, config, interface_name FROM peers WHERE peer_id = ?", (data.peer_id,)).fetchone()
        if saved and not data.force_recreate:
            saved_config = state_decrypt(saved[1])
            for line in saved_config.splitlines():
                if line.startswith("Address = "):
                    address = ip_interface(line.removeprefix("Address = "))
                    prefix = 32 if address.version == 4 else 128
                    wg("set", saved[2], "peer", saved[0], "allowed-ips",
                       f"{address.ip}/{prefix}")
                    break
            return {"public_key": saved[0], "config": saved_config}
        if saved:
            wg("set", saved[2], "peer", saved[0], "remove")
            db.execute("DELETE FROM peers WHERE peer_id = ?", (data.peer_id,))
    client = ip_interface(data.client_ip)
    private_key = wg("genkey")
    public_key = subprocess.run(["wg", "pubkey"], input=private_key, check=True,
                                capture_output=True, text=True, timeout=15).stdout.strip()
    prefix = 32 if client.version == 4 else 128
    wg("show", data.interface_name)
    wg("set", data.interface_name, "peer", public_key, "allowed-ips", f"{client.ip}/{prefix}")
    if data.previous_public_key and data.previous_public_key != public_key:
        try:
            wg("set", data.interface_name, "peer", data.previous_public_key, "remove")
        except HTTPException:
            pass
    config = ("[Interface]\n"
              f"PrivateKey = {private_key}\nAddress = {client}\nDNS = {data.dns}\n\n"
              "[Peer]\n"
              f"PublicKey = {data.server_public_key}\nEndpoint = {data.endpoint}\n"
              f"AllowedIPs = {data.allowed_ips}\nPersistentKeepalive = 25\n")
    with state_db() as db:
        db.execute("INSERT OR REPLACE INTO peers(peer_id, public_key, config, interface_name) VALUES(?, ?, ?, ?)",
                   (data.peer_id, public_key, state_encrypt(config), data.interface_name))
    return {"public_key": public_key, "config": config}


@app.delete("/internal/peers/{peer_id}")
def remove_peer(peer_id: str, authorization: str | None = Header(default=None)):
    authorize(authorization)
    with state_db() as db:
        saved = db.execute("SELECT public_key, interface_name FROM peers WHERE peer_id = ?", (peer_id,)).fetchone()
        if not saved:
            return {"status": "removed"}
        wg("set", saved[1], "peer", saved[0], "remove")
        db.execute("DELETE FROM peers WHERE peer_id = ?", (peer_id,))
    return {"status": "removed"}


@app.get("/internal/traffic")
def traffic(authorization: str | None = Header(default=None),
            interface_name: str = Query(default="wg0", pattern=r"^[a-zA-Z0-9_=+.@-]{1,15}$")):
    authorize(authorization)
    lines = wg("show", interface_name, "dump").splitlines()[1:]
    counters = []
    for line in lines:
        parts = line.split("\t")
        if len(parts) >= 8:
            try:
                counters.append({"public_key": parts[0], "rx_bytes": int(parts[6]), "tx_bytes": int(parts[7])})
            except ValueError:
                continue
    try:
        interface_index = (Path("/sys/class/net") / interface_name / "ifindex").read_text().strip()
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError as exc:
        raise HTTPException(502, "Could not read WireGuard interface identity") from exc
    return {"peers": counters, "interface_generation": f"{boot_id}:{interface_index}"}


@app.on_event("startup")
def restore_peers() -> None:
    if not settings.agent_token:
        return
    with state_db() as db:
        peers = db.execute("SELECT public_key, config, interface_name FROM peers").fetchall()
        for public_key, cipher, interface_name in peers:
            config = state_decrypt(cipher)
            for line in config.splitlines():
                if line.startswith("Address = "):
                    address = ip_interface(line.removeprefix("Address = "))
                    prefix = 32 if address.version == 4 else 128
                    wg("set", interface_name, "peer", public_key,
                       "allowed-ips", f"{address.ip}/{prefix}")
                    break
