from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from jopesvpn.admin import _broadcast_launch, admin_navigation
from jopesvpn.broadcasts import BroadcastButton, BroadcastStore, SENT
from jopesvpn.config import Settings
from jopesvpn.preferences import PreferencesStore


class BroadcastAdminFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_launch_sends_draft_and_records_delivery(self) -> None:
        sent: list[int] = []

        class Bot:
            async def send_message(self, chat_id, text, **kwargs):
                sent.append(chat_id)

        class Panel:
            pass

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            prefs = PreferencesStore(path)
            prefs.register_start(2002)
            prefs.register_start(2003)
            store = BroadcastStore(path)
            draft = store.create(1001, "Привет")
            store.set_buttons(
                draft.id, [BroadcastButton("Сайт", "https://example.com")]
            )
            draft = store.get(draft.id)
            edited: list[str] = []
            answers: list[str | None] = []

            async def edit_text(text, **kwargs):
                edited.append(text)

            async def answer(text=None, **kwargs):
                answers.append(text)

            callback = SimpleNamespace(
                data=f"admin:bcast:launch:{draft.id}",
                message=SimpleNamespace(
                    chat=SimpleNamespace(id=1001, type="private"),
                    photo=None,
                    edit_text=edit_text,
                    answer=answer,
                ),
                from_user=SimpleNamespace(id=1001),
                answer=answer,
            )
            settings = Settings(
                bot_token="token",
                panel_api_url="https://panel.example.invalid",
                panel_api_token="panel-token",
                admin_telegram_ids=frozenset({1001}),
                panel_internal_squad_uuid=str(uuid4()),
            )
            await _broadcast_launch(
                callback,
                Bot(),
                Panel(),
                settings,
                prefs,
                store,
                draft.id,
            )
            self.assertEqual(sent, [2002, 2003])
            updated = store.get(draft.id)
            self.assertEqual(updated.status, SENT)
            self.assertEqual(store.delivered_ids(draft.id), {2002, 2003})
            self.assertTrue(any("Рассылка" in text for text in edited))
            self.assertTrue(answers)


class BroadcastNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_buttons_callback_opens_button_screen(self) -> None:
        class State:
            async def clear(self) -> None:
                pass

        with tempfile.TemporaryDirectory() as directory:
            store = BroadcastStore(Path(directory) / "state.sqlite3")
            draft = store.create(1001, "Привет")
            edits: list[dict] = []
            answers: list[str | None] = []

            async def edit_text(text, **kwargs):
                edits.append({"text": text, **kwargs})

            async def answer(text=None, **kwargs):
                answers.append(text)

            callback = SimpleNamespace(
                data=f"admin:bcast:buttons:{draft.id}",
                message=SimpleNamespace(
                    chat=SimpleNamespace(id=1001, type="private"),
                    photo=None,
                    edit_text=edit_text,
                    answer=answer,
                ),
                from_user=SimpleNamespace(id=1001),
                answer=answer,
            )
            settings = Settings(
                bot_token="token",
                panel_api_url="https://panel.example.invalid",
                panel_api_token="panel-token",
                admin_telegram_ids=frozenset({1001}),
                panel_internal_squad_uuid=str(uuid4()),
            )

            await admin_navigation(
                callback,
                object(),
                object(),
                settings,
                State(),
                broadcasts=store,
            )

            self.assertTrue(edits)
            callbacks = [
                button.callback_data
                for row in edits[-1]["reply_markup"].inline_keyboard
                for button in row
            ]
            self.assertIn(f"admin:bcast:button:add:{draft.id}", callbacks)
            self.assertIn(f"admin:bcast:open:{draft.id}", callbacks)
            self.assertNotIn("Некорректная кнопка.", answers)

    async def test_broadcasts_callback_builds_keyboard(self) -> None:
        class State:
            async def clear(self) -> None:
                pass

        with tempfile.TemporaryDirectory() as directory:
            store = BroadcastStore(Path(directory) / "state.sqlite3")
            store.create(1001, "Привет")
            edits: list[dict] = []
            async def edit_text(text, **kwargs):
                edits.append({"text": text, **kwargs})

            async def answer(text=None, **kwargs):
                pass

            callback = SimpleNamespace(
                data="admin:broadcasts",
                message=SimpleNamespace(
                    chat=SimpleNamespace(id=1001, type="private"),
                    photo=None,
                    edit_text=edit_text,
                    answer=answer,
                ),
                from_user=SimpleNamespace(id=1001),
                answer=answer,
            )
            settings = Settings(
                bot_token="token",
                panel_api_url="https://panel.example.invalid",
                panel_api_token="panel-token",
                admin_telegram_ids=frozenset({1001}),
                panel_internal_squad_uuid=str(uuid4()),
            )

            await admin_navigation(
                callback,
                object(),
                object(),
                settings,
                State(),
                broadcasts=store,
            )

            self.assertTrue(edits)
            markup = edits[-1]["reply_markup"]
            self.assertEqual(markup.inline_keyboard[-1][0].callback_data, "admin:home")

    async def test_button_add_callback_opens_label_prompt(self) -> None:
        class State:
            def __init__(self) -> None:
                self.current = None
                self.data: dict = {}

            async def clear(self) -> None:
                self.current = None
                self.data = {}

            async def set_state(self, value) -> None:
                self.current = value

            async def update_data(self, **kwargs) -> None:
                self.data.update(kwargs)

        with tempfile.TemporaryDirectory() as directory:
            store = BroadcastStore(Path(directory) / "state.sqlite3")
            draft = store.create(1001, "Привет")
            state = State()
            edited: list[str] = []
            answers: list[str | None] = []

            async def edit_text(text, **kwargs):
                edited.append(text)

            async def answer(text=None, **kwargs):
                answers.append(text)

            callback = SimpleNamespace(
                data=f"admin:bcast:button:add:{draft.id}",
                message=SimpleNamespace(
                    chat=SimpleNamespace(id=1001, type="private"),
                    photo=None,
                    edit_text=edit_text,
                    answer=answer,
                ),
                from_user=SimpleNamespace(id=1001),
                answer=answer,
            )
            settings = Settings(
                bot_token="token",
                panel_api_url="https://panel.example.invalid",
                panel_api_token="panel-token",
                admin_telegram_ids=frozenset({1001}),
                panel_internal_squad_uuid=str(uuid4()),
            )
            await admin_navigation(
                callback,
                object(),
                object(),
                settings,
                state,
                broadcasts=store,
            )
            self.assertEqual(state.data["broadcast_id"], draft.id)
            self.assertTrue(edited)
            self.assertTrue(answers)


if __name__ == "__main__":
    unittest.main()
