from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from jopesvpn.orders import PAID, OrderStore
from jopesvpn.plans import load_plans, stars_for_rub_amount
from jopesvpn.preferences import PreferencesStore
from jopesvpn.wallet import (
    MIN_TOPUP_MINOR,
    MIN_WITHDRAWAL_MINOR,
    InsufficientFunds,
    WalletStore,
    WithdrawalPending,
    WithdrawalUnavailable,
)


class WalletStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "state.sqlite3"
        self.prefs = PreferencesStore(self.path)
        self.orders = OrderStore(self.path)
        self.wallet = WalletStore(self.path)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def create_fulfilled_purchase(self, telegram_id: int):
        plan = next(item for item in load_plans() if item.code == "unlimited-7")
        term = plan.term_for(180)
        self.assertIsNotNone(term)
        order = self.orders.create_order(telegram_id, plan, term)
        self.assertIsNotNone(order.rub_price_minor)
        self.orders.confirm_payment(
            order.id,
            provider="test",
            provider_payment_id=f"payment:{order.id}",
            amount_minor=order.amount_minor,
            currency=order.currency,
        )
        self.assertTrue(self.orders.claim_fulfillment(order.id))
        self.assertTrue(self.orders.mark_fulfilled(order.id))
        return order

    def test_credit_is_idempotent_and_debit_never_goes_negative(self) -> None:
        self.assertTrue(
            self.wallet.credit(
                1001, 25_000, kind="TOPUP", reference="topup:one"
            )
        )
        self.assertFalse(
            self.wallet.credit(
                1001, 25_000, kind="TOPUP", reference="topup:one"
            )
        )
        self.assertEqual(self.wallet.account(1001).balance_minor, 25_000)
        self.assertTrue(
            self.wallet.debit(
                1001, 10_000, kind="PURCHASE", reference="order:one"
            )
        )
        self.assertEqual(self.wallet.account(1001).balance_minor, 15_000)
        with self.assertRaises(InsufficientFunds):
            self.wallet.debit(
                1001, 20_000, kind="PURCHASE", reference="order:two"
            )
        self.assertEqual(self.wallet.account(1001).balance_minor, 15_000)

    def test_migration_converts_legacy_negative_balances_to_debt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                """
                CREATE TABLE wallet_accounts (
                    telegram_id INTEGER PRIMARY KEY,
                    balance_minor INTEGER NOT NULL DEFAULT 0,
                    referral_earned_minor INTEGER NOT NULL DEFAULT 0,
                    referral_withdrawn_minor INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            connection.execute(
                """
                INSERT INTO wallet_accounts (
                    telegram_id, balance_minor, referral_earned_minor
                ) VALUES (1001, -5000, -2000)
                """
            )
            connection.commit()
            connection.close()
            migrated = WalletStore(path).account(1001)
            self.assertEqual(migrated.balance_minor, 0)
            self.assertEqual(migrated.balance_debt_minor, 5_000)
            self.assertEqual(migrated.referral_earned_minor, 0)
            self.assertEqual(migrated.referral_debt_minor, 2_000)

    def test_topup_confirmation_credits_exact_rubles_and_referral_twenty_percent(self) -> None:
        self.prefs.register_start(2002, 1001)
        plans = load_plans()
        amount = 50_000
        stars = stars_for_rub_amount(amount, plans)
        topup = self.wallet.create_topup(2002, amount, stars)
        confirmed, changed, reward = self.wallet.confirm_topup(
            topup.id,
            provider_payment_id="charge-1",
            amount_minor=stars,
            currency="XTR",
        )
        self.assertTrue(changed)
        self.assertEqual(confirmed.status, "CREDITED")
        self.assertEqual(self.wallet.account(2002).balance_minor, amount)
        self.assertIsNotNone(reward)
        self.assertEqual(reward.percent, 20)
        self.assertEqual(reward.amount_minor, amount * 20 // 100)
        self.assertEqual(
            self.wallet.account(1001).referral_earned_minor,
            amount * 20 // 100,
        )
        _, changed_again, reward_again = self.wallet.confirm_topup(
            topup.id,
            provider_payment_id="charge-1",
            amount_minor=stars,
            currency="XTR",
        )
        self.assertFalse(changed_again)
        self.assertIsNone(reward_again)
        self.assertEqual(self.wallet.account(2002).balance_minor, amount)
        self.assertEqual(
            self.wallet.account(1001).referral_earned_minor,
            amount * 20 // 100,
        )

    def test_topup_bonus_and_payment_history_are_atomic_and_idempotent(self) -> None:
        amount = 50_050
        stars = stars_for_rub_amount(amount, load_plans())
        self.wallet.configure_bonus(9001, "percent", 10)
        topup = self.wallet.create_topup(9002, amount, stars)
        self.wallet.confirm_topup(
            topup.id,
            provider_payment_id="bonus-charge",
            amount_minor=stars,
            currency="XTR",
        )
        self.wallet.confirm_topup(
            topup.id,
            provider_payment_id="bonus-charge",
            amount_minor=stars,
            currency="XTR",
        )
        self.assertEqual(
            self.wallet.account(9002).balance_minor,
            amount + amount // 10,
        )
        payment = self.wallet.payment_transaction(topup.id)
        self.assertIsNotNone(payment)
        self.assertEqual(payment.method, "telegram_stars")
        self.assertEqual(payment.rub_amount_minor, amount)
        self.assertEqual(payment.status, "SUCCESS")
        self.assertEqual(
            self.wallet.payment_transaction("bonus-charge").id, topup.id
        )
        self.assertEqual(
            [entry.purpose for entry in self.wallet.payment_history(9002)],
            ["topup", "topup_bonus"],
        )

    def test_topup_refund_reverses_bonus_and_marks_both_records_refunded(self) -> None:
        self.wallet.configure_bonus(9001, "fixed", 500)
        topup = self.wallet.create_topup(9003, 30_000, 282)
        self.wallet.confirm_topup(
            topup.id,
            provider_payment_id="bonus-refund",
            amount_minor=282,
            currency="XTR",
        )
        self.assertEqual(self.wallet.account(9003).balance_minor, 30_500)
        self.assertTrue(self.wallet.refund_topup(topup.id))
        self.assertEqual(self.wallet.account(9003).balance_minor, 0)
        self.assertEqual(
            {payment.status for payment in self.wallet.payment_history(9003)},
            {"REFUNDED"},
        )
        self.assertFalse(self.wallet.refund_topup(topup.id))

    def test_admin_balance_adjustment_requires_reason_and_is_idempotent(self) -> None:
        with self.assertRaises(ValueError):
            self.wallet.adjust_balance(
                operation_id="bad-adjust",
                admin_telegram_id=1,
                telegram_id=2,
                amount_minor=1_000,
                reason=" ",
            )
        self.assertTrue(
            self.wallet.adjust_balance(
                operation_id="adjust-1",
                admin_telegram_id=1,
                telegram_id=2,
                amount_minor=1_000,
                reason="support compensation",
            )
        )
        self.assertFalse(
            self.wallet.adjust_balance(
                operation_id="adjust-1",
                admin_telegram_id=1,
                telegram_id=2,
                amount_minor=1_000,
                reason="support compensation",
            )
        )
        with self.assertRaises(InsufficientFunds):
            self.wallet.adjust_balance(
                operation_id="adjust-2",
                admin_telegram_id=1,
                telegram_id=2,
                amount_minor=-1_001,
                reason="correction",
            )
        self.assertEqual(self.wallet.account(2).balance_minor, 1_000)
        payment = self.wallet.payment_transaction("adjust-1")
        self.assertEqual(payment.purpose, "balance_adjustment")
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute(
                """
                SELECT admin_telegram_id, amount_minor, reason
                FROM wallet_admin_operations
                WHERE operation_id = 'adjust-1'
                """
            ).fetchone()
        self.assertEqual(row, (1, 1_000, "support compensation"))

    def test_referral_purchase_rates_are_seventy_then_forty_then_twenty(self) -> None:
        self.prefs.register_start(3003, 1001)
        percents = [70, 40, 20, 20]
        orders = [
            self.create_fulfilled_purchase(3003) for _ in percents
        ]
        amounts = [int(order.rub_price_minor) for order in orders]
        for order, amount, percent in zip(orders, amounts, percents):
            reward = self.wallet.reward_referral_purchase(
                order.id, 3003, amount
            )
            self.assertIsNotNone(reward)
            self.assertEqual(reward.percent, percent)
            self.assertEqual(reward.amount_minor, amount * percent // 100)
        duplicate = self.wallet.reward_referral_purchase(
            orders[-1].id, 3003, amounts[-1]
        )
        self.assertIsNone(duplicate)
        expected = sum(
            amount * percent // 100
            for amount, percent in zip(amounts, percents)
        )
        self.assertEqual(
            self.wallet.account(1001).referral_earned_minor, expected
        )
        summary = self.wallet.referral_summary(1001)
        self.assertEqual(summary.paying, 1)
        self.assertEqual(summary.purchases, 4)
        self.assertEqual(summary.available_minor, expected)

    def test_balance_order_payment_is_atomic_and_idempotent(self) -> None:
        plan = next(item for item in load_plans() if item.code == "lite-1")
        term = plan.term_for(30)
        self.assertIsNotNone(term)
        self.wallet.credit(
            1001, 100_000, kind="TOPUP", reference="topup:fund"
        )
        order = self.orders.create_order(1001, plan, term)
        self.assertTrue(
            self.wallet.pay_order(order.id, 1001, term.price_rub_minor)
        )
        self.assertEqual(self.orders.get(order.id).status, PAID)
        balance = self.wallet.account(1001).balance_minor
        self.assertEqual(balance, 100_000 - term.price_rub_minor)
        self.assertFalse(
            self.wallet.pay_order(order.id, 1001, term.price_rub_minor)
        )
        self.assertEqual(self.wallet.account(1001).balance_minor, balance)
        payment = self.wallet.payment_transaction(order.id)
        self.assertEqual(payment.status, "CONFIRMED")
        self.wallet.complete_order_payment(order.id, status="SUCCESS")
        self.assertEqual(
            self.wallet.payment_transaction(order.id).status, "SUCCESS"
        )
        self.assertTrue(self.wallet.refund_order(order.id))
        self.assertEqual(self.wallet.account(1001).balance_minor, 100_000)
        self.assertEqual(self.wallet.payment_transaction(order.id).status, "REFUNDED")
        self.assertEqual(
            self.wallet.payment_transaction(f"refund_{order.id}").purpose,
            "subscription_refund",
        )
        self.assertFalse(self.wallet.refund_order(order.id))

    def test_withdrawal_requires_minimum_and_holds_referral_balance_once(self) -> None:
        self.prefs.register_start(4004, 1001)
        order = self.create_fulfilled_purchase(4004)
        self.wallet.reward_referral_purchase(
            order.id, 4004, order.rub_price_minor
        )
        with self.assertRaises(WithdrawalUnavailable):
            self.wallet.request_withdrawal(1001, MIN_WITHDRAWAL_MINOR - 1)
        request = self.wallet.request_withdrawal(
            1001, self.wallet.account(1001).referral_available_minor
        )
        self.assertGreaterEqual(request.amount_minor, MIN_WITHDRAWAL_MINOR)
        self.assertEqual(self.wallet.account(1001).referral_available_minor, 0)
        with self.assertRaises(WithdrawalPending):
            self.wallet.request_withdrawal(1001, MIN_WITHDRAWAL_MINOR)

    def test_rejected_withdrawal_releases_reserved_referral_funds_once(self) -> None:
        self.prefs.register_start(4004, 1001)
        order = self.create_fulfilled_purchase(4004)
        self.wallet.reward_referral_purchase(
            order.id, 4004, order.rub_price_minor
        )
        available = self.wallet.account(1001).referral_available_minor
        request = self.wallet.request_withdrawal(1001, available)
        settled, changed = self.wallet.settle_withdrawal(
            request.id, paid=False
        )
        self.assertTrue(changed)
        self.assertEqual(settled.status, "REJECTED")
        self.assertEqual(
            self.wallet.account(1001).referral_available_minor, available
        )
        _, duplicate = self.wallet.settle_withdrawal(request.id, paid=False)
        self.assertFalse(duplicate)
        self.assertEqual(
            self.wallet.account(1001).referral_available_minor, available
        )

    def test_withdrawal_notification_is_durable_until_marked(self) -> None:
        self.prefs.register_start(4005, 1001)
        order = self.create_fulfilled_purchase(4005)
        reward = self.wallet.reward_referral_purchase(
            order.id, 4005, int(order.rub_price_minor)
        )
        request = self.wallet.request_withdrawal(
            1001, reward.amount_minor
        )
        self.assertEqual(
            self.wallet.pending_withdrawal_notifications(), [request]
        )
        self.assertTrue(self.wallet.mark_withdrawal_notified(request.id))
        self.assertFalse(self.wallet.mark_withdrawal_notified(request.id))
        self.assertEqual(self.wallet.pending_withdrawal_notifications(), [])

    def test_refunded_purchase_restores_withdrawn_commission_as_future_debt(
        self,
    ) -> None:
        self.prefs.register_start(4010, 1001)
        first_order = self.create_fulfilled_purchase(4010)
        first = self.wallet.reward_referral_purchase(
            first_order.id, 4010, first_order.rub_price_minor
        )
        self.assertGreaterEqual(first.amount_minor, MIN_WITHDRAWAL_MINOR)
        request = self.wallet.request_withdrawal(
            1001, first.amount_minor
        )
        self.wallet.settle_withdrawal(request.id, paid=True)
        payment = self.wallet.payment_transaction(request.id)
        self.assertEqual(payment.purpose, "withdrawal")
        self.assertEqual(payment.status, "SUCCESS")
        self.assertTrue(self.wallet.refund_order(first_order.id))
        self.assertEqual(
            self.wallet.account(1001).referral_debt_minor, first.amount_minor
        )
        second_order = self.create_fulfilled_purchase(4010)
        next_reward = self.wallet.reward_referral_purchase(
            second_order.id, 4010, int(second_order.rub_price_minor)
        )
        self.assertEqual(next_reward.percent, 70)
        account = self.wallet.account(1001)
        self.assertEqual(account.referral_debt_minor, 0)
        self.assertEqual(account.referral_available_minor, 0)
        third_order = self.create_fulfilled_purchase(4010)
        final_reward = self.wallet.reward_referral_purchase(
            third_order.id, 4010, int(third_order.rub_price_minor)
        )
        self.assertEqual(final_reward.percent, 40)
        self.assertEqual(final_reward.amount_minor, 17_910)
        self.assertEqual(self.wallet.account(1001).referral_debt_minor, 0)
        self.assertEqual(
            self.wallet.account(1001).referral_available_minor, 17_910
        )
        self.assertFalse(self.wallet.refund_order(first_order.id))

    def test_referral_reward_requires_a_confirmed_fulfilled_order(self) -> None:
        self.prefs.register_start(4011, 1001)
        self.assertIsNone(
            self.wallet.reward_referral_purchase("made-up", 4011, 50_000)
        )

    def test_refund_after_spending_balance_creates_debt_settled_by_future_topup(
        self,
    ) -> None:
        topup = self.wallet.create_topup(6006, 30_000, 300)
        self.wallet.confirm_topup(
            topup.id,
            provider_payment_id="spent-refund",
            amount_minor=300,
            currency="XTR",
        )
        self.wallet.debit(
            6006, 25_000, kind="PURCHASE", reference="spent-purchase"
        )
        self.assertTrue(self.wallet.refund_topup(topup.id))
        account = self.wallet.account(6006)
        self.assertEqual(account.balance_minor, 0)
        self.assertEqual(account.balance_debt_minor, 25_000)
        second = self.wallet.create_topup(6006, 25_000, 250)
        self.wallet.confirm_topup(
            second.id,
            provider_payment_id="debt-settlement",
            amount_minor=250,
            currency="XTR",
        )
        account = self.wallet.account(6006)
        self.assertEqual(account.balance_minor, 0)
        self.assertEqual(account.balance_debt_minor, 0)

    def test_topup_refund_reverses_balance_and_referral_cashback(self) -> None:
        self.prefs.register_start(5005, 1001)
        plans = load_plans()
        amount = 30_000
        stars = stars_for_rub_amount(amount, plans)
        topup = self.wallet.create_topup(5005, amount, stars)
        self.wallet.confirm_topup(
            topup.id,
            provider_payment_id="charge-refund",
            amount_minor=stars,
            currency="XTR",
        )
        self.assertTrue(self.wallet.refund_topup(topup.id))
        self.assertEqual(self.wallet.account(5005).balance_minor, 0)
        self.assertEqual(self.wallet.account(1001).referral_earned_minor, 0)
        self.assertEqual(self.wallet.account(1001).referral_debt_minor, 0)
        self.assertFalse(self.wallet.refund_topup(topup.id))

    def test_topup_refund_restores_referral_debt_paid_by_its_commission(self) -> None:
        self.prefs.register_start(5006, 1001)
        order = self.create_fulfilled_purchase(5006)
        reward = self.wallet.reward_referral_purchase(
            order.id, 5006, int(order.rub_price_minor)
        )
        withdrawal = self.wallet.request_withdrawal(
            1001, reward.amount_minor
        )
        self.wallet.settle_withdrawal(withdrawal.id, paid=True)
        self.assertTrue(self.wallet.refund_order(order.id))
        initial_debt = reward.amount_minor
        topup = self.wallet.create_topup(5006, 50_000, 470)
        self.wallet.confirm_topup(
            topup.id,
            provider_payment_id="debt-topup-refund",
            amount_minor=470,
            currency="XTR",
        )
        self.assertEqual(
            self.wallet.account(1001).referral_debt_minor,
            initial_debt - 10_000,
        )
        self.assertTrue(self.wallet.refund_topup(topup.id))
        self.assertEqual(
            self.wallet.account(1001).referral_debt_minor, initial_debt
        )

    def test_history_records_every_operation(self) -> None:
        self.wallet.credit(
            1001, MIN_TOPUP_MINOR, kind="TOPUP", reference="topup:history"
        )
        self.wallet.debit(
            1001, 5_000, kind="PURCHASE", reference="order:history"
        )
        entries = self.wallet.history(1001)
        self.assertEqual([entry.kind for entry in entries], ["PURCHASE", "TOPUP"])
        self.assertEqual(entries[0].balance_delta_minor, -5_000)
        self.assertEqual(entries[1].balance_delta_minor, MIN_TOPUP_MINOR)


class TopupPricingTests(unittest.TestCase):
    def test_stars_are_ceil_for_requested_rubles(self) -> None:
        plans = load_plans()
        self.assertEqual(stars_for_rub_amount(25_000, plans), 235)
        self.assertEqual(stars_for_rub_amount(50_000, plans), 470)

    def test_topup_bounds_are_validated(self) -> None:
        plans = load_plans()
        with self.assertRaises(ValueError):
            stars_for_rub_amount(24_999, plans)
        with self.assertRaises(ValueError):
            stars_for_rub_amount(10_000_001, plans)


if __name__ == "__main__":
    unittest.main()
