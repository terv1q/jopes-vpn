from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.types import Chat, FSInputFile, InlineKeyboardButton, Message, User
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendAnimation

from jopesvpn.bot import (
    _documents_consent_keyboard,
    _documents_consent_text,
    _answer_start,
    _start_keyboard,
    _start_text,
    start,
)
from jopesvpn.config import Settings
from jopesvpn.panel import PanelNotFound
from jopesvpn.preferences import PreferencesStore
from jopesvpn.screens import strip_heading
from jopesvpn.screens import edit_screen
from jopesvpn.ui import home_keyboard


ANIMATION = Path(__file__).resolve().parents[1] / "jopesvpn/assets/banners/start-welcome.gif"


def animation_cache_key() -> str:
    return f"start-animation-v3:{sha256(ANIMATION.read_bytes()).hexdigest()}"


class StartKeyboardTests(unittest.TestCase):
    def test_aiogram_serializes_official_decoration_fields(self) -> None:
        for style in ("primary", "success", "danger"):
            with self.subTest(style=style):
                button = InlineKeyboardButton(
                    text="VPN",
                    callback_data="user:setup",
                    icon_custom_emoji_id="5368324170671202286",
                    style=style,
                )
                payload = button.model_dump(exclude_none=True)
                self.assertEqual(payload["style"], style)
                self.assertEqual(payload["icon_custom_emoji_id"], "5368324170671202286")
                self.assertIn("style", InlineKeyboardButton.model_fields)
                self.assertIn("icon_custom_emoji_id", InlineKeyboardButton.model_fields)

    def test_all_home_actions_and_urls_survive_without_changing_shared_keyboard(self) -> None:
        for admin in (False, True):
            for options in (
                {},
                {"onboarding": True},
                {"trial_available": True},
                {"has_access": True},
                {"has_access": True, "connect_url": "https://example.com/sub?ref=123"},
            ):
                with self.subTest(admin=admin, options=options):
                    original = home_keyboard(admin, **options)
                    before = original.model_dump()
                    decorated = _start_keyboard(original)
                    self.assertEqual(original.model_dump(), before)
                    self.assertEqual(len(original.inline_keyboard), len(decorated.inline_keyboard))
                    for old_row, new_row in zip(original.inline_keyboard, decorated.inline_keyboard):
                        self.assertEqual(len(old_row), len(new_row))
                        for old, new in zip(old_row, new_row):
                            self.assertEqual(old.callback_data, new.callback_data)
                            self.assertEqual(old.url, new.url)
                            self.assertIn(new.style, {"primary", "success", "danger"})
                            self.assertIsNone(new.icon_custom_emoji_id)
                            self.assertIn(new.text.split()[0], {"ϟ", "✓", "?", "ⓘ", "≡", "⌂", "↗", "⚙\ufe0e"})
                            self.assertTrue(new.text.endswith(old.text.partition(" ")[2] or old.text))
                    if admin:
                        self.assertEqual(decorated.inline_keyboard[-1][0].style, "danger")

    def test_welcome_escapes_user_name_and_survives_banner_heading_stripping(self) -> None:
        text = _start_text('<Test & "user">', "Existing screen")
        self.assertIn('&lt;Test &amp; &quot;user&quot;&gt;', text)
        self.assertNotIn("<tg-emoji", text)
        self.assertIn("Jopes VPN", strip_heading(text))
        self.assertTrue(text.endswith("Existing screen"))


class StartHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_decorates_consent_and_home_and_preserves_referral(self) -> None:
        for consented in (False, True):
            with self.subTest(consented=consented), tempfile.TemporaryDirectory() as directory:
                prefs = PreferencesStore(Path(directory) / "state.sqlite3")
                prefs.set_document_url("privacy", "https://example.com/privacy")
                prefs.set_document_url("agreement", "https://example.com/agreement")
                prefs.register_start(123)
                if consented:
                    prefs.accept_documents(123, prefs.documents_version())
                settings = Settings(
                    bot_token="test-token",
                    panel_api_url="https://panel.example.test",
                    panel_api_token="test-token",
                    admin_telegram_ids=frozenset({999}),
                )
                message = Message(
                    message_id=1, date=datetime.now(UTC),
                    chat=Chat(id=123, type="private"),
                    from_user=User(id=123, is_bot=False, first_name="Test <&>"),
                    text="/start ref_456",
                )
                bot = AsyncMock()
                panel = AsyncMock()
                panel.get_user_by_telegram_id.side_effect = PanelNotFound("missing")
                with patch("jopesvpn.bot._answer_start", new_callable=AsyncMock) as render:
                    await start(message, bot, panel, settings, prefs=prefs)
                render.assert_awaited_once()
                text, markup = render.call_args.args[1:]
                self.assertIn("Добро пожаловать в Jopes VPN", text)
                self.assertIn("Test &lt;&amp;&gt;", text)
                self.assertLessEqual(len(text), 1024)
                callbacks = {b.callback_data for row in markup.inline_keyboard for b in row}
                if consented:
                    self.assertIn("user:setup", callbacks)
                    self.assertEqual(render.call_args.kwargs["banner"], "home")
                    panel.get_user_by_telegram_id.assert_awaited_once_with(123)
                else:
                    self.assertIn(f"docs:accept:{prefs.documents_version()}", callbacks)
                    self.assertIn(_documents_consent_text(prefs), text)
                    self.assertEqual(render.call_args.kwargs["banner"], "support")
                    panel.get_user_by_telegram_id.assert_not_awaited()
                    original = _documents_consent_keyboard(prefs)
                    self.assertTrue(all(b.style is None for row in original.inline_keyboard for b in row))
                    self.assertFalse(prefs.documents_accepted(123, prefs.documents_version()))
                with closing(prefs._connect()) as connection:
                    referrer = connection.execute(
                        "SELECT referrer_id FROM referrals WHERE referred_id = ?", (123,)
                    ).fetchone()
                self.assertEqual(referrer[0], 456)
                self.assertEqual(bot.send_message.call_args.kwargs["chat_id"], 456)


