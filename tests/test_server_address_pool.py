import unittest

from pydantic import ValidationError

from app.schemas import ServerCreate


class ServerAddressPoolTests(unittest.TestCase):
    def test_accepts_ipv4_pool(self):
        server = ServerCreate(
            name="Germany",
            country="DE",
            endpoint="vpn.example.com:51820",
            public_key="s" * 44,
            node_id="node-de",
            address_pool="10.44.0.0/24",
        )
        self.assertEqual(server.address_pool, "10.44.0.0/24")

    def test_rejects_ipv6_pool_until_dual_stack_is_configured(self):
        with self.assertRaises(ValidationError):
            ServerCreate(
                name="Germany",
                country="DE",
                endpoint="vpn.example.com:51820",
                public_key="s" * 44,
                node_id="node-de",
                address_pool="fd00::/64",
            )


if __name__ == "__main__":
    unittest.main()
