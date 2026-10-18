from __future__ import annotations

import json
import unittest

import httpx

from jopesvpn.panel import FREE_TRAFFIC_BYTES, RemnawaveClient


class PanelFreeAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_free_user_is_created_with_confirmed_server_quota(self) -> None:
        requests: list[tuple[str, dict | None]] = []
        user = {
            "id": 7,
            "username": "tg_12345",
            "telegramId": 12345,
            "status": "ACTIVE",
            "expireAt": "2099-01-01T00:00:00+00:00",
            "tag": "FREE",
            "trafficLimitBytes": FREE_TRAFFIC_BYTES,
            "trafficLimitStrategy": "NO_RESET",
            "userTraffic": {"usedTrafficBytes": 0},
        }

        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content) if request.content else None
            requests.append((request.method, body))
            if request.method == "POST":
                return httpx.Response(201, json={"response": user})
            return httpx.Response(200, json={"response": user})

        panel = RemnawaveClient(
            "https://panel.example.test",
            "test-token",
            transport=httpx.MockTransport(handler),
        )
        try:
            created = await panel.create_free_user(
                12345, "00000000-0000-0000-0000-000000000001"
            )
        finally:
            await panel.close()

        self.assertEqual(created.tag, "FREE")
        self.assertEqual(created.traffic_limit_bytes, FREE_TRAFFIC_BYTES)
        self.assertEqual(created.traffic_limit_strategy, "NO_RESET")
        self.assertEqual(requests[0][0], "POST")
        self.assertEqual(requests[0][1]["trafficLimitBytes"], FREE_TRAFFIC_BYTES)
        self.assertEqual(requests[0][1]["trafficLimitStrategy"], "NO_RESET")
        self.assertEqual(requests[0][1]["tag"], "FREE")
        self.assertEqual(requests[1][0], "GET")


if __name__ == "__main__":
    unittest.main()
