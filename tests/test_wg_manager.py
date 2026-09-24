import json
import unittest

import httpx

from wg_manager import WGManager, WGManagerError


class WGManagerTests(unittest.TestCase):
    def make_manager(self, handler):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        return WGManager("https://panel.example", "wg_live_test", client=client), client

    def test_create_order_sends_api_key_and_idempotency_key(self):
        def handler(request):
            self.assertEqual(request.headers["X-API-Key"], "wg_live_test")
            self.assertEqual(request.headers["Idempotency-Key"], "purchase-123")
            self.assertEqual(
                json.loads(request.content),
                {"user_id": "usr_1", "plan_id": "plan_1", "peer_count": 1, "country": "DE"},
            )
            return httpx.Response(201, json={"success": True, "data": {"id": "ord_1"}})

        manager, client = self.make_manager(handler)
        try:
            order = manager.create_order(
                "usr_1", "plan_1", country="de", idempotency_key="purchase-123"
            )
            self.assertEqual(order, {"id": "ord_1"})
        finally:
            manager.close()
            client.close()

    def test_api_error_exposes_code_and_request_id(self):
        def handler(_request):
            return httpx.Response(
                403,
                json={
                    "success": False,
                    "error": {"code": "INSUFFICIENT_SCOPE", "message": "Missing scope"},
                    "request_id": "req_test",
                },
            )

        manager, client = self.make_manager(handler)
        try:
            with self.assertRaises(WGManagerError) as raised:
                manager.list_plans()
            self.assertEqual(raised.exception.status_code, 403)
            self.assertEqual(raised.exception.code, "INSUFFICIENT_SCOPE")
            self.assertEqual(raised.exception.request_id, "req_test")
        finally:
            manager.close()
            client.close()

    def test_config_and_qr_return_raw_content(self):
        def handler(request):
            if request.url.path.endswith("/config"):
                return httpx.Response(200, text="[Interface]\nPrivateKey = secret\n")
            return httpx.Response(200, content=b"png-bytes", headers={"Content-Type": "image/png"})

        manager, client = self.make_manager(handler)
        try:
            self.assertIn("[Interface]", manager.download_peer_config("peer_1"))
            self.assertEqual(manager.get_peer_qr("peer_1"), b"png-bytes")
        finally:
            manager.close()
            client.close()

    def test_rejects_api_key_over_non_local_http(self):
        with self.assertRaises(ValueError):
            WGManager("http://panel.example", "wg_live_test")


if __name__ == "__main__":
    unittest.main()
