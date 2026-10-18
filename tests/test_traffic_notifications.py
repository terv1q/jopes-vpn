from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jopesvpn.panel import PanelNotFound, PanelUser
from jopesvpn.preferences import PreferencesStore
from jopesvpn.traffic_notifications import process_traffic_notifications_once


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []
        self.failures = 0

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        if self.failures:
            self.failures -= 1
            from aiogram.exceptions import TelegramNetworkError

            raise TelegramNetworkError(
                method=None, message="temporary failure"
            )
        self.messages.append((chat_id, text))


class FakePanel:
    def __init__(self, user: PanelUser | None) -> None:
        self.user = user

    async def get_user_by_telegram_id(self, telegram_id: int) -> PanelUser:
        if self.user is None or self.user.telegram_id != telegram_id:
            raise PanelNotFound("not found")
        return self.user


def _user(
    used: int,
    *,
    status: str = "ACTIVE",
    reset_at: str | None = None,
) -> PanelUser:
    return PanelUser(
        id=10,
        username="tg_42",
        telegram_id=42,
        status=status,
        expire_at="2099-01-01T00:00:00+00:00",
        subscription_url=None,
        tag="PLAN_DATA_10",
        traffic_used_bytes=used,
        traffic_limit_bytes=1000,
        traffic_limit_strategy="NO_RESET",
        last_traffic_reset_at=reset_at,
    )


class TrafficNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_thresholds_send_once_for_each_plan_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prefs = PreferencesStore(Path(directory) / "state.sqlite3")
            prefs.remember(42)
            panel = FakePanel(_user(1000, status="LIMITED"))
            bot = FakeBot()

            self.assertEqual(
                await process_traffic_notifications_once(bot, panel, prefs), 3
            )
            self.assertEqual(
                await process_traffic_notifications_once(bot, panel, prefs), 0
            )
            self.assertEqual(len(bot.messages), 3)
            self.assertTrue(
                any("Панель ограничила доступ" in text for _, text in bot.messages)
            )

            panel.user = _user(760, reset_at="2026-10-18T00:00:00+00:00")
            self.assertEqual(
                await process_traffic_notifications_once(bot, panel, prefs), 2
            )
            self.assertEqual(len(bot.messages), 5)

    async def test_failed_delivery_is_retried(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prefs = PreferencesStore(Path(directory) / "state.sqlite3")
            prefs.remember(42)
            panel = FakePanel(_user(500))
            bot = FakeBot()
            bot.failures = 1

            self.assertEqual(
                await process_traffic_notifications_once(bot, panel, prefs), 0
            )
            self.assertEqual(
                await process_traffic_notifications_once(bot, panel, prefs), 1
            )
            self.assertEqual(len(bot.messages), 1)


if __name__ == "__main__":
    unittest.main()
