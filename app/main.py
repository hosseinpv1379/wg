import hashlib
import io
import re
import secrets
import socket
import zipfile
from pathlib import Path
from uuid import uuid4
from ipaddress import ip_address
from urllib.parse import urlparse

import qrcode
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import require_scope
from app.crypto import decrypt, encrypt
from app.db import get_db
from app.models import (ApiCredential, AuditLog, Job, Node, Order, Peer, Plan, Server, Subscription,
                        User, WebhookDelivery, WebhookEndpoint, as_utc, utc_now)
from app.schemas import (ApiResponse, OrderCreate, OrderOut, PaymentConfirm, PeerCreate, PeerOut, PlanCreate,
                         PlanOut, PlanUpdate, ServerCreate, ServerOut, SubscriptionOut, UserCreate, UserOut, UserUpdate,
                         ActiveUpdate, ApiKeyCreate, NodeCreate, NodeOut, WebhookCreate, WebhookOut)
from app.services import (ALL_SCOPES, allocate_peer, audit, confirm_payment, create_order,
                          create_node, create_plan, create_renewal_order, create_server, create_user, emit)

app = FastAPI(title="WireGuard Service API", version="1.0.0",
              description="Headless multi-tenant WireGuard management and billing service")


def success(data, request: Request):
    return {"success": True, "data": data, "request_id": request.state.request_id}


def tenant_scope(value: tuple[str, str]) -> str:
    return value[0]


def validate_webhook_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(422, detail={"code": "INVALID_WEBHOOK_URL", "message": "Webhook URL must be an HTTPS URL"})
    try:
        addresses = {ip_address(item[4][0]) for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443)}
    except OSError as exc:
        raise HTTPException(422, detail={"code": "INVALID_WEBHOOK_URL", "message": "Webhook host could not be resolved"}) from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise HTTPException(422, detail={"code": "INVALID_WEBHOOK_URL", "message": "Webhook host must resolve only to public addresses"})


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    requested_id = request.headers.get("X-Request-ID", "")
    request.state.request_id = requested_id if re.fullmatch(r"[A-Za-z0-9._-]{1,100}", requested_id) else f"req_{uuid4().hex}"
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "HTTP_ERROR", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"success": False, "error": detail,
        "request_id": getattr(request.state, "request_id", "unknown")}, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"success": False,
        "error": {"code": "VALIDATION_ERROR", "message": "Request validation failed"},
        "request_id": getattr(request.state, "request_id", "unknown")})


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"success": False,
        "error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred"},
        "request_id": getattr(request.state, "request_id", "unknown")})


@app.get("/health")
def health(request: Request):
    return success({"status": "ok"}, request)


@app.get("/panel", include_in_schema=False)
def panel():
    return FileResponse(Path(__file__).parent / "static" / "panel.html",
                        media_type="text/html; charset=utf-8")


@app.get("/api/v1/plans", response_model=ApiResponse[list[PlanOut]])
def list_plans(request: Request, auth=Depends(require_scope("plans:read")),
               db: Session = Depends(get_db)):
    tenant_id = tenant_scope(auth)
    rows = db.scalars(select(Plan).where(Plan.tenant_id == tenant_id, Plan.active.is_(True))).all()
    return success(rows, request)


@app.post("/api/v1/plans", response_model=ApiResponse[PlanOut], status_code=201)
def post_plan(data: PlanCreate, request: Request, auth=Depends(require_scope("plans:write")),
              db: Session = Depends(get_db)):
    return success(create_plan(db, tenant_scope(auth), data), request)


@app.delete("/api/v1/plans/{plan_id}")
def disable_plan(plan_id: str, request: Request, auth=Depends(require_scope("plans:write")),
                 db: Session = Depends(get_db)):
    plan = db.scalar(select(Plan).where(Plan.id == plan_id, Plan.tenant_id == tenant_scope(auth)))
    if not plan:
        raise HTTPException(404, detail={"code": "PLAN_NOT_FOUND", "message": "Plan not found"})
    plan.active = False
    audit(db, plan.tenant_id, auth[1], "plan.disabled", plan.id)
    db.commit()
    return success({"id": plan.id, "active": False}, request)


