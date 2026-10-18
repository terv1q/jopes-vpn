from __future__ import annotations

import unittest

from jopesvpn.bot import _send_ticket_update


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.photos: list[dict] = []

    async def send_message(self, **kwargs) -> None:
        self.messages.append(kwargs)

    async def send_photo(self, **kwargs) -> None:
        self.photos.append(kwargs)


class TicketNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_ticket_message_is_not_truncated(self) -> None:
        bot = FakeBot()
        body = "x" * 4000
        delivered = await _send_ticket_update(
            bot,
            1001,
            "N" * 150,
            body,
            None,
            "ticket:admin:view:1",
        )
        self.assertTrue(delivered)
        self.assertEqual(len(bot.messages), 2)
        self.assertEqual(bot.messages[1]["text"], body)

    async def test_photo_and_caption_are_delivered_separately(self) -> None:
        bot = FakeBot()
        delivered = await _send_ticket_update(
            bot,
            1001,
            "New ticket",
            "Text attached to screenshot",
            "telegram-photo-id",
            "ticket:admin:view:1",
        )
        self.assertTrue(delivered)
        self.assertEqual(bot.photos[0]["caption"], "New ticket")
        self.assertEqual(bot.messages[0]["text"], "Text attached to screenshot")

    async def test_ticket_text_is_html_escaped_before_delivery(self) -> None:
        bot = FakeBot()
        await _send_ticket_update(
            bot,
            1001,
            "New ticket",
            "<b>not markup</b>",
            None,
            "ticket:admin:view:1",
        )
        self.assertIn("&lt;b&gt;", bot.messages[0]["text"])


if __name__ == "__main__":
    unittest.main()
