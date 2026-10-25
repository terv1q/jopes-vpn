from __future__ import annotations

import json
import unittest
from decimal import Decimal
from uuid import uuid4

import httpx

from jopesvpn.platega import PlategaClient, PlategaError, PlategaProtocolError


class QueueTransport(httpx.AsyncBaseTransport):
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json=self.response, request=request)


class PlategaClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_payment_uses_documented_headers_and_request_shape(self) -> None:
        merchant_id = str(uuid4())
        transaction_id = str(uuid4())
        transport = QueueTransport(
            {
                "transactionId": transaction_id,
                "status": "PENDING",
                "url": "https://pay.example.test/transaction",
                "expiresIn": "00:15:00",
            }
        )
        client = PlategaClient(merchant_id, "test-secret", transport=transport)
        order_id = str(uuid4())
        try:
            result = await client.create_payment(
                amount=Decimal("125.50"),
                currency="RUB",
                payment_method=2,
                description="Jopes VPN order",
                return_url="https://example.test/success",
                failed_url="https://example.test/failure",
                order_id=order_id,
            )
            request = transport.requests[0]
            self.assertEqual(request.url.path, "/transaction/process")
            self.assertEqual(request.headers["X-MerchantId"], merchant_id)
            self.assertEqual(request.headers["X-Secret"], "test-secret")
            self.assertEqual(
                json.loads(request.content),
                {
                    "paymentMethod": 2,
                    "paymentDetails": {"amount": 125.5, "currency": "RUB"},
                    "description": "Jopes VPN order",
                    "return": "https://example.test/success",
                    "failedUrl": "https://example.test/failure",
                    "payload": order_id,
                    "orderId": order_id,
                },
            )
            self.assertEqual(result.transaction_id, transaction_id)
            self.assertEqual(result.url, "https://pay.example.test/transaction")
        finally:
            await client.close()

    async def test_status_and_callback_check_transaction_details_and_auth(self) -> None:
        merchant_id = str(uuid4())
        transaction_id = str(uuid4())
        transport = QueueTransport(
            {
                "id": transaction_id,
                "status": "CONFIRMED",
                "paymentDetails": {"amount": 125.5, "currency": "RUB"},
            }
        )
        client = PlategaClient(merchant_id, "test-secret", transport=transport)
        try:
            status = await client.get_transaction_status(transaction_id)
            self.assertEqual(status.status, "CONFIRMED")
            self.assertEqual(status.amount, Decimal("125.5"))
            callback = client.validate_callback(
                {"X-MerchantId": merchant_id, "X-Secret": "test-secret"},
                {
                    "id": transaction_id,
                    "amount": 125.5,
                    "currency": "RUB",
                    "status": "CONFIRMED",
                },
            )
            self.assertEqual(callback, status)
            with self.assertRaises(PlategaError):
                client.validate_callback(
                    {"X-MerchantId": merchant_id, "X-Secret": "wrong"},
                    {
                        "id": transaction_id,
                        "amount": 125.5,
                        "currency": "RUB",
                        "status": "CONFIRMED",
                    },
                )
        finally:
            await client.close()

    async def test_status_distinguishes_payer_and_merchant_commissions(self) -> None:
        transaction_id = str(uuid4())
        transport = QueueTransport(
            {
                "id": transaction_id,
                "status": "CONFIRMED",
                "paymentDetails": {"amount": 2160, "currency": "RUB"},
                "comission": 160,
                "comissionType": 1,
            }
        )
        client = PlategaClient(str(uuid4()), "test-secret", transport=transport)
        try:
            status = await client.get_transaction_status(transaction_id)
            self.assertEqual(status.amount, Decimal("2160"))
            self.assertTrue(status.matches_service_amount(Decimal("2000")))
            self.assertFalse(status.matches_service_amount(Decimal("2001")))
            transport.response["comissionType"] = 0
            status = await client.get_transaction_status(transaction_id)
            self.assertTrue(status.matches_service_amount(Decimal("2160")))
            self.assertFalse(status.matches_service_amount(Decimal("2000")))
        finally:
            await client.close()

    async def test_separate_and_optional_fee_metadata_preserve_the_service_quote(self) -> None:
        transaction_id = str(uuid4())
        transport = QueueTransport(
            {
                "id": transaction_id,
                "status": "CONFIRMED",
                "paymentDetails": {"amount": 1, "currency": "RUB"},
                "comission": 0.05,
                "comissionType": 1,
            }
        )
        client = PlategaClient(str(uuid4()), "test-secret", transport=transport)
        try:
            status = await client.get_transaction_status(transaction_id)
            self.assertTrue(status.matches_service_amount(Decimal("1")))
            self.assertTrue(status.matches_callback_amount(Decimal("1.05")))
            transport.response["comission"] = 2
            status = await client.get_transaction_status(transaction_id)
            self.assertTrue(status.matches_service_amount(Decimal("1")))
            self.assertTrue(status.matches_callback_amount(Decimal("3")))
            del transport.response["comission"]
            transport.response["paymentDetails"] = {"amount": 1.08, "currency": "RUB"}
            status = await client.get_transaction_status(transaction_id)
            self.assertFalse(status.matches_service_amount(Decimal("1")))
            self.assertTrue(status.matches_service_amount(Decimal("1.08")))
        finally:
            await client.close()

    async def test_invalid_commissions_cannot_be_used_to_validate_a_payment(self) -> None:
        transaction_id = str(uuid4())
        transport = QueueTransport({})
        client = PlategaClient(str(uuid4()), "test-secret", transport=transport)
        invalid_commissions = (
            {"comissionType": 1, "comission": "invalid"},
            {"comissionType": 1, "comission": -0.01},
            {"comissionType": -1, "comission": 0.08},
            {"comissionType": 1, "comission": float("nan")},
            {"comissionType": True, "comission": 0.08},
            {"comissionType": 1, "comission": True},
        )
        try:
            for invalid in invalid_commissions:
                with self.subTest(commission=invalid):
                    transport.response = {
                        "id": transaction_id,
                        "status": "CONFIRMED",
                        "paymentDetails": {"amount": 1.08, "currency": "RUB"},
                        **invalid,
                    }
                    with self.assertRaises(PlategaProtocolError):
                        await client.get_transaction_status(transaction_id)
        finally:
            await client.close()

    async def test_method_specific_payment_response_without_status_is_pending(self) -> None:
        transport = QueueTransport(
            {
                "transactionId": str(uuid4()),
                "redirect": "https://pay.example.test/transaction",
                "return": "https://example.test/success",
            }
        )
        client = PlategaClient(str(uuid4()), "test-secret", transport=transport)
        try:
            result = await client.create_payment(
                amount=Decimal("50"),
                currency="RUB",
                payment_method=11,
                description="Jopes VPN",
                return_url="https://example.test/success",
                failed_url="https://example.test/failure",
                order_id=str(uuid4()),
            )
            self.assertEqual(result.status, "PENDING")
            self.assertEqual(result.url, "https://pay.example.test/transaction")
        finally:
            await client.close()


if __name__ == "__main__":
    unittest.main()
