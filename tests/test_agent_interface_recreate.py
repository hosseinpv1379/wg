import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.fernet import Fernet

from app import agent
from app.schemas import AgentPeerCreate


class AgentInterfaceRecreateTests(unittest.TestCase):
    def test_recreate_removes_previous_peer_from_its_saved_interface(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = str(Path(temp_dir) / "agent.sqlite3")
            state_key = Fernet.generate_key().decode()
            with (patch.object(agent.settings, "agent_state_path", state_path),
                  patch.object(agent.settings, "agent_state_key", state_key),
                  patch.object(agent, "authorize")):
                old_config = agent.state_encrypt(
                    "[Interface]\nPrivateKey = old-private\nAddress = 10.44.0.8/32\n"
                )
                with agent.state_db() as db:
                    db.execute(
                        "INSERT INTO peers(peer_id, public_key, config, interface_name) "
                        "VALUES(?, ?, ?, ?)",
                        ("peer-1", "old-public", old_config, "wg1"),
                    )

                data = AgentPeerCreate(
                    peer_id="peer-1",
                    interface_name="wg1",
                    client_ip="10.44.0.8/32",
                    endpoint="vpn.example.com:51820",
                    server_public_key="s" * 44,
                    dns="1.1.1.1",
                    force_recreate=True,
                )
                with (patch.object(agent, "wg", side_effect=lambda *args: "new-private" if args == ("genkey",) else "") as wg_mock,
                      patch.object(agent.subprocess, "run", return_value=SimpleNamespace(stdout="new-public\n")) as run,
                      patch.object(agent.settings, "agent_interface", "wg0")):
                    result = agent.create_peer(data, authorization="Bearer test-token")

                calls = [call.args for call in wg_mock.call_args_list]
                self.assertIn(("set", "wg1", "peer", "old-public", "remove"), calls)
                self.assertIn(("show", "wg1"), calls)
                self.assertEqual(result["public_key"], "new-public")
                run.assert_called_once()
                with agent.state_db() as db:
                    saved = db.execute(
                        "SELECT interface_name FROM peers WHERE peer_id = ?", ("peer-1",)
                    ).fetchone()
                self.assertEqual(saved[0], "wg1")


if __name__ == "__main__":
    unittest.main()