@app.patch("/api/v1/plans/{plan_id}", response_model=ApiResponse[PlanOut])
def patch_plan(plan_id: str, data: PlanUpdate, request: Request,
               auth=Depends(require_scope("plans:write")), db: Session = Depends(get_db)):
    plan = db.scalar(select(Plan).where(Plan.id == plan_id,
                                         Plan.tenant_id == tenant_scope(auth)).with_for_update())
    if not plan:
        raise HTTPException(404, detail={"code": "PLAN_NOT_FOUND", "message": "Plan not found"})
    values = data.model_dump(exclude_unset=True)
    countries = values.pop("server_countries", None)
    if any(value is None for value in values.values()):
        raise HTTPException(422, detail={"code": "INVALID_PLAN_UPDATE", "message": "Plan fields cannot be null"})
    for key, value in values.items():
        setattr(plan, key, value)
    if countries is not None:
        plan.server_countries = ",".join(country.upper() for country in countries)
    audit(db, plan.tenant_id, auth[1], "plan.updated", plan.id)
    db.commit()
    db.refresh(plan)
    return success(plan, request)


@app.post("/api/v1/users", response_model=ApiResponse[UserOut], status_code=201)
def post_user(data: UserCreate, request: Request, auth=Depends(require_scope("users:write")),
              db: Session = Depends(get_db)):
    return success(create_user(db, tenant_scope(auth), data), request)


@app.get("/api/v1/users/{user_id}", response_model=ApiResponse[UserOut])
def get_user(user_id: str, request: Request, auth=Depends(require_scope("users:read")),
             db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.id == user_id, User.tenant_id == tenant_scope(auth)))
    if not user:
        raise HTTPException(404, detail={"code": "USER_NOT_FOUND", "message": "User not found"})
    return success(user, request)


@app.patch("/api/v1/users/{user_id}", response_model=ApiResponse[UserOut])
def patch_user(user_id: str, data: UserUpdate, request: Request,
               auth=Depends(require_scope("users:write")), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.id == user_id,
                                         User.tenant_id == tenant_scope(auth)).with_for_update())
    if not user:
        raise HTTPException(404, detail={"code": "USER_NOT_FOUND", "message": "User not found"})
    user.status = data.status
    audit(db, user.tenant_id, auth[1], "user.status_changed", user.id)
    db.commit()
    return success(user, request)


@app.get("/api/v1/servers", response_model=ApiResponse[list[ServerOut]])
def list_servers(request: Request, auth=Depends(require_scope("servers:read")),
                 include_disabled: bool = False, db: Session = Depends(get_db)):
    query = select(Server).where(Server.tenant_id == tenant_scope(auth))
    if not include_disabled:
        query = query.where(Server.active.is_(True))
    rows = db.scalars(query.order_by(Server.name)).all()
    return success(rows, request)


@app.get("/api/v1/servers/{server_id}/health", response_model=ApiResponse[dict])
def get_server_health(server_id: str, request: Request,
                      auth=Depends(require_scope("servers:read")), db: Session = Depends(get_db)):
    server = db.scalar(select(Server).where(Server.id == server_id,
                                              Server.tenant_id == tenant_scope(auth)))
    if not server:
        raise HTTPException(404, detail={"code": "SERVER_NOT_FOUND", "message": "Server not found"})
    return success({"server_id": server.id, "healthy": server.healthy,
                    "last_seen_at": server.last_seen_at}, request)


@app.post("/api/v1/servers", response_model=ApiResponse[ServerOut], status_code=201)
def post_server(data: ServerCreate, request: Request, auth=Depends(require_scope("servers:write")),
                db: Session = Depends(get_db)):
    return success(create_server(db, tenant_scope(auth), data), request)


