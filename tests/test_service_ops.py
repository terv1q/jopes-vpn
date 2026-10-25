from __future__ import annotations

import importlib.util
import io
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from jopesvpn.config import ConfigurationError, ServicePolicy, Settings
from jopesvpn.payment_check import PaymentCheckStore, check_once, prepare
from jopesvpn.payments import PaymentStore
from jopesvpn.plans import load_plans
from jopesvpn.preferences import PreferencesStore
from jopesvpn.service_ops import ServiceOpsStore
from jopesvpn.service_ui import process_erasure
from jopesvpn.tickets import TicketStore
from jopesvpn.wallet import WalletStore

DEPLOY = Path(__file__).resolve().parents[1] / "deploy/production"


def load(name):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), DEPLOY / (name + ".py")
    )
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


monitor = load("monitor")
cloud = load("cloud-backup")
auth = load("backup-auth")


def settings():
    return Settings(
        bot_token="test",
        panel_api_url="https://panel.test",
        panel_api_token="test",
        admin_telegram_ids=frozenset({1}),
    )


class StorageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "state.sqlite3"
        PreferencesStore(self.path)
        TicketStore(self.path)
        self.store = ServiceOpsStore(self.path)
        with closing(self.store.connect()) as db, db:
            db.execute("CREATE TABLE preserved_payments(id INTEGER, amount INTEGER)")
            db.execute("INSERT INTO preserved_payments VALUES (1, 1000)")

    def ticket(self, *, status="open", age_days=0):
        created = (datetime.now(UTC) - timedelta(days=age_days, hours=3)).isoformat()
        with closing(self.store.connect()) as db, db:
            ticket_id = db.execute(
                "INSERT INTO support_tickets(user_id,user_name,category,status,created_at,closed_at) VALUES (1,'User','connection',?,?,?)",
                (status, created, created if status == "closed" else None),
            ).lastrowid
            db.execute(
                "INSERT INTO support_ticket_messages(ticket_id,sender_id,sender_role,body,created_at) VALUES (?,1,'user','Help',?)",
                (ticket_id, created),
            )
            db.execute(
                "INSERT INTO support_ticket_notifications(ticket_id,recipient_id) VALUES (?,1)",
                (ticket_id,),
            )
            return ticket_id

    def test_support_reminder_is_deduplicated_and_resolved_by_reply(self):
        ticket_id = self.ticket()
        self.store.collect(ServicePolicy())
        self.store.collect(ServicePolicy())
        self.assertEqual(len(self.store.pending()), 1)
        with closing(self.store.connect()) as db, db:
            db.execute(
                "INSERT INTO support_ticket_messages(ticket_id,sender_id,sender_role,body) VALUES (?,2,'admin','Done')",
                (ticket_id,),
            )
        self.store.collect(ServicePolicy())
        self.assertEqual(self.store.pending(), [])

    def test_trial_attempts_deduplicate_clicks_and_survive_restart(self):
        now = datetime.now(UTC)
        for offset in (0, 0, 1, 2):
            self.store.repeated_trial(1, 3, now=now + timedelta(minutes=offset))
        self.assertEqual(len(ServiceOpsStore(self.path).pending()), 1)
        self.assertEqual(self.store.pending()[0]["facts"]["attempts"], 3)

    def test_shared_device_is_hashed_and_only_creates_review_signal(self):
        self.store.observe_device(1, "private-device", "private-key")
        self.store.observe_device(2, "private-device", "private-key")
        self.store.collect(ServicePolicy())
        self.assertEqual(self.store.pending()[0]["kind"], "shared_device")
        self.assertEqual(self.store.pending()[0]["facts"]["users"], [1, 2])
        with closing(self.store.connect()) as db:
            data = str(
                db.execute("SELECT * FROM service_device_observations").fetchall()
            )
        self.assertNotIn("private-device", data)
        self.assertNotIn("private-key", data)

    def test_referral_burst_and_cycle_are_flagged_without_changing_rewards(self):
        with closing(self.store.connect()) as db, db:
            db.executemany(
                "INSERT INTO referrals(referred_id,referrer_id) VALUES (?,?)",
                [(2, 1), (1, 2), (3, 1), (4, 1), (5, 1), (6, 1)],
            )
        self.store.collect(ServicePolicy())
        kinds = {event["kind"] for event in self.store.pending()}
        self.assertEqual(kinds, {"referral_burst", "referral_loop"})
        with closing(self.store.connect()) as db:
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM referrals WHERE reward_status='eligible'"
                ).fetchone()[0],
                6,
            )

    def test_retention_cascades_only_old_closed_tickets_and_preserves_money(self):
        self.ticket(status="closed", age_days=91)
        self.ticket(status="closed", age_days=89)
        self.ticket(status="open", age_days=100)
        self.store.record(
            "old",
            "review",
            {},
            now=(datetime.now(UTC) - timedelta(days=31)).isoformat(),
        )
        result = self.store.retain(ServicePolicy())
        self.assertEqual(result["closed_tickets"], 1)
        with closing(self.store.connect()) as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM support_tickets").fetchone()[0], 2
            )
            self.assertEqual(
                db.execute("SELECT count(*) FROM support_ticket_messages").fetchone()[
                    0
                ],
                2,
            )
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM support_ticket_notifications"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                db.execute("SELECT amount FROM preserved_payments").fetchone()[0], 1000
            )
        self.assertEqual(self.store.pending(), [])

    def test_retention_failure_rolls_back_deleted_tickets(self):
        self.ticket(status="closed", age_days=100)
        with closing(self.store.connect()) as db, db:
            db.execute(
                "CREATE TRIGGER prevent_cleanup BEFORE DELETE ON service_events BEGIN SELECT RAISE(ABORT,'test'); END"
            )
        self.store.record(
            "old",
            "review",
            {},
            now=(datetime.now(UTC) - timedelta(days=40)).isoformat(),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.retain(ServicePolicy())
        with closing(self.store.connect()) as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM support_tickets").fetchone()[0], 1
            )

    def test_erasure_requests_are_idempotent_and_preserve_billing(self):
        self.ticket()
        request_id = self.store.request_erasure(1)
        self.assertEqual(self.store.request_erasure(1), request_id)
        self.assertTrue(self.store.erase_personal_data(request_id))
        self.assertFalse(self.store.erase_personal_data(request_id))
        with closing(self.store.connect()) as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM support_tickets").fetchone()[0], 0
            )
            self.assertEqual(
                db.execute("SELECT amount FROM preserved_payments").fetchone()[0], 1000
            )


