import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.main import app
from app.models import ApiCredential, Node, NodeCommand, NodeCredential, Server, Tenant


class NodeSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.temp_dir.name) / 'test.db'}")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.admin_key = "wg_panel_test_key_with_sufficient_length"
        with self.sessions() as db:
            db.add(Tenant(id="tenant-1", name="Test tenant"))
            db.add(ApiCredential(
                tenant_id="tenant-1", name="panel-admin",
                key_hash=hashlib.sha256(self.admin_key.encode()).hexdigest(),
                scopes="servers:read,servers:write", active=True,
            ))
            db.commit()

        def override_get_db():
            with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = override_get_db
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()
        self.headers = {"X-API-Key": self.admin_key}

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        app.dependency_overrides.clear()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def create_node(self):
        response = self.client.post("/api/v1/nodes/setup", headers=self.headers,
                                    json={"name": "Germany-1", "country": "de"})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["data"]

    def test_setup_creates_one_time_node_key_hash_and_installer(self):
        installer = self.client.get("/install/node.sh")
        self.assertEqual(installer.status_code, 200)
        self.assertIn("wg-node.service", installer.text)
        self.assertNotIn("docker compose", installer.text)

        node_data = self.create_node()
        self.assertTrue(node_data["node_api_key"].startswith("wg_node_"))
        with self.sessions() as db:
            credential = db.get(NodeCredential, node_data["node_id"])
            self.assertEqual(credential.key_hash,
                             hashlib.sha256(node_data["node_api_key"].encode()).hexdigest())
            self.assertEqual(db.get(Node, node_data["node_id"]).country, "DE")

    def test_node_polls_commands_registers_interface_and_returns_result(self):
        node_data = self.create_node()
        node_id, key = node_data["node_id"], node_data["node_api_key"]
        node_headers = {"X-Node-Key": key}
        rejected = self.client.get(f"/api/v1/node-agent/{node_id}/commands",
                                   headers={"X-Node-Key": "invalid"}, params={"wait_seconds": 0})
        self.assertEqual(rejected.status_code, 401)

        response = self.client.post(f"/api/v1/node-agent/{node_id}/register", headers=node_headers,
                                    json={"interface_name": "wg0", "endpoint": "192.0.2.10:51820",
                                          "public_key": "A" * 43, "address_pool": "10.44.0.0/24"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["data"]["registered"])

        with self.sessions() as db:
            db.add(NodeCommand(node_id=node_id, operation="ping", payload=json.dumps({"interface_name": "wg0"})))
            db.commit()
            command_id = db.query(NodeCommand).one().id

        polled = self.client.get(f"/api/v1/node-agent/{node_id}/commands", headers=node_headers,
                                 params={"wait_seconds": 0})
        self.assertEqual(polled.json()["data"]["id"], command_id)
        finished = self.client.post(
            f"/api/v1/node-agent/{node_id}/commands/{command_id}/result", headers=node_headers,
            json={"result": {"status": "ok"}, "error": ""})
        self.assertEqual(finished.status_code, 200)
        with self.sessions() as db:
            self.assertEqual(db.get(NodeCommand, command_id).status, "done")
            self.assertIsNotNone(db.query(Server).filter_by(node_id=node_id).one())

    def test_disabling_node_revokes_its_polling_access(self):
        node_data = self.create_node()
        disabled = self.client.patch(f"/api/v1/nodes/{node_data['node_id']}", headers=self.headers,
                                     json={"active": False})
        self.assertEqual(disabled.status_code, 200)
        response = self.client.get(f"/api/v1/node-agent/{node_data['node_id']}/commands",
                                   headers={"X-Node-Key": node_data["node_api_key"]},
                                   params={"wait_seconds": 0})
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
