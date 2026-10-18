from __future__ import annotations

import tempfile
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path

from jopesvpn.ticket_ui import ticket_detail_text, ticket_list_keyboard
from jopesvpn.tickets import TicketStore


class TicketStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "tickets.sqlite3"
        self.store = TicketStore(self.path)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_ticket_lifecycle_and_one_time_rating(self) -> None:
        ticket_id = self.store.create(
            101,
            "Tester",
            "connection",
            "VPN не подключается",
            "telegram-photo-id",
        )
        self.assertEqual(len(self.store.messages(ticket_id)), 1)
        self.assertFalse(self.store.has_admin_reply(ticket_id))
        self.assertFalse(self.store.rate(ticket_id, 101, 5))

        self.assertTrue(
            self.store.add_message(ticket_id, 900, "admin", "Проверьте сеть.")
        )
        self.assertTrue(self.store.has_admin_reply(ticket_id))
        self.assertTrue(self.store.close(ticket_id, "admin"))
        self.assertFalse(self.store.add_message(ticket_id, 101, "user", "Ответ"))
        self.assertFalse(self.store.rate(ticket_id, 202, 5))
        self.assertTrue(self.store.rate(ticket_id, 101, 5))
        self.assertFalse(self.store.rate(ticket_id, 101, 4))

        ticket = self.store.get(ticket_id)
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["status"], "closed")
        self.assertEqual(ticket["rating"], 5)
        self.assertEqual(self.store.summary(), {"open": 0, "average": 5.0, "rated": 1})

    def test_ticket_schema_initialization_is_idempotent(self) -> None:
        store = TicketStore(Path(self.directory.name) / "tickets.sqlite3")
        ticket_id = store.create(101, "Tester", "other", "Message")
        self.assertEqual(store.get(ticket_id)["status"], "open")

    def test_closed_ticket_cannot_be_reopened_and_messages_return_in_order(self) -> None:
        ticket_id = self.store.create(101, "Tester", "other", "Первое")
        self.assertTrue(self.store.add_message(ticket_id, 101, "user", "Второе"))
        self.assertTrue(self.store.add_message(ticket_id, 900, "admin", "Третье"))
        self.assertEqual(
            [item["body"] for item in self.store.messages(ticket_id, limit=2)],
            ["Второе", "Третье"],
        )
        self.assertTrue(self.store.close(ticket_id, "user"))
        self.assertFalse(self.store.add_message(ticket_id, 101, "user", "Позже"))
        self.assertEqual(self.store.get(ticket_id)["status"], "closed")

    def test_close_prompt_is_due_once_after_twelve_hours_without_user_reply(self) -> None:
        ticket_id = self.store.create(101, "Tester", "other", "Первое")
        self.store.add_message(ticket_id, 900, "admin", "Ответ поддержки")
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                """
                UPDATE support_ticket_messages
                SET created_at = datetime('now', '-13 hours')
                WHERE ticket_id = ? AND sender_role = 'admin'
                """,
                (ticket_id,),
            )
            connection.commit()
        due = self.store.pending_close_prompts()
        self.assertEqual(len(due), 1)
        message_id = due[0]["message_id"]
        self.assertTrue(
            self.store.mark_close_prompt_attempted(ticket_id, message_id)
        )
        self.assertEqual(self.store.pending_close_prompts(), [])
        self.assertTrue(self.store.mark_close_prompt_sent(ticket_id, message_id))
        self.assertEqual(self.store.pending_close_prompts(), [])

        self.store.add_message(ticket_id, 900, "admin", "Ещё один ответ")
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                """
                UPDATE support_ticket_messages
                SET created_at = datetime('now', '-13 hours')
                WHERE ticket_id = ? AND sender_role = 'admin'
                  AND message_id = (
                      SELECT MAX(message_id) FROM support_ticket_messages
                      WHERE ticket_id = ? AND sender_role = 'admin'
                  )
                """,
                (ticket_id, ticket_id),
            )
            connection.commit()
        self.assertEqual(len(self.store.pending_close_prompts()), 1)

        self.store.add_message(ticket_id, 101, "user", "Спасибо")
        self.assertEqual(self.store.pending_close_prompts(), [])

    def test_open_queue_supports_stable_offset_pagination(self) -> None:
        first = self.store.create(101, "Первый", "other", "Сообщение 1")
        second = self.store.create(102, "Второй", "account", "Сообщение 2")
        self.assertEqual(
            [item["ticket_id"] for item in self.store.open_tickets(1, 0)],
            [first],
        )
        self.assertEqual(
            [item["ticket_id"] for item in self.store.open_tickets(1, 1)],
            [second],
        )

    def test_user_history_supports_offset_pagination(self) -> None:
        first = self.store.create(101, "Tester", "other", "Первое")
        second = self.store.create(101, "Tester", "account", "Второе")
        self.assertEqual(self.store.user_ticket_count(101), 2)
        self.assertEqual(
            [
                item["ticket_id"]
                for item in self.store.user_tickets(101, limit=1, offset=0)
            ],
            [second],
        )
        self.assertEqual(
            [
                item["ticket_id"]
                for item in self.store.user_tickets(101, limit=1, offset=1)
            ],
            [first],
        )


class TicketScreenTests(unittest.TestCase):
    def test_closed_ticket_offers_one_to_five_rating_and_no_reopen(self) -> None:
        from jopesvpn.ticket_ui import ticket_detail_keyboard

        markup = ticket_detail_keyboard(
            {"ticket_id": 7, "status": "closed", "rating": None},
            admin=False,
            has_admin_reply=True,
        )
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertEqual(
            callbacks[:5],
            [f"ticket:rate:7:{score}" for score in range(1, 6)],
        )
        self.assertFalse(any("reopen" in callback for callback in callbacks))

    def test_admin_queue_paginates_after_twenty_tickets(self) -> None:
        markup = ticket_list_keyboard(
            [{"ticket_id": 1, "user_name": "Tester", "category": "other"}],
            admin=True,
            page=0,
            total=21,
        )
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertIn("ticket:admin:list:1", callbacks)

    def test_user_history_paginates_after_ten_tickets(self) -> None:
        markup = ticket_list_keyboard(
            [{"ticket_id": 1, "category": "other"}],
            page=0,
            total=11,
        )
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertIn("ticket:mine:1", callbacks)

    def test_long_ticket_history_is_bounded_and_html_safe(self) -> None:
        ticket = {
            "ticket_id": 5,
            "category": "connection",
            "status": "open",
            "updated_at": "2026-10-17 12:00:00",
            "user_id": 101,
            "user_name": "Tester",
            "rating": None,
        }
        messages = [
            {
                "sender_role": "user",
                "body": "<script>&" * 2000,
                "photo_file_id": None,
                "created_at": "2026-10-17 12:00:00",
            }
            for _ in range(25)
        ]
        rendered = ticket_detail_text(
            ticket,
            messages,
            utc_offset=5,
            admin=True,
        )
        self.assertLessEqual(len(rendered), 4096)
        self.assertIn("UTC+5", rendered)
        self.assertIn("Показаны последние сообщения", rendered)
        self.assertNotIn("<script>", rendered)


if __name__ == "__main__":
    unittest.main()
