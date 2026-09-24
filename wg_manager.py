"""Small synchronous client for the WireGuard Service API."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx


class WGManagerError(RuntimeError):
    """An API or transport error returned by :class:`WGManager`."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.request_id = request_id


class WGManager:
    """Call the WireGuard Service API from a trusted server-side application.

    Keep ``api_key`` in a secret manager or environment variable. Never embed it
    in browser JavaScript, a mobile app, or another public client.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 15.0,
        client: httpx.Client | None = None,
    ) -> None:
        parsed = urlparse(base_url)
        is_local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not is_local_http:
            raise ValueError("base_url must use HTTPS (HTTP is allowed only for localhost)")
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url must be an absolute URL without credentials, query, or fragment")
        if not api_key or not api_key.strip():
            raise ValueError("api_key must not be empty")

        self.base_url = base_url.rstrip("/")
        self._api_key = api_key.strip()
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=False)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> WGManager:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        response_type: str = "json",
    ) -> Any:
        request_headers = {"X-API-Key": self._api_key}
        if headers:
            request_headers.update(headers)
        try:
            response = self._client.request(
                method,
                f"{self.base_url}{path}",
                params=params,
                json=json,
                headers=request_headers,
            )
        except httpx.HTTPError as exc:
            raise WGManagerError(
                f"Could not reach WireGuard Service API: {exc}", code="TRANSPORT_ERROR"
            ) from exc

        try:
            body = response.json()
        except ValueError:
            body = None

        if not response.is_success:
            error = body.get("error", {}) if isinstance(body, dict) else {}
            if not isinstance(error, dict):
                error = {}
            raise WGManagerError(
                error.get("message", f"API request failed with HTTP {response.status_code}"),
                status_code=response.status_code,
                code=error.get("code"),
                request_id=body.get("request_id") if isinstance(body, dict) else None,
            )

        if response_type == "bytes":
            return response.content
        if response_type == "text":
            return response.text
        if not isinstance(body, dict) or "success" not in body:
            raise WGManagerError(
                "API returned an invalid JSON response",
                status_code=response.status_code,
                code="INVALID_RESPONSE",
                request_id=response.headers.get("X-Request-ID"),
            )
        if not body["success"]:
            error = body.get("error", {})
            if not isinstance(error, dict):
                error = {}
            raise WGManagerError(
                error.get("message", "API request failed"),
                status_code=response.status_code,
                code=error.get("code"),
                request_id=body.get("request_id"),
            )
        if "data" not in body:
            raise WGManagerError(
                "API response did not contain data",
                status_code=response.status_code,
                code="INVALID_RESPONSE",
                request_id=body.get("request_id"),
            )
        return body["data"]

    def list_plans(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/v1/plans")

    def create_user(self, external_id: str) -> dict[str, Any]:
        return self._request("POST", "/api/v1/users", json={"external_id": external_id})

    def get_user(self, user_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/users/{user_id}")

    def create_order(
        self,
        user_id: str,
        plan_id: str,
        *,
        peer_count: int = 1,
        country: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "user_id": user_id,
            "plan_id": plan_id,
            "peer_count": peer_count,
        }
        if country:
            payload["country"] = country.upper()
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        return self._request("POST", "/api/v1/orders", json=payload, headers=headers)

    def get_order(self, order_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/orders/{order_id}")

    def confirm_payment(
        self,
        order_id: str,
        *,
        provider: str,
        provider_reference: str,
        amount_minor: int,
        currency: str,
    ) -> dict[str, Any]:
        """Confirm a payment only after your own payment provider verified it."""
        return self._request(
            "POST",
            f"/api/v1/orders/{order_id}/confirm-payment",
            json={
                "provider": provider,
                "provider_reference": provider_reference,
                "amount_minor": amount_minor,
                "currency": currency.upper(),
            },
        )

    def list_subscriptions(self, *, offset: int = 0, limit: int = 50) -> list[dict[str, Any]]:
        return self._request(
            "GET", "/api/v1/subscriptions", params={"offset": offset, "limit": limit}
        )

    def get_subscription(self, subscription_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/subscriptions/{subscription_id}")

    def get_subscription_usage(self, subscription_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/subscriptions/{subscription_id}/usage")

    def list_peers(self, subscription_id: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/api/v1/subscriptions/{subscription_id}/peers")

    def create_peer(self, subscription_id: str, *, country: str | None = None) -> dict[str, Any]:
        payload = {"country": country.upper()} if country else {}
        return self._request(
            "POST", f"/api/v1/subscriptions/{subscription_id}/peers", json=payload
        )

    def get_peer_usage(self, peer_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/peers/{peer_id}/usage")

    def revoke_peer(self, peer_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/v1/peers/{peer_id}/revoke")

    def recreate_peer(self, peer_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/v1/peers/{peer_id}/recreate")

    def download_peer_config(self, peer_id: str) -> str:
        return self._request("GET", f"/api/v1/peers/{peer_id}/config", response_type="text")

    def get_peer_qr(self, peer_id: str) -> bytes:
        return self._request("GET", f"/api/v1/peers/{peer_id}/qr", response_type="bytes")

    def download_subscription_configs(self, subscription_id: str) -> bytes:
        return self._request(
            "GET", f"/api/v1/subscriptions/{subscription_id}/configs.zip", response_type="bytes"
        )
