import hashlib
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.config import settings
from app.main import app
from app.models import ApiCredential, Order, Plan, Subscription, Tenant, User, WebhookEndpoint


class PanelApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.temp_dir.name) / 'panel.db'}")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.key = "panel_api_test_key_long_enough"
        with self.sessions() as db:
            db.add_all([Tenant(id="tenant-a", name="A"), Tenant(id="tenant-b", name="B")])
            db.flush()
            db.add_all([
                ApiCredential(id="key-admin", tenant_id="tenant-a", name="Admin",
                              key_hash=hashlib.sha256(self.key.encode()).hexdigest(),
                              scopes="users:read,keys:write,plans:read,orders:read,subscriptions:read,webhooks:write",
                              active=True),
                ApiCredential(id="key-disabled", tenant_id="tenant-a", name="Old store",
                              key_hash="a" * 64, scopes="orders:read", active=False),
                ApiCredential(id="key-other", tenant_id="tenant-b", name="Other tenant",
                              key_hash="b" * 64, scopes="keys:write", active=True),
            ])
            db.add_all([
                User(id="user-a", tenant_id="tenant-a", external_id="customer-007"),
                User(id="user-b", tenant_id="tenant-a", external_id="other-customer", status="disabled"),
                User(id="user-private", tenant_id="tenant-b", external_id="customer-007-private"),
                Plan(id="plan-a", tenant_id="tenant-a", name="Current", traffic_limit_bytes=1000,
                     duration_days=30, peer_limit=1, price_minor=0, currency="IRR", active=True),
                Plan(id="plan-old", tenant_id="tenant-a", name="Retired", traffic_limit_bytes=1000,
                     duration_days=30, peer_limit=1, price_minor=0, currency="IRR", active=False),
                Plan(id="plan-private", tenant_id="tenant-b", name="Private", traffic_limit_bytes=1000,
                     duration_days=30, peer_limit=1, price_minor=0, currency="IRR"),
            ])
            db.flush()
            db.add_all([
                Order(id="order-a", tenant_id="tenant-a", user_id="user-a", plan_id="plan-a",
                      price_minor=0, currency="IRR", plan_snapshot="{}", status="pending_payment"),
                Order(id="order-b", tenant_id="tenant-a", user_id="user-b", plan_id="plan-old",
                      price_minor=0, currency="IRR", plan_snapshot="{}", status="paid"),
                Subscription(id="sub-a", tenant_id="tenant-a", user_id="user-a", plan_id="plan-a",
                             traffic_limit_bytes=1000, duration_days=30, peer_limit=1,
                             price_minor=0, currency="IRR", status="active"),
                Subscription(id="sub-b", tenant_id="tenant-a", user_id="user-b", plan_id="plan-old",
                             traffic_limit_bytes=1000, duration_days=30, peer_limit=1,
                             price_minor=0, currency="IRR", status="suspended"),
                WebhookEndpoint(id="hook-a", tenant_id="tenant-a", url="https://example.com/hook",
                                secret="ciphertext", events="subscription.active", active=False),
                WebhookEndpoint(id="hook-private", tenant_id="tenant-b", url="https://example.com/private",
                                secret="ciphertext", events="subscription.active", active=False),
            ])
            db.commit()

        def override_get_db():
            with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = override_get_db
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()
        self.headers = {"X-API-Key": self.key}

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        app.dependency_overrides.clear()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def data(self, path):
        response = self.client.get(path, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

    def test_user_directory_is_searchable_and_tenant_scoped(self):
        result = self.data("/api/v1/users?q=customer-007")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["id"], "user-a")
        disabled = self.data("/api/v1/users?status=disabled")
        self.assertEqual([item["id"] for item in disabled["items"]], ["user-b"])

    def test_api_key_directory_exposes_metadata_only(self):
        result = self.data("/api/v1/api-keys")
        self.assertEqual(result["total"], 2)
        self.assertEqual({item["id"] for item in result["items"]}, {"key-admin", "key-disabled"})
        self.assertFalse(any("secret" in item or "key_hash" in item for item in result["items"]))

    def test_plan_visibility_and_server_side_order_filters(self):
        self.assertEqual([plan["id"] for plan in self.data("/api/v1/plans")], ["plan-a"])
        self.assertEqual({plan["id"] for plan in self.data("/api/v1/plans?include_disabled=true")},
                         {"plan-a", "plan-old"})
        self.assertEqual([order["id"] for order in self.data("/api/v1/orders?q=customer-007")],
                         ["order-a"])
        self.assertEqual([sub["id"] for sub in self.data("/api/v1/subscriptions?status=suspended")],
                         ["sub-b"])

    def test_webhook_reactivation_is_tenant_scoped(self):
        denied = self.client.patch("/api/v1/webhooks/hook-private", headers=self.headers,
                                   json={"active": True})
        self.assertEqual(denied.status_code, 404)
        response = self.client.patch("/api/v1/webhooks/hook-a", headers=self.headers,
                                     json={"active": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["data"]["active"])

    def test_validation_error_identifies_field_without_echoing_input(self):
        response = self.client.get("/api/v1/users?limit=not-a-number", headers=self.headers)
        self.assertEqual(response.status_code, 422)
        error = response.json()["error"]
        self.assertEqual(error["code"], "VALIDATION_ERROR")
        self.assertIn("limit", error["message"])
        self.assertNotIn("not-a-number", error["message"])

    def test_concurrent_bootstrap_requests_create_tenant_once(self):
        original_key = settings.bootstrap_api_key
        original_tenant = settings.bootstrap_tenant_id
        settings.bootstrap_api_key = "temporary-bootstrap-key"
        settings.bootstrap_tenant_id = "tenant-bootstrap"
        try:
            def request_users(_):
                return self.client.get("/api/v1/users", headers={"X-API-Key": settings.bootstrap_api_key})

            with ThreadPoolExecutor(max_workers=8) as pool:
                responses = list(pool.map(request_users, range(12)))
            self.assertEqual([response.status_code for response in responses], [200] * 12)
            with self.sessions() as db:
                self.assertIsNotNone(db.get(Tenant, "tenant-bootstrap"))
        finally:
            settings.bootstrap_api_key = original_key
            settings.bootstrap_tenant_id = original_tenant


if __name__ == "__main__":
    unittest.main()