@app.get("/api/v1/nodes", response_model=ApiResponse[list[NodeOut]])
def list_nodes(request: Request, auth=Depends(require_scope("servers:read")),
               db: Session = Depends(get_db)):
    tenant_id = tenant_scope(auth)
    rows = db.scalars(select(Node).where(Node.tenant_id == tenant_id).order_by(Node.created_at.desc())).all()
    return success(rows, request)


@app.post("/api/v1/nodes", response_model=ApiResponse[NodeOut], status_code=201)
def post_node(data: NodeCreate, request: Request, auth=Depends(require_scope("servers:write")),
              db: Session = Depends(get_db)):
    node = create_node(db, tenant_scope(auth), data)
    return success(node, request)


@app.patch("/api/v1/nodes/{node_id}", response_model=ApiResponse[NodeOut])
def patch_node(node_id: str, data: ActiveUpdate, request: Request,
               auth=Depends(require_scope("servers:write")), db: Session = Depends(get_db)):
    node = db.scalar(select(Node).where(Node.id == node_id, Node.tenant_id == tenant_scope(auth)))
    if not node:
        raise HTTPException(404, detail={"code": "NODE_NOT_FOUND", "message": "Node not found"})
    node.active = data.active
    audit(db, node.tenant_id, auth[1], "node.status_changed", node.id)
    db.commit()
    db.refresh(node)
    return success(node, request)


@app.patch("/api/v1/servers/{server_id}", response_model=ApiResponse[ServerOut])
def patch_server(server_id: str, data: ActiveUpdate, request: Request,
                 auth=Depends(require_scope("servers:write")), db: Session = Depends(get_db)):
    server = db.scalar(select(Server).where(Server.id == server_id,
                                              Server.tenant_id == tenant_scope(auth)))
    if not server:
        raise HTTPException(404, detail={"code": "INTERFACE_NOT_FOUND", "message": "Interface not found"})
    server.active = data.active
    audit(db, server.tenant_id, auth[1], "interface.status_changed", server.id)
    db.commit()
    db.refresh(server)
    return success(server, request)


@app.get("/api/v1/nodes/{node_id}/interfaces", response_model=ApiResponse[list[ServerOut]])
def list_node_interfaces(node_id: str, request: Request,
                         auth=Depends(require_scope("servers:read")), db: Session = Depends(get_db)):
    tenant_id = tenant_scope(auth)
    node = db.scalar(select(Node).where(Node.id == node_id, Node.tenant_id == tenant_id))
    if not node:
        raise HTTPException(404, detail={"code": "NODE_NOT_FOUND", "message": "Node not found"})
    rows = db.scalars(select(Server).where(Server.tenant_id == tenant_id,
                                           Server.node_id == node_id).order_by(Server.name)).all()
    return success(rows, request)


@app.post("/api/v1/nodes/{node_id}/test-connection", response_model=ApiResponse[dict])
def test_node_connection(node_id: str, request: Request,
                         auth=Depends(require_scope("servers:write")), db: Session = Depends(get_db)):
    tenant_id = tenant_scope(auth)
    node = db.scalar(select(Node).where(Node.id == node_id, Node.tenant_id == tenant_id))
    if not node:
        raise HTTPException(404, detail={"code": "NODE_NOT_FOUND", "message": "Node not found"})
    from app.services import test_agent_connection
    interface = db.scalar(select(Server.interface_name).where(Server.node_id == node.id,
                                                               Server.tenant_id == tenant_id)) or "wg0"
    ok = test_agent_connection(node.agent_url, decrypt(node.agent_secret_ciphertext), interface)
    node.healthy = ok
    node.last_seen_at = utc_now() if ok else node.last_seen_at
    audit(db, tenant_id, auth[1], "node.connection_tested", node.id)
    db.commit()
    return success({"node_id": node.id, "healthy": ok, "last_seen_at": node.last_seen_at}, request)


