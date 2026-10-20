from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiogram.exceptions import TelegramBadRequest

from jopesvpn.preferences import PreferencesStore
from jopesvpn.screens import (
    banner_for_callback,
    banner_path,
    edit_screen,
    strip_heading,
)


class CabinetKeyboardTests(unittest.TestCase):
    def test_trial_screen_links_all_official_resources_without_gating_them(self) -> None:
        from jopesvpn.ui import trial_keyboard, trial_text

        text = trial_text(3, "https://t.me/jopesvpn")
        self.assertIn("https://t.me/JopesVpnChat", text)
        self.assertIn("https://t.me/JopesVpnReviews", text)
        self.assertIn("проверяем только на канале", text)
        markup = trial_keyboard("https://t.me/jopesvpn")
        urls = {
            button.url
            for row in markup.inline_keyboard
            for button in row
            if button.url
        }
        self.assertIn("https://t.me/addlist/cEJd4DRCx25kMmEy", urls)
        self.assertIn("https://t.me/JopesVpnChat", urls)
        self.assertIn("https://t.me/JopesVpnReviews", urls)
        self.assertIn("https://t.me/jopesvpn", urls)

    def test_admin_users_list_has_search_action_and_never_zero_id_buttons(self) -> None:
        from jopesvpn.panel import PanelUser
        from jopesvpn.ui import admin_users_keyboard

        users = [
            PanelUser(1, "unlinked", None, "ACTIVE", None, None),
            PanelUser(2, "tg_12345", 12345, "ACTIVE", None, None),
        ]
        markup = admin_users_keyboard(users, 2, 5)
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data
        ]
        self.assertIn("admin:u:12345:2", callbacks)
        self.assertIn("admin:find:2", callbacks)
        self.assertFalse(any("admin:u:0:" in data for data in callbacks))

    def test_limited_traffic_shows_remaining_volume(self) -> None:
        from jopesvpn.panel import PanelUser
        from jopesvpn.ui import traffic_text

        user = PanelUser(
            1,
            "tg_1",
            1,
            "ACTIVE",
            None,
            None,
            traffic_used_bytes=4 * 1024**3,
            traffic_limit_bytes=10 * 1024**3,
        )
        text = traffic_text(user)
        self.assertIn("Осталось 6.0 ГБ из 10.0 ГБ", text)
        self.assertIn("использовано 4.0 ГБ", text)

    def test_inline_url_buttons_reject_unsupported_schemes(self) -> None:
        from jopesvpn.ui import _url_button

        with self.assertRaisesRegex(ValueError, "require an HTTPS URL"):
            _url_button("Import", "happ://add/https://example.test/sub")

    def test_subscription_page_button_is_url(self) -> None:
        from jopesvpn.ui import cabinet_keyboard

        url = "https://connect.example.test/abc"
        markup = cabinet_keyboard(url)
        first = markup.inline_keyboard[0][0]
        self.assertEqual(first.url, url)
        self.assertIsNone(first.callback_data)

    def test_subscription_page_button_absent_without_url(self) -> None:
        from jopesvpn.ui import cabinet_keyboard

        markup = cabinet_keyboard(None)
        for row in markup.inline_keyboard:
            for button in row:
                self.assertNotIsInstance(button.url, str)

    def test_home_primary_action_matches_access_state(self) -> None:
        from jopesvpn.ui import home_keyboard

        trial = home_keyboard(False, trial_available=True)
        self.assertEqual(trial.inline_keyboard[0][0].callback_data, "user:trial")
        self.assertNotIn("🔴", trial.inline_keyboard[0][0].text)

        onboarding = home_keyboard(False, onboarding=True)
        self.assertEqual(
            onboarding.inline_keyboard[0][0].callback_data, "user:setup"
        )

        active = home_keyboard(
            False,
            "https://connect.example.test/user",
            has_access=True,
        )
        button = active.inline_keyboard[0][0]
        self.assertEqual(button.url, "https://connect.example.test/user")
        self.assertIsNone(button.callback_data)
        self.assertEqual(active.inline_keyboard[1][0].callback_data, "user:cabinet")
        self.assertEqual(len(active.inline_keyboard), 4)

        expired = home_keyboard(False, trial_available=False)
        self.assertEqual(expired.inline_keyboard[0][0].callback_data, "user:plans")

    def test_more_menu_is_single_column_and_includes_user_sections(self) -> None:
        from jopesvpn.ui import more_keyboard, more_text

        markup = more_keyboard()
        self.assertTrue(all(len(row) == 1 for row in markup.inline_keyboard))
        text = more_text()
        self.assertNotIn("/devices", text)
        self.assertNotIn("/settings", text)
        callbacks = {
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        }
        self.assertIn("user:settings", callbacks)
        self.assertIn("user:stats", callbacks)
        self.assertIn("user:devices", callbacks)
        self.assertNotIn("user:subscription", callbacks)

    def test_cabinet_uses_png_purchase_artwork(self) -> None:
        from jopesvpn.ui import cabinet_keyboard, topup_methods_keyboard

        self.assertEqual(banner_path("purchase_success").suffix, ".png")
        callbacks = {
            button.callback_data
            for row in cabinet_keyboard().inline_keyboard
            for button in row
        }
        self.assertNotIn("user:subscription", callbacks)
        self.assertIn("user:topup", callbacks)
        payment_methods = topup_methods_keyboard(470).inline_keyboard
        self.assertEqual(payment_methods[0][0].callback_data, "topup:pay:stars")
        self.assertIn("470", payment_methods[0][0].text)

    def test_payment_history_displays_operation_id_and_method(self) -> None:
        from jopesvpn.ui import wallet_text
        from jopesvpn.wallet import PaymentTransaction

        transaction = PaymentTransaction(
            "payment-123",
            1001,
            470,
            "XTR",
            50_000,
            "telegram_stars",
            "topup",
            "SUCCESS",
            "2026-10-20T12:00:00+00:00",
        )
        text = wallet_text([transaction], None)
        self.assertIn("payment-123", text)
        self.assertIn("Telegram Stars", text)
        self.assertIn("500 ₽", text)

    def test_stars_catalog_hides_unsupported_traffic_plans(self) -> None:
        from jopesvpn.plans import Plan
        from jopesvpn.ui import home_keyboard, more_keyboard, plans_keyboard, plans_text

        self.assertIn(
            "user:about",
            {
                button.callback_data
                for row in home_keyboard(False).inline_keyboard
                for button in row
            },
        )
        self.assertIn(
            "user:about",
            {
                button.callback_data
                for row in more_keyboard().inline_keyboard
                for button in row
            },
        )

        plans = (
            Plan("week", "7 дней", 7, "", 10, "XTR"),
            Plan("data", "20 ГБ", 30, "", 20, "XTR", traffic_limit_gb=20),
        )
        text = plans_text(plans, category="time")
        self.assertIn("7 дней", text)
        self.assertNotIn("20 ГБ", text)
        self.assertIn("безлимит", text.casefold())
        callbacks = {
            button.callback_data
            for row in plans_keyboard(plans, category="time").inline_keyboard
            for button in row
        }
        self.assertIn("checkout:week", callbacks)
        self.assertNotIn("checkout:data", callbacks)
        enabled_text = plans_text(
            plans, traffic_plans_enabled=True, category="traffic"
        )
        self.assertIn("Пакеты на срок", enabled_text)
        enabled_callbacks = {
            button.callback_data
            for row in plans_keyboard(
                plans, traffic_plans_enabled=True, category="traffic"
            ).inline_keyboard
            for button in row
        }
        self.assertIn("user:plans:traffic:family:NO_RESET", enabled_callbacks)
        family_text = plans_text(
            plans,
            traffic_plans_enabled=True,
            category="traffic",
            traffic_family="NO_RESET",
        )
        self.assertIn("20 ГБ", family_text)
        family_callbacks = {
            button.callback_data
            for row in plans_keyboard(
                plans,
                traffic_plans_enabled=True,
                category="traffic",
                traffic_family="NO_RESET",
            ).inline_keyboard
            for button in row
        }
        self.assertIn("checkout:data", family_callbacks)
        menu_callbacks = {
            button.callback_data
            for row in plans_keyboard(plans).inline_keyboard
            for button in row
        }
        self.assertIn("user:plans:time", menu_callbacks)
        self.assertIn("user:plans:traffic", menu_callbacks)
        unavailable_text = plans_text(plans, category="traffic")
        self.assertIn("временно не подключена", unavailable_text)

    def test_user_tariff_uses_catalog_title_instead_of_panel_tag(self) -> None:
        from jopesvpn.panel import PanelUser
        from jopesvpn.ui import tariff_text

        user = PanelUser(
            1,
            "tg_1",
            1,
            "ACTIVE",
            None,
            None,
            tag="PLAN_DATA_100",
        )
        self.assertEqual(tariff_text(user), "Скачал весь интернет")
        self.assertNotIn("PLAN_DATA_100", tariff_text(user) or "")

    def test_language_picker_pins_current_language_and_paginates(self) -> None:
        from jopesvpn.i18n import LANGUAGE_FLAGS, SUPPORTED_LANGUAGES
        from jopesvpn.ui import language_keyboard, language_page_count, language_text

        with patch.dict(
            SUPPORTED_LANGUAGES,
            {f"xx{index}": f"Язык {index}" for index in range(9)},
        ), patch.dict(LANGUAGE_FLAGS, {f"xx{index}": "🌐" for index in range(9)}):
            self.assertEqual(language_page_count("ru"), 2)
            page_zero = language_keyboard("ru", 0).inline_keyboard
            page_one = language_keyboard("ru", 1).inline_keyboard
            self.assertIn("🇷🇺 Русский", page_zero[0][0].text)
            self.assertEqual(page_zero[-2][0].callback_data, "user:languages:1")
            self.assertEqual(page_one[-2][0].callback_data, "user:languages:0")
            self.assertIn("Страница 2 из 2", language_text("ru", 1, 2))

        from jopesvpn.i18n import SUPPORTED_LANGUAGES as languages

        self.assertEqual(list(languages), ["ru"])
        only_language = language_keyboard("ru").inline_keyboard
        self.assertIn("🇷🇺 Русский", only_language[0][0].text)
        self.assertEqual(len(only_language), 2)

    def test_free_plan_is_listed_only_when_available_and_has_confirm_step(self) -> None:
        from jopesvpn.plans import load_plans
        from jopesvpn.ui import plans_keyboard, plans_text

        text = plans_text(load_plans(), free_available=True)
        self.assertIn("200 МБ", text)
        callbacks = {
            button.callback_data
            for row in plans_keyboard(load_plans(), free_available=True).inline_keyboard
            for button in row
        }
        self.assertIn("user:plans:free", callbacks)

        free_text = plans_text(
            load_plans(), category="free", free_available=True
        )
        self.assertIn("обновляется по UTC", free_text)
        free_callbacks = {
            button.callback_data
            for row in plans_keyboard(
                load_plans(), category="free", free_available=True
            ).inline_keyboard
            for button in row
        }
        self.assertIn("user:plans:free:confirm", free_callbacks)

    def test_term_choice_and_about_use_real_catalog_and_no_fake_links(self) -> None:
        from jopesvpn.plans import load_plans
        from jopesvpn.ui import (
            about_keyboard,
            about_text,
            payment_methods_keyboard,
            plan_terms_keyboard,
        )

        plan = next(plan for plan in load_plans() if plan.code == "lite-1")
        term = plan.term_for(30)
        self.assertIsNotNone(term)
        term_callbacks = {
            button.callback_data
            for row in plan_terms_keyboard(plan).inline_keyboard
            for button in row
        }
        self.assertIn("term:lite-1:30", term_callbacks)
        term_labels = " ".join(
            button.text
            for row in plan_terms_keyboard(plan).inline_keyboard
            for button in row
        )
        self.assertIn("💸 42,64 ₽ | 30 дней", term_labels)
        self.assertIn("(−", term_labels)
        self.assertIn(
            "user:plans:traffic:family:NO_RESET",
            {
                button.callback_data
                for row in plan_terms_keyboard(plan).inline_keyboard
                for button in row
            },
        )
        payment_buttons = {
            button.callback_data: button.text
            for row in payment_methods_keyboard(plan, term).inline_keyboard
            for button in row
        }
        self.assertIn("pay:stars:lite-1:30", payment_buttons)
        self.assertNotIn("₽", payment_buttons["pay:stars:lite-1:30"])
        self.assertIn("pay:balance:lite-1:30", payment_buttons)
        self.assertIn("42,64 ₽", payment_buttons["pay:balance:lite-1:30"])
        self.assertIn("pay:unavailable:sbp:lite-1:30", payment_buttons)
        self.assertIn("pay:unavailable:card:lite-1:30", payment_buttons)
        self.assertIn("pay:unavailable:crypto:lite-1:30", payment_buttons)
        self.assertIn("42,64 ₽", payment_buttons["pay:unavailable:sbp:lite-1:30"])
        self.assertIn("42,64 ₽", payment_buttons["pay:unavailable:card:lite-1:30"])
        self.assertIn("42,64 ₽", payment_buttons["pay:unavailable:crypto:lite-1:30"])
        self.assertEqual(len(payment_buttons), 7)

        text = about_text(
            load_plans(),
            traffic_plans_enabled=False,
            free_access_enabled=False,
            documents_configured=False,
        )
        self.assertNotIn("10,66 ₽", text)
        self.assertIn("FREE", text)
        self.assertIn("не настроены", text)
        docs = about_keyboard(None, None)
        self.assertTrue(
            all(
                button.url is None
                for row in docs.inline_keyboard
                for button in row
            )
        )

    def test_purchase_and_trial_copy_matches_available_flows(self) -> None:
        from jopesvpn.ui import cabinet_text, connect_text

        cabinet = cabinet_text(
            None,
            telegram_id=123,
            trial_available=True,
        )
        connection = connect_text(
            None,
            None,
            trial_available=True,
        )

        self.assertIn("бесплатный пробный период", cabinet)
        self.assertIn("Выберите тариф в разделе", connection)
        self.assertNotIn("Тариф — через поддержку", connection)
        self.assertNotIn("Пробный период — 3 дня", connection)

    def test_cabinet_shows_ruble_wallet_balance(self) -> None:
        from jopesvpn.ui import cabinet_text

        cabinet = cabinet_text(
            None,
            telegram_id=123,
            balance_minor=12_345,
        )
        self.assertIn("Рублёвый баланс: 123,45 ₽", cabinet)

    def test_admin_user_card_has_direct_chat_button(self) -> None:
        from jopesvpn.ui import admin_user_keyboard

        with_username = admin_user_keyboard(1001, username="vpn_user")
        self.assertEqual(
            with_username.inline_keyboard[0][0].url,
            "https://t.me/vpn_user",
        )
        without_username = admin_user_keyboard(1001)
        self.assertEqual(
            without_username.inline_keyboard[0][0].url,
            "tg://user?id=1001",
        )


