#!/bin/sh
set -eu

if [ "${#WG_AGENT_TOKEN}" -lt 32 ]; then
    echo "WG_AGENT_TOKEN must be at least 32 characters" >&2
    exit 1
fi
python -c 'import os; from cryptography.fernet import Fernet; Fernet(os.environ["WG_AGENT_STATE_KEY"].encode())'

interface="${WG_AGENT_INTERFACE:-wg0}"
attempt=0
until wg show "$interface" >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 60 ]; then
        echo "WireGuard interface $interface did not become ready" >&2
        exit 1
    fi
    sleep 2
done

exec uvicorn app.agent:app --host "${WG_AGENT_LISTEN_HOST:-127.0.0.1}" --port "${WG_AGENT_LISTEN_PORT:-8787}"
