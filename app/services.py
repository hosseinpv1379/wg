import hashlib
import hmac
import json
import socket
import time
from datetime import timedelta
from ipaddress import ip_address, ip_network
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.crypto import decrypt, encrypt
from app.models import (AuditLog, IdempotencyRecord, Job, Node, NodeCommand, Order, Payment, Peer,
                        Plan, Server, Subscription, User, WebhookDelivery, WebhookEndpoint,
                        as_utc, new_id, utc_now)
from app.schemas import NodeCreate, OrderCreate, PaymentConfirm, PlanCreate, ServerCreate, UserCreate

ALL_SCOPES = {
    "plans:read", "plans:write", "users:read", "users:write", "orders:read", "orders:create",
    "payments:confirm", "subscriptions:read", "subscriptions:write", "subscriptions:renew",
    "peers:read", "peers:write", "servers:read", "servers:write", "webhooks:write", "keys:write",
    "audit:read", "webhooks:read",
}


def audit(db: Session, tenant_id: str, actor: str, action: str, resource_id: str = "") -> None:
    db.add(AuditLog(tenant_id=tenant_id, actor=actor, action=action, resource_id=resource_id))


def emit(db: Session, tenant_id: str, event_type: str, data: dict) -> None:
    event = {"id": new_id("evt"), "type": event_type, "created_at": utc_now().isoformat(), "data": data}
    endpoints = db.scalars(select(WebhookEndpoint).where(WebhookEndpoint.tenant_id == tenant_id,
                                                           WebhookEndpoint.active.is_(True))).all()
    for endpoint in endpoints:
        if event_type in endpoint.events.split(",") or "*" in endpoint.events.split(","):
            db.add(WebhookDelivery(tenant_id=tenant_id, endpoint_id=endpoint.id,
                                   event_type=event_type, payload=json.dumps(event)))


def remember_idempotency(db: Session, tenant_id: str, key: str | None, resource_id: str) -> None:
    if not key:
        return
    db.add(IdempotencyRecord(tenant_id=tenant_id, key=key, resource_id=resource_id))


def prior_idempotency(db: Session, tenant_id: str, key: str | None) -> str | None:
    if not key:
        return None
    record = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.tenant_id == tenant_id,
                                                         IdempotencyRecord.key == key))
    return record.resource_id if record else None


def create_plan(db: Session, tenant_id: str, data: PlanCreate) -> Plan:
    plan = Plan(tenant_id=tenant_id, **data.model_dump(exclude={"server_countries"}),
                server_countries=",".join(c.upper() for c in data.server_countries))
    db.add(plan)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, detail={"code": "PLAN_EXISTS", "message": "Plan name already exists"}) from exc
    audit(db, tenant_id, "api", "plan.created", plan.id)
    db.commit()
    db.refresh(plan)
    return plan


def create_user(db: Session, tenant_id: str, data: UserCreate) -> User:
    user = db.scalar(select(User).where(User.tenant_id == tenant_id, User.external_id == data.external_id))
    if user:
        return user
    user = User(tenant_id=tenant_id, external_id=data.external_id)
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        user = db.scalar(select(User).where(User.tenant_id == tenant_id, User.external_id == data.external_id))
        if user:
            return user
        raise exc
    db.refresh(user)
    return user


def create_server(db: Session, tenant_id: str, data: ServerCreate) -> Server:
    values = data.model_dump()
    secret = values.pop("agent_secret")
    node_id = values.get("node_id")
    if node_id:
        node = db.scalar(select(Node).where(Node.id == node_id, Node.tenant_id == tenant_id,
                                             Node.active.is_(True)))
        if not node:
            raise HTTPException(404, detail={"code": "NODE_NOT_FOUND", "message": "Active node not found"})
        values["agent_url"] = node.agent_url
        values["agent_secret_ciphertext"] = node.agent_secret_ciphertext
    else:
        values["agent_secret_ciphertext"] = encrypt(secret)
    values["country"] = values["country"].upper()
    server = Server(tenant_id=tenant_id, **values)
    db.add(server)
    db.commit()
    audit(db, tenant_id, "api", "server.created", server.id)
    db.commit()
    db.refresh(server)
    return server


def create_node(db: Session, tenant_id: str, data: NodeCreate) -> Node:
    node = Node(tenant_id=tenant_id, name=data.name, country=data.country.upper(),
                agent_url=data.agent_url, agent_secret_ciphertext=encrypt(data.agent_secret))
    db.add(node)
    db.commit()
    audit(db, tenant_id, "api", "node.created", node.id)
    db.commit()
    db.refresh(node)
    return node


