from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from jopesvpn.bot import _grant_user_free_access
from jopesvpn.config import Settings
from jopesvpn.panel import FREE_TRAFFIC_BYTES, PanelNotFound, PanelUser
from jopesvpn.preferences import PreferencesStore


class FreeClaimTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_can_claim_free_once_without_an_existing_panel_account(self) -> None:
        user: PanelUser | None = None
        creations = 0

        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int) -> PanelUser:
                if user is None:
                    raise PanelNotFound("not found")
                return user

            async def create_free_user(
                self, telegram_id: int, squad_uuid: str
            ) -> PanelUser:
                nonlocal user, creations
                creations += 1
                user = PanelUser(
                    id=12,
                    username=f"tg_{telegram_id}",
                    telegram_id=telegram_id,
                    status="ACTIVE",
                    expire_at="2099-01-01T00:00:00+00:00",
                    subscription_url=None,
                    tag="FREE",
                    traffic_used_bytes=0,
                    traffic_lifetime_used_bytes=0,
                    traffic_limit_bytes=FREE_TRAFFIC_BYTES,
                    traffic_limit_strategy="NO_RESET",
                )
                return user

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=12345),
            message=None,
            answer=AsyncMock(),
        )
        settings = Settings(
            bot_token="test-token",
            panel_api_url="https://panel.example.test",
            panel_api_token="test-panel-token",
            admin_telegram_ids=frozenset(),
            panel_internal_squad_uuid="00000000-0000-0000-0000-000000000001",
            free_access_enabled=True,
        )

        with tempfile.TemporaryDirectory() as directory:
            prefs = PreferencesStore(Path(directory) / "state.sqlite3")
            prefs.remember(12345)
            panel = Panel()
            await _grant_user_free_access(callback, panel, settings, prefs)
            await _grant_user_free_access(callback, panel, settings, prefs)

            daily_state = prefs.free_daily_state(12345)

        self.assertEqual(creations, 1)
        self.assertEqual(
            daily_state,
            (datetime.now(UTC).date().isoformat(), 0),
        )
        self.assertEqual(callback.answer.await_count, 2)
        self.assertTrue(callback.answer.await_args.kwargs.get("show_alert"))


if __name__ == "__main__":
    unittest.main()
