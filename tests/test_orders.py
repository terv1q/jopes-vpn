from __future__ import annotations

import tempfile
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path

from jopesvpn.orders import (
    FULFILLED,
    FULFILLMENT_REVIEW,
    PAID,
    PENDING,
    REFUNDED,
    OrderConflict,
    OrderStateError,
    OrderStore,
    PlanPriceMissing,
)
from jopesvpn.plans import Plan
from jopesvpn.plans import load_plans, term_savings


def plan(price_minor: int | None = 49900) -> Plan:
    return Plan(
        code="month",
        title="1 месяц",
        days=30,
        description="",
        price_minor=price_minor,
        currency="RUB",
    )


class OrderStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = OrderStore(Path(self._tmp.name) / "orders.db")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_duplicate_payment_confirmation_is_idempotent(self):
        order = self.store.create_order(12345, plan())
        self.assertEqual(order.status, PENDING)
        confirmed, changed = self.store.confirm_payment(
            order.id,
            provider="test-provider",
            provider_payment_id="payment-1",
            amount_minor=49900,
            currency="RUB",
        )
        self.assertTrue(changed)
        self.assertEqual(confirmed.status, PAID)
        duplicate, changed_again = self.store.confirm_payment(
            order.id,
            provider="test-provider",
            provider_payment_id="payment-1",
            amount_minor=49900,
            currency="RUB",
        )
        self.assertFalse(changed_again)
        self.assertEqual(duplicate.status, PAID)
        self.assertEqual(duplicate.paid_at, confirmed.paid_at)

    def test_payment_id_cannot_be_reused_for_another_order(self):
        first = self.store.create_order(12345, plan())
        second = self.store.create_order(54321, plan())
        self.store.confirm_payment(
            first.id,
            provider="test-provider",
            provider_payment_id="payment-1",
            amount_minor=49900,
            currency="RUB",
        )
        with self.assertRaises(OrderConflict):
            self.store.confirm_payment(
                second.id,
                provider="test-provider",
                provider_payment_id="payment-1",
                amount_minor=49900,
                currency="RUB",
            )
        self.assertEqual(self.store.get(second.id).status, PENDING)

    def test_wrong_amount_does_not_confirm_the_order(self):
        order = self.store.create_order(12345, plan())
        with self.assertRaises(OrderConflict):
            self.store.confirm_payment(
                order.id,
                provider="test-provider",
                provider_payment_id="payment-2",
                amount_minor=1,
                currency="RUB",
            )
        self.assertEqual(self.store.get(order.id).status, PENDING)

    def test_fulfillment_is_recorded_once_and_requires_payment(self):
        order = self.store.create_order(12345, plan())
        with self.assertRaises(OrderStateError):
            self.store.mark_fulfilled(order.id)
        self.store.confirm_payment(
            order.id,
            provider="test-provider",
            provider_payment_id="payment-3",
            amount_minor=49900,
            currency="RUB",
        )
        self.assertTrue(self.store.claim_fulfillment(order.id))
        self.assertFalse(self.store.claim_fulfillment(order.id))
        self.assertTrue(self.store.mark_fulfilled(order.id))
        self.assertFalse(self.store.mark_fulfilled(order.id))
        self.assertEqual(self.store.get(order.id).status, FULFILLED)

    def test_interrupted_fulfillment_is_moved_to_review_on_restart(self):
        order = self.store.create_order(12345, plan())
        self.store.confirm_payment(
            order.id,
            provider="test-provider",
            provider_payment_id="payment-4",
            amount_minor=49900,
            currency="RUB",
        )
        self.assertTrue(self.store.claim_fulfillment(order.id))
        reopened = OrderStore(Path(self._tmp.name) / "orders.db")
        self.assertEqual(reopened.get(order.id).status, FULFILLMENT_REVIEW)

    def test_refund_state_is_claimed_and_recorded_once(self):
        order = self.store.create_order(12345, plan())
        self.store.confirm_payment(
            order.id,
            provider="test-provider",
            provider_payment_id="payment-5",
            amount_minor=49900,
            currency="RUB",
        )
        self.store.claim_fulfillment(order.id)
        self.store.mark_fulfilled(order.id)
        self.assertTrue(self.store.claim_refund(order.id))
        self.assertFalse(self.store.claim_refund(order.id))
        self.assertTrue(self.store.mark_refunded(order.id))
        self.assertFalse(self.store.mark_refunded(order.id))
        self.assertEqual(self.store.get(order.id).status, REFUNDED)

    def test_unpriced_plan_cannot_create_an_order(self):
        with self.assertRaises(PlanPriceMissing):
            self.store.create_order(12345, plan(price_minor=None))

    def test_selected_term_price_and_discount_are_saved_separately_from_stars(self):
        selected_plan = next(
            item for item in load_plans() if item.code == "lite-1"
        )
        term = selected_plan.term_for(30)
        self.assertIsNotNone(term)
        order = self.store.create_order(12345, selected_plan, term)
        savings, _ = term_savings(selected_plan, term)
        self.assertEqual(order.amount_minor, term.stars_amount)
        self.assertEqual(order.currency, "XTR")
        self.assertEqual(order.term_days, 30)
        self.assertEqual(order.rub_price_minor, term.price_rub_minor)
        self.assertEqual(order.discount_rub_minor, savings)
        self.assertEqual(self.store.get(order.id), order)

    def test_different_selected_terms_do_not_reuse_a_pending_order(self):
        selected_plan = next(
            item for item in load_plans() if item.code == "lite-1"
        )
        short_order = self.store.create_order(
            12345, selected_plan, selected_plan.term_for(7)
        )
        long_order = self.store.create_order(
            12345, selected_plan, selected_plan.term_for(30)
        )
        self.assertNotEqual(short_order.id, long_order.id)
        reused = self.store.create_order(
            12345, selected_plan, selected_plan.term_for(30)
        )
        self.assertEqual(reused.id, long_order.id)

    def test_existing_orders_survive_additive_schema_migration(self):
        path = Path(self._tmp.name) / "orders-legacy.db"
        with closing(sqlite3.connect(path)) as connection:
            connection.execute(
                """
                CREATE TABLE orders (
                    id TEXT PRIMARY KEY,
                    telegram_id INTEGER NOT NULL,
                    plan_code TEXT NOT NULL,
                    amount_minor INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    status TEXT NOT NULL,
                    provider TEXT,
                    provider_payment_id TEXT,
                    created_at TEXT NOT NULL,
                    paid_at TEXT,
                    fulfilled_at TEXT,
                    admin_notified_at TEXT,
                    UNIQUE (provider, provider_payment_id)
                )
                """
            )
            connection.execute(
                """
                INSERT INTO orders (
                    id, telegram_id, plan_code, amount_minor, currency,
                    status, created_at
                ) VALUES ('legacy-order', 12345, 'month', 10, 'XTR',
                          'PENDING', '2025-01-01T00:00:00+00:00')
                """
            )
            connection.commit()
        migrated = OrderStore(path)
        legacy = migrated.get("legacy-order")
        self.assertEqual(legacy.plan_code, "month")
        self.assertEqual(legacy.amount_minor, 10)
        self.assertIsNone(legacy.term_days)
        self.assertIsNone(legacy.rub_price_minor)


if __name__ == "__main__":
    unittest.main()
