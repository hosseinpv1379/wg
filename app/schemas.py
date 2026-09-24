from datetime import datetime
from ipaddress import ip_network
from typing import Generic, TypeVar
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    success: bool = True
    data: T
    request_id: str


class PlanCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    traffic_limit_bytes: int = Field(gt=0)
    duration_days: int = Field(gt=0, le=3650)
    peer_limit: int = Field(gt=0, le=100)
    price_minor: int = Field(ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    server_countries: list[str] = Field(default_factory=list)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        if not value.isalpha():
            raise ValueError("currency must be a three-letter code")
        return value.upper()


class PlanUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    traffic_limit_bytes: int | None = Field(default=None, gt=0)
    duration_days: int | None = Field(default=None, gt=0, le=3650)
    peer_limit: int | None = Field(default=None, gt=0, le=100)
    price_minor: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    server_countries: list[str] | None = None
    active: bool | None = None

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value.isalpha():
            raise ValueError("currency must be a three-letter code")
        return value.upper()


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: str
    traffic_limit_bytes: int
    duration_days: int
    peer_limit: int
    price_minor: int
    currency: str
    server_countries: list[str]
    active: bool

    @field_validator("server_countries", mode="before")
    @classmethod
    def split_countries(cls, value):
        return [item for item in value.split(",") if item] if isinstance(value, str) else value


class UserCreate(BaseModel):
    external_id: str = Field(min_length=1, max_length=200)


class UserUpdate(BaseModel):
    status: str = Field(pattern="^(active|disabled)$")


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    external_id: str
    status: str
    created_at: datetime


class ServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    country: str = Field(min_length=2, max_length=2)
    endpoint: str = Field(min_length=3, max_length=255)
    public_key: str = Field(min_length=10, max_length=64)
    agent_url: str = Field(default="", max_length=500)
    agent_secret: str = Field(default="", max_length=200)
    address_pool: str = Field(min_length=3, max_length=100)
    dns: str = "1.1.1.1"
    node_id: str | None = None
    interface_name: str = Field(default="wg0", pattern=r"^[a-zA-Z0-9_=+.@-]{1,15}$")

    @field_validator("address_pool")
    @classmethod
    def validate_pool(cls, value: str) -> str:
        network = ip_network(value, strict=False)
        if network.version != 4:
            raise ValueError("IPv6 address pools are not supported by this deployment yet")
        if network.num_addresses > 65536:
            raise ValueError("address pool must contain at most 65,536 addresses")
        return str(network)

    @field_validator("agent_url")
    @classmethod
    def validate_agent_url(cls, value: str) -> str:
        if not value:
            return value
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("agent_url must be an HTTPS URL without credentials")
        return value.rstrip("/")

    @model_validator(mode="after")
    def require_secret_without_node(self):
        if not self.node_id and len(self.agent_secret) < 32:
            raise ValueError("agent_secret must contain at least 32 characters when node_id is omitted")
        if not self.node_id and not self.agent_url:
            raise ValueError("agent_url is required when node_id is omitted")
        return self


class ServerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    node_id: str | None = None
    interface_name: str = "wg0"
    name: str
    country: str
    endpoint: str
    public_key: str
    address_pool: str
    active: bool
    healthy: bool
    last_seen_at: datetime | None


class NodeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    country: str = Field(min_length=2, max_length=2)
    agent_url: str = Field(min_length=8, max_length=500)
    agent_secret: str = Field(min_length=32, max_length=200)

    @field_validator("agent_url")
    @classmethod
    def validate_agent_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("agent_url must be an HTTPS URL without credentials")
        return value.rstrip("/")


class NodeSetupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    country: str = Field(min_length=2, max_length=2)


class NodeRegistration(BaseModel):
    interface_name: str = Field(default="wg0", pattern=r"^[a-zA-Z0-9_=+.@-]{1,15}$")
    endpoint: str = Field(min_length=3, max_length=255)
    public_key: str = Field(min_length=10, max_length=64)
    address_pool: str = Field(default="10.44.0.0/24", min_length=3, max_length=100)
    dns: str = Field(default="1.1.1.1", max_length=100)

    @field_validator("address_pool")
    @classmethod
    def validate_pool(cls, value: str) -> str:
        network = ip_network(value, strict=False)
        if network.version != 4 or network.num_addresses > 65536:
            raise ValueError("address_pool must be IPv4 and contain at most 65,536 addresses")
        return str(network)


class NodeCommandResult(BaseModel):
    result: dict = Field(default_factory=dict)
    error: str = Field(default="", max_length=2000)


class NodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    country: str
    agent_url: str
    active: bool
    healthy: bool
    last_seen_at: datetime | None
    created_at: datetime


class ActiveUpdate(BaseModel):
    active: bool


class OrderCreate(BaseModel):
    user_id: str
    plan_id: str
    peer_count: int = Field(default=1, ge=1, le=100)
    country: str | None = Field(default=None, min_length=2, max_length=2)


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    plan_id: str
    status: str
    kind: str
    price_minor: int
    currency: str
    created_at: datetime


class PaymentConfirm(BaseModel):
    provider: str = Field(min_length=1, max_length=80)
    provider_reference: str = Field(min_length=1, max_length=200)
    amount_minor: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)


class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    order_id: str | None
    plan_id: str
    status: str
    traffic_limit_bytes: int
    used_bytes: int
    peer_limit: int
    price_minor: int
    currency: str
    expires_at: datetime | None
    created_at: datetime


class PeerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    subscription_id: str
    server_id: str
    client_ip: str
    public_key: str
    status: str
    rx_bytes: int
    tx_bytes: int
    created_at: datetime


class PeerCreate(BaseModel):
    country: str | None = Field(default=None, min_length=2, max_length=2)


class WebhookCreate(BaseModel):
    url: str = Field(min_length=8, max_length=1000)
    events: list[str] = Field(min_length=1)


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(min_length=1)


class WebhookOut(BaseModel):
    id: str
    url: str
    events: list[str]
    active: bool


class AgentPeerCreate(BaseModel):
    peer_id: str
    interface_name: str = Field(default="wg0", pattern=r"^[a-zA-Z0-9_=+.@-]{1,15}$")
    client_ip: str
    endpoint: str
    server_public_key: str
    dns: str
    allowed_ips: str = "0.0.0.0/0"
    previous_public_key: str = ""
    force_recreate: bool = False


class AgentPeerOut(BaseModel):
    public_key: str
    config: str
