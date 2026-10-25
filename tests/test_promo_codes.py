from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Update

from jopesvpn.bot import router, user_navigation
from jopesvpn.config import ConfigurationError, PromoPolicy, Settings
from jopesvpn.orders import OrderStore
from jopesvpn.plans import load_plans
from jopesvpn.preferences import PreferencesStore
from jopesvpn.promo_codes import PromoCodeError, PromoCodeStore
from jopesvpn.promo_ui import (
    PromoCodeFlow,
    PromoInputExitMiddleware,
    promo_admin_command,
    promo_admin_help,
    promo_cancel,
    promo_code_input,
    promo_command,
    promo_prompt,
)
from jopesvpn.ui import (
    about_keyboard,
    cabinet_keyboard,
    connect_keyboard,
    help_keyboard,
    home_keyboard,
    more_keyboard,
    plans_keyboard,
    wallet_text,
)
from jopesvpn.wallet import WalletStore


class PromoCodeStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state.sqlite3"
        self.prefs = PreferencesStore(self.path)
        self.orders = OrderStore(self.path)
        self.wallet = WalletStore(self.path)
        self.now = datetime(2026, 10, 25, 12, tzinfo=UTC)
        self.codes = PromoCodeStore(self.wallet)

    def create(self, code="GIFT_TEST_123", uses=10, amount=5000):
        return self.codes.create(
            code,
            reward_minor=amount,
            valid_days=1,
            max_uses=uses,
            admin_id=1,
            now=self.now,
        )

    def test_reward_is_once_case_insensitive_and_visible_in_history(self):
        self.create()
        self.assertEqual(
            self.codes.redeem(101, " gift_test_123 ", now=self.now).status, "applied"
        )
        self.assertEqual(
            self.codes.redeem(101, "GIFT_TEST_123", now=self.now).status, "used"
        )
        self.assertEqual(self.wallet.account(101).balance_minor, 5000)
        self.assertEqual(self.wallet.account(101).referral_earned_minor, 0)
        self.assertEqual(len(self.wallet.history(101)), 1)
        history = self.wallet.payment_history(101)
        self.assertEqual(len(history), 1)
        self.assertIn("Подарок по промокоду", wallet_text(history, None))
        with closing(sqlite3.connect(self.path)) as db:
            self.assertNotIn(
                "GIFT_TEST_123",
                str(db.execute("SELECT * FROM voucher_codes").fetchall()),
            )

    def test_expiry_disable_and_quota_have_the_same_public_failure(self):
        self.create(uses=1)
        self.assertEqual(
            self.codes.redeem(101, "GIFT_TEST_123", now=self.now).status, "applied"
        )
        self.assertEqual(
            self.codes.redeem(102, "GIFT_TEST_123", now=self.now).status, "unavailable"
        )
        self.create("EXPIRES_TEST")
        self.assertEqual(
            self.codes.redeem(
                103, "EXPIRES_TEST", now=self.now + timedelta(days=1)
            ).status,
            "unavailable",
        )
        self.create("DISABLE_TEST")
        self.assertTrue(self.codes.disable("disable_test"))
        self.assertFalse(self.codes.disable("DISABLE_TEST"))
        self.assertEqual(
            self.codes.redeem(104, "DISABLE_TEST", now=self.now).status, "unavailable"
        )
        self.assertFalse(self.create("DISABLE_TEST"))

    def test_invalid_input_is_counted_and_lockout_survives_restart(self):
        for raw in ("x", "НЕ_ЛАТИНИЦА", "x' OR 1=1 --", "A" * 10000, "INTERNAL SPACE"):
            self.assertEqual(self.codes.redeem(101, raw, now=self.now).status, "format")
        self.create()
        reopened = PromoCodeStore(self.wallet)
        blocked = reopened.redeem(101, "GIFT_TEST_123", now=self.now)
        self.assertEqual((blocked.status, blocked.retry_after), ("limited", 900))
        self.assertEqual(
            reopened.redeem(
                101, "GIFT_TEST_123", now=self.now + timedelta(seconds=900)
            ).status,
            "applied",
        )

    def test_global_limit_covers_different_accounts(self):
        codes = PromoCodeStore(
            self.wallet, PromoPolicy(max_attempts=1, global_max_attempts=2)
        )
        self.assertEqual(
            codes.redeem(101, "NO_SUCH_CODE", now=self.now).status, "unavailable"
        )
        self.assertEqual(
            codes.redeem(102, "NO_SUCH_CODE", now=self.now).status, "unavailable"
        )
        self.assertEqual(
            codes.redeem(103, "NO_SUCH_CODE", now=self.now).status, "limited"
        )

    def test_parallel_claims_obey_quota_and_account_uniqueness(self):
        self.create(uses=3)
        with ThreadPoolExecutor(max_workers=12) as executor:
            outcomes = list(
                executor.map(
                    lambda uid: self.codes.redeem(uid, "GIFT_TEST_123", now=self.now),
                    range(101, 121),
                )
            )
        self.assertEqual(sum(item.status == "applied" for item in outcomes), 3)
        self.create("CONCURRENT_TEST")
        with ThreadPoolExecutor(max_workers=8) as executor:
            outcomes = list(
                executor.map(
                    lambda _: self.codes.redeem(200, "CONCURRENT_TEST", now=self.now),
                    range(8),
                )
            )
        self.assertEqual(sum(item.status == "applied" for item in outcomes), 1)
        self.assertEqual(self.wallet.account(200).balance_minor, 5000)

    def test_storage_failure_rolls_back_balance_history_and_quota(self):
        self.create(uses=1)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute(
                "CREATE TRIGGER reject_voucher BEFORE INSERT ON voucher_redemptions "
                "BEGIN SELECT RAISE(ABORT, 'test failure'); END"
            )
            db.commit()
        with self.assertRaises(PromoCodeError):
            self.codes.redeem(101, "GIFT_TEST_123", now=self.now)
        self.assertEqual(self.wallet.account(101).balance_minor, 0)
        self.assertEqual(self.wallet.payment_history(101), [])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(
                db.execute("SELECT uses FROM voucher_codes").fetchone()[0], 0
            )
            db.execute("DROP TRIGGER reject_voucher")
            db.commit()
        self.assertEqual(
            self.codes.redeem(102, "GIFT_TEST_123", now=self.now).status, "applied"
        )

    def test_existing_refund_debt_uses_wallet_accounting(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute(
                "INSERT INTO wallet_accounts (telegram_id, balance_debt_minor) VALUES (101, 1500)"
            )
            db.commit()
        self.create()
        result = self.codes.redeem(101, "GIFT_TEST_123", now=self.now)
        self.assertEqual(
            (result.reward_minor, result.balance_minor, result.settled_minor),
            (5000, 3500, 1500),
        )
        self.assertEqual(self.wallet.account(101).balance_debt_minor, 0)

    def test_migration_and_code_creation_preserve_existing_schemas_and_rows(self):
        self.prefs.remember(101, "Old account", None, "old_account")
        self.prefs.push_navigation(101, "settings")
        self.wallet.credit(101, 12345, kind="TOPUP", reference="old-topup")
        with closing(sqlite3.connect(self.path)) as db:
            # Start with an existing database without any of the new tables.
            for table in (
                "voucher_redemptions",
                "voucher_attempt_windows",
                "voucher_codes",
            ):
                db.execute(f"DROP TABLE {table}")
            db.commit()
            schemas = db.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
            before = {
                name: db.execute(f'SELECT * FROM "{name}"').fetchall()
                for name, _ in schemas
            }
        codes = PromoCodeStore(self.wallet)
        codes.create(
            "MIGRATION_TEST", reward_minor=1000, valid_days=7, max_uses=1, admin_id=1
        )
        PromoCodeStore(self.wallet)
        with closing(sqlite3.connect(self.path)) as db:
            for name, sql in schemas:
                self.assertEqual(
                    db.execute(
                        "SELECT sql FROM sqlite_master WHERE name = ?", (name,)
                    ).fetchone()[0],
                    sql,
                )
                self.assertEqual(
                    db.execute(f'SELECT * FROM "{name}"').fetchall(), before[name]
                )

    def test_bonus_pays_the_unchanged_catalog_price(self):
        term = next(plan for plan in load_plans() if plan.code == "lite-1").term_for(30)
        self.create(amount=term.price_rub_minor)
        self.codes.redeem(101, "GIFT_TEST_123", now=self.now)
        self.wallet.debit(
            101, term.price_rub_minor, kind="PURCHASE", reference="catalog-price"
        )
        self.assertEqual(self.wallet.account(101).balance_minor, 0)


class PromoConfigTests(unittest.TestCase):
    def test_all_limits_load_from_environment_and_validate(self):
        with patch.dict(
            os.environ,
            {"PROMO_MAX_ATTEMPTS": "2", "PROMO_MAX_VALID_DAYS": "30"},
            clear=True,
        ):
            policy = PromoPolicy.from_env()
        self.assertEqual((policy.max_attempts, policy.max_valid_days), (2, 30))
        for values in (
            {"PROMO_MAX_ATTEMPTS": "0"},
            {"PROMO_MAX_CODE_LENGTH": "2"},
            {"PROMO_MAX_USES": "bad"},
        ):
            with (
                patch.dict(os.environ, values, clear=True),
                self.assertRaises(ConfigurationError),
            ):
                PromoPolicy.from_env()

    def test_resource_settings_allow_hiding_urls_and_reject_credentials(self):
        base = {
            "BOT_TOKEN": "1:test",
            "BOT_ADMIN_IDS": "1",
            "PANEL_API_URL": "https://panel.example.test",
            "PANEL_API_TOKEN": "test",
            "COMMUNITY_URL": "",
            "REVIEWS_URL": "https://t.me/our_reviews",
            "BRAND_NAME": "Jopes VPN",
            "PROMO_MAX_ATTEMPTS": "2",
        }
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / "no-env-file"
            with patch.dict(os.environ, base, clear=True):
                settings = Settings.from_env(env_file)
            self.assertIsNone(settings.community_url)
            self.assertEqual(settings.reviews_url, "https://t.me/our_reviews")
            self.assertEqual(settings.promo_policy.max_attempts, 2)
            for url in (
                "http://t.me/reviews",
                "https://user:password@t.me/reviews",
                "https://t.me/has space",
            ):
                with (
                    patch.dict(os.environ, {**base, "REVIEWS_URL": url}, clear=True),
                    self.assertRaises(ConfigurationError),
                ):
                    Settings.from_env(env_file)


class PromoFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        path = Path(self.directory.name) / "state.sqlite3"
        self.prefs = PreferencesStore(path)
        self.wallet = WalletStore(path)
        self.codes = PromoCodeStore(self.wallet)
        self.settings = Settings(
            "test", "https://panel.example.test", "test", frozenset({1})
        )
        self.state = FSMContext(
            MemoryStorage(), StorageKey(bot_id=1, chat_id=101, user_id=101)
        )
        self.message = SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            from_user=SimpleNamespace(
                id=101, first_name="User", last_name=None, username=None
            ),
            text="/promo",
            answer=AsyncMock(),
        )

    async def navigate(self, action):
        callback = SimpleNamespace(
            data=f"user:{action}",
            message=self.message,
            from_user=self.message.from_user,
            answer=AsyncMock(),
        )
        await user_navigation(
            callback, None, None, self.settings, self.state, (), self.prefs
        )

    async def test_registered_handlers_route_command_input_and_cancel_with_real_fsm(
        self,
    ):
        dispatcher = Dispatcher(storage=self.state.storage)
        callbacks = {promo_command, promo_code_input, promo_cancel, promo_admin_command}
        for registered in router.message.handlers:
            if registered.callback in callbacks:
                dispatcher.message.register(
                    registered.callback, *[item.callback for item in registered.filters]
                )
        bot = Bot("1:test")
        self.addAsyncCleanup(bot.session.close)
        self.codes.create(
            "ROUTING_TEST", reward_minor=5000, valid_days=1, max_uses=1, admin_id=1
        )

        async def dispatch(text, update_id):
            update = Update.model_validate(
                {
                    "update_id": update_id,
                    "message": {
                        "message_id": update_id,
                        "date": int(datetime.now(UTC).timestamp()),
                        "chat": {"id": 101, "type": "private"},
                        "from": {"id": 101, "is_bot": False, "first_name": "User"},
                        "text": text,
                    },
                }
            )
            await dispatcher.feed_update(
                bot,
                update,
                settings=self.settings,
                prefs=self.prefs,
                promo_codes=self.codes,
            )

        with patch("jopesvpn.promo_ui.answer_screen", new_callable=AsyncMock):
            await dispatch("/promo", 1)
            self.assertEqual(
                await self.state.get_state(), PromoCodeFlow.awaiting_code.state
            )
            await dispatch("ROUTING_TEST", 2)
            self.assertEqual(self.wallet.account(101).balance_minor, 5000)
            self.assertIsNone(await self.state.get_state())
            await dispatch("/promo", 3)
            await dispatch("/cancel", 4)
            self.assertIsNone(await self.state.get_state())

    async def test_promo_enter_back_and_reenter_restore_input_state(self):
        self.prefs.reset_navigation(101, "home")
        self.prefs.push_navigation(101, "plans")
        with patch("jopesvpn.promo_ui.edit_screen", new_callable=AsyncMock):
            await self.navigate("promo")
        self.assertEqual(
            await self.state.get_state(), PromoCodeFlow.awaiting_code.state
        )
        with patch("jopesvpn.bot._user_screen", new_callable=AsyncMock) as render:
            await self.navigate("back")
        self.assertIsNone(await self.state.get_state())
        self.assertEqual(render.call_args.args[3], "plans")
        with patch("jopesvpn.promo_ui.edit_screen", new_callable=AsyncMock):
            await self.navigate("promo")
        self.prefs.push_navigation(101, "cabinet")
        with patch("jopesvpn.promo_ui.edit_screen", new_callable=AsyncMock):
            await self.navigate("back")
        self.assertEqual(
            await self.state.get_state(), PromoCodeFlow.awaiting_code.state
        )

    async def test_result_clears_input_and_keeps_routes_to_catalog_and_account(self):
        self.codes.create(
            "FLOW_TEST_123", reward_minor=5000, valid_days=1, max_uses=1, admin_id=1
        )
        with patch("jopesvpn.promo_ui.answer_screen", new_callable=AsyncMock) as answer:
            await promo_command(self.message, self.settings, self.state, self.prefs)
            self.message.text = "FLOW_TEST_123"
            await promo_code_input(self.message, self.settings, self.state, self.codes)
        self.assertIsNone(await self.state.get_state())
        markup = answer.call_args.args[2]
        targets = {
            button.callback_data for row in markup.inline_keyboard for button in row
        }
        self.assertTrue(
            {"user:plans", "user:cabinet", "user:promo", "user:back", "user:home"}
            <= targets
        )

    async def test_cancel_and_back_without_preferences_are_safe(self):
        await self.state.set_state(PromoCodeFlow.awaiting_code)
        with patch("jopesvpn.promo_ui.answer_screen", new_callable=AsyncMock):
            await promo_cancel(self.message, self.state)
        callback = SimpleNamespace(
            data="user:back",
            message=self.message,
            from_user=self.message.from_user,
            answer=AsyncMock(),
        )
        with patch("jopesvpn.bot._user_screen", new_callable=AsyncMock) as render:
            await user_navigation(callback, None, None, self.settings, self.state, ())
        self.assertEqual(render.call_args.args[3], "home")

    async def test_leaving_input_via_an_existing_checkout_button_clears_only_promo_state(
        self,
    ):
        middleware = PromoInputExitMiddleware()
        event = CallbackQuery.model_validate(
            {
                "id": "1",
                "chat_instance": "test",
                "data": "checkout:lite-1",
                "from": {"id": 101, "is_bot": False, "first_name": "User"},
                "message": {
                    "message_id": 1,
                    "date": 1,
                    "chat": {"id": 101, "type": "private"},
                },
            }
        )
        handler = AsyncMock()
        await self.state.set_state(PromoCodeFlow.awaiting_code)
        await middleware(handler, event, {"state": self.state})
        self.assertIsNone(await self.state.get_state())
        await self.state.set_state("TopUpFlow:amount")
        await middleware(handler, event, {"state": self.state})
        self.assertEqual(await self.state.get_state(), "TopUpFlow:amount")

    async def test_admin_creation_is_private_and_repeated_commands_do_not_reset_codes(
        self,
    ):
        self.message.text = "/promo_create ADMIN_TEST_123 50 7 2"
        with patch("jopesvpn.promo_ui.answer_screen", new_callable=AsyncMock) as answer:
            await promo_admin_command(
                self.message, self.settings, self.state, self.codes
            )
            self.assertFalse(answer.called)
            self.message.from_user.id = 1
            await promo_admin_command(
                self.message, self.settings, self.state, self.codes
            )
            self.assertIn("СОЗДАН", answer.call_args.args[1])
            self.codes.redeem(101, "ADMIN_TEST_123")
            await promo_admin_command(
                self.message, self.settings, self.state, self.codes
            )
            self.assertIn("уже существует", answer.call_args.args[1])
            self.message.text = "/promo_create auto 1e999999999 7 2"
            await promo_admin_command(
                self.message, self.settings, self.state, self.codes
            )
            self.assertIn("Проверьте формат", answer.call_args.args[1])
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=101), message=self.message, answer=AsyncMock()
        )
        with patch("jopesvpn.promo_ui.edit_screen", new_callable=AsyncMock) as render:
            await promo_admin_help(callback, self.settings, self.state)
        render.assert_not_called()

    def test_navigation_entries_and_prompt_are_available_without_subscription(self):
        for markup in (
            cabinet_keyboard(),
            connect_keyboard(has_access=False),
            more_keyboard(),
            plans_keyboard(()),
        ):
            targets = {b.callback_data for row in markup.inline_keyboard for b in row}
            self.assertTrue({"user:promo", "user:back", "user:home"} <= targets)
        targets = {
            b.callback_data for row in home_keyboard(False).inline_keyboard for b in row
        }
        self.assertTrue({"user:cabinet", "user:plans", "user:help"} <= targets)
        targets = {
            b.callback_data for row in help_keyboard().inline_keyboard for b in row
        }
        self.assertIn("user:about", targets)
        self.assertIn("Jopes VPN", promo_prompt(self.settings))
        self.assertLessEqual(len(promo_prompt(self.settings)), 1024)
        urls = {
            b.url
            for row in about_keyboard(
                None, None, channel_url=self.settings.trial_channel_url
            ).inline_keyboard
            for b in row
        }
        self.assertIn(self.settings.trial_channel_url, urls)
