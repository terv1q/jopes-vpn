from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aiogram.exceptions import TelegramAPIError

from jopesvpn.broadcasts import (
    ACTIVE,
    ACTIVE_INACTIVE_24H,
    ALL,
    DELIVERY_SENT,
    DRAFT,
    INACTIVE_24H,
    NEVER_CONNECTED,
    SUBSCRIBED,
    BroadcastButton,
    BroadcastError,
    BroadcastStore,
    activity_state,
    resolve_audience,
    send_broadcast,
    validate_button,
)
from jopesvpn.panel import PanelDevice, PanelNotFound, PanelUnavailable, PanelUser
from jopesvpn.preferences import PreferencesStore


NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def user(
    telegram_id: int,
    *,
    status: str = "ACTIVE",
    online_at: str | None = None,
    first_connected_at: str | None = None,
    traffic_used: int | None = None,
) -> PanelUser:
    return PanelUser(
        id=telegram_id + 100_000,
        username=f"tg_{telegram_id}",
        telegram_id=telegram_id,
        status=status,
        expire_at="2030-01-01T00:00:00Z",
        subscription_url="https://panel.example.invalid/sub",
        online_at=online_at,
        last_connected_at=first_connected_at,
        traffic_used_bytes=traffic_used,
    )


class FakePanel:
    def __init__(
        self,
        users: dict[int, PanelUser],
        devices: dict[int, list[PanelDevice]] | None = None,
        failures: set[int] | None = None,
    ) -> None:
        self.users = users
        self.devices = devices or {}
        self.failures = failures or set()

    async def get_user_by_telegram_id(self, telegram_id: int) -> PanelUser:
        if telegram_id in self.failures:
            raise PanelUnavailable("offline")
        if telegram_id not in self.users:
            raise PanelNotFound("missing")
        return self.users[telegram_id]

    async def get_user_devices(self, user: PanelUser) -> list[PanelDevice]:
        if user.telegram_id in self.failures:
            raise PanelUnavailable("offline")
        return self.devices.get(user.telegram_id or 0, [])


class BroadcastStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "state.sqlite3"
        self.prefs = PreferencesStore(self.path)
        self.store = BroadcastStore(self.path)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_draft_can_be_edited_and_listed(self) -> None:
        draft = self.store.create(1001, "Первый текст")
        self.assertEqual(draft.status, DRAFT)
        self.assertEqual(draft.audience, ALL)
        self.store.update_text(draft.id, "Новый текст")
        self.store.set_audience(draft.id, ACTIVE)
        self.store.set_buttons(
            draft.id,
            [
                BroadcastButton("Подключиться", "https://t.me/JopesVPNBot"),
                BroadcastButton("Поддержка", "https://t.me/JopesVPNBot"),
            ],
        )
        loaded = self.store.get(draft.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.text, "Новый текст")
        self.assertEqual(loaded.audience, ACTIVE)
        self.assertEqual(len(loaded.buttons), 2)
        self.assertEqual([item.id for item in self.store.list_recent()], [draft.id])

    def test_text_and_button_limits_are_enforced(self) -> None:
        with self.assertRaises(BroadcastError):
            self.store.create(1001, "")
        draft = self.store.create(1001, "Текст")
        with self.assertRaises(BroadcastError):
            self.store.update_text(draft.id, "x" * 4097)
        with self.assertRaises(BroadcastError):
            validate_button("Кнопка", "http://example.com")
        with self.assertRaises(BroadcastError):
            validate_button("", "https://example.com")
        with self.assertRaises(BroadcastError):
            validate_button("Кнопка", "https://user:pass@example.com")
        with self.assertRaises(BroadcastError):
            self.store.set_audience(draft.id, "unknown")
        with self.assertRaises(BroadcastError):
            self.store.set_buttons(
                draft.id,
                [
                    BroadcastButton(str(index), "https://example.com")
                    for index in range(9)
                ],
            )

    def test_delivery_status_is_idempotent(self) -> None:
        draft = self.store.create(1001, "Текст")
        self.store.record_delivery(draft.id, 2002, DELIVERY_SENT)
        self.store.record_delivery(draft.id, 2002, DELIVERY_SENT)
        self.assertEqual(self.store.delivered_ids(draft.id), {2002})
        self.assertEqual(self.store.delivery_counts(draft.id), (1, 0))

    def test_delete_removes_deliveries(self) -> None:
        draft = self.store.create(1001, "Текст")
        self.store.record_delivery(draft.id, 2002, DELIVERY_SENT)
        self.store.delete(draft.id)
        self.assertIsNone(self.store.get(draft.id))
        self.assertEqual(self.store.delivery_counts(draft.id), (0, 0))


class AudienceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.prefs = PreferencesStore(Path(self.tempdir.name) / "state.sqlite3")
        for telegram_id in (2002, 2003, 2004, 2005, 2006):
            self.prefs.register_start(telegram_id)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    async def test_all_excludes_admins_and_needs_no_panel(self) -> None:
        panel = FakePanel({})
        result = await resolve_audience(
            panel, self.prefs, ALL, exclude={2006}
        )
        self.assertEqual(result.recipients, [2002, 2003, 2004, 2005])
        self.assertEqual(result.errors, 0)

    async def test_subscribed_and_active_follow_panel_status(self) -> None:
        panel = FakePanel(
            {
                2002: user(2002, status="ACTIVE"),
                2003: user(2003, status="EXPIRED"),
            }
        )
        subscribed = await resolve_audience(panel, self.prefs, SUBSCRIBED)
        self.assertEqual(subscribed.recipients, [2002, 2003])
        active = await resolve_audience(panel, self.prefs, ACTIVE)
        self.assertEqual(active.recipients, [2002])

    async def test_never_connected_uses_connection_and_traffic_signals(self) -> None:
        panel = FakePanel(
            {
                2002: user(2002),
                2003: user(2003, traffic_used=1024),
                2004: user(2004),
                2005: user(2005),
            },
            devices={2004: [PanelDevice("hwid", None, None, None, None, None)]},
        )
        self.prefs.register_endpoint(
            2005 + 100_000,
            2005,
            "hwid",
            "203.0.113.5",
            now=NOW.isoformat(),
        )
        result = await resolve_audience(
            panel, self.prefs, NEVER_CONNECTED, now=NOW
        )
        self.assertEqual(result.recipients, [2002, 2004, 2005])

    async def test_inactive_includes_never_and_old_connections_only(self) -> None:
        old = (NOW - timedelta(hours=30)).isoformat()
        recent = (NOW - timedelta(hours=2)).isoformat()
        panel = FakePanel(
            {
                2002: user(2002),
                2003: user(2003, online_at=old),
                2004: user(2004, online_at=recent),
                2005: user(2005, first_connected_at=old),
            }
        )
        result = await resolve_audience(
            panel, self.prefs, INACTIVE_24H, now=NOW
        )
        self.assertEqual(result.recipients, [2002, 2003, 2005])

    async def test_active_inactive_requires_active_status(self) -> None:
        recent = (NOW - timedelta(hours=1)).isoformat()
        panel = FakePanel(
            {
                2002: user(2002, status="ACTIVE"),
                2003: user(2003, status="EXPIRED"),
                2004: user(2004, status="ACTIVE", online_at=recent),
            }
        )
        result = await resolve_audience(
            panel, self.prefs, ACTIVE_INACTIVE_24H, now=NOW
        )
        self.assertEqual(result.recipients, [2002])

    async def test_panel_failures_are_counted_and_skipped(self) -> None:
        panel = FakePanel(
            {2002: user(2002)},
            failures={2003},
        )
        result = await resolve_audience(panel, self.prefs, SUBSCRIBED)
        self.assertEqual(result.recipients, [2002])
        self.assertEqual(result.errors, 1)

    def test_activity_uses_latest_known_signal(self) -> None:
        old = (NOW - timedelta(hours=40)).isoformat()
        recent = (NOW - timedelta(minutes=10)).isoformat()
        panel_user = user(2002, online_at=old)
        device = PanelDevice("hwid", None, None, None, None, recent)
        connected, last = activity_state(panel_user, [device], None)
        self.assertTrue(connected)
        self.assertEqual(last, datetime.fromisoformat(recent))

    def test_traffic_means_connection_without_timestamps(self) -> None:
        connected, last = activity_state(user(2002, traffic_used=10), [], None)
        self.assertTrue(connected)
        self.assertIsNone(last)


class SendBroadcastTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = BroadcastStore(Path(self.tempdir.name) / "state.sqlite3")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    async def test_duplicate_recipients_are_not_messaged_twice(self) -> None:
        draft = self.store.create(1001, "<b>Текст</b>")
        self.store.set_buttons(
            draft.id, [BroadcastButton("Сайт", "https://example.com")]
        )
        draft = self.store.get(draft.id)
        sent: list[tuple[int, object]] = []

        class Bot:
            async def send_message(self, chat_id, text, **kwargs):
                sent.append((chat_id, kwargs.get("reply_markup")))

        result = await send_broadcast(Bot(), self.store, draft, [2002, 2003])
        self.assertEqual(result.delivered, 2)
        self.assertEqual([item[0] for item in sent], [2002, 2003])
        self.assertIsNotNone(sent[0][1])
        again = await send_broadcast(Bot(), self.store, draft, [2002, 2003])
        self.assertEqual(again.delivered, 0)
        self.assertEqual(again.skipped, 2)

    async def test_failed_delivery_is_retried_on_next_launch(self) -> None:
        draft = self.store.create(1001, "Текст")

        class FlakyBot:
            def __init__(self) -> None:
                self.calls = 0

            async def send_message(self, chat_id, text, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise TelegramAPIError(method="sendMessage", message="blocked")

        bot = FlakyBot()
        first = await send_broadcast(bot, self.store, draft, [2002])
        self.assertEqual(first.failed, 1)
        second = await send_broadcast(bot, self.store, draft, [2002])
        self.assertEqual(second.delivered, 1)
        self.assertEqual(self.store.delivery_counts(draft.id), (1, 0))


if __name__ == "__main__":
    unittest.main()
