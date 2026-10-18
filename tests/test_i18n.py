from __future__ import annotations

import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from jopesvpn.bot import LanguageMiddleware
from jopesvpn.i18n import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    current_language,
    normalize_language,
    set_language,
    t,
    tn,
)
from jopesvpn.i18n import ru
from jopesvpn.preferences import PreferencesStore


EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿⬀-⯿️]"
)


def _catalog_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _catalog_strings(item)


class CatalogTests(unittest.TestCase):
    def test_translation_returns_catalog_value(self) -> None:
        self.assertEqual(t("common.cabinet"), "👤 Личный кабинет")

    def test_unknown_key_falls_back_to_key(self) -> None:
        self.assertEqual(t("missing.key"), "missing.key")

    def test_interpolation(self) -> None:
        self.assertEqual(
            t("common.page_of", page=2, pages=5),
            "Страница: 2 из 5",
        )

    def test_russian_plural_forms(self) -> None:
        self.assertEqual(tn("plural.days", 1), "1 день")
        self.assertEqual(tn("plural.days", 3), "3 дня")
        self.assertEqual(tn("plural.days", 11), "11 дней")
        self.assertEqual(tn("plural.days", 21), "21 день")
        self.assertEqual(
            t("admin.extend_for", days=72, days_word=tn("plural.days", 72)),
            "Продлить на: 72 дня",
        )

    def test_unsupported_language_normalizes_to_default(self) -> None:
        self.assertEqual(normalize_language("en"), DEFAULT_LANGUAGE)
        self.assertEqual(normalize_language(None), DEFAULT_LANGUAGE)

    def test_supported_language_names_are_declared(self) -> None:
        self.assertIn(DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES)

    def test_catalog_has_no_italics(self) -> None:
        found = [
            text
            for text in _catalog_strings(ru.MESSAGES)
            if "<i>" in text or "</i>" in text
        ]
        self.assertEqual(found, [])

    def test_catalog_has_emoji_in_main_navigation(self) -> None:
        for key in (
            "common.menu",
            "common.back",
            "common.cabinet",
            "common.connect",
            "common.devices",
            "common.stats",
        ):
            self.assertIsNotNone(EMOJI.search(str(ru.MESSAGES[key])), key)


class SettingsScreenTests(unittest.TestCase):
    def test_home_text_has_no_brand_prefix(self) -> None:
        from jopesvpn.ui import home_text

        text = home_text("Алексей", "summary")
        self.assertNotIn("JOPES VPN", text)
        self.assertNotIn("<i>", text)

    def test_remaining_text_does_not_duplicate_day_number(self) -> None:
        from datetime import UTC, datetime, timedelta

        from jopesvpn.panel import PanelUser
        from jopesvpn.ui import remaining_text

        expires = datetime.now(UTC) + timedelta(days=3, hours=3)
        user = PanelUser(
            id=1,
            username="tg_1",
            telegram_id=1,
            status="ACTIVE",
            expire_at=expires.isoformat(),
            subscription_url=None,
        )
        text = remaining_text(user)
        self.assertEqual(text, "3 дня 2 ч")

    def test_admin_user_label_uses_name_and_tariff_emoji(self) -> None:
        from jopesvpn.panel import PanelUser
        from jopesvpn.ui import admin_user_label

        user = PanelUser(
            id=1,
            username="tg_1",
            telegram_id=1,
            status="ACTIVE",
            expire_at="2030-01-01T00:00:00Z",
            subscription_url=None,
            tag="TRIAL",
        )
        self.assertEqual(admin_user_label(user, "Иван Петров"), "🎁 Иван Петров (1)")
        self.assertEqual(admin_user_label(user), "🎁 tg_1 (1)")

    def test_settings_screen_renders_language_and_buttons(self) -> None:
        from jopesvpn.panel import PanelUser
        from jopesvpn.ui import settings_text

        user = PanelUser(
            id=1,
            username="tg_1",
            telegram_id=1,
            status="ACTIVE",
            expire_at="2030-01-01T00:00:00Z",
            subscription_url=None,
        )
        text = settings_text(
            telegram_id=1,
            reminders_enabled=True,
            user=user,
            support=None,
        )
        self.assertIn("Язык:", text)
        self.assertIn("Русский", text)
        self.assertIn("НАСТРОЙКИ", text)


class LanguageContextTests(unittest.TestCase):
    def test_context_language_is_restored(self) -> None:
        before = current_language()
        token = set_language(DEFAULT_LANGUAGE)
        try:
            self.assertEqual(current_language(), DEFAULT_LANGUAGE)
        finally:
            from jopesvpn.i18n import reset_language

            reset_language(token)
        self.assertEqual(current_language(), before)


class PreferencesLanguageTests(unittest.TestCase):
    def test_language_defaults_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PreferencesStore(Path(directory) / "state.sqlite3")
            self.assertEqual(store.language(1001), "ru")
            store.set_language(1001, "ru")
            self.assertEqual(store.language(1001), "ru")
            store.set_language(1001, "en")
            self.assertEqual(store.language(1001), "en")

    def test_display_name_is_stored_and_updated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PreferencesStore(Path(directory) / "state.sqlite3")
            self.assertIsNone(store.display_name(1001))
            store.remember(1001, "Иван", "Петров")
            self.assertEqual(store.display_name(1001), "Иван Петров")
            self.assertEqual(store.display_names(), {1001: "Иван Петров"})
            store.remember(1001, "Иван", None)
            self.assertEqual(store.display_name(1001), "Иван Петров")

    def test_existing_database_gets_language_column(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    """
                    CREATE TABLE user_preferences (
                        telegram_id INTEGER PRIMARY KEY,
                        reminders_enabled INTEGER NOT NULL DEFAULT 1,
                        last_reminder_on TEXT,
                        last_reminder_days INTEGER,
                        first_seen_at TEXT NOT NULL DEFAULT (datetime('now'))
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO user_preferences (telegram_id) VALUES (1001)"
                )
                connection.commit()
            store = PreferencesStore(path)
            self.assertEqual(store.language(1001), "ru")
            self.assertTrue(store.reminders_enabled(1001))


class LanguageMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_middleware_sets_and_restores_language(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PreferencesStore(Path(directory) / "state.sqlite3")
            store.set_language(1001, "ru")
            middleware = LanguageMiddleware(store)
            seen: list[str] = []

            async def handler(event, data):
                seen.append(current_language())
                return "handled"

            result = await middleware(
                handler,
                SimpleNamespace(from_user=SimpleNamespace(id=1001)),
                {},
            )
            self.assertEqual(result, "handled")
            self.assertEqual(seen, ["ru"])
            self.assertEqual(current_language(), DEFAULT_LANGUAGE)


if __name__ == "__main__":
    unittest.main()
