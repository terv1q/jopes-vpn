from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from jopesvpn.bot import (
    _order_plan_term,
    _plan_term_payment_text,
    create_stars_invoice,
)
from jopesvpn.config import Settings
from jopesvpn.orders import OrderStore
from jopesvpn.panel import PanelNotFound
from jopesvpn.plans import load_plans


class PlanCheckoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_payment_choice_screen_only_repeats_plan_and_term(self):
        plan = next(item for item in load_plans() if item.code == "lite-1")
        term = next(term for term in plan.terms if term.days == 30)

        text = _plan_term_payment_text(plan, term)

        self.assertIn(plan.title, text)
        self.assertIn("30 дней", text)
        self.assertNotIn("₽", text)
        self.assertNotIn("примерно", text)

    async def test_pending_order_keeps_its_original_ruble_quote(self):
        plan = next(item for item in load_plans() if item.code == "lite-1")
        order = SimpleNamespace(
            term_days=30,
            amount_minor=40,
            rub_price_minor=13500,
            discount_rub_minor=3214,
        )

        self.assertEqual(_order_plan_term(plan, order), (30, plan.term_for(30)))

    async def test_invoice_uses_selected_duration_and_records_ruble_total(self):
        user_id = 123456

        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                raise PanelNotFound("no subscription yet")

        message = SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            answer_invoice=AsyncMock(),
            answer=AsyncMock(),
        )
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=user_id),
            message=message,
            data="pay:stars:lite-1:30",
            answer=AsyncMock(),
        )
        settings = Settings(
            bot_token="test-token",
            panel_api_url="https://panel.example.test",
            panel_api_token="test-panel-token",
            admin_telegram_ids=frozenset(),
            panel_internal_squad_uuid="00000000-0000-0000-0000-000000000001",
            traffic_plans_enabled=True,
        )
        with tempfile.TemporaryDirectory() as tempdir:
            orders = OrderStore(Path(tempdir) / "orders.sqlite3")
            await create_stars_invoice(
                callback,
                None,
                Panel(),
                settings,
                load_plans(),
                orders,
            )
            invoice = message.answer_invoice.call_args.kwargs
            order = orders.latest_for_user(user_id)

        self.assertEqual(invoice["currency"], "XTR")
        self.assertEqual(invoice["prices"][0].amount, 40)
        self.assertEqual(order.term_days, 30)
        self.assertEqual(order.amount_minor, 40)
        self.assertEqual(order.rub_price_minor, 4264)

    async def test_unavailable_method_shows_alternatives_without_creating_order(self):
        user_id = 123456
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=user_id),
            message=SimpleNamespace(chat=SimpleNamespace(type="private")),
            data="pay:unavailable:sbp:lite-1:30",
            answer=AsyncMock(),
        )
        settings = Settings(
            bot_token="test-token",
            panel_api_url="https://panel.example.test",
            panel_api_token="test-panel-token",
            admin_telegram_ids=frozenset(),
            panel_internal_squad_uuid="00000000-0000-0000-0000-000000000001",
            traffic_plans_enabled=True,
        )
        with tempfile.TemporaryDirectory() as tempdir:
            orders = OrderStore(Path(tempdir) / "orders.sqlite3")
            await create_stars_invoice(
                callback,
                None,
                None,
                settings,
                load_plans(),
                orders,
            )
            self.assertIsNone(orders.latest_for_user(user_id))

        callback.answer.assert_awaited_once()
        self.assertEqual(
            callback.answer.await_args.args,
            ("Этот способ оплаты пока не подключён. Заказ и платёж не создавались. "
             "Выберите Telegram Stars или вернитесь к тарифам.",),
        )
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])

    async def test_invoice_cannot_skip_term_confirmation_payload(self):
        user_id = 123456
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=user_id),
            message=SimpleNamespace(chat=SimpleNamespace(type="private")),
            data="pay:stars:lite-1",
            answer=AsyncMock(),
        )
        settings = Settings(
            bot_token="test-token",
            panel_api_url="https://panel.example.test",
            panel_api_token="test-panel-token",
            admin_telegram_ids=frozenset(),
            panel_internal_squad_uuid="00000000-0000-0000-0000-000000000001",
            traffic_plans_enabled=True,
        )

        class UnusedPanel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                raise AssertionError("stale invoice button must be rejected first")

        await create_stars_invoice(
            callback,
            None,
            UnusedPanel(),
            settings,
            load_plans(),
            None,
        )
        callback.answer.assert_awaited_once()
        self.assertTrue(callback.answer.call_args.kwargs["show_alert"])


if __name__ == "__main__":
    unittest.main()