class StartAnimationTests(unittest.IsolatedAsyncioTestCase):
    async def test_animation_upload_is_cached_and_reused_with_original_markup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prefs = PreferencesStore(Path(directory) / "state.sqlite3")
            message = SimpleNamespace(answer_animation=AsyncMock(
                return_value=SimpleNamespace(animation=SimpleNamespace(file_id="telegram-animation-id"))
            ))
            markup = _start_keyboard(home_keyboard(False))
            await _answer_start(message, "Welcome", markup, prefs=prefs, banner="home")
            self.assertEqual(prefs.banner_file_id(animation_cache_key()), "telegram-animation-id")
            self.assertIsInstance(message.answer_animation.call_args.kwargs["animation"], FSInputFile)
            self.assertEqual(message.answer_animation.call_args.kwargs["caption"], "Welcome")
            self.assertIs(message.answer_animation.call_args.kwargs["reply_markup"], markup)
            await _answer_start(message, "Welcome again", markup, prefs=prefs, banner="home")
            self.assertEqual(message.answer_animation.call_args.kwargs["animation"], "telegram-animation-id")

    async def test_rejected_animation_falls_back_to_working_start_screen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prefs = PreferencesStore(Path(directory) / "state.sqlite3")
            prefs.set_banner_file_id(animation_cache_key(), "stale-animation-id")
            message = SimpleNamespace(answer_animation=AsyncMock(side_effect=TelegramBadRequest(
                method=SendAnimation(chat_id=123, animation="invalid"), message="Invalid animation"
            )))
            markup = _start_keyboard(home_keyboard(False))
            with patch("jopesvpn.bot.answer_screen", new_callable=AsyncMock) as fallback:
                await _answer_start(message, "Welcome", markup, prefs=prefs, banner="home")
            self.assertEqual(message.answer_animation.await_count, 2)
            fallback.assert_awaited_once_with(message, "Welcome", markup, prefs=prefs, banner="home")

    async def test_previous_versions_cannot_replace_updated_asset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prefs = PreferencesStore(Path(directory) / "state.sqlite3")
            for key in ("start-animation-v2", "start-animation-v3", "start-animation-v3:old-sha256"):
                prefs.set_banner_file_id(key, "previous-animation-id")
            message = SimpleNamespace(answer_animation=AsyncMock(
                return_value=SimpleNamespace(animation=SimpleNamespace(file_id="updated-animation-id"))
            ))
            await _answer_start(message, "Welcome", home_keyboard(False), prefs=prefs, banner="home")
            self.assertIsInstance(message.answer_animation.call_args.kwargs["animation"], FSInputFile)
            self.assertEqual(prefs.banner_file_id(animation_cache_key()), "updated-animation-id")

    async def test_rejected_cache_uploads_file_and_replaces_stale_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prefs = PreferencesStore(Path(directory) / "state.sqlite3")
            prefs.set_banner_file_id(animation_cache_key(), "stale-animation-id")
            message = SimpleNamespace(answer_animation=AsyncMock(side_effect=[
                TelegramBadRequest(method=SendAnimation(chat_id=123, animation="stale-animation-id"), message="Wrong file identifier"),
                SimpleNamespace(animation=SimpleNamespace(file_id="replacement-animation-id")),
            ]))
            markup = home_keyboard(False)
            with patch("jopesvpn.bot.answer_screen", new_callable=AsyncMock) as fallback:
                await _answer_start(message, "Welcome", markup, prefs=prefs, banner="home")
            fallback.assert_not_awaited()
            self.assertEqual(message.answer_animation.await_args_list[0].kwargs["animation"], "stale-animation-id")
            self.assertIsInstance(message.answer_animation.await_args_list[1].kwargs["animation"], FSInputFile)
            self.assertEqual(prefs.banner_file_id(animation_cache_key()), "replacement-animation-id")

    async def test_long_caption_preserves_full_text_in_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prefs = PreferencesStore(Path(directory) / "state.sqlite3")
            message = SimpleNamespace(answer_animation=AsyncMock())
            text = "A" * 1025
            markup = home_keyboard(False)
            with patch("jopesvpn.bot.answer_screen", new_callable=AsyncMock) as fallback:
                await _answer_start(message, text, markup, prefs=prefs, banner="home")
            message.answer_animation.assert_not_awaited()
            fallback.assert_awaited_once_with(message, text, markup, prefs=prefs, banner="home")

    async def test_animation_message_transitions_to_existing_screens(self) -> None:
        for data in ("docs:accept:3", "user:help", "user:setup"):
            with self.subTest(data=data):
                message = SimpleNamespace(
                    animation=SimpleNamespace(file_id="animation"), photo=None,
                    answer=AsyncMock(), delete=AsyncMock(), edit_text=AsyncMock(),
                )
                with patch("jopesvpn.screens._can_use_banner", return_value=False):
                    await edit_screen(message, "Next screen", None, data=data)
                message.answer.assert_awaited_once_with("Next screen", reply_markup=None, parse_mode="HTML")
                message.delete.assert_awaited_once()
                message.edit_text.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