class BannerMappingTests(unittest.TestCase):
    def test_all_banner_files_exist(self) -> None:
        for data in ("user:home", "admin:home", "admin:list:0"):
            name = banner_for_callback(data)
            self.assertIsNotNone(name)
            self.assertTrue(banner_path(name).is_file())

    def test_user_screens_map_to_banners(self) -> None:
        expected = {
            "user:home": "home",
            "user:cabinet": "purchase_success",
            "user:profile": "purchase_success",
            "user:connect": "connect",
            "user:subscription": "purchase_success",
            "user:key": "purchase_success",
            "user:devices": "devices",
            "user:referrals": "stats",
            "dev:del:0": "devices",
            "user:stats": "stats",
            "user:settings": "settings",
            "set:rem:1": "settings",
            "user:plans": "plans",
            "term:lite-1:30": "plans",
            "trial:check": "plans",
            "user:guides": "guides",
            "guide:android": "guides",
            "platform:desktop": "guides",
            "user:help": "support",
            "user:troubleshoot": "support",
        }
        for data, banner in expected.items():
            self.assertEqual(banner_for_callback(data), banner, data)

    def test_admin_screens_map_to_admin_banners(self) -> None:
        self.assertEqual(banner_for_callback("admin:home"), "admin")
        self.assertEqual(banner_for_callback("admin:status"), "admin")
        self.assertEqual(banner_for_callback("admin:list:2"), "admin_users")
        self.assertEqual(banner_for_callback("admin:u:1001:0"), "admin_users")
        self.assertEqual(banner_for_callback("ticket:mine"), "support")

    def test_unknown_callback_has_no_banner(self) -> None:
        self.assertIsNone(banner_for_callback(None))
        self.assertIsNone(banner_for_callback("other:thing"))


