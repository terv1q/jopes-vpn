from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, User
from aiohttp.test_utils import TestClient, TestServer

from jopesvpn.bot import (
    _apply_platega_status,
    _parse_rub_amount,
    _platega_web_application,
    payment_notice_loop,
    platega_payment_iteration,
    process_stars_payment,
    topup_navigation,
)
from jopesvpn.config import Settings
from jopesvpn.orders import FULFILLED, FULFILLMENT_REVIEW, OrderStore
from jopesvpn.panel import PanelError
from jopesvpn.payments import PaymentStore
from jopesvpn.plans import load_plans, stars_for_rub_amount
from jopesvpn.platega import (
    PlategaClient,
    PlategaError,
    PlategaProtocolError,
    TransactionStatus,
)
from jopesvpn.preferences import PreferencesStore
from jopesvpn.ui import topup_methods_keyboard
from jopesvpn.wallet import MIN_TOPUP_MINOR, WalletStore


class PaymentProcessingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "state.sqlite3"
        self.preferences = PreferencesStore(self.path)
        self.orders = OrderStore(self.path)
        self.wallet = WalletStore(self.path)
        self.payments = PaymentStore(self.path)
        self.plans = load_plans()
        self.merchant_id = str(uuid4())
        self.settings = Settings(
            bot_token="test-token",
            panel_api_url="https://panel.example.test",
            panel_api_token="test-panel-token",
            admin_telegram_ids=frozenset({42}),
            state_db_path=str(self.path),
            platega_merchant_id=self.merchant_id,
            platega_secret="test-secret",
            traffic_plans_enabled=True,
        )
        self.bot = AsyncMock()
        self.panel = AsyncMock()
        self.responses: dict[str, dict | int] = {}
        self.requests: list[httpx.Request] = []
        self.client = PlategaClient(
            self.merchant_id,
            "test-secret",
            transport=httpx.MockTransport(self.respond),
        )
        self.web: TestClient | None = None

    def respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses[request.url.path.rsplit("/", 1)[-1]]
        if isinstance(response, int):
            return httpx.Response(response, json={"error": "unavailable"})
        return httpx.Response(200, json=response)

    async def asyncTearDown(self) -> None:
        if self.web is not None:
            await self.web.close()
        await self.client.close()
        self.directory.cleanup()

    def topup(
        self,
        *,
        linked: bool = True,
        amount: int = MIN_TOPUP_MINOR,
        expires_at: str | None = None,
    ):
        topup = self.wallet.create_topup(
            1001,
            amount,
            stars_for_rub_amount(amount, self.plans),
            provider="platega",
            payment_method=2,
        )
        transaction_id = str(uuid4())
        if linked:
            topup = self.wallet.save_topup_payment_link(
                topup.id,
                transaction_id,
                "https://pay.example.test/invoice",
                expires_at=expires_at,
            )
        self.responses[transaction_id] = {
            "id": transaction_id,
            "status": "CONFIRMED",
            "paymentDetails": {"amount": amount / 100, "currency": "RUB"},
            "payload": topup.id,
        }
        return topup, transaction_id

    def status(self, transaction_id: str, status: str = "CONFIRMED", payload=None):
        response = self.responses[transaction_id]
        return TransactionStatus(
            transaction_id,
            status,
            Decimal(str(response["paymentDetails"]["amount"])),
            "RUB",
            payload,
        )

    async def iterate(self, payments: PaymentStore | None = None) -> None:
        await platega_payment_iteration(
            bot=self.bot,
            panel=self.panel,
            settings=self.settings,
            plans=self.plans,
            orders=self.orders,
            wallet=self.wallet,
            payments=payments or self.payments,
            client=self.client,
        )

    async def apply(self, payment: TransactionStatus) -> str:
        return await _apply_platega_status(
            payment,
            bot=self.bot,
            panel=self.panel,
            settings=self.settings,
            plans=self.plans,
            orders=self.orders,
            wallet=self.wallet,
            payments=self.payments,
        )

    async def start_web(self) -> TestClient:
        self.web = TestClient(
            TestServer(
                _platega_web_application(
                    bot=self.bot,
                    panel=self.panel,
                    settings=self.settings,
                    plans=self.plans,
                    orders=self.orders,
                    wallet=self.wallet,
                    client=self.client,
                    payments=self.payments,
                )
            )
        )
        await self.web.start_server()
        return self.web

    async def post_callback(
        self, transaction_id: str, *, amount: float = 1, secret="test-secret"
    ):
        if self.web is None:
            await self.start_web()
        return await self.web.post(
            "/platega/callback",
            headers={"X-MerchantId": self.merchant_id, "X-Secret": secret},
            json={
                "id": transaction_id,
                "status": "CONFIRMED",
                "amount": amount,
                "currency": "RUB",
                "paymentMethod": 2,
            },
        )

    async def test_documented_callback_is_durable_and_credits_without_user_action(self):
        topup, transaction_id = self.topup()
        response = await self.post_callback(transaction_id)
        self.assertEqual(response.status, 200)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.wallet.account(1001).balance_minor, 0)
        reopened = PaymentStore(self.path)
        self.assertEqual(len(reopened.pending_checks()), 1)
        await self.iterate(reopened)
        self.assertEqual(self.wallet.account(1001).balance_minor, MIN_TOPUP_MINOR)
        self.assertEqual(self.wallet.topup(topup.id).status, "CREDITED")
        self.bot.send_message.assert_awaited_once()
        self.assertNotIn("platega", self.bot.send_message.call_args.args[1].lower())
        self.assertEqual(reopened.pending_checks(), [])

    async def test_polling_recovers_payment_when_callback_never_arrives(self):
        topup, transaction_id = self.topup()
        self.assertEqual(self.payments.pending_checks(), [])
        await self.iterate()
        self.assertEqual(self.wallet.topup(topup.id).status, "CREDITED")
        self.assertEqual(self.wallet.account(1001).balance_minor, MIN_TOPUP_MINOR)
        self.assertEqual(self.requests[0].url.path, f"/transaction/{transaction_id}")

    async def test_payer_commission_credits_quoted_topup_once_for_gross_and_net_callbacks(self):
        topup, transaction_id = self.topup()
        self.responses[transaction_id].update(
            paymentDetails={"amount": 1.08, "currency": "RUB"},
            comission=0.08,
            comissionType=1,
        )
        await self.post_callback(transaction_id, amount=1.08)
        await self.iterate()
        await self.post_callback(transaction_id, amount=1)
        await self.iterate()
        self.assertEqual(self.wallet.account(1001).balance_minor, 100)
        self.assertEqual(self.wallet.topup(topup.id).status, "CREDITED")
        self.assertEqual(self.wallet.payment_transaction(topup.id).rub_amount_minor, 100)
        self.bot.send_message.assert_awaited_once()

    async def test_pending_crypto_quote_with_separate_fee_keeps_automatic_polling(self):
        topup, transaction_id = self.topup()
        self.responses[transaction_id].update(
            status="PENDING",
            paymentDetails={"amount": 1, "currency": "RUB"},
            comission=0.05,
            comissionType=1,
        )
        await self.iterate()
        checks = self.payments.pending_checks(now=time.time() + 100)
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].attempts, 0)
        self.assertEqual(self.wallet.topup(topup.id).status, "PENDING")
        self.assertEqual(self.wallet.account(1001).balance_minor, 0)
        self.bot.send_message.assert_not_awaited()

    async def test_confirmed_quote_with_separate_fee_credits_once_for_gross_callback(self):
        topup, transaction_id = self.topup()
        self.responses[transaction_id].update(
            paymentDetails={"amount": 1, "currency": "RUB"},
            comission=0.05,
            comissionType=1,
        )
        await self.post_callback(transaction_id, amount=1.05)
        await self.iterate()
        await self.post_callback(transaction_id, amount=1)
        await self.iterate()
        self.assertEqual(self.wallet.topup(topup.id).status, "CREDITED")
        self.assertEqual(self.wallet.account(1001).balance_minor, 100)
        self.bot.send_message.assert_awaited_once()

    async def test_merchant_commission_does_not_reduce_quoted_topup_credit(self):
        topup, transaction_id = self.topup()
        self.responses[transaction_id].update(comission=0.08, comissionType=0)
        await self.post_callback(transaction_id)
        await self.iterate()
        self.assertEqual(self.wallet.account(1001).balance_minor, 100)
        self.assertEqual(self.wallet.topup(topup.id).status, "CREDITED")

    async def test_payer_commission_does_not_hide_a_wrong_order_amount(self):
        _, transaction_id = self.topup()
        self.responses[transaction_id].update(
            paymentDetails={"amount": 1.08, "currency": "RUB"},
            comission=0.07,
            comissionType=1,
        )
        await self.post_callback(transaction_id, amount=1.08)
        await self.iterate()
        self.assertEqual(self.wallet.account(1001).balance_minor, 0)
        self.bot.send_message.assert_not_awaited()

    async def test_canceled_legacy_invoice_with_payer_commission_is_reconciled(self):
        topup, transaction_id = self.topup(amount=200_000)
        self.responses[transaction_id].update(
            status="CANCELED",
            paymentDetails={"amount": 2160, "currency": "RUB"},
            comission=160,
            comissionType=1,
        )
        await self.iterate()
        self.assertEqual(self.wallet.topup(topup.id).status, "CANCELED")
        self.assertEqual(self.wallet.account(1001).balance_minor, 0)
        self.assertEqual(self.payments.pending_checks(), [])

    async def test_refund_with_payer_commission_reverses_only_the_credited_amount(self):
        topup, transaction_id = self.topup()
        self.responses[transaction_id].update(
            paymentDetails={"amount": 1.08, "currency": "RUB"},
            comission=0.08,
            comissionType=1,
        )
        await self.iterate()
        self.assertEqual(self.wallet.account(1001).balance_minor, 100)
        self.responses[transaction_id]["status"] = "CHARGEBACKED"
        self.payments.wake_check(transaction_id)
        await self.iterate()
        self.payments.wake_check(transaction_id)
        await self.iterate()
        self.assertEqual(self.wallet.topup(topup.id).status, "REFUNDED")
        self.assertEqual(self.wallet.account(1001).balance_minor, 0)

    async def test_duplicate_callbacks_and_concurrent_checks_credit_once(self):
        topup, transaction_id = self.topup()
        await self.post_callback(transaction_id)
        await self.post_callback(transaction_id)
        await asyncio.gather(
            self.apply(self.status(transaction_id)),
            self.apply(self.status(transaction_id)),
        )
        await self.iterate()
        await self.post_callback(transaction_id)
        await self.iterate()
        self.assertEqual(self.wallet.account(1001).balance_minor, MIN_TOPUP_MINOR)
        self.assertEqual(self.wallet.topup(topup.id).status, "CREDITED")
        self.bot.send_message.assert_awaited_once()

    async def test_invalid_auth_and_amount_never_credit_wallet(self):
        _, transaction_id = self.topup()
        response = await self.post_callback(transaction_id, secret="wrong-secret")
        self.assertEqual(response.status, 401)
        self.assertEqual(self.payments.pending_checks(), [])
        response = await self.post_callback(transaction_id, amount=2)
        self.assertEqual(response.status, 200)
        await self.iterate()
        self.assertEqual(self.wallet.account(1001).balance_minor, 0)
        self.assertEqual(self.payments.pending_checks(), [])
        self.assertEqual(len(self.payments.pending_checks(now=time.time() + 100)), 1)
        await self.post_callback(transaction_id)
        await self.iterate()
        self.assertEqual(self.wallet.account(1001).balance_minor, MIN_TOPUP_MINOR)

    async def test_callback_rejection_logs_source_without_addresses_or_sensitive_data(self):
        web = await self.start_web()
        with (
            patch.object(self.client, "validate_callback", side_effect=PlategaError("sensitive-error")),
            self.assertLogs("jopesvpn.bot", level="WARNING") as logs,
        ):
            response = await web.post(
                "/platega/callback",
                headers={
                    "X-Secret": "sensitive-secret",
                    "X-Forwarded-For": "198.51.100.1",
                    "X-Real-IP": "198.51.100.1",
                },
                json={"private": "sensitive-payload"},
            )
        self.assertEqual(response.status, 401)
        message = logs.records[0].getMessage()
        self.assertRegex(message, r"\(source=[0-9a-f]{16}, error=PlategaError\)$")
        for value in (
            "sensitive-error", "sensitive-secret", "sensitive-payload",
            "198.51.100.1", "127.0.0.1", self.settings.bot_token,
        ):
            self.assertNotIn(value, message)
        self.assertEqual(self.payments.pending_checks(), [])

    async def test_callback_source_identity_accepts_only_valid_trusted_proxy_addresses(self):
        self.settings = replace(self.settings, platega_trusted_proxy_ip="127.0.0.1")
        web = await self.start_web()
        sources = {}
        for address, expected in (
            ("198.51.100.1", "198.51.100.1"),
            ("2001:db8::1", "2001:db8::1"),
            ("2001:0db8::1", "2001:db8::1"),
            ("invalid-source", "127.0.0.1"),
            ("198.51.100.1, 198.51.100.2", "127.0.0.1"),
            ("", "127.0.0.1"),
        ):
            with self.subTest(address=address), self.assertLogs("jopesvpn.bot", level="WARNING") as logs:
                response = await web.post(
                    "/platega/callback",
                    headers={"X-Secret": "wrong-secret", "X-Real-IP": address},
                    json={"private": "sensitive-payload"},
                )
            self.assertEqual(response.status, 401)
            message = logs.records[0].getMessage()
            self.assertRegex(message, r"\(source=[0-9a-f]{16}, error=PlategaError\)$")
            source = message.split("source=", 1)[1].split(",", 1)[0]
            if expected in sources:
                self.assertEqual(source, sources[expected])
            else:
                self.assertNotIn(source, sources.values())
                sources[expected] = source
            self.assertNotIn("wrong-secret", message)
            self.assertNotIn("sensitive-payload", message)
            self.assertNotIn(expected, message)
            if address:
                self.assertNotIn(address, message)
        self.assertEqual(self.payments.pending_checks(), [])

    async def test_late_stars_payment_keeps_payment_history_without_granting_access(self):
        selected_plan = next(item for item in self.plans if item.sellable)
        order = self.orders.create_order(
            1001, selected_plan, selected_plan.term_for(7), now="2026-01-01T00:00:00Z"
        )
        self.orders.cancel_stale_pending(now="2026-01-02T00:00:00Z")
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=1001),
            successful_payment=SimpleNamespace(
                invoice_payload=f"jvpay_{order.id}", telegram_payment_charge_id="late-stars-charge",
                total_amount=order.amount_minor, currency=order.currency,
            ),
            answer=AsyncMock(),
        )
        for _ in range(2):
            await process_stars_payment(
                message, self.bot, self.panel, self.settings, self.plans, self.orders, self.wallet
            )
        confirmed = self.orders.get(order.id)
        self.assertEqual(confirmed.status, FULFILLMENT_REVIEW)
        self.assertEqual(confirmed.provider_payment_id, "late-stars-charge")
        transaction = self.wallet.payment_transaction(order.id)
        self.assertIsNotNone(transaction)
        self.assertEqual(transaction.status, "REVIEW")
        self.assertEqual(transaction.amount_paid_minor, order.amount_minor)
        self.assertEqual(transaction.currency, "XTR")
        self.panel.get_user_by_telegram_id.assert_not_awaited()
        self.panel.create_user.assert_not_awaited()
        self.panel.apply_paid_plan.assert_not_awaited()
        self.bot.send_message.assert_awaited_once()
        message.answer.assert_awaited_once()

    async def test_wrong_currency_and_payload_never_change_balance(self):
        _, transaction_id = self.topup()
        wrong_currency = TransactionStatus(
            transaction_id, "CONFIRMED", Decimal(1), "USD"
        )
        with self.assertRaises(PlategaProtocolError):
            await self.apply(wrong_currency)
        with self.assertRaises(PlategaProtocolError):
            await self.apply(self.status(transaction_id, payload=uuid4().hex))
        self.assertEqual(self.wallet.account(1001).balance_minor, 0)
        self.bot.send_message.assert_not_awaited()

    async def test_payment_is_recovered_after_creation_response_was_lost(self):
        topup, transaction_id = self.topup(linked=False)
        await self.post_callback(transaction_id)
        await self.iterate()
        saved = self.wallet.topup(topup.id)
        self.assertEqual(saved.provider_payment_id, transaction_id)
        self.assertEqual(saved.status, "CREDITED")
        self.assertEqual(self.wallet.account(1001).balance_minor, MIN_TOPUP_MINOR)

    async def test_notification_failure_retries_after_restart_without_recredit(self):
        self.topup()
        self.bot.send_message.side_effect = TelegramNetworkError(
            method=SendMessage(chat_id=1001, text="test"),
            message="unavailable",
        )
        await self.iterate()
        self.assertEqual(self.wallet.account(1001).balance_minor, MIN_TOPUP_MINOR)
        reopened = PaymentStore(self.path)
        self.assertEqual(len(reopened.pending_notices(now=time.time() + 100)), 1)
        self.bot.send_message.side_effect = None
        future = time.time() + 100
        with patch("jopesvpn.payments.time.time", return_value=future):
            await self.iterate(reopened)
        self.assertEqual(reopened.pending_notices(), [])
        self.assertEqual(self.wallet.account(1001).balance_minor, MIN_TOPUP_MINOR)
        self.assertEqual(len(self.requests), 1)

    async def test_new_callback_is_not_lost_while_previous_check_completes(self):
        _, transaction_id = self.topup()
        self.payments.track(transaction_id)
        previous = self.payments.pending_checks()[0]
        self.payments.request_check(self.status(transaction_id, "CHARGEBACKED"))
        self.payments.finish_check(previous, "confirmed")
        pending = self.payments.pending_checks()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].callback.status, "CHARGEBACKED")

    async def test_refund_confirmation_is_idempotent_and_ignores_old_callbacks(self):
        topup, transaction_id = self.topup()
        await self.iterate()
        self.wallet.claim_topup_refund(topup.id)
        self.payments.request_check(self.status(transaction_id, "CHARGEBACKED"))
        await self.iterate()
        self.assertEqual(self.wallet.topup(topup.id).status, "REFUNDING")
        self.assertEqual(len(self.payments.pending_checks(now=time.time() + 100)), 1)
        self.responses[transaction_id]["status"] = "CHARGEBACKED"
        self.payments.wake_check(transaction_id)
        await self.iterate()
        self.assertEqual(self.wallet.topup(topup.id).status, "REFUNDED")
        self.assertEqual(self.wallet.account(1001).balance_minor, 0)
        notifications = self.bot.send_message.await_count
        await self.post_callback(transaction_id)
        await self.iterate()
        self.assertEqual(self.bot.send_message.await_count, notifications)
        self.assertEqual(self.wallet.account(1001).balance_minor, 0)

    async def test_refund_before_local_credit_does_not_create_wallet_debt(self):
        topup, transaction_id = self.topup()
        self.responses[transaction_id]["status"] = "CHARGEBACKED"
        await self.iterate()
        account = self.wallet.account(1001)
        self.assertEqual(account.balance_minor, 0)
        self.assertEqual(account.balance_debt_minor, 0)
        self.assertEqual(self.wallet.topup(topup.id).status, "REFUNDED")

    async def test_late_verified_confirmation_can_settle_canceled_payment(self):
        topup, transaction_id = self.topup()
        self.responses[transaction_id]["status"] = "CANCELED"
        await self.iterate()
        self.assertEqual(self.wallet.topup(topup.id).status, "CANCELED")
        self.responses[transaction_id]["status"] = "CONFIRMED"
        await self.post_callback(transaction_id)
        await self.iterate()
        self.assertEqual(self.wallet.topup(topup.id).status, "CREDITED")
        self.assertEqual(self.wallet.account(1001).balance_minor, MIN_TOPUP_MINOR)

    async def test_one_provider_failure_does_not_block_other_payments(self):
        _, failing_id = self.topup()
        topup = self.wallet.create_topup(
            2002, 200, 2, provider="platega", payment_method=2
        )
        succeeding_id = str(uuid4())
        self.wallet.save_topup_payment_link(
            topup.id, succeeding_id, "https://pay.example.test/second"
        )
        self.responses[failing_id] = 503
        self.responses[succeeding_id] = {
            "id": succeeding_id,
            "status": "CONFIRMED",
            "paymentDetails": {"amount": 2, "currency": "RUB"},
            "payload": topup.id,
        }
        await self.iterate()
        self.assertEqual(self.wallet.account(1001).balance_minor, 0)
        self.assertEqual(self.wallet.account(2002).balance_minor, 200)
        self.assertEqual(len(self.payments.pending_checks(now=time.time() + 100)), 1)

    async def test_paid_order_resumes_fulfillment_and_never_extends_twice(self):
        plan = next(plan for plan in self.plans if plan.code == "lite-1")
        term = plan.term_for(30)
        order = self.orders.create_order(
            1001, plan, term, provider="platega", payment_method=2
        )
        transaction_id = str(uuid4())
        self.orders.save_payment_link(
            order.id, transaction_id, "https://pay.example.test/order"
        )
        self.orders.confirm_payment(
            order.id,
            provider="platega",
            provider_payment_id=transaction_id,
            amount_minor=order.amount_minor,
            currency="RUB",
            payment_recorder=lambda connection, paid: (
                self.wallet.record_order_payment_locked(
                    connection,
                    order_id=paid.id,
                    telegram_id=paid.telegram_id,
                    amount_paid_minor=paid.amount_minor,
                    currency="RUB",
                    method="platega",
                    external_payment_id=transaction_id,
                )
            ),
        )
        self.responses[transaction_id] = {
            "id": transaction_id,
            "status": "CONFIRMED",
            "paymentDetails": {"amount": order.amount_minor / 100, "currency": "RUB"},
            "payload": order.id,
        }
        with (
            patch(
                "jopesvpn.bot._fulfill_order",
                new=AsyncMock(return_value=SimpleNamespace()),
            ) as fulfill,
            patch("jopesvpn.bot._payment_success_text", return_value="Paid"),
        ):
            await self.iterate()
            self.payments.wake_check(transaction_id)
            await self.iterate()
        fulfill.assert_awaited_once()
        self.assertEqual(self.orders.get(order.id).status, FULFILLED)
        self.bot.send_message.assert_awaited_once()

    async def test_subscription_with_payer_commission_fulfills_once(self):
        plan = next(plan for plan in self.plans if plan.code == "lite-1")
        term = plan.term_for(30)
        order = self.orders.create_order(
            1001, plan, term, provider="platega", payment_method=2
        )
        transaction_id = str(uuid4())
        self.orders.save_payment_link(
            order.id, transaction_id, "https://pay.example.test/order"
        )
        price = Decimal(order.amount_minor) / 100
        commission = (price * Decimal("0.08")).quantize(Decimal("0.01"))
        self.responses[transaction_id] = {
            "id": transaction_id,
            "status": "CONFIRMED",
            "paymentDetails": {"amount": float(price + commission), "currency": "RUB"},
            "payload": order.id,
            "comission": float(commission),
            "comissionType": 1,
        }
        with (
            patch("jopesvpn.bot._fulfill_order", new=AsyncMock(return_value=SimpleNamespace())) as fulfill,
            patch("jopesvpn.bot._payment_success_text", return_value="Paid"),
        ):
            await self.iterate()
            self.payments.wake_check(transaction_id)
            await self.iterate()
        fulfill.assert_awaited_once()
        self.assertEqual(self.orders.get(order.id).status, FULFILLED)
        self.bot.send_message.assert_awaited_once()

    async def test_uncertain_subscription_fulfillment_goes_to_review_once(self):
        plan = next(plan for plan in self.plans if plan.code == "lite-1")
        order = self.orders.create_order(
            1001, plan, plan.term_for(30), provider="platega", payment_method=2
        )
        transaction_id = str(uuid4())
        self.orders.save_payment_link(
            order.id, transaction_id, "https://pay.example.test/order"
        )
        self.responses[transaction_id] = {
            "id": transaction_id,
            "status": "CONFIRMED",
            "paymentDetails": {"amount": order.amount_minor / 100, "currency": "RUB"},
            "payload": order.id,
        }
        with patch(
            "jopesvpn.bot._fulfill_order",
            new=AsyncMock(side_effect=PanelError("unavailable")),
        ) as fulfill:
            await self.iterate()
            self.payments.wake_check(transaction_id)
            await self.iterate()
        fulfill.assert_awaited_once()
        self.assertEqual(self.orders.get(order.id).status, "FULFILLMENT_REVIEW")
        self.assertEqual(self.wallet.payment_transaction(order.id).status, "REVIEW")
        self.assertEqual(self.bot.send_message.await_count, 2)

    async def test_slow_status_api_does_not_block_ready_payment_notification(self):
        _, transaction_id = self.topup()
        blocked = asyncio.Event()
        delivered = asyncio.Event()

        async def get_status(_transaction_id):
            await blocked.wait()
            return self.status(transaction_id, "PENDING")

        self.bot.send_message.side_effect = lambda *args, **kwargs: delivered.set()
        self.payments.queue_notice("ready", 1001, "Payment confirmed")
        processing = asyncio.create_task(
            platega_payment_iteration(
                bot=self.bot,
                panel=self.panel,
                settings=self.settings,
                plans=self.plans,
                orders=self.orders,
                wallet=self.wallet,
                payments=self.payments,
                client=SimpleNamespace(get_transaction_status=get_status),
                deliver_notices=False,
            )
        )
        sender = asyncio.create_task(
            payment_notice_loop(self.bot, self.payments, interval_seconds=0.01)
        )
        try:
            await asyncio.wait_for(delivered.wait(), timeout=2)
            self.assertFalse(processing.done())
        finally:
            blocked.set()
            await processing
            sender.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await sender

    async def test_payment_message_updates_automatically_after_confirmation(self):
        _, transaction_id = self.topup()
        self.payments.track(transaction_id)
        self.payments.record_screen(transaction_id, 1001, 123)
        await self.iterate()
        self.bot.edit_message_text.assert_awaited_once()
        kwargs = self.bot.edit_message_text.call_args.kwargs
        self.assertEqual(kwargs["message_id"], 123)
        self.assertFalse(
            any(
                button.url
                for row in kwargs["reply_markup"].inline_keyboard
                for button in row
            )
        )

    async def test_amount_button_survives_lost_fsm_state(self):
        user = User(id=1001, is_bot=False, first_name="User")
        message = Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=1001, type="private"),
            from_user=user,
        )
        sent = Message(message_id=2, date=datetime.now(UTC), chat=message.chat)
        callback = SimpleNamespace(from_user=user, message=message, answer=AsyncMock())
        markup = topup_methods_keyboard(
            1, amount_minor=MIN_TOPUP_MINOR, platega_enabled=True
        )
        callback.data = next(
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data.startswith("topup:pay:platega:2:")
        )
        state = SimpleNamespace(get_data=AsyncMock(return_value={}), clear=AsyncMock())
        self.bot.me.return_value = User(
            id=10, is_bot=True, first_name="Bot", username="JopesVPNBot"
        )
        transaction_id = str(uuid4())
        client = SimpleNamespace(
            create_payment=AsyncMock(
                return_value=SimpleNamespace(
                    transaction_id=transaction_id,
                    url="https://pay.example.test/invoice",
                    expires_at=lambda: (
                        datetime.now(UTC) + timedelta(minutes=15)
                    ).isoformat(),
                )
            )
        )
        with patch.object(Message, "answer", new=AsyncMock(return_value=sent)):
            await topup_navigation(
                callback,
                self.plans,
                state,
                self.settings,
                self.bot,
                wallet=self.wallet,
                platega=client,
                payments=self.payments,
            )
            await topup_navigation(
                callback,
                self.plans,
                state,
                self.settings,
                self.bot,
                wallet=self.wallet,
                platega=client,
                payments=self.payments,
            )
        state.get_data.assert_not_awaited()
        client.create_payment.assert_awaited_once()
        self.bot.edit_message_text.assert_awaited_once()
        self.assertEqual(client.create_payment.call_args.kwargs["amount"], Decimal(1))
        self.assertEqual(
            client.create_payment.call_args.kwargs["return_url"],
            "https://t.me/JopesVPNBot",
        )
        self.assertEqual(len(self.payments.pending_checks()), 1)

    async def test_legacy_amount_button_recovers_from_message_after_restart(self):
        user = User(id=1001, is_bot=False, first_name="User")
        message = Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=1001, type="private"),
            from_user=user,
            text="Сумма пополнения: 50 ₽\nВыберите способ оплаты.",
        )
        sent = Message(message_id=2, date=datetime.now(UTC), chat=message.chat)
        callback = SimpleNamespace(
            from_user=user,
            message=message,
            answer=AsyncMock(),
            data="topup:pay:platega:2",
        )
        state = SimpleNamespace(get_data=AsyncMock(return_value={}), clear=AsyncMock())
        self.bot.me.return_value = User(
            id=10, is_bot=True, first_name="Bot", username="JopesVPNBot"
        )
        client = SimpleNamespace(
            create_payment=AsyncMock(
                return_value=SimpleNamespace(
                    transaction_id=str(uuid4()),
                    url="https://pay.example.test/invoice",
                    expires_at=lambda: None,
                )
            )
        )
        with patch.object(Message, "answer", new=AsyncMock(return_value=sent)):
            await topup_navigation(
                callback,
                self.plans,
                state,
                self.settings,
                self.bot,
                wallet=self.wallet,
                platega=client,
                payments=self.payments,
            )
        self.assertEqual(client.create_payment.call_args.kwargs["amount"], Decimal(50))

    def test_amount_parser_rejects_overflow_and_unsupported_precision(self):
        self.assertIsNone(_parse_rub_amount("1e99999999"))
        self.assertIsNone(_parse_rub_amount("1.001"))
        self.assertIsNone(_parse_rub_amount("9" * 1000))
        self.assertEqual(_parse_rub_amount("1,50 ₽"), 150)

    def test_topup_reuse_respects_method_and_link_expiry(self):
        topup, transaction_id = self.topup(
            expires_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        )
        card = self.wallet.create_topup(
            1001, MIN_TOPUP_MINOR, 1, provider="platega", payment_method=11
        )
        self.assertNotEqual(card.id, topup.id)
        replacement = self.wallet.create_topup(
            1001, MIN_TOPUP_MINOR, 1, provider="platega", payment_method=2
        )
        self.assertNotEqual(replacement.id, topup.id)
        self.assertEqual(
            self.wallet.topup(topup.id).provider_payment_id, transaction_id
        )
        self.assertEqual(self.wallet.topup(topup.id).status, "PENDING")

    def test_subscription_reuse_separates_stars_methods_and_expired_links(self):
        plan = next(plan for plan in self.plans if plan.code == "lite-1")
        term = plan.term_for(30)
        stars = self.orders.create_order(1001, plan, term)
        sbp = self.orders.create_order(
            1001, plan, term, provider="platega", payment_method=2
        )
        card = self.orders.create_order(
            1001, plan, term, provider="platega", payment_method=11
        )
        self.assertEqual(len({stars.id, sbp.id, card.id}), 3)
        self.orders.save_payment_link(
            sbp.id,
            str(uuid4()),
            "https://pay.example.test/order",
            expires_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        )
        replacement = self.orders.create_order(
            1001, plan, term, provider="platega", payment_method=2
        )
        self.assertNotEqual(replacement.id, sbp.id)
        self.assertEqual(self.orders.get(sbp.id).status, "PENDING")


if __name__ == "__main__":
    unittest.main()
