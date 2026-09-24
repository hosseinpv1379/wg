import hashlib
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.main import app
from app.models import ApiCredential, Peer, Server, Subscription, Tenant, User


class PeerDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.temp_dir.name) / 'test.db'}")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.api_key = "wg_panel_directory_test_key_long_enough"
        with self.sessions() as db:
            db.add_all([
                Tenant(id="tenant-a", name="Tenant A"),
                Tenant(id="tenant-b", name="Tenant B"),
                ApiCredential(
                    tenant_id="tenant-a", name="panel-admin",
                    key_hash=hashlib.sha256(self.api_key.encode()).hexdigest(),
                    scopes="servers:read,servers:write,peers:read,peers:write", active=True,
                ),
            ])
            db.flush()
            self._add_peer(db, "tenant-a", "customer-100", "Frankfurt", "DE", "10.44.0.2",
                           "active", 50, 25)
            self._add_peer(db, "tenant-a", "customer-200", "New York", "US", "10.45.0.2",
                           "revoked", 100, 200)
            self._add_peer(db, "tenant-b", "private-customer", "Tokyo", "JP", "10.46.0.2",
                           "active", 1000, 2000)
            db.commit()

        def override_get_db():
            with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = override_get_db
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()
        self.headers = {"X-API-Key": self.api_key}

    def _add_peer(self, db, tenant_id, external_id, server_name, country, address, status, rx, tx):
        user = User(tenant_id=tenant_id, external_id=external_id)
        server = Server(
            tenant_id=tenant_id, name=server_name, country=country,
            endpoint="vpn.example.com:51820", public_key="s" * 44,
            agent_url="node-poll://test", agent_secret_ciphertext="", address_pool="10.44.0.0/24",
        )
        db.add_all([user, server])
        db.flush()
        subscription = Subscription(
            tenant_id=tenant_id, user_id=user.id, plan_id="plan-test", status="active",
            traffic_limit_bytes=10_000, duration_days=30, peer_limit=3,
            provisioned_peer_count=1, price_minor=100, currency="USD",
        )
        db.add(subscription)
        db.flush()
        db.add(Peer(
            tenant_id=tenant_id, subscription_id=subscription.id, server_id=server.id,
            client_ip=address, status=status, rx_bytes=rx, tx_bytes=tx,
            config_ciphertext="must-not-be-returned", public_key="p" * 44,
        ))

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        app.dependency_overrides.clear()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_search_filters_and_summary_are_tenant_scoped(self):
        response = self.client.get("/api/v1/peers?q=customer-100", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["external_user_id"], "customer-100")
        self.assertEqual(data["summary"], {"total": 2, "active": 1, "used_bytes": 375})
        self.assertNotIn("config_ciphertext", data["items"][0])
        self.assertNotIn("public_key", data["items"][0])

    def test_status_country_and_pagination_filters(self):
        response = self.client.get("/api/v1/peers?status=active&country=de&limit=1", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["total"], 1)
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["country"], "DE")

    def test_peer_directory_requires_peers_read_scope(self):
        with self.sessions() as db:
            limited_key = "wg_panel_limited_test_key_long_enough"
            db.add(ApiCredential(
                tenant_id="tenant-a", name="infra-only",
                key_hash=hashlib.sha256(limited_key.encode()).hexdigest(),
                scopes="servers:read", active=True,
            ))
            db.commit()
        response = self.client.get("/api/v1/peers", headers={"X-API-Key": limited_key})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "INSUFFICIENT_SCOPE")

    def test_panel_serves_peer_management_view(self):
        response = self.client.get("/panel")
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-view="clients"', response.text)
        self.assertIn("peer-recreate", response.text)


if __name__ == "__main__":
    unittest.main()