@app.post("/api/v1/orders", response_model=ApiResponse[OrderOut], status_code=201)
def post_order(data: OrderCreate, request: Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
               auth=Depends(require_scope("orders:create")), db: Session = Depends(get_db)):
    return success(create_order(db, tenant_scope(auth), data, idempotency_key), request)


@app.get("/api/v1/orders/{order_id}", response_model=ApiResponse[OrderOut])
def get_order(order_id: str, request: Request, auth=Depends(require_scope("orders:read")),
              db: Session = Depends(get_db)):
    order = db.scalar(select(Order).where(Order.id == order_id, Order.tenant_id == tenant_scope(auth)))
    if not order:
        raise HTTPException(404, detail={"code": "ORDER_NOT_FOUND", "message": "Order not found"})
    return success(order, request)


@app.get("/api/v1/orders", response_model=ApiResponse[list[OrderOut]])
def list_orders(request: Request, offset: int = 0, limit: int = 50,
                auth=Depends(require_scope("orders:read")), db: Session = Depends(get_db)):
    rows = db.scalars(select(Order).where(Order.tenant_id == tenant_scope(auth))
                      .order_by(Order.created_at.desc()).offset(max(offset, 0)).limit(min(limit, 100))).all()
    return success(rows, request)


@app.post("/api/v1/orders/{order_id}/confirm-payment", response_model=ApiResponse[SubscriptionOut], status_code=202)
def post_payment_confirmation(order_id: str, data: PaymentConfirm, request: Request,
                              auth=Depends(require_scope("payments:confirm")), db: Session = Depends(get_db)):
    sub = confirm_payment(db, tenant_scope(auth), order_id, data)
    return success(sub, request)


@app.get("/api/v1/subscriptions", response_model=ApiResponse[list[SubscriptionOut]])
def list_subscriptions(request: Request, offset: int = 0, limit: int = 50,
                       auth=Depends(require_scope("subscriptions:read")), db: Session = Depends(get_db)):
    rows = db.scalars(select(Subscription).where(Subscription.tenant_id == tenant_scope(auth))
                      .order_by(Subscription.created_at.desc()).offset(max(offset, 0)).limit(min(limit, 100))).all()
    return success(rows, request)


@app.get("/api/v1/subscriptions/{subscription_id}", response_model=ApiResponse[SubscriptionOut])
def get_subscription(subscription_id: str, request: Request,
                     auth=Depends(require_scope("subscriptions:read")), db: Session = Depends(get_db)):
    sub = db.scalar(select(Subscription).where(Subscription.id == subscription_id,
                                                Subscription.tenant_id == tenant_scope(auth)))
    if not sub:
        raise HTTPException(404, detail={"code": "SUBSCRIPTION_NOT_FOUND", "message": "Subscription not found"})
    return success(sub, request)


@app.post("/api/v1/subscriptions/{subscription_id}/renew", response_model=ApiResponse[OrderOut], status_code=201)
def renew_subscription(subscription_id: str, request: Request,
                       idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                       auth=Depends(require_scope("subscriptions:renew")), db: Session = Depends(get_db)):
    return success(create_renewal_order(db, tenant_scope(auth), subscription_id, idempotency_key), request)


@app.post("/api/v1/subscriptions/{subscription_id}/suspend", response_model=ApiResponse[SubscriptionOut])
def suspend_subscription(subscription_id: str, request: Request,
                         auth=Depends(require_scope("subscriptions:write")), db: Session = Depends(get_db)):
    sub = db.scalar(select(Subscription).where(Subscription.id == subscription_id,
                                                Subscription.tenant_id == tenant_scope(auth)).with_for_update())
    if not sub:
        raise HTTPException(404, detail={"code": "SUBSCRIPTION_NOT_FOUND", "message": "Subscription not found"})
    if sub.status != "active":
        raise HTTPException(409, detail={"code": "SUBSCRIPTION_STATE_INVALID", "message": "Only active subscriptions can be suspended"})
    sub.status = "suspended"
    for peer in db.scalars(select(Peer).where(Peer.subscription_id == sub.id,
                          Peer.status.in_(["active", "provisioning", "recreating"]))).all():
        peer.status = "revoking"
        db.add(Job(tenant_id=sub.tenant_id, kind="revoke_peer", resource_id=peer.id))
    emit(db, sub.tenant_id, "subscription.suspended", {"subscription_id": sub.id})
    audit(db, sub.tenant_id, auth[1], "subscription.suspended", sub.id)
    db.commit()
    return success(sub, request)


