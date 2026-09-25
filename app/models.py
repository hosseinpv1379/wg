from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint, text)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ApiCredential(Base):
    __tablename__ = "api_credentials"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("key"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="API key")
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[str] = mapped_column(String(1000))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "external_id"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("usr"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("plan"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(500), default="")
    traffic_limit_bytes: Mapped[int] = mapped_column(BigInteger)
    duration_days: Mapped[int] = mapped_column(Integer)
    peer_limit: Mapped[int] = mapped_column(Integer)
    price_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    server_countries: Mapped[str] = mapped_column(String(200), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Node(Base):
    __tablename__ = "nodes"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("node"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    country: Mapped[str] = mapped_column(String(2))
    agent_url: Mapped[str] = mapped_column(String(500))
    agent_secret_ciphertext: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    healthy: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class NodeCredential(Base):
    __tablename__ = "node_credentials"
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class NodeCommand(Base):
    __tablename__ = "node_commands"
    __table_args__ = (Index("ix_node_commands_poll", "node_id", "status", "created_at"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("cmd"))
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), index=True)
    operation: Mapped[str] = mapped_column(String(40))
    payload: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(20), default="queued")
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Server(Base):
    __tablename__ = "servers"
    __table_args__ = (Index("uq_servers_node_interface", "node_id", "interface_name", unique=True),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("srv"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    node_id: Mapped[str | None] = mapped_column(ForeignKey("nodes.id"), nullable=True, index=True)
    interface_name: Mapped[str] = mapped_column(String(15), default="wg0")
    interface_generation: Mapped[str] = mapped_column(String(100), default="")
    name: Mapped[str] = mapped_column(String(120))
    country: Mapped[str] = mapped_column(String(2))
    endpoint: Mapped[str] = mapped_column(String(255))
    public_key: Mapped[str] = mapped_column(String(64))
    agent_url: Mapped[str] = mapped_column(String(500))
    agent_secret_ciphertext: Mapped[str] = mapped_column(Text)
    address_pool: Mapped[str] = mapped_column(String(100))
    dns: Mapped[str] = mapped_column(String(100), default="1.1.1.1")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    healthy: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("ord"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    plan_id: Mapped[str] = mapped_column(String(40))
    subscription_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    kind: Mapped[str] = mapped_column(String(20), default="purchase")
    peer_count: Mapped[int] = mapped_column(Integer, default=1)
    selected_country: Mapped[str] = mapped_column(String(2), default="")
    status: Mapped[str] = mapped_column(String(24), default="pending_payment")
    price_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    plan_snapshot: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("pay"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), unique=True)
    provider: Mapped[str] = mapped_column(String(80))
    provider_reference: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(24), default="pending")
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("sub"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    plan_id: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(24), default="provisioning")
    traffic_limit_bytes: Mapped[int] = mapped_column(BigInteger)
    duration_days: Mapped[int] = mapped_column(Integer)
    peer_limit: Mapped[int] = mapped_column(Integer)
    provisioned_peer_count: Mapped[int] = mapped_column(Integer, default=1)
    server_countries: Mapped[str] = mapped_column(String(200), default="")
    price_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    used_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    notified_thresholds: Mapped[str] = mapped_column(String(20), default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Peer(Base):
    __tablename__ = "peers"
    __table_args__ = (Index("uq_peers_server_ip_active", "server_id", "client_ip", unique=True,
                             sqlite_where=text("status != 'revoked'"),
                             postgresql_where=text("status != 'revoked'")),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("peer"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    subscription_id: Mapped[str] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    server_id: Mapped[str] = mapped_column(ForeignKey("servers.id"), index=True)
    client_ip: Mapped[str] = mapped_column(String(64))
    public_key: Mapped[str] = mapped_column(String(64), default="")
    config_ciphertext: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="provisioning")
    rx_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    tx_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    last_rx_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    last_tx_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("job"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    kind: Mapped[str] = mapped_column(String(50))
    resource_id: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("wh"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    url: Mapped[str] = mapped_column(String(1000))
    secret: Mapped[str] = mapped_column(String(100))
    events: Mapped[str] = mapped_column(String(1000))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("whd"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    endpoint_id: Mapped[str] = mapped_column(ForeignKey("webhook_endpoints.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(100))
    payload: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("idem"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    key: Mapped[str] = mapped_column(String(200))
    resource_id: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("aud"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(120))
    resource_id: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
