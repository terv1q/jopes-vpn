from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.methods import SendMessage

from jopesvpn.config import Settings
from jopesvpn.free_access import process_free_access_once
from jopesvpn.panel import PanelNotFound, PanelUnavailable, PanelUser
from jopesvpn.preferences import PreferencesError, PreferencesStore


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


class FailingBot(FakeBot):
    def __init__(self, error: TelegramAPIError) -> None:
        super().__init__()
        self.calls = 0
        self.error = error

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        self.calls += 1
        raise self.error


def free_user(telegram_id: int) -> PanelUser:
    return PanelUser(
        id=telegram_id,
        username=f"tg_{telegram_id}",
        telegram_id=telegram_id,
        status="ACTIVE",
        expire_at="2099-01-01T00:00:00+00:00",
        subscription_url=None,
        tag="FREE",
        traffic_lifetime_used_bytes=700,
        traffic_limit_bytes=200 * 1024**2,
        last_traffic_reset_at="2026-01-02T00:05:00+00:00",
        traffic_limit_strategy="NO_RESET",
    )


def report_row(
    path: Path, telegram_id: int, report_day: str
) -> tuple[int, str | None, str | None]:
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT attempts, abandoned_at, abandon_reason FROM free_daily_reports "
            "WHERE telegram_id = ? AND report_day = ?",
            (telegram_id, report_day),
        ).fetchone()
    assert row is not None
    return (
        int(row["attempts"]),
        row["abandoned_at"],
        row["abandon_reason"],
    )


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
        self.assertIn("Суточная квота FREE обновлена", bot.messages[0][1])
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

    async def test_blocked_user_report_is_closed_after_first_failure(self) -> None:
        panel = FakePanel(free_user(456))
        bot = FailingBot(
            TelegramForbiddenError(
                method=SendMessage(chat_id=456, text="probe"),
                message="Forbidden: bot was blocked by the user",
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            prefs = PreferencesStore(path)
            prefs.remember(456)
            prefs.initialize_free_daily(456, "2026-01-01", 500)
            await process_free_access_once(
                bot,
                panel,
                prefs,
                settings(enabled=True),
                now=datetime(2026, 1, 2, 0, 10, tzinfo=UTC),
            )
            self.assertEqual(prefs.pending_free_daily_reports(), [])
            attempts, abandoned_at, reason = report_row(path, 456, "2026-01-01")
            self.assertEqual(attempts, 1)
            self.assertIsNotNone(abandoned_at)
            self.assertEqual(reason, "blocked")

            with self.assertNoLogs("jopesvpn.free_access", level="WARNING"):
                await process_free_access_once(
                    bot,
                    panel,
                    prefs,
                    settings(enabled=True),
                    now=datetime(2026, 1, 2, 0, 15, tzinfo=UTC),
                )
            self.assertEqual(prefs.pending_free_daily_reports(), [])
            self.assertEqual(report_row(path, 456, "2026-01-01")[0], 1)
        self.assertEqual(bot.calls, 1)

    async def test_transient_failure_is_retried_then_closed(self) -> None:
        panel = FakePanel(free_user(457))
        bot = FailingBot(
            TelegramAPIError(
                method=SendMessage(chat_id=457, text="probe"),
                message="Bad Gateway",
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            prefs = PreferencesStore(path)
            prefs.remember(457)
            prefs.initialize_free_daily(457, "2026-01-01", 500)
            with self.assertLogs("jopesvpn.free_access", level="WARNING") as logs:
                for minute, calls in ((10, 1), (14, 1), (15, 2), (24, 2), (25, 3)):
                    prefs = PreferencesStore(path)
                    await process_free_access_once(
                        bot,
                        panel,
                        prefs,
                        settings(enabled=True),
                        now=datetime(2026, 1, 2, 0, minute, tzinfo=UTC),
                    )
                    self.assertEqual(bot.calls, calls)
            self.assertEqual(len(logs.records), 2)
            self.assertEqual(prefs.pending_free_daily_reports(), [])
            attempts, abandoned_at, reason = report_row(path, 457, "2026-01-01")
            self.assertEqual(attempts, 3)
            self.assertIsNotNone(abandoned_at)
            self.assertEqual(reason, "TelegramAPIError")

            with self.assertNoLogs("jopesvpn.free_access", level="WARNING"):
                await process_free_access_once(
                    bot,
                    panel,
                    prefs,
                    settings(enabled=True),
                    now=datetime(2026, 1, 2, 0, 30, tzinfo=UTC),
                )
        self.assertEqual(bot.calls, 3)

    async def test_permanent_failure_closes_all_reports_for_the_recipient(self) -> None:
        failures = (
            (TelegramBadRequest, "Bad Request: chat not found", "chat_not_found"),
            (TelegramForbiddenError, "Forbidden: user is deactivated", "deactivated"),
            (TelegramForbiddenError, "Forbidden", "forbidden"),
        )
        for error_type, message, reason in failures:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "state.sqlite3"
                prefs = PreferencesStore(path)
                prefs.remember(456)
                prefs.initialize_free_daily(456, "2025-12-30", 100)
                for previous, current in (
                    ("2025-12-30", "2025-12-31"),
                    ("2025-12-31", "2026-01-01"),
                    ("2026-01-01", "2026-01-02"),
                ):
                    prefs.rollover_free_daily(456, previous, current, 100, 700)
                bot = FailingBot(
                    error_type(method=SendMessage(chat_id=456, text="probe"), message=message)
                )
                with self.assertNoLogs("jopesvpn.free_access", level="WARNING"):
                    await process_free_access_once(
                        bot,
                        FakePanel(free_user(456)),
                        prefs,
                        settings(enabled=True),
                        now=datetime(2026, 1, 2, 0, 10, tzinfo=UTC),
                    )
                self.assertEqual(bot.calls, 1)
                self.assertEqual(prefs.pending_free_daily_reports(), [])
                for day in ("2025-12-30", "2025-12-31", "2026-01-01"):
                    attempts, abandoned_at, actual_reason = report_row(path, 456, day)
                    self.assertEqual(attempts, int(day == "2025-12-30"))
                    self.assertIsNotNone(abandoned_at)
                    self.assertEqual(actual_reason, reason)

    async def test_successful_retry_records_attempt_and_clears_delay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            prefs = PreferencesStore(path)
            prefs.remember(456)
            prefs.initialize_free_daily(456, "2026-01-01", 500)
            panel = FakePanel(free_user(456))
            failed_bot = FailingBot(
                TelegramAPIError(method=SendMessage(chat_id=456, text="probe"), message="Bad Gateway")
            )
            start = datetime(2026, 1, 2, 0, 10, tzinfo=UTC)
            await process_free_access_once(failed_bot, panel, prefs, settings(enabled=True), now=start)
            delivered_bot = FakeBot()
            await process_free_access_once(
                delivered_bot, panel, prefs, settings(enabled=True), now=start + timedelta(minutes=5)
            )
            self.assertEqual(len(delivered_bot.messages), 1)
            self.assertEqual(prefs.pending_free_daily_reports(), [])
            self.assertEqual(report_row(path, 456, "2026-01-01"), (2, None, None))
            with closing(sqlite3.connect(path)) as connection:
                row = connection.execute(
                    "SELECT sent_at, last_attempt_at, next_attempt_at FROM free_daily_reports"
                ).fetchone()
            self.assertEqual(row, ("2026-01-02T00:15:00+00:00", "2026-01-02T00:15:00+00:00", None))

    async def test_rate_limit_pauses_queue_across_restart_and_new_reports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            prefs = PreferencesStore(path)
            panel = AsyncMock()
            panel.get_user_by_telegram_id.side_effect = free_user
            for telegram_id in (456, 457):
                prefs.remember(telegram_id)
                prefs.initialize_free_daily(telegram_id, "2026-01-01", 500)
            limited_bot = FailingBot(
                TelegramRetryAfter(
                    method=SendMessage(chat_id=456, text="probe"),
                    message="Too Many Requests", retry_after=900,
                )
            )
            start = datetime(2026, 1, 2, 0, 10, tzinfo=UTC)
            await process_free_access_once(limited_bot, panel, prefs, settings(enabled=True), now=start)
            self.assertEqual(limited_bot.calls, 1)
            self.assertEqual(report_row(path, 457, "2026-01-01")[0], 0)
            prefs = PreferencesStore(path)
            delivered_bot = FakeBot()
            await process_free_access_once(
                delivered_bot, panel, prefs, settings(enabled=True), now=start + timedelta(minutes=5)
            )
            prefs.remember(458)
            prefs.initialize_free_daily(458, "2026-01-01", 500)
            await process_free_access_once(
                delivered_bot, panel, prefs, settings(enabled=True), now=start + timedelta(minutes=10)
            )
            self.assertEqual(delivered_bot.messages, [])
            self.assertEqual(prefs.pending_free_daily_reports(now=start + timedelta(minutes=10)), [])
            await process_free_access_once(
                delivered_bot, panel, prefs, settings(enabled=True), now=start + timedelta(minutes=15)
            )
            self.assertEqual([chat_id for chat_id, _ in delivered_bot.messages], [456, 457, 458])
            self.assertEqual(prefs.pending_free_daily_reports(), [])

    async def test_rate_limit_does_not_exhaust_delivery_retries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            prefs = PreferencesStore(path)
            prefs.remember(456)
            prefs.initialize_free_daily(456, "2026-01-01", 500)
            panel = FakePanel(free_user(456))
            failed_bot = FailingBot(
                TelegramAPIError(
                    method=SendMessage(chat_id=456, text="probe"), message="Bad Gateway"
                )
            )
            start = datetime(2026, 1, 2, 0, 10, tzinfo=UTC)
            for minutes in (0, 5):
                await process_free_access_once(
                    failed_bot, panel, prefs, settings(enabled=True),
                    now=start + timedelta(minutes=minutes),
                )
            limited_bot = FailingBot(
                TelegramRetryAfter(
                    method=SendMessage(chat_id=456, text="probe"),
                    message="Too Many Requests", retry_after=300,
                )
            )
            for minutes in (15, 20, 25, 30):
                prefs = PreferencesStore(path)
                with self.assertNoLogs("jopesvpn.free_access", level="WARNING"):
                    await process_free_access_once(
                        limited_bot, panel, prefs, settings(enabled=True),
                        now=start + timedelta(minutes=minutes),
                    )
                self.assertEqual(report_row(path, 456, "2026-01-01"), (2, None, None))
                self.assertEqual(
                    prefs.pending_free_daily_reports(now=start + timedelta(minutes=minutes + 4)),
                    [],
                )
            delivered_bot = FakeBot()
            await process_free_access_once(
                delivered_bot, panel, prefs, settings(enabled=True),
                now=start + timedelta(minutes=35),
            )
            self.assertEqual(limited_bot.calls, 4)
            self.assertEqual(len(delivered_bot.messages), 1)
            self.assertEqual(report_row(path, 456, "2026-01-01"), (3, None, None))
            self.assertEqual(prefs.pending_free_daily_reports(), [])

    def test_shorter_rate_limit_does_not_reduce_existing_pause(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            prefs = PreferencesStore(path)
            prefs.initialize_free_daily(456, "2026-01-01", 500)
            prefs.rollover_free_daily(456, "2026-01-01", "2026-01-02", 100, 600)
            start = datetime(2026, 1, 2, 0, 10, tzinfo=UTC)
            prefs.defer_free_daily_reports(900, now=start)
            prefs = PreferencesStore(path)
            prefs.defer_free_daily_reports(60, now=start + timedelta(minutes=5))
            self.assertEqual(prefs.pending_free_daily_reports(now=start + timedelta(minutes=14)), [])
            self.assertEqual(
                prefs.pending_free_daily_reports(now=start + timedelta(minutes=15)),
                [(456, "2026-01-01", 100)],
            )

    async def test_rate_limit_delay_starts_when_telegram_responds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            prefs = PreferencesStore(path)
            prefs.remember(456)
            prefs.initialize_free_daily(456, "2026-01-01", 500)
            start = datetime(2026, 1, 2, 0, 10, tzinfo=UTC)
            limited_bot = FailingBot(
                TelegramRetryAfter(
                    method=SendMessage(chat_id=456, text="probe"),
                    message="Too Many Requests", retry_after=900,
                )
            )
            with patch("jopesvpn.free_access.datetime", wraps=datetime) as clock:
                clock.now.side_effect = (
                    start,
                    start + timedelta(minutes=10),
                    start + timedelta(minutes=12),
                )
                await process_free_access_once(
                    limited_bot, FakePanel(free_user(456)), prefs, settings(enabled=True)
                )
            self.assertEqual(prefs.pending_free_daily_reports(now=start + timedelta(minutes=26)), [])
            self.assertEqual(
                prefs.pending_free_daily_reports(now=start + timedelta(minutes=27)),
                [(456, "2026-01-01", 200)],
            )

    async def test_missing_panel_user_state_is_purged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            prefs = PreferencesStore(path)
            prefs.remember(456)
            prefs.initialize_free_daily(456, "2026-01-01", 500)
            prefs.rollover_free_daily(456, "2026-01-01", "2026-01-02", 100, 600)
            panel = AsyncMock()
            panel.get_user_by_telegram_id.side_effect = PanelNotFound("User missing")
            bot = FakeBot()
            await process_free_access_once(
                bot, panel, prefs, settings(enabled=True), now=datetime(2026, 1, 2, 12, tzinfo=UTC)
            )
            self.assertIsNone(prefs.free_daily_state(456))
            self.assertEqual(bot.messages, [])
            self.assertEqual(report_row(path, 456, "2026-01-01")[2], "not_free")

    async def test_orphaned_free_state_and_reports_are_reconciled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            prefs = PreferencesStore(path)
            for telegram_id in (456, 457):
                prefs.initialize_free_daily(telegram_id, "2026-01-01", 500)
                prefs.rollover_free_daily(telegram_id, "2026-01-01", "2026-01-02", 100, 600)
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("DELETE FROM free_daily_state WHERE telegram_id = 457")
                connection.commit()
            self.assertEqual(prefs.known_users(), [])
            panel = AsyncMock()
            panel.get_user_by_telegram_id.side_effect = PanelNotFound("User missing")
            bot = FakeBot()
            await process_free_access_once(
                bot, panel, prefs, settings(enabled=True),
                now=datetime(2026, 1, 2, 12, tzinfo=UTC),
            )
            self.assertEqual(
                [call.args[0] for call in panel.get_user_by_telegram_id.await_args_list],
                [456, 457],
            )
            self.assertEqual(bot.messages, [])
            for telegram_id in (456, 457):
                self.assertIsNone(prefs.free_daily_state(telegram_id))
                self.assertEqual(report_row(path, telegram_id, "2026-01-01")[2], "not_free")
            self.assertEqual(prefs.pending_free_daily_reports(), [])

    async def test_panel_outage_preserves_free_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prefs = PreferencesStore(Path(directory) / "state.sqlite3")
            prefs.remember(456)
            prefs.initialize_free_daily(456, "2026-01-01", 500)
            panel = AsyncMock()
            panel.get_user_by_telegram_id.side_effect = PanelUnavailable("Unavailable")
            with self.assertLogs("jopesvpn.free_access", level="WARNING"):
                await process_free_access_once(
                    FakeBot(), panel, prefs, settings(enabled=True),
                    now=datetime(2026, 1, 2, 12, tzinfo=UTC),
                )
            self.assertEqual(prefs.free_daily_state(456), ("2026-01-01", 500))

    def test_free_cleanup_rolls_back_if_reports_cannot_be_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            prefs = PreferencesStore(path)
            prefs.initialize_free_daily(456, "2026-01-01", 500)
            prefs.rollover_free_daily(456, "2026-01-01", "2026-01-02", 100, 600)
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "CREATE TRIGGER reject_report_abandon BEFORE UPDATE OF abandoned_at "
                    "ON free_daily_reports BEGIN SELECT RAISE(ABORT, 'forced failure'); END"
                )
                connection.commit()
            with self.assertRaises(PreferencesError):
                prefs.purge_free_daily_state(456)
            self.assertEqual(prefs.free_daily_state(456), ("2026-01-02", 600))
            self.assertEqual(prefs.pending_free_daily_reports(), [(456, "2026-01-01", 100)])

    def test_existing_reports_survive_additive_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "CREATE TABLE free_daily_reports (telegram_id INTEGER NOT NULL, "
                    "report_day TEXT NOT NULL, used_bytes INTEGER, "
                    "created_at TEXT NOT NULL DEFAULT (datetime('now')), sent_at TEXT, "
                    "PRIMARY KEY (telegram_id, report_day))"
                )
                connection.executemany(
                    "INSERT INTO free_daily_reports (telegram_id, report_day, used_bytes, sent_at) "
                    "VALUES (?, ?, ?, ?)",
                    ((456, "2026-01-01", 100, None), (457, "2026-01-01", 200, "2026-01-02")),
                )
                connection.commit()
            for _ in range(2):
                prefs = PreferencesStore(path)
                self.assertEqual(prefs.pending_free_daily_reports(), [(456, "2026-01-01", 100)])
                self.assertEqual(report_row(path, 456, "2026-01-01"), (0, None, None))
                with closing(sqlite3.connect(path)) as connection:
                    sent_at = connection.execute(
                        "SELECT sent_at FROM free_daily_reports WHERE telegram_id = 457"
                    ).fetchone()[0]
                self.assertEqual(sent_at, "2026-01-02")

    async def test_state_of_user_who_left_free_is_purged(self) -> None:
        user = free_user(458)
        paid = PanelUser(
            id=user.id,
            username=user.username,
            telegram_id=user.telegram_id,
            status="ACTIVE",
            expire_at="2026-02-01T00:00:00+00:00",
            subscription_url=None,
            tag="PLAN_UNLIMITED_7",
            traffic_lifetime_used_bytes=700,
            traffic_limit_bytes=0,
            last_traffic_reset_at="2026-01-02T00:05:00+00:00",
            traffic_limit_strategy="NO_RESET",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            prefs = PreferencesStore(path)
            prefs.remember(458)
            prefs.initialize_free_daily(458, "2026-01-01", 500)
            prefs.rollover_free_daily(458, "2026-01-01", "2026-01-02", 100, 600)
            await process_free_access_once(
                FakeBot(),
                FakePanel(paid),
                prefs,
                settings(enabled=True),
                now=datetime(2026, 1, 2, 12, tzinfo=UTC),
            )
            self.assertIsNone(prefs.free_daily_state(458))
            self.assertEqual(prefs.pending_free_daily_reports(), [])
            _, abandoned_at, reason = report_row(path, 458, "2026-01-01")
            self.assertIsNotNone(abandoned_at)
            self.assertEqual(reason, "not_free")