@app.post("/api/v1/subscriptions/{subscription_id}/resume", response_model=ApiResponse[SubscriptionOut])
def resume_subscription(subscription_id: str, request: Request,
                         auth=Depends(require_scope("subscriptions:write")), db: Session = Depends(get_db)):
    sub = db.scalar(select(Subscription).where(Subscription.id == subscription_id,
                                                Subscription.tenant_id == tenant_scope(auth)))
    if not sub or sub.status != "suspended":
        raise HTTPException(409, detail={"code": "SUBSCRIPTION_STATE_INVALID", "message": "Subscription is not suspended"})
    if sub.expires_at and as_utc(sub.expires_at) <= utc_now():
        raise HTTPException(409, detail={"code": "SUBSCRIPTION_EXPIRED", "message": "Renew subscription before resuming"})
    peers = db.scalars(select(Peer).where(Peer.subscription_id == sub.id)).all()
    if not peers:
        raise HTTPException(409, detail={"code": "SUBSCRIPTION_HAS_NO_PEERS", "message": "Create a peer after renewing the subscription"})
    sub.status = "provisioning"
    for peer in peers:
        peer.status = "provisioning"
        db.add(Job(tenant_id=sub.tenant_id, kind="provision_peer", resource_id=peer.id))
    emit(db, sub.tenant_id, "subscription.resumed", {"subscription_id": sub.id})
    audit(db, sub.tenant_id, auth[1], "subscription.resumed", sub.id)
    db.commit()
    return success(sub, request)


@app.post("/api/v1/subscriptions/{subscription_id}/peers", response_model=ApiResponse[PeerOut], status_code=202)
def post_peer(subscription_id: str, data: PeerCreate, request: Request,
              auth=Depends(require_scope("peers:write")), db: Session = Depends(get_db)):
    tenant_id = tenant_scope(auth)
    sub = db.scalar(select(Subscription).where(Subscription.id == subscription_id,
                                                Subscription.tenant_id == tenant_id).with_for_update())
    if not sub or sub.status != "active":
        raise HTTPException(409, detail={"code": "SUBSCRIPTION_INACTIVE", "message": "Subscription is not active"})
    peer = allocate_peer(db, tenant_id, sub, sub.server_countries, (data.country or "").upper())
    db.add(peer)
    db.flush()
    db.add(Job(tenant_id=tenant_id, kind="provision_peer", resource_id=peer.id))
    sub.provisioned_peer_count += 1
    audit(db, tenant_id, auth[1], "peer.create_requested", peer.id)
    db.commit()
    db.refresh(peer)
    return success(peer, request)


@app.get("/api/v1/subscriptions/{subscription_id}/peers", response_model=ApiResponse[list[PeerOut]])
def list_peers(subscription_id: str, request: Request, auth=Depends(require_scope("peers:read")),
               db: Session = Depends(get_db)):
    tenant_id = tenant_scope(auth)
    if not db.scalar(select(Subscription.id).where(Subscription.id == subscription_id,
                                                   Subscription.tenant_id == tenant_id)):
        raise HTTPException(404, detail={"code": "SUBSCRIPTION_NOT_FOUND", "message": "Subscription not found"})
    rows = db.scalars(select(Peer).where(Peer.subscription_id == subscription_id,
                                          Peer.tenant_id == tenant_id)).all()
    return success(rows, request)


