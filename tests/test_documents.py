from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

from aiogram.types import Chat, Message, User

from jopesvpn.bot import DocumentsConsentMiddleware
from jopesvpn.config import Settings
from jopesvpn.admin import receive_document_url
from jopesvpn.plans import load_plans
from jopesvpn.preferences import PreferencesStore
from jopesvpn.ui import documents_keyboard


class DocumentConsentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.prefs = PreferencesStore(Path(self.tempdir.name) / "state.sqlite3")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_consent_is_versioned_and_invalidated_when_a_link_changes(self) -> None:
        self.assertFalse(self.prefs.documents_accepted(123, "1"))
        self.prefs.set_document_url("privacy", "https://example.com/privacy")
        first_config_version = self.prefs.documents_version()
        self.assertEqual(first_config_version, "2")
        self.prefs.set_document_url("agreement", "https://example.com/agreement")
        current_version = self.prefs.documents_version()
        self.assertEqual(current_version, "3")

        self.prefs.accept_documents(123, current_version)
        self.assertTrue(self.prefs.documents_accepted(123, current_version))

        self.prefs.set_document_url("agreement", "https://example.com/terms-v2")
        self.assertFalse(
            self.prefs.documents_accepted(123, self.prefs.documents_version())
        )

    def test_unconfigured_document_buttons_do_not_contain_fake_urls(self) -> None:
        markup = documents_keyboard(None, None, acceptance_required=True)
        buttons = [
            button
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertFalse(any(button.url for button in buttons))
        self.assertFalse(
            any(button.callback_data == "docs:accept:1" for button in buttons)
        )
        self.assertTrue(
            any(button.callback_data == "ticket:open" for button in buttons)
        )

    def test_configured_documents_show_links_and_versioned_consent(self) -> None:
        markup = documents_keyboard(
            "https://example.com/privacy",
            "https://example.com/agreement",
            acceptance_required=True,
            version="7",
        )
        buttons = [
            button
            for row in markup.inline_keyboard
            for button in row
        ]
        urls = {button.url for button in buttons if button.url}
        callbacks = {button.callback_data for button in buttons if button.callback_data}
        self.assertEqual(
            urls,
            {"https://example.com/privacy", "https://example.com/agreement"},
        )
        self.assertIn("docs:accept:7", callbacks)

    def test_each_shipped_tariff_has_a_unique_panel_tag(self) -> None:
        plans = load_plans()
        tags = [plan.panel_tag for plan in plans]
        self.assertTrue(all(tags))
        self.assertEqual(len(tags), len(set(tags)))

    def test_admin_plan_callback_claim_is_idempotent(self) -> None:
        self.assertTrue(
            self.prefs.claim_admin_plan_operation("callback-1", 999, "month")
        )
        self.assertFalse(
            self.prefs.claim_admin_plan_operation("callback-1", 999, "month")
        )
        self.prefs.complete_admin_plan_operation("callback-1")
        self.assertFalse(
            self.prefs.claim_admin_plan_operation("callback-1", 999, "month")
        )


class DocumentConsentMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_unaccepted_message_is_blocked_by_server_side_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            prefs = PreferencesStore(Path(tempdir) / "state.sqlite3")
            prefs.set_document_url("privacy", "https://example.com/privacy")
            prefs.set_document_url("agreement", "https://example.com/agreement")
            event = Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=Chat(id=123, type="private"),
                from_user=User(id=123, is_bot=False, first_name="Test"),
                text="buy",
            )
            object.__setattr__(event, "answer", AsyncMock())
            handler = AsyncMock()
            settings = Settings(
                bot_token="test-token",
                panel_api_url="https://panel.example.test",
                panel_api_token="test-token",
                admin_telegram_ids=frozenset({999}),
            )

            await DocumentsConsentMiddleware()(
                handler,
                event,
                {"prefs": prefs, "settings": settings},
            )

            handler.assert_not_awaited()
            event.answer.assert_awaited_once()

    async def test_unconfigured_documents_do_not_block_bot_access(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            prefs = PreferencesStore(Path(tempdir) / "state.sqlite3")
            event = Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=Chat(id=123, type="private"),
                from_user=User(id=123, is_bot=False, first_name="Test"),
                text="buy",
            )
            handler = AsyncMock()
            settings = Settings(
                bot_token="test-token",
                panel_api_url="https://panel.example.test",
                panel_api_token="test-token",
                admin_telegram_ids=frozenset({999}),
            )
            data = {"prefs": prefs, "settings": settings}

            await DocumentsConsentMiddleware()(handler, event, data)

            handler.assert_awaited_once_with(event, data)

    async def test_admin_can_set_https_document_link_and_version_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            prefs = PreferencesStore(Path(tempdir) / "state.sqlite3")
            admin_id = 999
            event = Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=Chat(id=admin_id, type="private"),
                from_user=User(id=admin_id, is_bot=False, first_name="Admin"),
                text="https://example.com/privacy",
            )
            object.__setattr__(event, "answer", AsyncMock())
            state = AsyncMock()
            state.get_data.return_value = {"document": "privacy"}
            settings = Settings(
                bot_token="test-token",
                panel_api_url="https://panel.example.test",
                panel_api_token="test-token",
                admin_telegram_ids=frozenset({admin_id}),
            )

            await receive_document_url(event, settings, state, prefs)

            self.assertEqual(
                prefs.document_url("privacy"),
                "https://example.com/privacy",
            )
            self.assertEqual(prefs.documents_version(), "2")
            state.clear.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