def test_agent_connection(agent_url: str, token: str, interface_name: str = "wg0") -> bool:
    try:
        response = httpx.get(agent_url.rstrip("/") + "/internal/health",
                             params={"interface_name": interface_name},
                             headers={"Authorization": f"Bearer {token}"}, timeout=8)
        response.raise_for_status()
        return response.json().get("status") == "ok"
    except (httpx.HTTPError, ValueError):
        return False


def create_order(db: Session, tenant_id: str, data: OrderCreate,
                 idempotency_key: str | None) -> Order:
    scoped_key = f"orders:create:{idempotency_key}" if idempotency_key else None
    if scoped_key and len(scoped_key) > 200:
        raise HTTPException(422, detail={"code": "INVALID_IDEMPOTENCY_KEY", "message": "Idempotency-Key is too long"})
    previous = prior_idempotency(db, tenant_id, scoped_key)
    if previous:
        order = db.scalar(select(Order).where(Order.id == previous, Order.tenant_id == tenant_id))
        if order:
            return order
        raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT", "message": "Key already used"})
    user = db.scalar(select(User).where(User.id == data.user_id, User.tenant_id == tenant_id,
                                        User.status == "active"))
    plan = db.scalar(select(Plan).where(Plan.id == data.plan_id, Plan.tenant_id == tenant_id,
                                        Plan.active.is_(True)))
    if not user:
        raise HTTPException(404, detail={"code": "USER_NOT_FOUND", "message": "User not found"})
    if not plan:
        raise HTTPException(404, detail={"code": "INVALID_PLAN", "message": "Active plan not found"})
    if data.peer_count > plan.peer_limit:
        raise HTTPException(422, detail={"code": "PEER_LIMIT_EXCEEDED", "message": "Requested peers exceed plan limit"})
    allowed_countries = {c for c in plan.server_countries.split(",") if c}
    requested_country = (data.country or "").upper()
    if requested_country and allowed_countries and requested_country not in allowed_countries:
        raise HTTPException(422, detail={"code": "SERVER_POLICY_MISMATCH", "message": "Requested country is not permitted by plan"})
    snapshot = {"name": plan.name, "traffic_limit_bytes": plan.traffic_limit_bytes,
                "duration_days": plan.duration_days, "peer_limit": plan.peer_limit,
                "price_minor": plan.price_minor, "currency": plan.currency,
                "server_countries": plan.server_countries}
    order = Order(tenant_id=tenant_id, user_id=user.id, plan_id=plan.id, peer_count=data.peer_count,
                  price_minor=plan.price_minor, currency=plan.currency,
                  selected_country=requested_country,
                  plan_snapshot=json.dumps(snapshot))
    db.add(order)
    db.flush()
    remember_idempotency(db, tenant_id, scoped_key, order.id)
    audit(db, tenant_id, "api", "order.created", order.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        previous = prior_idempotency(db, tenant_id, scoped_key)
        existing = db.scalar(select(Order).where(Order.id == previous,
                                                   Order.tenant_id == tenant_id)) if previous else None
        if existing:
            return existing
        raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT", "message": "Order request conflicted"}) from exc
    db.refresh(order)
    return order


def create_renewal_order(db: Session, tenant_id: str, subscription_id: str,
                         idempotency_key: str | None) -> Order:
    scoped_key = f"subscriptions:renew:{idempotency_key}" if idempotency_key else None
    if scoped_key and len(scoped_key) > 200:
        raise HTTPException(422, detail={"code": "INVALID_IDEMPOTENCY_KEY", "message": "Idempotency-Key is too long"})
    previous = prior_idempotency(db, tenant_id, scoped_key)
    if previous:
        order = db.scalar(select(Order).where(Order.id == previous, Order.tenant_id == tenant_id))
        if order:
            return order
        raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT", "message": "Key already used"})
    sub = db.scalar(select(Subscription).where(Subscription.id == subscription_id,
                                               Subscription.tenant_id == tenant_id).with_for_update())
    if not sub or sub.status == "suspended":
        raise HTTPException(404, detail={"code": "SUBSCRIPTION_NOT_FOUND", "message": "Renewable subscription not found"})
    order = Order(tenant_id=tenant_id, user_id=sub.user_id, plan_id=sub.plan_id,
                  subscription_id=sub.id, kind="renewal", peer_count=sub.provisioned_peer_count,
                  selected_country="",
                  price_minor=sub.price_minor, currency=sub.currency,
                  plan_snapshot=json.dumps({"traffic_limit_bytes": sub.traffic_limit_bytes,
                      "duration_days": sub.duration_days, "peer_limit": sub.peer_limit}))
    db.add(order)
    db.flush()
    remember_idempotency(db, tenant_id, scoped_key, order.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        previous = prior_idempotency(db, tenant_id, scoped_key)
        existing = db.scalar(select(Order).where(Order.id == previous,
                                                   Order.tenant_id == tenant_id)) if previous else None
        if existing:
            return existing
        raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT", "message": "Renewal request conflicted"}) from exc
    db.refresh(order)
    return order


def confirm_payment(db: Session, tenant_id: str, order_id: str, data: PaymentConfirm) -> Subscription:
    order = db.scalar(select(Order).where(Order.id == order_id,
                                           Order.tenant_id == tenant_id).with_for_update())
    if not order:
        raise HTTPException(404, detail={"code": "ORDER_NOT_FOUND", "message": "Order not found"})
    if order.status == "paid":
        sub = db.scalar(select(Subscription).where(Subscription.id == order.subscription_id))
        if sub:
            return sub
        raise HTTPException(409, detail={"code": "ORDER_STATE_INVALID", "message": "Paid order has no subscription"})
    if order.status != "pending_payment":
        raise HTTPException(409, detail={"code": "ORDER_STATE_INVALID", "message": "Order cannot be paid in its current state"})
    if data.amount_minor != order.price_minor or data.currency.upper() != order.currency.upper():
        raise HTTPException(422, detail={"code": "PAYMENT_AMOUNT_MISMATCH", "message": "Payment amount or currency does not match order"})
    payment = Payment(tenant_id=tenant_id, order_id=order.id, provider=data.provider,
                      provider_reference=data.provider_reference, status="confirmed",
                      amount_minor=data.amount_minor, currency=data.currency.upper(), confirmed_at=utc_now())
    db.add(payment)
    order.status = "paid"
    if order.kind == "renewal":
        sub = db.scalar(select(Subscription).where(Subscription.id == order.subscription_id,
                                                   Subscription.tenant_id == tenant_id).with_for_update())
        if not sub:
            raise HTTPException(404, detail={"code": "SUBSCRIPTION_NOT_FOUND", "message": "Subscription not found"})
        base = as_utc(sub.expires_at) if sub.expires_at and as_utc(sub.expires_at) > utc_now() else utc_now()
        sub.expires_at = base + timedelta(days=sub.duration_days)
        sub.traffic_limit_bytes += int(json.loads(order.plan_snapshot)["traffic_limit_bytes"])
        sub.notified_thresholds = ""
        if sub.status in {"expired", "exhausted", "suspended", "failed"}:
            sub.status = "provisioning"
            peers = db.scalars(select(Peer).where(Peer.subscription_id == sub.id)).all()
            for peer in peers:
                peer.status = "provisioning"
                db.add(Job(tenant_id=tenant_id, kind="provision_peer", resource_id=peer.id))
    else:
        snapshot = json.loads(order.plan_snapshot)
        sub = Subscription(tenant_id=tenant_id, user_id=order.user_id, order_id=order.id,
                           plan_id=order.plan_id, status="provisioning",
                           traffic_limit_bytes=snapshot["traffic_limit_bytes"],
                           duration_days=snapshot["duration_days"], peer_limit=snapshot["peer_limit"],
                           provisioned_peer_count=order.peer_count,
                           server_countries=snapshot.get("server_countries", ""), price_minor=order.price_minor,
                           currency=order.currency)
        db.add(sub)
        db.flush()
        for _ in range(order.peer_count):
            peer = allocate_peer(db, tenant_id, sub, snapshot.get("server_countries", ""), order.selected_country)
            db.add(peer)
            db.flush()
            db.add(Job(tenant_id=tenant_id, kind="provision_peer", resource_id=peer.id))
        order.subscription_id = sub.id
    emit(db, tenant_id, "payment.confirmed", {"order_id": order.id, "subscription_id": sub.id})
    audit(db, tenant_id, "api", "payment.confirmed", payment.id)
    db.commit()
    db.refresh(sub)
    return sub


def allocate_peer(db: Session, tenant_id: str, subscription: Subscription, countries: str = "",
                  requested_country: str = "") -> Peer:
    peer_count = db.scalar(select(func.count(Peer.id)).where(Peer.subscription_id == subscription.id,
                                                              Peer.status != "revoked")) or 0
    if peer_count >= subscription.peer_limit:
        raise HTTPException(409, detail={"code": "PEER_LIMIT_EXCEEDED", "message": "Subscription peer limit reached"})
    allowed = {c for c in countries.split(",") if c}
    if requested_country:
        if allowed and requested_country not in allowed:
            raise HTTPException(422, detail={"code": "SERVER_POLICY_MISMATCH", "message": "Requested country is not permitted by plan"})
        allowed = {requested_country}
    servers = db.scalars(select(Server).where(Server.tenant_id == tenant_id,
                                               Server.active.is_(True), Server.healthy.is_(True))
                         .with_for_update()).all()
    eligible_servers = []
    for server in servers:
        node = db.get(Node, server.node_id) if server.node_id else None
        if not server.node_id or (node is not None and node.active):
            eligible_servers.append(server)
    servers = eligible_servers
    peers_by_server = {server.id: db.scalar(select(func.count(Peer.id)).where(
        Peer.server_id == server.id, Peer.status.in_(["active", "provisioning"]))) or 0 for server in servers}
    candidates = [s for s in servers if not allowed or s.country.upper() in allowed]
    candidates.sort(key=lambda s: peers_by_server[s.id])
    for server in candidates:
        network = ip_network(server.address_pool, strict=False)
        used = set(db.scalars(select(Peer.client_ip).where(Peer.server_id == server.id,
                                                            Peer.status != "revoked")).all())
        for address in network.hosts():
            value = str(address)
            if server.agent_url.startswith("node-poll://") and address == network.network_address + 1:
                continue
            if value not in used:
                return Peer(tenant_id=tenant_id, subscription_id=subscription.id,
                            server_id=server.id, client_ip=value)
    raise HTTPException(503, detail={"code": "SERVER_UNAVAILABLE", "message": "No healthy server has an available address"})


def node_command(db: Session, node_id: str, operation: str, payload: dict | None = None,
                 timeout: float = 40):
    node = db.get(Node, node_id)
    if not node or not node.active:
        raise RuntimeError("Node is missing or disabled")
    if operation != "ping" and (not node.healthy or not node.last_seen_at or
            (utc_now() - as_utc(node.last_seen_at)).total_seconds() > 60):
        raise RuntimeError(f"Node {node.name} is not connected to the panel")
    command = NodeCommand(node_id=node_id, operation=operation,
                          payload=json.dumps(payload or {}), status="queued")
    db.add(command)
    db.commit()
    command_id = command.id
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        db.expire_all()
        command = db.get(NodeCommand, command_id)
        if command and command.status == "done":
            return json.loads(command.result or "{}")
        if command and command.status in {"failed", "expired"}:
            raise RuntimeError(command.error or f"Node command {operation} failed")
        db.rollback()
        time.sleep(0.25)
    command = db.get(NodeCommand, command_id)
    if command and command.status in {"queued", "running"}:
        command.status = "expired"
        command.error = "Node command timed out"
        db.commit()
    else:
        db.rollback()
    raise RuntimeError(f"Node {node.name} did not respond to {operation} in time")


def agent_request(db: Session, server: Server, method: str, path: str, payload: dict | None = None):
    if server.node_id and server.agent_url.startswith("node-poll://"):
        parsed = urlparse(path)
        if parsed.path == "/internal/health":
            operation = "ping"
            command_payload = {"interface_name": parse_qs(parsed.query).get("interface_name", ["wg0"])[0]}
        elif parsed.path == "/internal/peers" and method.upper() == "POST":
            operation, command_payload = "peer.create", payload or {}
        elif parsed.path.startswith("/internal/peers/") and method.upper() == "DELETE":
            operation = "peer.delete"
            command_payload = {"peer_id": parsed.path.rsplit("/", 1)[-1],
                               "interface_name": parse_qs(parsed.query).get("interface_name", [server.interface_name])[0]}
        elif parsed.path == "/internal/traffic":
            operation = "traffic"
            command_payload = {"interface_name": parse_qs(parsed.query).get("interface_name", [server.interface_name])[0]}
        else:
            raise RuntimeError(f"Unsupported Node operation: {method} {path}")
        return node_command(db, server.node_id, operation, command_payload)
    token = decrypt(server.agent_secret_ciphertext)
    try:
        response = httpx.request(method, server.agent_url.rstrip("/") + path, json=payload,
                                 headers={"Authorization": f"Bearer {token}"}, timeout=20)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(f"Agent request failed for {server.name}") from exc


def process_job(db: Session, job: Job) -> None:
    if job.kind == "provision_peer":
        peer = db.get(Peer, job.resource_id)
        if not peer:
            job.status = "done"
            return
        server = db.get(Server, peer.server_id)
        sub = db.get(Subscription, peer.subscription_id)
        if not server or not sub:
            raise RuntimeError("Peer server or subscription is missing")
        if sub.status in {"suspended", "expired", "exhausted", "failed"}:
            agent_request(db, server, "DELETE", f"/internal/peers/{peer.id}?interface_name={server.interface_name}")
            peer.status = "revoked"
            job.status = "done"
            db.commit()
            return
        was_recreated = peer.status == "recreating"
        result = agent_request(db, server, "POST", "/internal/peers", {
            "peer_id": peer.id,
            "interface_name": server.interface_name,
            "client_ip": f"{peer.client_ip}/{32 if ip_address(peer.client_ip).version == 4 else 128}",
            "endpoint": server.endpoint,
            "server_public_key": server.public_key, "dns": server.dns,
            "previous_public_key": peer.public_key,
            "force_recreate": was_recreated,
        })
        peer.public_key = result["public_key"]
        peer.config_ciphertext = encrypt(result["config"])
        peer.status = "active"
        if was_recreated:
            peer.last_rx_bytes = 0
            peer.last_tx_bytes = 0
        active_left = db.scalar(select(func.count(Peer.id)).where(
            Peer.subscription_id == sub.id,
            Peer.status.in_(["provisioning", "recreating", "revoking", "failed"]))) or 0
        if active_left == 0 and sub.status != "active":
            sub.status = "active"
            if not sub.expires_at or as_utc(sub.expires_at) <= utc_now():
                sub.expires_at = utc_now() + timedelta(days=sub.duration_days)
            emit(db, sub.tenant_id, "subscription.active", {"subscription_id": sub.id})
        emit(db, sub.tenant_id, "peer.created", {"subscription_id": sub.id, "peer_id": peer.id})
        job.status = "done"
        db.commit()
        return
    if job.kind == "revoke_peer":
        peer = db.get(Peer, job.resource_id)
        if peer:
            server = db.get(Server, peer.server_id)
            if server:
                agent_request(db, server, "DELETE", f"/internal/peers/{peer.id}?interface_name={server.interface_name}")
            peer.status = "revoked"
        job.status = "done"
        db.commit()
        return
    raise RuntimeError(f"Unknown job type: {job.kind}")


def poll_usage(db: Session) -> None:
    servers = db.scalars(select(Server)).all()
    for server in servers:
        if not server.active:
            active_peer_count = db.scalar(select(func.count(Peer.id)).where(
                Peer.server_id == server.id, Peer.status == "active")) or 0
            if not active_peer_count:
                continue
        try:
            result = agent_request(db, server, "GET", f"/internal/traffic?interface_name={server.interface_name}")
            generation = str(result.get("interface_generation") or "")
            generation_changed = bool(generation and server.interface_generation
                                      and generation != server.interface_generation)
            if generation:
                server.interface_generation = generation
            server.healthy = True
            server.last_seen_at = utc_now()
            if server.node_id:
                node = db.get(Node, server.node_id)
                if node:
                    node.healthy = True
                    node.last_seen_at = server.last_seen_at
            counters = {row["public_key"]: row for row in result.get("peers", [])}
            peers = db.scalars(select(Peer).where(Peer.server_id == server.id, Peer.status == "active")).all()
            for peer in peers:
                if generation_changed:
                    peer.last_rx_bytes = 0
                    peer.last_tx_bytes = 0
                counter = counters.get(peer.public_key)
                if not counter:
                    node = db.get(Node, server.node_id) if server.node_id else None
                    if not server.active or (node is not None and not node.active):
                        continue
                    peer.last_rx_bytes = 0
                    peer.last_tx_bytes = 0
                    pending = db.scalar(select(Job.id).where(Job.kind == "provision_peer",
                        Job.resource_id == peer.id, Job.status.in_(["queued", "retry", "running"])))
                    if not pending:
                        peer.status = "provisioning"
                        db.add(Job(tenant_id=peer.tenant_id, kind="provision_peer", resource_id=peer.id))
                        sub = db.get(Subscription, peer.subscription_id)
                        if sub:
                            sub.status = "provisioning"
                    continue
                rx, tx = int(counter["rx_bytes"]), int(counter["tx_bytes"])
                rx_delta = rx - peer.last_rx_bytes if rx >= peer.last_rx_bytes else rx
                tx_delta = tx - peer.last_tx_bytes if tx >= peer.last_tx_bytes else tx
                peer.rx_bytes += rx_delta
                peer.tx_bytes += tx_delta
                peer.last_rx_bytes, peer.last_tx_bytes = rx, tx
                delta = rx_delta + tx_delta
                sub = db.get(Subscription, peer.subscription_id)
                if sub and delta:
                    sub.used_bytes += delta
                    notified = {value for value in sub.notified_thresholds.split(",") if value}
                    percentage = (sub.used_bytes * 100 / sub.traffic_limit_bytes
                                  if sub.traffic_limit_bytes else 100)
                    for threshold in (80, 90):
                        if percentage >= threshold and str(threshold) not in notified:
                            notified.add(str(threshold))
                            emit(db, sub.tenant_id, f"subscription.quota_{threshold}", {
                                "subscription_id": sub.id, "used_bytes": sub.used_bytes,
                                "limit_bytes": sub.traffic_limit_bytes,
                            })
                    sub.notified_thresholds = ",".join(sorted(notified))
                    if sub.used_bytes >= sub.traffic_limit_bytes and sub.status == "active":
                        sub.status = "exhausted"
                        emit(db, sub.tenant_id, "subscription.exhausted", {"subscription_id": sub.id})
                        for item in db.scalars(select(Peer).where(Peer.subscription_id == sub.id,
                                                                    Peer.status == "active")).all():
                            db.add(Job(tenant_id=sub.tenant_id, kind="revoke_peer", resource_id=item.id))
                            item.status = "revoking"
            db.commit()
        except Exception:
            server.healthy = False
            if server.node_id:
                node = db.get(Node, server.node_id)
                if node:
                    node.healthy = False
            db.commit()


def expire_due(db: Session) -> None:
    now = utc_now()
    due = db.scalars(select(Subscription).where(Subscription.status == "active",
                                                Subscription.expires_at <= now)).all()
    for sub in due:
        sub.status = "expired"
        emit(db, sub.tenant_id, "subscription.expired", {"subscription_id": sub.id})
        for peer in db.scalars(select(Peer).where(Peer.subscription_id == sub.id,
                                                   Peer.status == "active")).all():
            peer.status = "revoking"
            db.add(Job(tenant_id=sub.tenant_id, kind="revoke_peer", resource_id=peer.id))
    db.commit()


def deliver_webhooks(db: Session) -> None:
    now = utc_now()
    items = db.scalars(select(WebhookDelivery).where(WebhookDelivery.status.in_(["queued", "retry"]),
                                                       WebhookDelivery.run_after <= now).limit(25)).all()
    for delivery in items:
        endpoint = db.get(WebhookEndpoint, delivery.endpoint_id)
        if not endpoint or not endpoint.active:
            delivery.status = "failed"
            continue
        timestamp = str(int(time.time()))
        signature = hmac.new(decrypt(endpoint.secret).encode(),
                             f"{timestamp}.{delivery.payload}".encode(), hashlib.sha256).hexdigest()
        try:
            parsed = urlparse(endpoint.url)
            addresses = {ip_address(item[4][0]) for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443)}
            if parsed.scheme != "https" or not addresses or any(not address.is_global for address in addresses):
                raise RuntimeError("Webhook destination failed public HTTPS validation")
            response = httpx.post(endpoint.url, content=delivery.payload, timeout=10,
                                  headers={"Content-Type": "application/json", "X-WG-Timestamp": timestamp,
                                           "X-WG-Signature": f"sha256={signature}", "X-WG-Event": delivery.event_type,
                                           "X-WG-Delivery": delivery.id})
            response.raise_for_status()
            delivery.status = "delivered"
        except (httpx.HTTPError, OSError, RuntimeError):
            delivery.attempts += 1
            if delivery.attempts >= 8:
                delivery.status = "failed"
            else:
                delivery.status = "retry"
                delivery.run_after = now + timedelta(seconds=min(3600, 2 ** delivery.attempts * 5))
    db.commit()
