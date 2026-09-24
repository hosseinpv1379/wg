import hashlib
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.main import app
from app.models import ApiCredential, Tenant


class NodeInstallerKeyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "test.db"
        self.engine = create_engine(f"sqlite:///{database_path}")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.admin_key = "wg_panel_test_key_with_sufficient_length"
        with self.sessions() as db:
            db.add(Tenant(id="tenant-1", name="Test tenant"))
            db.add(ApiCredential(
                tenant_id="tenant-1",
                name="panel-admin",
                key_hash=hashlib.sha256(self.admin_key.encode()).hexdigest(),
                scopes="servers:read,servers:write",
                active=True,
            ))
            db.commit()

        def override_get_db():
            with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = override_get_db
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        app.dependency_overrides.clear()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_setup_key_is_scoped_listed_and_revocable(self):
        installer = self.client.get("/install/node.sh")
        self.assertEqual(installer.status_code, 200)
        self.assertIn("Registering Node in the panel", installer.text)

        headers = {"X-API-Key": self.admin_key}
        created = self.client.post("/api/v1/node-installer-keys", headers=headers, json={})
        self.assertEqual(created.status_code, 201)
        credentials = created.json()["data"]
        raw_key = credentials["api_key"]
        self.assertTrue(raw_key.startswith("wg_node_"))

        with self.sessions() as db:
            stored = db.get(ApiCredential, credentials["id"])
            self.assertEqual(stored.key_hash, hashlib.sha256(raw_key.encode()).hexdigest())
            self.assertEqual(stored.scopes, "servers:read,servers:write")

        listed = self.client.get("/api/v1/node-installer-keys", headers=headers)
        self.assertEqual([item["id"] for item in listed.json()["data"]], [credentials["id"]])
        revoked = self.client.delete(f"/api/v1/node-installer-keys/{credentials['id']}", headers=headers)
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/node-installer-keys", headers=headers).json()["data"], [])


if __name__ == "__main__":
    unittest.main()