@app.post("/api/v1/peers/{peer_id}/revoke", response_model=ApiResponse[PeerOut], status_code=202)
def revoke_peer(peer_id: str, request: Request, auth=Depends(require_scope("peers:write")),
                db: Session = Depends(get_db)):
    tenant_id = tenant_scope(auth)
    peer = db.scalar(select(Peer).where(Peer.id == peer_id, Peer.tenant_id == tenant_id))
    if not peer:
        raise HTTPException(404, detail={"code": "PEER_NOT_FOUND", "message": "Peer not found"})
    if peer.status not in {"revoked", "revoking"}:
        peer.status = "revoking"
        db.add(Job(tenant_id=tenant_id, kind="revoke_peer", resource_id=peer.id))
        audit(db, tenant_id, auth[1], "peer.revoke_requested", peer.id)
        db.commit()
    return success(peer, request)


@app.post("/api/v1/peers/{peer_id}/recreate", response_model=ApiResponse[PeerOut], status_code=202)
def recreate_peer(peer_id: str, request: Request,
                  auth=Depends(require_scope("peers:write")), db: Session = Depends(get_db)):
    peer = db.scalar(select(Peer).where(Peer.id == peer_id,
                                         Peer.tenant_id == tenant_scope(auth)).with_for_update())
    if not peer:
        raise HTTPException(404, detail={"code": "PEER_NOT_FOUND", "message": "Peer not found"})
    sub = db.get(Subscription, peer.subscription_id)
    if not sub or sub.status != "active":
        raise HTTPException(409, detail={"code": "SUBSCRIPTION_INACTIVE", "message": "Subscription is not active"})
    if peer.status in {"recreating", "provisioning", "revoking"}:
        raise HTTPException(409, detail={"code": "PEER_STATE_INVALID", "message": "Peer already has an operation in progress"})
    peer.status = "recreating"
    db.add(Job(tenant_id=peer.tenant_id, kind="provision_peer", resource_id=peer.id))
    audit(db, peer.tenant_id, auth[1], "peer.recreate_requested", peer.id)
    db.commit()
    return success(peer, request)


@app.get("/api/v1/peers/{peer_id}/config")
def get_peer_config(peer_id: str, auth=Depends(require_scope("peers:read")), db: Session = Depends(get_db)):
    peer = db.scalar(select(Peer).where(Peer.id == peer_id, Peer.tenant_id == tenant_scope(auth),
                                         Peer.status == "active"))
    if not peer:
        raise HTTPException(404, detail={"code": "PEER_NOT_FOUND", "message": "Active peer not found"})
    return Response(decrypt(peer.config_ciphertext), media_type="text/plain",
                    headers={"Cache-Control": "no-store, private", "Content-Disposition": f'attachment; filename="{peer.id}.conf"'})