class PolicyTests(unittest.TestCase):
    def test_owner_fallback_is_only_allowed_for_one_admin(self):
        self.assertEqual(settings().service_owner_id, 1)
        self.assertIsNone(
            replace(settings(), admin_telegram_ids=frozenset({1, 2})).service_owner_id
        )
        self.assertEqual(replace(settings(), owner_telegram_id=2).service_owner_id, 2)

    def test_invalid_retention_and_thresholds_are_rejected(self):
        with (
            patch.dict("os.environ", {"OPS_TICKET_RETENTION_DAYS": "0"}),
            self.assertRaises(ConfigurationError),
        ):
            ServicePolicy.from_env()

    def test_cloud_and_windows_backup_hmac_are_compatible(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key"
            data = Path(directory) / "data"
            key.write_bytes(b"test-secret\n")
            data.write_bytes(b"encrypted-data")
            self.assertEqual(
                cloud.authenticate(b"test-secret", data), auth.mac_for(key, data)
            )

    def test_noncritical_warning_is_daily_and_critical_recovery_is_deduplicated(self):
        config = {
            "telegram": {"token": "test", "admin_ids": [1]},
            "daily_summary": True,
            "summary_hour_utc": 0,
            "critical_labels": ["Backup"],
            "single_owner": True,
        }
        state = {"active": ["RAM"]}
        context = MagicMock()
        context.__enter__.side_effect = lambda: io.BytesIO(b'{"ok":true}')
        with patch.object(monitor, "urlopen", return_value=context) as request:
            self.assertEqual(monitor.notify(config, state), 0)
            self.assertEqual(request.call_count, 0)
            self.assertTrue(
                monitor.daily_summary(config, state, {"available_mb": 400})[1]
            )
            self.assertFalse(monitor.daily_summary(config, state, {})[1])
            self.assertEqual(request.call_count, 1)
            state["active"] = ["Backup"]
            monitor.notify(config, state)
            monitor.notify(config, state)
            self.assertEqual(request.call_count, 2)
            self.assertEqual(set(state["notifications"]), {"owner"})

    def test_disk_and_backup_missing_are_observed(self):
        config = {
            "endpoints": {},
            "containers": [],
            "min_available_mb": 500,
            "max_swap_percent": 75,
            "failure_threshold": 1,
            "check_backup": True,
        }
        resource = {
            "containers": {},
            "available_mb": 700,
            "swap_percent": 10,
            "vpn_tcp_ports": True,
            "disk_percent": 97,
            "disk_free_gb": 1,
            "backup_age_minutes": None,
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(monitor, "remote_health", return_value=resource),
        ):
            report = monitor.monitor(config, Path(directory) / "state")
        self.assertIn("Disk critical", report["issues"])
        self.assertIn("Backup", report["issues"])


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_stars_success_reverses_real_wallet_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state"
            PreferencesStore(path)
            wallet = WalletStore(path)
            topup = wallet.create_topup(1, 100, 1)
            store = PaymentCheckStore(path)
            store.add(topup.id, "telegram_stars", 1, 100)
            wallet.confirm_topup(
                topup.id,
                provider_payment_id="test-charge",
                amount_minor=1,
                currency="XTR",
            )
            bot = AsyncMock()
            bot.refund_star_payment.return_value = True
            await check_once(bot, settings(), wallet, None, store)
            await check_once(bot, settings(), wallet, None, store)
            self.assertEqual(bot.refund_star_payment.await_count, 1)
            self.assertEqual(wallet.topup(topup.id).status, "REFUNDED")
            self.assertEqual(wallet.account(1).balance_minor, 0)

    async def test_platega_waits_for_provider_confirmation_before_reversing_wallet(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state"
            PreferencesStore(path)
            wallet = WalletStore(path)
            topup = wallet.create_topup(1, 100, 1, provider="platega", payment_method=2)
            store = PaymentCheckStore(path)
            store.add(topup.id, "platega", 1, 100)
            wallet.confirm_topup(
                topup.id,
                provider_payment_id="test-charge",
                amount_minor=100,
                currency="RUB",
                provider="platega",
            )
            client = AsyncMock()
            client.request_refund.return_value = True
            client.get_transaction_status.side_effect = [
                MagicMock(status="CONFIRMED"),
                MagicMock(status="CHARGEBACKED"),
            ]
            bot = AsyncMock()
            await check_once(bot, settings(), wallet, client, store)
            self.assertEqual(wallet.topup(topup.id).status, "CREDITED")
            await check_once(bot, settings(), wallet, client, store)
            await check_once(bot, settings(), wallet, client, store)
            self.assertEqual(client.request_refund.await_count, 1)
            self.assertEqual(wallet.topup(topup.id).status, "REFUNDED")
            self.assertEqual(wallet.account(1).balance_minor, 0)

    async def test_expired_control_link_does_not_lose_late_payment(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PaymentCheckStore(Path(directory) / "state")
            store.add("test", "platega", 1, 100)
            store.state("test", "expired")
            wallet = MagicMock()
            wallet.topup.return_value = MagicMock(
                telegram_id=1,
                provider="platega",
                amount_minor=100,
                status="CREDITED",
                provider_payment_id="charge",
                id="test",
            )
            client = AsyncMock()
            client.request_refund.return_value = True
            client.get_transaction_status.return_value = MagicMock(
                status="CHARGEBACKED"
            )
            await check_once(AsyncMock(), settings(), wallet, client, store)
            wallet.refund_topup.assert_called_once_with("test")

    async def test_prepare_rejects_existing_owner_payment_even_in_same_second(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state"
            wallet = WalletStore(path)
            wallet.create_topup(1, 100, 1)
            with self.assertRaises(ValueError):
                await prepare(
                    AsyncMock(),
                    settings(),
                    wallet,
                    PaymentStore(path),
                    None,
                    load_plans(),
                )
            self.assertEqual(PaymentCheckStore(path).rows(), [])

    async def test_privacy_processing_is_owner_only(self):
        message = MagicMock()
        message.chat.type = "private"
        message.from_user.id = 2
        message.text = "/privacy_process 1 confirm"
        store = MagicMock()
        await process_erasure(message, settings(), store)
        store.erase_personal_data.assert_not_called()

    async def test_uncertain_star_refund_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PaymentCheckStore(Path(directory) / "state")
            store.add("test", "telegram_stars", 1, 100)
            wallet = MagicMock()
            wallet.topup.return_value = MagicMock(
                telegram_id=1,
                provider="telegram_stars",
                amount_minor=100,
                status="CREDITED",
                provider_payment_id="charge",
                id="test",
            )
            bot = AsyncMock()
            from aiogram.exceptions import TelegramNetworkError
            from aiogram.methods import RefundStarPayment

            bot.refund_star_payment.side_effect = TelegramNetworkError(
                method=RefundStarPayment(
                    user_id=1, telegram_payment_charge_id="charge"
                ),
                message="private",
            )
            await check_once(bot, settings(), wallet, None, store)
            await check_once(bot, settings(), wallet, None, store)
            self.assertEqual(bot.refund_star_payment.await_count, 1)
            wallet.refund_topup.assert_not_called()
            self.assertEqual(store.rows()[0]["state"], "refund_review")

    async def test_control_check_never_refunds_another_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PaymentCheckStore(Path(directory) / "state")
            store.add("test", "telegram_stars", 2, 100)
            bot = AsyncMock()
            wallet = MagicMock()
            await check_once(bot, settings(), wallet, None, store)
            bot.refund_star_payment.assert_not_called()
            wallet.topup.assert_not_called()