class HeadingTests(unittest.TestCase):
    def test_uppercase_heading_is_removed(self) -> None:
        text = "<b>💎 ТАРИФЫ</b>\n\n<blockquote>└ body</blockquote>"
        self.assertEqual(strip_heading(text), "<blockquote>└ body</blockquote>")

    def test_mixed_case_heading_is_kept(self) -> None:
        text = "<b>Android</b> · Happ\n\nbody"
        self.assertEqual(strip_heading(text), text)

    def test_text_without_heading_is_kept(self) -> None:
        text = "Просто текст"
        self.assertEqual(strip_heading(text), text)


class BannerCacheTests(unittest.TestCase):
    def test_file_id_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PreferencesStore(Path(directory) / "state.sqlite3")
            self.assertIsNone(store.banner_file_id("home"))
            store.set_banner_file_id("home", "AgACAgIAAxkBAAI")
            self.assertEqual(store.banner_file_id("home"), "AgACAgIAAxkBAAI")
            store.set_banner_file_id("home", "new-file-id")
            self.assertEqual(store.banner_file_id("home"), "new-file-id")

    def test_empty_file_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PreferencesStore(Path(directory) / "state.sqlite3")
            with self.assertRaises(ValueError):
                store.set_banner_file_id("home", "")


class FakePrefs:
    def __init__(self) -> None:
        self._cache: dict[str, str] = {}
        self.cached = 0
        self.navigation = "home"

    def banner_file_id(self, name: str) -> str | None:
        self.cached += 1
        return self._cache.get(name)

    def set_banner_file_id(self, name: str, file_id: str) -> None:
        self._cache[name] = file_id

    def current_navigation(self, _telegram_id: int) -> str:
        return self.navigation