@app.get("/api/v1/peers/{peer_id}/qr")
def get_peer_qr(peer_id: str, auth=Depends(require_scope("peers:read")), db: Session = Depends(get_db)):
    peer = db.scalar(select(Peer).where(Peer.id == peer_id, Peer.tenant_id == tenant_scope(auth),
                                         Peer.status == "active"))
    if not peer:
        raise HTTPException(404, detail={"code": "PEER_NOT_FOUND", "message": "Active peer not found"})
    image = qrcode.make(decrypt(peer.config_ciphertext))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return Response(output.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store, private"})


@app.get("/api/v1/peers/{peer_id}/usage", response_model=ApiResponse[dict])
def get_peer_usage(peer_id: str, request: Request, auth=Depends(require_scope("peers:read")),
                   db: Session = Depends(get_db)):
    peer = db.scalar(select(Peer).where(Peer.id == peer_id, Peer.tenant_id == tenant_scope(auth)))
    if not peer:
        raise HTTPException(404, detail={"code": "PEER_NOT_FOUND", "message": "Peer not found"})
    used = peer.rx_bytes + peer.tx_bytes
    return success({"peer_id": peer.id, "rx_bytes": peer.rx_bytes, "tx_bytes": peer.tx_bytes,
                    "used_bytes": used}, request)


@app.get("/api/v1/subscriptions/{subscription_id}/configs.zip")
def get_configs_zip(subscription_id: str, auth=Depends(require_scope("peers:read")),
                    db: Session = Depends(get_db)):
    tenant_id = tenant_scope(auth)
    sub = db.scalar(select(Subscription).where(Subscription.id == subscription_id,
                                                Subscription.tenant_id == tenant_id))
    if not sub:
        raise HTTPException(404, detail={"code": "SUBSCRIPTION_NOT_FOUND", "message": "Subscription not found"})
    peers = db.scalars(select(Peer).where(Peer.subscription_id == sub.id, Peer.status == "active")).all()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for peer in peers:
            archive.writestr(f"{peer.id}.conf", decrypt(peer.config_ciphertext))
    return Response(output.getvalue(), media_type="application/zip",
                    headers={"Cache-Control": "no-store, private",
                             "Content-Disposition": f'attachment; filename="{sub.id}-configs.zip"'})


@app.get("/api/v1/subscriptions/{subscription_id}/usage", response_model=ApiResponse[dict])
def get_usage(subscription_id: str, request: Request,
              auth=Depends(require_scope("subscriptions:read")), db: Session = Depends(get_db)):
    sub = db.scalar(select(Subscription).where(Subscription.id == subscription_id,
                                                Subscription.tenant_id == tenant_scope(auth)))
    if not sub:
        raise HTTPException(404, detail={"code": "SUBSCRIPTION_NOT_FOUND", "message": "Subscription not found"})
    remaining = max(0, sub.traffic_limit_bytes - sub.used_bytes)
    pct = round(100 * sub.used_bytes / sub.traffic_limit_bytes, 2) if sub.traffic_limit_bytes else 0
    return success({"limit_bytes": sub.traffic_limit_bytes, "used_bytes": sub.used_bytes,
                    "remaining_bytes": remaining, "percentage": pct}, request)


@app.post("/api/v1/webhooks", response_model=ApiResponse[dict], status_code=201)
def post_webhook(data: WebhookCreate, request: Request,
                 auth=Depends(require_scope("webhooks:write")), db: Session = Depends(get_db)):
    tenant_id = tenant_scope(auth)
    validate_webhook_url(data.url)
    secret = secrets.token_urlsafe(32)
    endpoint = WebhookEndpoint(tenant_id=tenant_id, url=data.url, secret=encrypt(secret),
                               events=",".join(data.events), active=True)
    db.add(endpoint)
    db.commit()
    db.refresh(endpoint)
    return success({"id": endpoint.id, "url": endpoint.url, "events": data.events,
                    "active": endpoint.active, "secret": secret}, request)


@app.delete("/api/v1/webhooks/{webhook_id}")
def disable_webhook(webhook_id: str, request: Request,
                    auth=Depends(require_scope("webhooks:write")), db: Session = Depends(get_db)):
    endpoint = db.scalar(select(WebhookEndpoint).where(WebhookEndpoint.id == webhook_id,
                                                         WebhookEndpoint.tenant_id == tenant_scope(auth)))
    if not endpoint:
        raise HTTPException(404, detail={"code": "WEBHOOK_NOT_FOUND", "message": "Webhook not found"})
    endpoint.active = False
    db.commit()
    return success({"id": endpoint.id, "active": False}, request)


@app.get("/api/v1/webhooks", response_model=ApiResponse[list[WebhookOut]])
def list_webhooks(request: Request, auth=Depends(require_scope("webhooks:read")),
                  db: Session = Depends(get_db)):
    endpoints = db.scalars(select(WebhookEndpoint).where(WebhookEndpoint.tenant_id == tenant_scope(auth))).all()
    return success([{"id": item.id, "url": item.url, "events": item.events.split(","),
                     "active": item.active} for item in endpoints], request)


@app.get("/api/v1/webhooks/{webhook_id}/deliveries", response_model=ApiResponse[list[dict]])
def list_webhook_deliveries(webhook_id: str, request: Request, offset: int = 0, limit: int = 50,
                            auth=Depends(require_scope("webhooks:read")), db: Session = Depends(get_db)):
    endpoint = db.scalar(select(WebhookEndpoint).where(WebhookEndpoint.id == webhook_id,
                                                         WebhookEndpoint.tenant_id == tenant_scope(auth)))
    if not endpoint:
        raise HTTPException(404, detail={"code": "WEBHOOK_NOT_FOUND", "message": "Webhook not found"})
    rows = db.scalars(select(WebhookDelivery).where(WebhookDelivery.endpoint_id == endpoint.id)
                      .order_by(WebhookDelivery.created_at.desc()).offset(max(offset, 0)).limit(min(limit, 100))).all()
    return success([{"id": row.id, "event": row.event_type, "status": row.status,
                     "attempts": row.attempts, "payload": row.payload} for row in rows], request)


@app.post("/api/v1/api-keys", status_code=201)
def post_api_key(data: ApiKeyCreate, request: Request,
                 auth=Depends(require_scope("keys:write")), db: Session = Depends(get_db)):
    invalid = set(data.scopes) - ALL_SCOPES
    if invalid:
        raise HTTPException(422, detail={"code": "INVALID_SCOPE", "message": f"Unknown scopes: {', '.join(sorted(invalid))}"})
    secret = f"wg_live_{secrets.token_urlsafe(36)}"
    credential = ApiCredential(tenant_id=tenant_scope(auth), name=data.name,
                               key_hash=hashlib.sha256(secret.encode()).hexdigest(),
                               scopes=",".join(sorted(set(data.scopes))), active=True)
    db.add(credential)
    db.commit()
    db.refresh(credential)
    audit(db, credential.tenant_id, auth[1], "api_key.created", credential.id)
    db.commit()
    return success({"id": credential.id, "name": credential.name, "scopes": data.scopes,
                    "secret": secret}, request)


@app.delete("/api/v1/api-keys/{key_id}")
def delete_api_key(key_id: str, request: Request, auth=Depends(require_scope("keys:write")),
                   db: Session = Depends(get_db)):
    credential = db.scalar(select(ApiCredential).where(ApiCredential.id == key_id,
                                                         ApiCredential.tenant_id == tenant_scope(auth)))
    if not credential:
        raise HTTPException(404, detail={"code": "API_KEY_NOT_FOUND", "message": "API key not found"})
    credential.active = False
    audit(db, credential.tenant_id, auth[1], "api_key.revoked", credential.id)
    db.commit()
    return success({"id": credential.id, "revoked": True}, request)


@app.get("/api/v1/audit-logs", response_model=ApiResponse[list[dict]])
def get_audit_logs(request: Request, offset: int = 0, limit: int = 100,
                   auth=Depends(require_scope("audit:read")), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditLog).where(AuditLog.tenant_id == tenant_scope(auth))
                      .order_by(AuditLog.created_at.desc()).offset(max(offset, 0)).limit(min(limit, 500))).all()
    return success([{"id": x.id, "actor": x.actor, "action": x.action,
                     "resource_id": x.resource_id, "created_at": x.created_at} for x in rows], request)


@app.get("/api/v1/jobs/{job_id}", response_model=ApiResponse[dict])
def get_job(job_id: str, request: Request, auth=Depends(require_scope("subscriptions:read")),
            db: Session = Depends(get_db)):
    job = db.scalar(select(Job).where(Job.id == job_id, Job.tenant_id == tenant_scope(auth)))
    if not job:
        raise HTTPException(404, detail={"code": "JOB_NOT_FOUND", "message": "Job not found"})
    return success({"id": job.id, "kind": job.kind, "resource_id": job.resource_id,
                    "status": job.status, "attempts": job.attempts, "error": job.last_error}, request)
