import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Peer, Server, Subscription, Tenant, User
from app.services import poll_usage


class PersistentUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.temp_dir.name) / 'usage.db'}")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.sessions() as db:
            tenant = Tenant(id="tenant-usage", name="Usage")
            db.add(tenant)
            db.flush()
            user = User(tenant_id=tenant.id, external_id="customer-1")
            server = Server(
                tenant_id=tenant.id, name="Node", country="DE",
                endpoint="vpn.example.com:51820", public_key="s" * 44,
                agent_url="https://node.example.com", agent_secret_ciphertext="encrypted",
                address_pool="10.44.0.0/24", interface_generation="boot-a:3",
            )
            db.add_all([user, server])
            db.flush()
            subscription = Subscription(
                tenant_id=tenant.id, user_id=user.id, plan_id="plan-usage", status="active",
                traffic_limit_bytes=10_000, duration_days=30, peer_limit=1,
                price_minor=0, currency="IRR", used_bytes=400,
            )
            db.add(subscription)
            db.flush()
            self.peer_id = "peer-usage"
            db.add(Peer(
                id=self.peer_id, tenant_id=tenant.id, subscription_id=subscription.id,
                server_id=server.id, client_ip="10.44.0.2", public_key="p" * 44,
                status="active", rx_bytes=300, tx_bytes=100,
                last_rx_bytes=300, last_tx_bytes=100,
            ))
            self.server_id = server.id
            self.subscription_id = subscription.id
            db.commit()

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_usage_survives_interface_outage_and_counter_reset(self):
        responses = [
            {"interface_generation": "boot-a:3", "peers": [
                {"public_key": "p" * 44, "rx_bytes": 350, "tx_bytes": 130},
            ]},
            RuntimeError("interface is temporarily unavailable"),
            {"interface_generation": "boot-b:3", "peers": [
                {"public_key": "p" * 44, "rx_bytes": 5, "tx_bytes": 8},
            ]},
        ]
        with patch("app.services.agent_request", side_effect=responses):
            for _ in responses:
                with self.sessions() as db:
                    poll_usage(db)

        with self.sessions() as db:
            peer = db.get(Peer, self.peer_id)
            subscription = db.get(Subscription, self.subscription_id)
            server = db.get(Server, self.server_id)
            self.assertEqual((peer.rx_bytes, peer.tx_bytes), (355, 138))
            self.assertEqual((peer.last_rx_bytes, peer.last_tx_bytes), (5, 8))
            self.assertEqual(subscription.used_bytes, 493)
            self.assertEqual(server.interface_generation, "boot-b:3")
            self.assertTrue(server.healthy)


if __name__ == "__main__":
    unittest.main()
