from __future__ import annotations

import sqlite3
import tempfile
import unittest
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
        again, changed_again, reward_again = self.wallet.confirm_topup(
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

    def test_referral_purchase_rates_are_seventy_then_forty_then_twenty(self) -> None:
        self.prefs.register_start(3003, 1001)
        amounts = [10_000, 20_000, 30_000, 40_000]
        percents = [70, 40, 20, 20]
        for index, (amount, percent) in enumerate(zip(amounts, percents)):
            reward = self.wallet.reward_referral_purchase(
                f"order-{index}", 3003, amount
            )
            self.assertIsNotNone(reward)
            self.assertEqual(reward.percent, percent)
            self.assertEqual(reward.amount_minor, amount * percent // 100)
        duplicate = self.wallet.reward_referral_purchase(
            "order-3", 3003, 40_000
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

    def test_withdrawal_requires_minimum_and_holds_referral_balance_once(self) -> None:
        self.prefs.register_start(4004, 1001)
        self.wallet.reward_referral_purchase("order-x", 4004, 40_000)
        with self.assertRaises(WithdrawalUnavailable):
            self.wallet.request_withdrawal(1001, MIN_WITHDRAWAL_MINOR - 1)
        request = self.wallet.request_withdrawal(
            1001, self.wallet.account(1001).referral_available_minor
        )
        self.assertEqual(request.amount_minor, 28_000)
        self.assertEqual(self.wallet.account(1001).referral_available_minor, 0)
        with self.assertRaises(WithdrawalPending):
            self.wallet.request_withdrawal(1001, MIN_WITHDRAWAL_MINOR)

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
        self.assertFalse(self.wallet.refund_topup(topup.id))

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
