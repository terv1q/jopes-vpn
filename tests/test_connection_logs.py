from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from aiogram.exceptions import TelegramAPIError
from aiogram.methods import SendMessage

from jopesvpn.config import Settings
from jopesvpn.connection_logs import baseline, check_once
from jopesvpn.panel import PanelDevice, PanelUser
from jopesvpn.preferences import PreferencesStore
from jopesvpn.ui import (
    admin_log_keyboard,
    admin_logs_keyboard,
    admin_user_keyboard,
)


def _user(**overrides) -> PanelUser:
    values = {
        "id": 41,
        "username": "tg_123456",
        "telegram_id": 123456,
        "status": "ACTIVE",
        "expire_at": "2030-01-01T00:00:00.000Z",
        "subscription_url": "https://sub.example.invalid/short",
        "tag": "ADMIN",
        "online_at": "2026-10-17T10:00:00.000Z",
    }
    values.update(overrides)
    return PanelUser(**values)


def _device(hwid: str = "device-hwid-1", ip: str = "203.0.113.7") -> PanelDevice:
    return PanelDevice(
        hwid=hwid,
        platform="android",
        os_version="14",
        device_model="Pixel",
        created_at="2026-10-01T00:00:00Z",
        updated_at="2026-10-17T10:00:05Z",
        request_ip=ip,
        user_agent="Happ/2.0 (Android 14)",
    )


class FakePanel:
    def __init__(self, user: PanelUser, devices: list[PanelDevice]) -> None:
        self.user = user
        self.devices = devices

    async def get_user_by_telegram_id(self, telegram_id: int) -> PanelUser:
        return self.user

    async def get_user_devices(self, user: PanelUser) -> list[PanelDevice]:
        return list(self.devices)


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.fail_next_send = False

    async def get_chat(self, chat_id: int):
        return SimpleNamespace(username="tester")

    async def send_message(self, **kwargs) -> None:
        if self.fail_next_send:
            self.fail_next_send = False
            raise TelegramAPIError(
                method=SendMessage(chat_id=kwargs["chat_id"], text=kwargs["text"]),
                message="temporary failure",
            )
        self.messages.append(kwargs)


def _settings() -> Settings:
    return Settings(
        bot_token="123:test-token",
        panel_api_url="https://panel.example.invalid",
        panel_api_token="token",
        admin_telegram_ids=frozenset({99}),
    )


class ConnectionLogStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = PreferencesStore(Path(self.directory.name) / "state.sqlite3")

    def test_logs_toggle_roundtrip(self) -> None:
        self.assertFalse(self.store.admin_logs_enabled())
        self.store.set_admin_logs_enabled(True)
        self.assertTrue(self.store.admin_logs_enabled())
        self.store.set_admin_logs_enabled(False)
        self.assertFalse(self.store.admin_logs_enabled())

    def test_endpoint_remains_deduplicated_after_inactivity(self) -> None:
        now = datetime(2026, 10, 17, 12, tzinfo=UTC)
        self.assertTrue(
            self.store.register_endpoint(41, 123456, "hwid", "1.2.3.4", now=now.isoformat())
        )
        self.assertTrue(
            self.store.register_endpoint(
                41,
                123456,
                "hwid",
                "1.2.3.4",
                now=(now + timedelta(minutes=5)).isoformat(),
            )
        )
        self.store.mark_endpoint_notified(
            41,
            "hwid",
            "1.2.3.4",
            notified_at=(now + timedelta(minutes=5)).isoformat(),
        )
        stale = now + timedelta(days=2)
        self.assertFalse(
            self.store.register_endpoint(
                41, 123456, "hwid", "1.2.3.4", now=stale.isoformat()
            )
        )

    def test_endpoint_notification_stays_pending_until_marked_delivered(self) -> None:
        now = datetime(2026, 10, 17, 12, tzinfo=UTC).isoformat()
        self.assertTrue(
            self.store.register_endpoint(41, 123456, "hwid", "1.2.3.4", now=now)
        )
        self.assertTrue(
            self.store.register_endpoint(
                41, 123456, "hwid", "1.2.3.4", now=now
            )
        )
        self.store.mark_endpoint_notified(
            41, "hwid", "1.2.3.4", notified_at=now
        )
        self.assertFalse(
            self.store.register_endpoint(
                41, 123456, "hwid", "1.2.3.4", now=now
            )
        )

    def test_existing_endpoint_rows_migrate_as_already_notified(self) -> None:
        path = Path(self.directory.name) / "legacy.sqlite3"
        with closing(sqlite3.connect(path)) as connection:
            connection.execute(
                """
                CREATE TABLE connection_log_endpoints (
                    panel_user_id INTEGER NOT NULL,
                    telegram_id INTEGER,
                    hwid TEXT NOT NULL,
                    ip TEXT NOT NULL DEFAULT '',
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY (panel_user_id, hwid, ip)
                )
                """
            )
            connection.execute(
                """
                INSERT INTO connection_log_endpoints
                VALUES (41, 123456, 'old-hwid', '1.2.3.4', ?, ?)
                """,
                ("2026-10-17T12:00:00+00:00", "2026-10-17T12:00:00+00:00"),
            )
            connection.commit()
        migrated = PreferencesStore(path)
        self.assertFalse(
            migrated.register_endpoint(
                41,
                123456,
                "old-hwid",
                "1.2.3.4",
                now="2026-10-17T12:01:00+00:00",
            )
        )

    def test_accounts_on_ip_are_deduplicated(self) -> None:
        now = datetime.now(UTC).isoformat()
        self.store.register_endpoint(41, 123456, "a", "1.2.3.4", now=now)
        self.store.register_endpoint(41, 123456, "b", "1.2.3.4", now=now)
        self.store.register_endpoint(42, 654321, "c", "1.2.3.4", now=now)
        self.store.register_endpoint(43, 777777, "d", "5.6.7.8", now=now)
        self.assertEqual(self.store.accounts_on_ip("1.2.3.4"), [123456, 654321])

    def test_state_keeps_last_log_time_when_updating_online_at(self) -> None:
        logged = "2026-10-17T12:00:00+00:00"
        self.store.save_connection_state(41, 123456, "old", logged_at=logged)
        self.store.save_connection_state(41, 123456, "new")
        state = self.store.connection_state(41)
        assert state is not None
        self.assertEqual(state["online_at"], "new")
        self.assertEqual(state["last_logged_at"], logged)


class ConnectionLogCheckTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = PreferencesStore(Path(self.directory.name) / "state.sqlite3")
        self.store.remember(123456, "Tester")
        self.bot = FakeBot()
        self.settings = _settings()

    async def test_baseline_is_silent_and_new_device_is_logged_once(self) -> None:
        device = _device()
        panel = FakePanel(_user(), [device])
        await baseline(panel, self.store)
        self.assertEqual(self.bot.messages, [])

        panel.devices = [device, _device("device-hwid-2", "198.51.100.9")]
        sent = await check_once(self.bot, self.settings, panel, self.store)
        self.assertEqual(sent, 1)
        self.assertEqual(len(self.bot.messages), 1)
        message = self.bot.messages[0]
        self.assertEqual(message["chat_id"], 99)
        self.assertIn("device-hwid-2", message["text"])
        self.assertIn("198.51.100.9", message["text"])
        self.assertIn("<code>", message["text"])
        self.assertIsNotNone(message["reply_markup"])

        again = await check_once(self.bot, self.settings, panel, self.store)
        self.assertEqual(again, 0)
        self.assertEqual(len(self.bot.messages), 1)

    async def test_online_at_refresh_does_not_create_false_reconnect_event(self) -> None:
        device = _device()
        old_online = "2026-10-15T10:00:00.000Z"
        user = _user(online_at=old_online)
        panel = FakePanel(user, [device])
        now = datetime.now(UTC)
        self.store.register_endpoint(
            user.id,
            123456,
            device.hwid,
            device.request_ip or "",
            now=now.isoformat(),
        )
        self.store.mark_endpoint_notified(
            user.id,
            device.hwid,
            device.request_ip or "",
            notified_at=now.isoformat(),
        )
        panel.user = replace(
            user, online_at="2026-10-17T11:00:00.000Z"
        )
        sent = await check_once(self.bot, self.settings, panel, self.store)
        self.assertEqual(sent, 0)
        self.assertEqual(self.bot.messages, [])

    async def test_failed_telegram_delivery_is_retried_without_duplicate_state(self) -> None:
        device = _device()
        panel = FakePanel(_user(), [])
        await baseline(panel, self.store)
        panel.devices = [device]
        self.bot.fail_next_send = True

        self.assertEqual(
            await check_once(self.bot, self.settings, panel, self.store), 0
        )
        self.assertEqual(self.bot.messages, [])
        self.assertEqual(
            await check_once(self.bot, self.settings, panel, self.store), 1
        )
        self.assertEqual(len(self.bot.messages), 1)
        self.assertEqual(
            await check_once(self.bot, self.settings, panel, self.store), 0
        )
        self.assertEqual(len(self.bot.messages), 1)

    async def test_same_ip_warning_is_included(self) -> None:
        self.store.remember(654321, "Second")
        device = _device()
        panel = FakePanel(_user(), [device])
        await baseline(panel, self.store)
        second = _user(id=42, telegram_id=654321, username="tg_654321")
        original = panel.get_user_by_telegram_id

        async def by_id(telegram_id: int) -> PanelUser:
            if telegram_id == 654321:
                return second
            return await original(telegram_id)

        panel.get_user_by_telegram_id = by_id  # type: ignore[method-assign]
        panel.devices = [device]
        await baseline(panel, self.store)
        self.bot.messages.clear()
        panel.devices = [device, _device("device-hwid-3", "203.0.113.7")]
        await check_once(self.bot, self.settings, panel, self.store)
        texts = "\n".join(message["text"] for message in self.bot.messages)
        self.assertIn("мультиаккаунт", texts)


class AdminLogNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_logs_screen_toggles_setting(self) -> None:
        from jopesvpn.bot import admin_navigation

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        store = PreferencesStore(Path(directory.name) / "state.sqlite3")
        texts: list[str] = []

        async def edit_text(text: str, **kwargs) -> None:
            texts.append(text)

        async def answer(text: str = "", **kwargs) -> None:
            return None

        message = SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            edit_text=edit_text,
            answer=answer,
        )

        def callback(data: str):
            return SimpleNamespace(
                data=data,
                message=message,
                from_user=SimpleNamespace(id=99),
                answer=answer,
            )

        class State:
            async def clear(self) -> None:
                return None

        await admin_navigation(
            callback("admin:logs"), None, None, _settings(), State(), prefs=store
        )
        self.assertIn("Логи выключены", texts[-1])
        await admin_navigation(
            callback("admin:logs:on"),
            None,
            FakePanel(_user(), []),
            _settings(),
            State(),
            prefs=store,
        )
        self.assertTrue(store.admin_logs_enabled())
        self.assertIn("Логи включены", texts[-1])
        await admin_navigation(
            callback("admin:logs:off"),
            None,
            FakePanel(_user(), []),
            _settings(),
            State(),
            prefs=store,
        )
        self.assertFalse(store.admin_logs_enabled())


class AdminLogUiTests(unittest.TestCase):
    def test_logs_toggle_keyboard(self) -> None:
        enabled_buttons = admin_logs_keyboard(True)
        self.assertEqual(
            enabled_buttons.inline_keyboard[0][0].callback_data, "admin:logs:off"
        )
        disabled_buttons = admin_logs_keyboard(False)
        self.assertEqual(
            disabled_buttons.inline_keyboard[0][0].callback_data, "admin:logs:on"
        )

    def test_log_keyboard_has_profile_page_and_device_actions(self) -> None:
        markup = admin_log_keyboard(
            123456, "ACTIVE", "https://connect.example.invalid/short"
        )
        flat = [button for row in markup.inline_keyboard for button in row]
        urls = [button.url for button in flat if button.url]
        callbacks = [
            button.callback_data for button in flat if button.callback_data
        ]
        self.assertEqual(urls, ["https://connect.example.invalid/short"])
        self.assertIn("admin:u:123456:0", callbacks)
        self.assertIn("admin:devices:123456:0", callbacks)
        self.assertIn("admin:confirm:disable:123456", callbacks)
        self.assertIn("admin:logs:off", callbacks)
        self.assertTrue(
            all(len(value.encode()) <= 64 for value in callbacks)
        )

    def test_admin_card_page_button_is_a_url(self) -> None:
        markup = admin_user_keyboard(
            123456, "ACTIVE", page_url="https://connect.example.invalid/short"
        )
        buttons = [button for row in markup.inline_keyboard for button in row]
        chat_button = next(
            button for button in buttons if button.url == "tg://user?id=123456"
        )
        page_button = next(
            button
            for button in buttons
            if button.url == "https://connect.example.invalid/short"
        )
        self.assertIsNone(chat_button.callback_data)
        self.assertEqual(page_button.url, "https://connect.example.invalid/short")


if __name__ == "__main__":
    unittest.main()