class FakeMessage:
    def __init__(self, photo=None, fail_media: bool = False) -> None:
        self.chat = SimpleNamespace(id=1001)
        self.photo = photo
        self.fail_media = fail_media
        self.edited: list[str] = []
        self.sent_photos: list[dict] = []
        self.sent_texts: list[str] = []
        self.deleted = False
        self.events: list[str] = []

    async def edit_text(self, text, **kwargs) -> None:
        self.edited.append(text)

    async def edit_media(self, media, **kwargs):
        if self.fail_media:
            raise TelegramBadRequest(method="editMessageMedia", message="cannot edit")
        self.edited.append(media.caption)
        self.events.append("edit_media")
        return SimpleNamespace(photo=[SimpleNamespace(file_id="edited-id")])

    async def delete(self) -> None:
        self.deleted = True
        self.events.append("delete")

    async def answer(self, text, **kwargs) -> None:
        self.sent_texts.append(text)
        self.events.append("answer_text")

    async def answer_photo(self, photo, caption, **kwargs):
        self.sent_photos.append({"caption": caption})
        self.events.append("answer_photo")
        return SimpleNamespace(photo=[SimpleNamespace(file_id="photo-id")])


class ScreenSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_screen_stays_text_without_store(self) -> None:
        message = FakeMessage()
        await edit_screen(
            message,
            "<b>💎 ТАРИФЫ</b>\n\nbody",
            None,
            data="user:plans",
            prefs=None,
        )
        self.assertEqual(message.edited, ["<b>💎 ТАРИФЫ</b>\n\nbody"])

    async def test_photo_screen_sends_banner_and_removes_heading(self) -> None:
        message = FakeMessage()
        prefs = FakePrefs()
        await edit_screen(
            message,
            "<b>💎 ТАРИФЫ</b>\n\nbody",
            None,
            data="user:plans",
            prefs=prefs,
        )
        self.assertTrue(message.deleted)
        self.assertEqual(message.sent_photos[0]["caption"], "body")
        self.assertEqual(prefs.banner_file_id("plans"), "photo-id")

    async def test_back_screen_uses_the_banner_for_the_navigation_target(self) -> None:
        message = FakeMessage()
        prefs = FakePrefs()
        prefs.navigation = "plans"
        await edit_screen(
            message,
            "<b>💎 ТАРИФЫ</b>\n\nbody",
            None,
            data="user:back",
            prefs=prefs,
        )
        self.assertEqual(prefs.banner_file_id("plans"), "photo-id")

    async def test_photo_message_is_edited_in_place(self) -> None:
        message = FakeMessage(photo=[SimpleNamespace(file_id="old")])
        prefs = FakePrefs()
        prefs.set_banner_file_id("plans", "cached-file-id")
        await edit_screen(
            message,
            "<b>💎 ТАРИФЫ</b>\n\nbody",
            None,
            data="user:plans",
            prefs=prefs,
        )
        self.assertFalse(message.deleted)
        self.assertEqual(message.edited, ["body"])

    async def test_failed_photo_edit_sends_replacement_before_deleting_old_screen(self) -> None:
        message = FakeMessage(
            photo=[SimpleNamespace(file_id="old")],
            fail_media=True,
        )
        prefs = FakePrefs()
        prefs.set_banner_file_id("subscription", "cached-file-id")
        await edit_screen(
            message,
            "<b>МОЯ ПОДПИСКА</b>\n\nДанные подписки",
            None,
            data="user:subscription",
            prefs=prefs,
        )
        self.assertEqual(message.events, ["answer_photo", "delete"])
        self.assertEqual(
            message.sent_photos[0]["caption"], "Данные подписки"
        )


if __name__ == "__main__":
    unittest.main()
