from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from jopesvpn.announcements import (
    ANNOUNCEMENT_CALLBACK_PREFIX,
    ANNOUNCEMENT_CAMPAIGN,
    CAMPAIGN_START_PROTOCOLS,
    connection_keyboard,
    preview_keyboard,
)
from jopesvpn.bot import (
    _send_announcement_preview,
    channel_post_approval,
    protocol_announcement_approval,
    receive_announcement_image,
    start,
)
from jopesvpn.config import Settings
from jopesvpn.panel import PanelNotFound
from jopesvpn.preferences import PreferencesStore
from jopesvpn.tickets import TicketStore


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int | str, str, dict[str, object]]] = []
        self.deleted: list[tuple[int, int]] = []

    async def get_me(self):
        return SimpleNamespace(username="jopesvpn_bot")

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        return SimpleNamespace(message_id=55)

    async def send_photo(self, chat_id, photo, caption, **kwargs):
        self.sent.append((chat_id, caption, {"photo": photo, **kwargs}))
        return SimpleNamespace(message_id=66)

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))


class AnnouncementTests(unittest.TestCase):
    def test_protocol_buttons_are_green_bot_deep_links(self) -> None:
        keyboard = connection_keyboard("jopesvpn_bot")
        buttons = [row[0] for row in keyboard.inline_keyboard]
        self.assertEqual(len(buttons), 3)
        self.assertTrue(all(button.style == "success" for button in buttons))
        parameters = {
            button.url.partition("start=")[2]
            for button in buttons
            if button.url is not None
        }
        self.assertEqual(parameters, set(CAMPAIGN_START_PROTOCOLS))

    def test_preview_adds_owner_approval_buttons(self) -> None:
        keyboard = preview_keyboard("jopesvpn_bot")
        self.assertEqual(len(keyboard.inline_keyboard), 5)
        self.assertEqual(
            [button.callback_data for button in keyboard.inline_keyboard[-1]],
            ["announcement:hy2:publish", "announcement:hy2:cancel"],
        )

    def test_announcement_approval_is_single_use_and_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            prefs = PreferencesStore(path)
            self.assertTrue(
                prefs.begin_announcement_preview(ANNOUNCEMENT_CAMPAIGN, 1001)
            )
            self.assertFalse(
                prefs.begin_announcement_preview(ANNOUNCEMENT_CAMPAIGN, 1001)
            )
            prefs.finish_announcement_preview(ANNOUNCEMENT_CAMPAIGN, 44)
            self.assertTrue(
                prefs.claim_announcement_publication(ANNOUNCEMENT_CAMPAIGN, 1001)
            )
            self.assertFalse(
                prefs.claim_announcement_publication(ANNOUNCEMENT_CAMPAIGN, 1001)
            )
            PreferencesStore(path).finish_announcement_publication(
                ANNOUNCEMENT_CAMPAIGN, 55
            )
            self.assertEqual(
                prefs.announcement_status(ANNOUNCEMENT_CAMPAIGN), "published"
            )
            self.assertFalse(
                prefs.claim_announcement_publication(ANNOUNCEMENT_CAMPAIGN, 1001)
            )

    def test_optional_announcement_image_is_saved_before_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prefs = PreferencesStore(Path(directory) / "state.sqlite3")
            self.assertTrue(
                prefs.begin_announcement_preview(ANNOUNCEMENT_CAMPAIGN, 1001)
            )
            prefs.finish_announcement_preview(ANNOUNCEMENT_CAMPAIGN, 44)
            self.assertTrue(prefs.begin_announcement_image(ANNOUNCEMENT_CAMPAIGN, 1001))
            self.assertEqual(
                prefs.claim_announcement_image(
                    ANNOUNCEMENT_CAMPAIGN, 1001, "telegram-file-id"
                ),
                44,
            )
            prefs.finish_announcement_image(ANNOUNCEMENT_CAMPAIGN, 45)
            self.assertEqual(
                prefs.announcement_photo(ANNOUNCEMENT_CAMPAIGN),
                "telegram-file-id",
            )
            self.assertTrue(
                prefs.claim_announcement_publication(ANNOUNCEMENT_CAMPAIGN, 1001)
            )
            self.assertFalse(
                prefs.begin_announcement_image(ANNOUNCEMENT_CAMPAIGN, 1001)
            )

    def test_campaign_click_is_logged_and_cleared_after_connection_view(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prefs = PreferencesStore(Path(directory) / "state.sqlite3")
            click_id = prefs.log_campaign_click(
                ANNOUNCEMENT_CAMPAIGN,
                "hysteria2",
                1002,
                "Test",
                "User",
                "test_user",
            )
            self.assertEqual(
                prefs.pending_campaign_click(1002), (click_id, "hysteria2")
            )
            prefs.finish_campaign_click(click_id)
            self.assertIsNone(prefs.pending_campaign_click(1002))


class AnnouncementFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_created_channel_post_publishes_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tickets = TicketStore(Path(directory) / "state.sqlite3")
            post_id = tickets.create_channel_post(
                1001,
                "<b>Тестовый пост</b>",
                [[{"text": "Открыть", "url": "https://example.com"}]],
                None,
                None,
            )
            self.assertTrue(tickets.claim_channel_post_preview(post_id))
            self.assertTrue(tickets.finish_channel_post_preview(post_id, 44))
            settings = Settings(
                bot_token="token",
                panel_api_url="https://panel.example.invalid",
                panel_api_token="panel-token",
                admin_telegram_ids=frozenset({1001}),
                trial_channel_username="officialchannel",
            )
            bot = FakeBot()
            callback = SimpleNamespace(
                data=f"channelpost:{post_id}:approve",
                from_user=SimpleNamespace(id=1001),
                message=None,
                answer=self._callback_answer,
            )

            await channel_post_approval(callback, bot, settings, tickets)
            self.assertEqual(tickets.get_channel_post(post_id)["status"], "published")
            self.assertEqual(len(bot.sent), 1)
            self.assertEqual(bot.sent[0][0], "@officialchannel")
            button = bot.sent[0][2]["reply_markup"].inline_keyboard[0][0]
            self.assertEqual(
                button.url,
                f"https://t.me/jopesvpn_bot?start=cp_{post_id}_0",
            )

            await channel_post_approval(callback, bot, settings, tickets)
            self.assertEqual(len(bot.sent), 1)

    async def _callback_answer(self, *args, **kwargs) -> None:
        return None

    async def test_campaign_deep_link_logs_click_and_shows_selected_connection(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prefs = PreferencesStore(Path(directory) / "state.sqlite3")
            settings = Settings(
                bot_token="token",
                panel_api_url="https://panel.example.invalid",
                panel_api_token="panel-token",
                admin_telegram_ids=frozenset({1001}),
            )
            bot = FakeBot()
            user = SimpleNamespace(
                id=2001,
                first_name="Visitor",
                last_name="Example",
                username="visitor",
            )
            answers: list[tuple[str, dict[str, object]]] = []

            async def answer(text: str, **kwargs) -> None:
                answers.append((text, kwargs))

            message = SimpleNamespace(
                chat=SimpleNamespace(type="private"),
                from_user=user,
                text="/start refc_hy2",
                answer=answer,
            )

            class Panel:
                async def get_user_by_telegram_id(self, telegram_id: int):
                    raise PanelNotFound("missing")

            await start(message, bot, Panel(), settings, prefs=prefs)

            self.assertIn("Hysteria 2", answers[0][0])
            self.assertEqual(prefs.pending_campaign_click(user.id), None)
            self.assertEqual(len(bot.sent), 1)
            self.assertIn("Telegram ID: 2001", bot.sent[0][1])

    async def test_owner_preview_and_approval_publish_once_with_deep_links(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prefs = PreferencesStore(Path(directory) / "state.sqlite3")
            settings = Settings(
                bot_token="token",
                panel_api_url="https://panel.example.invalid",
                panel_api_token="panel-token",
                admin_telegram_ids=frozenset({1001, 1002}),
                trial_channel_username="officialchannel",
                owner_telegram_id=1002,
            )
            bot = FakeBot()

            await _send_announcement_preview(bot, settings, prefs)
            self.assertEqual(len(bot.sent), 2)
            self.assertEqual(bot.sent[0][0], 1002)
            self.assertEqual(
                prefs.announcement_status(ANNOUNCEMENT_CAMPAIGN),
                "awaiting_approval",
            )
            await _send_announcement_preview(bot, settings, prefs)
            self.assertEqual(len(bot.sent), 2)

            answers: list[str] = []

            async def answer(text: str, **kwargs) -> None:
                answers.append(text)

            async def edit_reply_markup(**kwargs) -> None:
                return None

            callback = SimpleNamespace(
                from_user=SimpleNamespace(id=1002),
                data=f"{ANNOUNCEMENT_CALLBACK_PREFIX}:publish",
                message=SimpleNamespace(edit_reply_markup=edit_reply_markup),
                answer=answer,
            )
            await protocol_announcement_approval(callback, bot, settings, prefs)

            self.assertEqual(
                prefs.announcement_status(ANNOUNCEMENT_CAMPAIGN), "published"
            )
            channel_post = bot.sent[2]
            self.assertEqual(channel_post[0], "@officialchannel")
            self.assertEqual(len(channel_post[2]["reply_markup"].inline_keyboard), 3)
            self.assertIn("Пост опубликован.", answers)

            await protocol_announcement_approval(callback, bot, settings, prefs)
            self.assertEqual(len(bot.sent), 4)
            self.assertIn("Публикация уже обработана.", answers)

    async def test_owner_can_add_photo_then_publish_photo_post(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prefs = PreferencesStore(Path(directory) / "state.sqlite3")
            settings = Settings(
                bot_token="token",
                panel_api_url="https://panel.example.invalid",
                panel_api_token="panel-token",
                admin_telegram_ids=frozenset({1001}),
                trial_channel_username="officialchannel",
            )
            bot = FakeBot()

            class State:
                async def set_state(self, value) -> None:
                    return None

                async def clear(self) -> None:
                    return None

            state = State()
            await _send_announcement_preview(bot, settings, prefs)

            async def answer(text: str = "", **kwargs) -> None:
                return None

            async def edit_reply_markup(**kwargs) -> None:
                return None

            image_callback = SimpleNamespace(
                from_user=SimpleNamespace(id=1001),
                data=f"{ANNOUNCEMENT_CALLBACK_PREFIX}:image",
                message=SimpleNamespace(edit_reply_markup=edit_reply_markup),
                answer=answer,
            )
            await protocol_announcement_approval(
                image_callback, bot, settings, prefs, state
            )

            message_answers: list[str] = []

            async def message_answer(text: str, **kwargs) -> None:
                message_answers.append(text)

            image_message = SimpleNamespace(
                chat=SimpleNamespace(type="private"),
                from_user=SimpleNamespace(id=1001),
                photo=[SimpleNamespace(file_id="uploaded-file-id")],
                answer=message_answer,
            )
            await receive_announcement_image(image_message, bot, settings, prefs, state)
            self.assertEqual(
                prefs.announcement_photo(ANNOUNCEMENT_CAMPAIGN), "uploaded-file-id"
            )
            self.assertEqual(
                prefs.announcement_status(ANNOUNCEMENT_CAMPAIGN), "awaiting_approval"
            )
            self.assertEqual(bot.deleted, [(1001, 55)])

            publish_callback = SimpleNamespace(
                from_user=SimpleNamespace(id=1001),
                data=f"{ANNOUNCEMENT_CALLBACK_PREFIX}:publish",
                message=SimpleNamespace(edit_reply_markup=edit_reply_markup),
                answer=answer,
            )
            await protocol_announcement_approval(
                publish_callback, bot, settings, prefs, state
            )
            channel_post = bot.sent[-2]
            self.assertEqual(channel_post[0], "@officialchannel")
            self.assertEqual(channel_post[2]["photo"], "uploaded-file-id")
            self.assertTrue(message_answers)
