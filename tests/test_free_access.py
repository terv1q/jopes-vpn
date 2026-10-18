from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from jopesvpn.config import Settings
from jopesvpn.free_access import process_free_access_once
from jopesvpn.panel import PanelUnavailable, PanelUser
from jopesvpn.preferences import PreferencesStore


def settings(*, enabled: bool) -> Settings:
    return Settings(
        bot_token="token",
        panel_api_url="https://panel.example.invalid",
        panel_api_token="panel-token",
        admin_telegram_ids=frozenset({9001}),
        free_access_enabled=enabled,
    )


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        self.messages.append((chat_id, text))


class FakePanel:
    def __init__(self, user: PanelUser) -> None:
        self.user = user
        self.lookups = 0
        self.free_grants = 0
        self.resets = 0
        self.reactivations = 0

    async def get_user_by_telegram_id(self, telegram_id: int) -> PanelUser:
        self.lookups += 1
        return self.user

    async def apply_free_access(self, user: PanelUser) -> PanelUser:
        self.free_grants += 1
        self.user = PanelUser(
            id=user.id,
            username=user.username,
            telegram_id=user.telegram_id,
            status="ACTIVE",
            expire_at="2099-01-01T00:00:00+00:00",
            subscription_url=None,
            tag="FREE",
            traffic_used_bytes=0,
            traffic_lifetime_used_bytes=100,
            traffic_limit_bytes=200 * 1024**2,
            traffic_limit_strategy="NO_RESET",
        )
        return self.user

    async def reset_user_traffic(self, user: PanelUser) -> PanelUser:
        self.resets += 1
        self.user = PanelUser(
            id=user.id,
            username=user.username,
            telegram_id=user.telegram_id,
            status=user.status,
            expire_at=user.expire_at,
            subscription_url=user.subscription_url,
            tag=user.tag,
            traffic_used_bytes=0,
            traffic_lifetime_used_bytes=user.traffic_lifetime_used_bytes,
            traffic_limit_bytes=user.traffic_limit_bytes,
            last_traffic_reset_at="2026-01-02T00:00:01+00:00",
            traffic_limit_strategy="NO_RESET",
        )
        return self.user

    async def reactivate_free_after_reset(self, user: PanelUser) -> PanelUser:
        self.reactivations += 1
        self.user = PanelUser(
            id=user.id,
            username=user.username,
            telegram_id=user.telegram_id,
            status="ACTIVE",
            expire_at=user.expire_at,
            subscription_url=user.subscription_url,
            tag=user.tag,
            traffic_used_bytes=user.traffic_used_bytes,
            traffic_lifetime_used_bytes=user.traffic_lifetime_used_bytes,
            traffic_limit_bytes=user.traffic_limit_bytes,
            last_traffic_reset_at=user.last_traffic_reset_at,
            traffic_limit_strategy="NO_RESET",
        )
        return self.user


class FailingRepairPanel(FakePanel):
    async def repair_free_access(self, user: PanelUser) -> PanelUser:
        raise PanelUnavailable("Panel update failed")


class FreeAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_feature_is_inert_until_explicitly_enabled(self) -> None:
        panel = FakePanel(
            PanelUser(
                id=1,
                username="tg_123",
                telegram_id=123,
                status="EXPIRED",
                expire_at="2026-01-01T00:00:00+00:00",
                subscription_url=None,
                tag="TRIAL",
            )
        )
        bot = FakeBot()
        with tempfile.TemporaryDirectory() as directory:
            prefs = PreferencesStore(Path(directory) / "state.sqlite3")
            prefs.remember(123)
            await process_free_access_once(
                bot,
                panel,
                prefs,
                settings(enabled=False),
                now=datetime(2026, 1, 2, tzinfo=UTC),
            )
        self.assertEqual(panel.lookups, 0)
        self.assertEqual(panel.free_grants, 0)
        self.assertEqual(bot.messages, [])

    async def test_report_rollover_waits_for_panel_daily_reset_and_is_deduplicated(self) -> None:
        user = PanelUser(
            id=2,
            username="tg_456",
            telegram_id=456,
            status="ACTIVE",
            expire_at="2099-01-01T00:00:00+00:00",
            subscription_url=None,
            tag="FREE",
            traffic_lifetime_used_bytes=700,
            traffic_limit_bytes=200 * 1024**2,
            last_traffic_reset_at="2026-01-02T00:05:00+00:00",
            traffic_limit_strategy="NO_RESET",
        )
        panel = FakePanel(user)
        bot = FakeBot()
        with tempfile.TemporaryDirectory() as directory:
            prefs = PreferencesStore(Path(directory) / "state.sqlite3")
            prefs.remember(456)
            prefs.set_timezone_offset(456, 5)
            prefs.initialize_free_daily(456, "2026-01-01", 500)
            await process_free_access_once(
                bot,
                panel,
                prefs,
                settings(enabled=True),
                now=datetime(2026, 1, 2, 0, 10, tzinfo=UTC),
            )
            await process_free_access_once(
                bot,
                panel,
                prefs,
                settings(enabled=True),
                now=datetime(2026, 1, 2, 0, 11, tzinfo=UTC),
            )
            self.assertEqual(
                prefs.free_daily_state(456), ("2026-01-02", 700)
            )
            self.assertEqual(prefs.pending_free_daily_reports(), [])
        self.assertEqual(len(bot.messages), 1)
        self.assertIn("Использовано: 200 Б", bot.messages[0][1])
        self.assertIn(
            "Период начался: 01.01.2026 05:00 [UTC+5]",
            bot.messages[0][1],
        )

    async def test_limited_free_user_is_reset_and_reactivated_once_per_utc_day(self) -> None:
        user = PanelUser(
            id=3,
            username="tg_789",
            telegram_id=789,
            status="LIMITED",
            expire_at="2099-01-01T00:00:00+00:00",
            subscription_url=None,
            tag="FREE",
            traffic_used_bytes=200 * 1024**2,
            traffic_lifetime_used_bytes=1500,
            traffic_limit_bytes=200 * 1024**2,
            traffic_limit_strategy="NO_RESET",
            last_traffic_reset_at="2026-01-01T00:05:00+00:00",
        )
        panel = FakePanel(user)
        bot = FakeBot()
        with tempfile.TemporaryDirectory() as directory:
            prefs = PreferencesStore(Path(directory) / "state.sqlite3")
            prefs.remember(789)
            prefs.initialize_free_daily(789, "2026-01-01", 1000)
            for _ in range(2):
                await process_free_access_once(
                    bot,
                    panel,
                    prefs,
                    settings(enabled=True),
                    now=datetime(2026, 1, 2, 0, 0, 1, tzinfo=UTC),
                )
            self.assertEqual(prefs.free_daily_state(789), ("2026-01-02", 1500))
            self.assertEqual(prefs.pending_free_daily_reports(), [])
        self.assertEqual(panel.resets, 1)
        self.assertEqual(panel.reactivations, 1)
        self.assertEqual(panel.user.status, "ACTIVE")
        self.assertEqual(len(bot.messages), 1)
        self.assertIn("Использовано: 500 Б", bot.messages[0][1])

    async def test_panel_repair_error_does_not_stop_processing_cycle(self) -> None:
        user = PanelUser(
            id=4,
            username="tg_1000",
            telegram_id=1000,
            status="ACTIVE",
            expire_at="2099-01-01T00:00:00+00:00",
            subscription_url=None,
            tag="FREE",
            traffic_limit_bytes=300 * 1024**2,
            traffic_limit_strategy="DAY",
        )
        panel = FailingRepairPanel(user)
        with tempfile.TemporaryDirectory() as directory:
            prefs = PreferencesStore(Path(directory) / "state.sqlite3")
            prefs.remember(1000)
            await process_free_access_once(
                FakeBot(),
                panel,
                prefs,
                settings(enabled=True),
                now=datetime(2026, 1, 2, 12, tzinfo=UTC),
            )
