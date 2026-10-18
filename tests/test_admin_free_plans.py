from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

from jopesvpn.bot import create_stars_invoice
from jopesvpn.config import Settings
from jopesvpn.panel import PanelNotFound, PanelUser
from jopesvpn.plans import load_plans
from jopesvpn.preferences import PreferencesStore


class AdminFreePlanTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_tariff_grant_calls_panel_without_creating_payment(self) -> None:
        admin_id = 123456
        created: list[dict[str, object]] = []

        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                raise PanelNotFound("not found")

            async def create_user(
                self,
                telegram_id: int,
                days: int,
                squad_uuid: str,
                *,
                tag: str | None = None,
                traffic_limit_gb: int | None = None,
                permanent_traffic: bool = False,
                traffic_limit_strategy: str = "NO_RESET",
            ) -> PanelUser:
                created.append(
                    {
                        "telegram_id": telegram_id,
                        "days": days,
                        "squad_uuid": squad_uuid,
                        "tag": tag,
                        "traffic_limit_gb": traffic_limit_gb,
                        "permanent_traffic": permanent_traffic,
                        "traffic_limit_strategy": traffic_limit_strategy,
                    }
                )
                return PanelUser(
                    id=51,
                    username="tg_123456",
                    telegram_id=telegram_id,
                    status="ACTIVE",
                    expire_at=(
                        datetime.now(UTC) + timedelta(days=days)
                    ).isoformat(),
                    subscription_url=None,
                    tag=tag,
                )

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=admin_id, first_name="Admin"),
            message=SimpleNamespace(
                chat=SimpleNamespace(id=admin_id, type="private"),
                photo=None,
                edit_text=AsyncMock(),
                answer_invoice=AsyncMock(
                    side_effect=AssertionError("admin must not receive an invoice")
                ),
            ),
            data="pay:unlimited-7:1234567890abcdef",
            answer=AsyncMock(),
        )
        orders = SimpleNamespace(
            create_order=AsyncMock(
                side_effect=AssertionError("admin must not create a paid order")
            )
        )
        settings = Settings(
            bot_token="test-token",
            panel_api_url="https://panel.example.test",
            panel_api_token="test-panel-token",
            admin_telegram_ids=frozenset({admin_id}),
            panel_internal_squad_uuid="00000000-0000-0000-0000-000000000001",
        )

        with tempfile.TemporaryDirectory() as tempdir:
            prefs = PreferencesStore(Path(tempdir) / "state.sqlite3")
            panel = Panel()
            await create_stars_invoice(
                callback,
                None,
                panel,
                settings,
                load_plans(),
                orders,
                prefs,
            )
            await create_stars_invoice(
                callback,
                None,
                panel,
                settings,
                load_plans(),
                orders,
                prefs,
            )

        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["telegram_id"], admin_id)
        self.assertEqual(created[0]["tag"], "PLAN_UNLIMITED_7")
        orders.create_order.assert_not_awaited()
        callback.message.answer_invoice.assert_not_awaited()
        self.assertEqual(callback.answer.await_count, 2)


if __name__ == "__main__":
    unittest.main()
