from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
from aiogram.exceptions import TelegramBadRequest

from jopesvpn.admin import AdminFlow, admin_find_input
from jopesvpn.bot import (
    _admin_only,
    _home_text,
    _home_view,
    _is_admin,
    _is_stale_callback_query_error,
    _stars_plan,
    _subscription_view,
    _user_screen,
    admin_disable,
    admin_enable,
    admin_extend,
    admin_grant,
    admin_navigation,
    admin_revoke,
    connect_command,
    ping_command,
    router,
    trial_check,
)
from jopesvpn.config import ConfigurationError, Settings
from jopesvpn.i18n import t
from jopesvpn.notifications import send_due_reminders, send_due_trial_nudges
from jopesvpn.panel import (
    PanelAccessDenied,
    PanelDataError,
    PanelDevice,
    PanelNotFound,
    PanelUnavailable,
    PanelUser,
    RemnawaveClient,
)
from jopesvpn.plans import load_plans
from jopesvpn.preferences import PreferencesStore
from jopesvpn.ui import (
    PLATFORM_APPS,
    devices_keyboard,
    devices_text,
    guide_apps_keyboard,
    guide_platform_keyboard,
    guide_step_keyboard,
    plans_text,
    subscription_keyboard,
    subscription_text,
    trial_text,
)


class QueueTransport(httpx.AsyncBaseTransport):
    def __init__(self, responses: list[tuple[int, object]]) -> None:
        self.responses = responses
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("Unexpected Panel request")
        status, data = self.responses.pop(0)
        return httpx.Response(
            status_code=status,
            json=data,
            request=request,
        )


class RemnawaveClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_parser_keeps_card_data_when_optional_fields_are_missing(self) -> None:
        user = RemnawaveClient._user(
            {
                "response": {
                    "id": 51,
                    "username": "tg_123456",
                    "status": "LIMITED",
                    "userTraffic": None,
                    "trafficLimitBytes": "invalid",
                    "subscriptionUrl": 12,
                    "createdAt": None,
                }
            }
        )
        self.assertEqual(user.telegram_id, None)
        self.assertIsNone(user.expire_at)
        self.assertIsNone(user.subscription_url)
        self.assertIsNone(user.traffic_limit_bytes)
        self.assertIsNone(user.traffic_used_bytes)
        self.assertIsNone(user.created_at)

    async def test_panel_managed_user_lookup_is_bound_to_telegram_id(self) -> None:
        transport = QueueTransport(
            [
                (
                    200,
                    {
                        "response": {
                            "id": 41,
                            "username": "tg_123456",
                            "telegramId": 123456,
                            "status": "ACTIVE",
                            "expireAt": "2030-01-01T00:00:00.000Z",
                            "subscriptionUrl": "https://sub.example.invalid/secret",
                        }
                    },
                )
            ]
        )
        client = RemnawaveClient(
            "https://panel.example.invalid",
            "test-api-token",
            transport=transport,
        )
        try:
            user = await client.get_user_by_telegram_id(123456)
            self.assertEqual(user.telegram_id, 123456)
            self.assertEqual(user.username, "tg_123456")
            self.assertEqual(
                transport.requests[0].url.path,
                "/api/users/by-username/tg_123456",
            )
            self.assertEqual(
                transport.requests[0].headers["Authorization"],
                "Bearer test-api-token",
            )
        finally:
            await client.close()

    async def test_user_lookup_accepts_missing_optional_telegram_id_for_canonical_name(self) -> None:
        transport = QueueTransport(
            [
                (
                    200,
                    {
                        "response": {
                            "id": 41,
                            "username": "tg_123456",
                            "status": "ACTIVE",
                            "expireAt": None,
                        }
                    },
                )
            ]
        )
        client = RemnawaveClient(
            "https://panel.example.invalid",
            "test-api-token",
            transport=transport,
        )
        try:
            user = await client.get_user_by_telegram_id(123456)
            self.assertIsNone(user.telegram_id)
            self.assertIsNone(user.expire_at)
        finally:
            await client.close()

    async def test_permanent_traffic_plan_adds_quota_and_uses_panel_technical_date(self) -> None:
        expiry = datetime.now(UTC).replace(microsecond=0) + timedelta(days=30)
        current_limit = 10 * 1024**3
        used = 4 * 1024**3
        expected_limit = current_limit + 5 * 1024**3
        transport = QueueTransport(
            [
                (
                    200,
                    {
                        "response": {
                            "id": 41,
                            "username": "tg_123456",
                            "telegramId": 123456,
                            "status": "ACTIVE",
                            "expireAt": "2099-01-01T00:00:00+00:00",
                            "trafficLimitBytes": expected_limit,
                            "trafficLimitStrategy": "NO_RESET",
                            "tag": "PLAN_DATA_5",
                            "userTraffic": {"usedTrafficBytes": used},
                        }
                    },
                )
            ]
        )
        client = RemnawaveClient(
            "https://panel.example.invalid",
            "test-api-token",
            transport=transport,
        )
        user = PanelUser(
            id=41,
            username="tg_123456",
            telegram_id=123456,
            status="ACTIVE",
            expire_at=expiry.isoformat(),
            subscription_url=None,
            traffic_used_bytes=used,
            traffic_limit_bytes=current_limit,
        )
        try:
            updated = await client.apply_paid_plan(
                user, 365, 5, permanent_traffic=True, tag="PLAN_DATA_5"
            )
            self.assertEqual(updated.traffic_limit_bytes, expected_limit)
            request = transport.requests[0]
            self.assertEqual(request.method, "PATCH")
            self.assertEqual(request.url.path, "/api/users")
            self.assertEqual(
                json.loads(request.content),
                {
                    "id": 41,
                    "status": "ACTIVE",
                    "expireAt": "2099-01-01T00:00:00+00:00",
                    "trafficLimitBytes": expected_limit,
                    "trafficLimitStrategy": "NO_RESET",
                    "tag": "PLAN_DATA_5",
                },
            )
        finally:
            await client.close()

    async def test_limited_user_can_buy_additional_no_reset_traffic(self) -> None:
        expiry = "2099-01-01T00:00:00+00:00"
        transport = QueueTransport(
            [
                (
                    200,
                    {
                        "response": {
                            "id": 41,
                            "username": "tg_123456",
                            "telegramId": 123456,
                            "status": "ACTIVE",
                            "expireAt": expiry,
                            "trafficLimitBytes": 15 * 1024**3,
                            "trafficLimitStrategy": "NO_RESET",
                            "tag": "PLAN_DATA_5",
                            "userTraffic": {"usedTrafficBytes": 10 * 1024**3},
                        }
                    },
                )
            ]
        )
        client = RemnawaveClient(
            "https://panel.example.invalid",
            "test-api-token",
            transport=transport,
        )
        user = PanelUser(
            id=41,
            username="tg_123456",
            telegram_id=123456,
            status="LIMITED",
            expire_at=expiry,
            subscription_url=None,
            traffic_used_bytes=10 * 1024**3,
            traffic_limit_bytes=10 * 1024**3,
        )
        try:
            updated = await client.apply_paid_plan(
                user, 365, 5, permanent_traffic=True, tag="PLAN_DATA_5"
            )
            self.assertEqual(updated.status, "ACTIVE")
            self.assertEqual(updated.traffic_limit_bytes, 15 * 1024**3)
            self.assertEqual(
                json.loads(transport.requests[0].content),
                {
                    "id": 41,
                    "status": "ACTIVE",
                    "expireAt": expiry,
                    "trafficLimitBytes": 15 * 1024**3,
                    "trafficLimitStrategy": "NO_RESET",
                    "tag": "PLAN_DATA_5",
                },
            )
        finally:
            await client.close()

    async def test_paid_periodic_plan_sets_panel_reset_strategy_and_duration(self) -> None:
        expiry = "2030-01-01T00:00:00+00:00"
        quota = 5 * 1024**3
        transport = QueueTransport(
            [
                (
                    200,
                    {
                        "response": {
                            "id": 41,
                            "username": "tg_123456",
                            "telegramId": 123456,
                            "status": "ACTIVE",
                            "expireAt": "2030-01-31T00:00:00+00:00",
                            "trafficLimitBytes": quota,
                            "trafficLimitStrategy": "WEEK",
                            "tag": "PLAN_WEEK_5",
                        }
                    },
                )
            ]
        )
        client = RemnawaveClient(
            "https://panel.example.invalid",
            "test-api-token",
            transport=transport,
        )
        user = PanelUser(
            id=41,
            username="tg_123456",
            telegram_id=123456,
            status="ACTIVE",
            expire_at=expiry,
            subscription_url=None,
            traffic_used_bytes=2 * 1024**3,
            traffic_limit_bytes=3 * 1024**3,
            traffic_limit_strategy="WEEK",
        )
        try:
            updated = await client.apply_paid_plan(
                user,
                30,
                5,
                tag="PLAN_WEEK_5",
                traffic_limit_strategy="WEEK",
            )
            body = json.loads(transport.requests[0].content)
            self.assertEqual(body["trafficLimitBytes"], quota)
            self.assertEqual(body["trafficLimitStrategy"], "WEEK")
            self.assertEqual(body["expireAt"], "2030-01-31T00:00:00+00:00")
            self.assertEqual(updated.traffic_limit_strategy, "WEEK")
        finally:
            await client.close()

    async def test_free_access_sets_daily_panel_limit_only_after_expired_trial(self) -> None:
        quota = 200 * 1024**2
        transport = QueueTransport(
            [
                (
                    200,
                    {
                        "response": {
                            "id": 41,
                            "username": "tg_123456",
                            "telegramId": 123456,
                            "status": "EXPIRED",
                            "expireAt": "2025-01-01T00:00:00+00:00",
                            "tag": "TRIAL",
                            "userTraffic": {"usedTrafficBytes": 42},
                        }
                    },
                ),
                (
                    200,
                    {
                        "response": {
                            "id": 41,
                            "username": "tg_123456",
                            "telegramId": 123456,
                            "status": "ACTIVE",
                            "expireAt": "2099-01-01T00:00:00+00:00",
                            "tag": "FREE",
                            "trafficLimitBytes": quota,
                            "trafficLimitStrategy": "NO_RESET",
                        }
                    },
                ),
            ]
        )
        client = RemnawaveClient(
            "https://panel.example.invalid",
            "test-api-token",
            transport=transport,
        )
        user = PanelUser(
            id=41,
            username="tg_123456",
            telegram_id=123456,
            status="EXPIRED",
            expire_at="2025-01-01T00:00:00+00:00",
            subscription_url=None,
            tag="TRIAL",
        )
        try:
            updated = await client.apply_free_access(user)
            self.assertEqual(updated.status, "ACTIVE")
            self.assertEqual(updated.tag, "FREE")
            self.assertEqual(updated.traffic_limit_bytes, quota)
            self.assertEqual(
                transport.requests[0].url.path,
                "/api/users/41/actions/reset-traffic",
            )
            self.assertEqual(
                json.loads(transport.requests[1].content),
                {
                    "id": 41,
                    "status": "ACTIVE",
                    "expireAt": "2099-01-01T00:00:00+00:00",
                    "trafficLimitBytes": quota,
                    "trafficLimitStrategy": "NO_RESET",
                    "tag": "FREE",
                },
            )
        finally:
            await client.close()

    async def test_free_account_repair_uses_manual_utc_reset_strategy(self) -> None:
        quota = 200 * 1024**2
        transport = QueueTransport(
            [
                (
                    200,
                    {
                        "response": {
                            "id": 43,
                            "username": "tg_123456",
                            "telegramId": 123456,
                            "status": "LIMITED",
                            "expireAt": "2099-01-01T00:00:00+00:00",
                            "tag": "FREE",
                            "trafficLimitBytes": quota,
                            "trafficLimitStrategy": "NO_RESET",
                            "userTraffic": {
                                "usedTrafficBytes": quota,
                                "lifetimeUsedTrafficBytes": 1000,
                            },
                        }
                    },
                )
            ]
        )
        client = RemnawaveClient(
            "https://panel.example.invalid",
            "test-api-token",
            transport=transport,
        )
        user = PanelUser(
            id=43,
            username="tg_123456",
            telegram_id=123456,
            status="LIMITED",
            expire_at="2099-01-01T00:00:00+00:00",
            subscription_url=None,
            tag="FREE",
            traffic_limit_bytes=quota,
            traffic_limit_strategy="DAY",
        )
        try:
            updated = await client.repair_free_access(user)
            self.assertEqual(updated.status, "LIMITED")
            self.assertEqual(updated.traffic_limit_strategy, "NO_RESET")
            self.assertEqual(
                json.loads(transport.requests[0].content),
                {
                    "id": 43,
                    "expireAt": "2099-01-01T00:00:00+00:00",
                    "trafficLimitBytes": quota,
                    "trafficLimitStrategy": "NO_RESET",
                    "tag": "FREE",
                },
            )
        finally:
            await client.close()

    async def test_limited_free_account_is_activated_only_after_reset(self) -> None:
        quota = 200 * 1024**2
        transport = QueueTransport(
            [
                (
                    200,
                    {
                        "response": {
                            "id": 44,
                            "username": "tg_123456",
                            "telegramId": 123456,
                            "status": "ACTIVE",
                            "expireAt": "2099-01-01T00:00:00+00:00",
                            "tag": "FREE",
                            "trafficLimitBytes": quota,
                            "trafficLimitStrategy": "NO_RESET",
                            "userTraffic": {
                                "usedTrafficBytes": 0,
                                "lifetimeUsedTrafficBytes": 1000,
                            },
                        }
                    },
                )
            ]
        )
        client = RemnawaveClient(
            "https://panel.example.invalid",
            "test-api-token",
            transport=transport,
        )
        user = PanelUser(
            id=44,
            username="tg_123456",
            telegram_id=123456,
            status="LIMITED",
            expire_at="2099-01-01T00:00:00+00:00",
            subscription_url=None,
            tag="FREE",
            traffic_used_bytes=0,
            traffic_limit_bytes=quota,
            traffic_limit_strategy="NO_RESET",
        )
        try:
            updated = await client.reactivate_free_after_reset(user)
            self.assertEqual(updated.status, "ACTIVE")
            self.assertEqual(
                json.loads(transport.requests[0].content),
                {"id": 44, "status": "ACTIVE"},
            )
        finally:
            await client.close()

    async def test_create_user_applies_a_no_reset_traffic_quota(self) -> None:
        quota = 3 * 1024**3
        transport = QueueTransport(
            [
                (201, {"response": {"subscriptionUrl": "https://sub.example.test/abc"}}),
                (
                    200,
                    {
                        "response": {
                            "id": 42,
                            "username": "tg_123456",
                            "telegramId": 123456,
                            "status": "ACTIVE",
                            "expireAt": "2099-01-01T00:00:00+00:00",
                            "tag": "STARS_DATA_3",
                            "trafficLimitBytes": quota,
                            "trafficLimitStrategy": "NO_RESET",
                            "userTraffic": {
                                "usedTrafficBytes": 0,
                                "lifetimeUsedTrafficBytes": 0,
                            },
                        }
                    },
                ),
            ]
        )
        client = RemnawaveClient(
            "https://panel.example.invalid",
            "test-api-token",
            transport=transport,
        )
        try:
            user = await client.create_user(
                123456,
                30,
                "00000000-0000-0000-0000-000000000001",
                tag="STARS_DATA_3",
                traffic_limit_gb=3,
                permanent_traffic=True,
            )
            body = json.loads(transport.requests[0].content)
            self.assertEqual(body["trafficLimitBytes"], quota)
            self.assertEqual(body["trafficLimitStrategy"], "NO_RESET")
            self.assertEqual(body["expireAt"], "2099-01-01T00:00:00+00:00")
            self.assertEqual(user.traffic_limit_bytes, quota)
        finally:
            await client.close()

    def test_traffic_plan_requires_explicit_enablement_for_billing(self) -> None:
        plans = load_plans()
        self.assertIsNone(_stars_plan(plans, "data-3"))
        self.assertEqual(
            _stars_plan(plans, "data-3", include_traffic=True).traffic_limit_gb,
            3,
        )
    async def test_list_users_uses_pagination_and_returns_total(self) -> None:
        transport = QueueTransport(
            [
                (
                    200,
                    {
                        "response": {
                            "users": [
                                {
                                    "id": 41,
                                    "username": "tg_123456",
                                    "telegramId": 123456,
                                    "status": "ACTIVE",
                                    "expireAt": "2030-01-01T00:00:00.000Z",
                                    "tag": "TRIAL",
                                    "subscriptionUrl": "https://sub.example.invalid/secret",
                                    "userTraffic": {"usedTrafficBytes": 1024},
                                }
                            ],
                            "total": 9,
                        }
                    },
                )
            ]
        )
        client = RemnawaveClient(
            "https://panel.example.invalid",
            "test-api-token",
            transport=transport,
        )
        try:
            page = await client.list_users(8, 8)
            self.assertEqual(page.total, 9)
            self.assertEqual(len(page.users), 1)
            self.assertEqual(page.users[0].tag, "TRIAL")
            self.assertEqual(page.users[0].traffic_used_bytes, 1024)
            request = transport.requests[0]
            self.assertEqual(request.url.path, "/api/users")
            self.assertEqual(request.url.params["start"], "8")
            self.assertEqual(request.url.params["size"], "8")
        finally:
            await client.close()

    async def test_lookup_rejects_username_owned_by_a_different_telegram_id(self) -> None:
        transport = QueueTransport(
            [
                (
                    200,
                    {
                        "response": {
                            "id": 41,
                            "username": "tg_123456",
                            "telegramId": 654321,
                            "status": "ACTIVE",
                            "expireAt": "2030-01-01T00:00:00.000Z",
                        }
                    },
                )
            ]
        )
        client = RemnawaveClient(
            "https://panel.example.invalid", "token", transport=transport
        )
        try:
            with self.assertRaises(PanelNotFound):
                await client.get_user_by_telegram_id(123456)
        finally:
            await client.close()

    async def test_create_user_uses_panel_and_verifies_telegram_binding(self) -> None:
        squad_uuid = str(uuid4())
        transport = QueueTransport(
            [
                (201, {"response": {"subscriptionUrl": "https://sub.example.invalid/id"}}),
                (
                    200,
                    {
                        "response": {
                            "id": 52,
                            "username": "tg_123456",
                            "telegramId": 123456,
                            "status": "ACTIVE",
                            "expireAt": "2030-01-01T00:00:00.000Z",
                            "subscriptionUrl": "https://sub.example.invalid/id",
                        }
                    },
                ),
            ]
        )
        client = RemnawaveClient(
            "https://panel.example.invalid", "token", transport=transport
        )
        try:
            user = await client.create_user(123456, 30, squad_uuid)
            self.assertEqual(user.telegram_id, 123456)
            request = transport.requests[0]
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/api/users")
            body = json.loads(request.content)
            self.assertEqual(body["username"], "tg_123456")
            self.assertEqual(body["telegramId"], 123456)
            self.assertEqual(body["status"], "ACTIVE")
            self.assertEqual(body["activeInternalSquads"], [squad_uuid])
            self.assertNotIn("vlessUuid", body)
        finally:
            await client.close()

    async def test_create_trial_user_sets_and_confirms_panel_tag(self) -> None:
        squad_uuid = str(uuid4())
        transport = QueueTransport(
            [
                (201, {"response": {"subscriptionUrl": "https://sub.example.invalid/id"}}),
                (
                    200,
                    {
                        "response": {
                            "id": 53,
                            "username": "tg_123456",
                            "telegramId": 123456,
                            "status": "ACTIVE",
                            "expireAt": "2030-01-01T00:00:00.000Z",
                            "subscriptionUrl": "https://sub.example.invalid/id",
                            "tag": "TRIAL",
                        }
                    },
                ),
            ]
        )
        client = RemnawaveClient(
            "https://panel.example.invalid", "token", transport=transport
        )
        try:
            user = await client.create_user(123456, 3, squad_uuid, tag="TRIAL")
            self.assertEqual(user.tag, "TRIAL")
            body = json.loads(transport.requests[0].content)
            self.assertEqual(body["tag"], "TRIAL")
        finally:
            await client.close()

    async def test_create_user_rejects_invalid_tag_before_panel_call(self) -> None:
        transport = QueueTransport([])
        client = RemnawaveClient(
            "https://panel.example.invalid", "token", transport=transport
        )
        try:
            with self.assertRaises(ValueError):
                await client.create_user(123456, 3, None, tag="trial")
            self.assertEqual(transport.requests, [])
        finally:
            await client.close()

    async def test_create_user_requires_panel_to_confirm_the_tag(self) -> None:
        transport = QueueTransport(
            [
                (201, {"response": {"subscriptionUrl": "https://sub.example.invalid/id"}}),
                (
                    200,
                    {
                        "response": {
                            "id": 54,
                            "username": "tg_123456",
                            "telegramId": 123456,
                            "status": "ACTIVE",
                            "expireAt": "2030-01-01T00:00:00.000Z",
                            "subscriptionUrl": "https://sub.example.invalid/id",
                            "tag": None,
                        }
                    },
                ),
            ]
        )
        client = RemnawaveClient(
            "https://panel.example.invalid", "token", transport=transport
        )
        try:
            with self.assertRaises(PanelDataError):
                await client.create_user(123456, 3, None, tag="TRIAL")
        finally:
            await client.close()

    async def test_disable_uses_panel_action_and_checks_confirmation(self) -> None:
        transport = QueueTransport(
            [
                (
                    200,
                    {
                        "response": {
                            "id": 52,
                            "username": "tg_123456",
                            "telegramId": 123456,
                            "status": "DISABLED",
                            "expireAt": "2030-01-01T00:00:00.000Z",
                        }
                    },
                )
            ]
        )
        client = RemnawaveClient(
            "https://panel.example.invalid", "token", transport=transport
        )
        try:
            user = client._user(
                {
                    "response": {
                        "id": 52,
                        "username": "tg_123456",
                        "telegramId": 123456,
                        "status": "ACTIVE",
                        "expireAt": "2030-01-01T00:00:00.000Z",
                    }
                }
            )
            await client.disable_user(user)
            self.assertEqual(
                transport.requests[0].url.path,
                "/api/users/52/actions/disable",
            )
        finally:
            await client.close()

    async def test_extend_uses_panel_action_and_checks_new_expiry(self) -> None:
        transport = QueueTransport(
            [
                (
                    200,
                    {
                        "response": {
                            "id": 52,
                            "username": "tg_123456",
                            "telegramId": 123456,
                            "status": "ACTIVE",
                            "expireAt": "2030-01-31T00:00:00.000Z",
                        }
                    },
                )
            ]
        )
        client = RemnawaveClient(
            "https://panel.example.invalid", "token", transport=transport
        )
        try:
            user = client._user(
                {
                    "response": {
                        "id": 52,
                        "username": "tg_123456",
                        "telegramId": 123456,
                        "status": "ACTIVE",
                        "expireAt": "2030-01-01T00:00:00.000Z",
                    }
                }
            )
            updated = await client.extend_user(user, 30)
            self.assertEqual(updated.expire_at, "2030-01-31T00:00:00.000Z")
            request = transport.requests[0]
            self.assertEqual(request.method, "POST")
            self.assertEqual(
                request.url.path, "/api/users/52/actions/extend"
            )
            self.assertEqual(json.loads(request.content), {"days": 30})
        finally:
            await client.close()

    async def test_extend_rejects_confirmation_without_a_later_expiry(self) -> None:
        transport = QueueTransport(
            [
                (
                    200,
                    {
                        "response": {
                            "id": 52,
                            "username": "tg_123456",
                            "telegramId": 123456,
                            "status": "ACTIVE",
                            "expireAt": "2030-01-01T00:00:00.000Z",
                        }
                    },
                )
            ]
        )
        client = RemnawaveClient(
            "https://panel.example.invalid", "token", transport=transport
        )
        try:
            user = client._user(
                {
                    "response": {
                        "id": 52,
                        "username": "tg_123456",
                        "telegramId": 123456,
                        "status": "ACTIVE",
                        "expireAt": "2030-01-01T00:00:00.000Z",
                    }
                }
            )
            with self.assertRaises(PanelDataError):
                await client.extend_user(user, 30)
        finally:
            await client.close()

    async def test_panel_auth_errors_are_not_converted_to_success(self) -> None:
        transport = QueueTransport([(403, {"message": "Forbidden"})])
        client = RemnawaveClient(
            "https://panel.example.invalid", "token", transport=transport
        )
        try:
            with self.assertRaises(PanelAccessDenied):
                await client.get_user_by_telegram_id(123456)
        finally:
            await client.close()

    async def test_panel_timeout_is_reported_without_request_details(self) -> None:
        class TimeoutTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(
                self, request: httpx.Request
            ) -> httpx.Response:
                raise httpx.ConnectTimeout("sensitive request detail", request=request)

        client = RemnawaveClient(
            "https://panel.example.invalid",
            "test-api-token",
            transport=TimeoutTransport(),
        )
        try:
            with self.assertRaises(PanelUnavailable) as raised:
                await client.get_user_by_telegram_id(123456)
            self.assertEqual(str(raised.exception), "Panel request failed")
            self.assertNotIn("test-api-token", str(raised.exception))
            self.assertNotIn("sensitive request detail", str(raised.exception))
        finally:
            await client.close()

    async def test_panel_server_errors_are_reported_without_response_body(self) -> None:
        transport = QueueTransport(
            [(500, {"message": "must-not-be-disclosed", "token": "fake-secret"})]
        )
        client = RemnawaveClient(
            "https://panel.example.invalid", "token", transport=transport
        )
        try:
            with self.assertRaises(PanelUnavailable) as raised:
                await client.get_user_by_telegram_id(123456)
            self.assertNotIn("must-not-be-disclosed", str(raised.exception))
            self.assertNotIn("fake-secret", str(raised.exception))
        finally:
            await client.close()

    async def test_redirect_response_is_not_followed_or_treated_as_success(self) -> None:
        transport = QueueTransport(
            [(302, {"response": {"id": 41}, "location": "https://elsewhere.invalid"})]
        )
        client = RemnawaveClient(
            "https://panel.example.invalid", "token", transport=transport
        )
        try:
            with self.assertRaises(PanelUnavailable):
                await client.get_user_by_telegram_id(123456)
            self.assertEqual(len(transport.requests), 1)
        finally:
            await client.close()

    async def test_unexpected_node_response_is_a_data_error(self) -> None:
        client = RemnawaveClient(
            "https://panel.example.invalid",
            "token",
            transport=QueueTransport([(200, {"response": [{"name": "bridge"}]})]),
        )
        try:
            with self.assertRaises(PanelDataError):
                await client.get_nodes()
        finally:
            await client.close()

    async def test_revoke_sends_empty_schema_body_and_confirms_subscription_rotation(self) -> None:
        transport = QueueTransport(
            [
                (
                    200,
                    {
                        "response": {
                            "id": 52,
                            "username": "tg_123456",
                            "telegramId": 123456,
                            "status": "ACTIVE",
                            "expireAt": "2030-01-01T00:00:00.000Z",
                            "subscriptionUrl": "https://sub.example.invalid/new-id",
                        }
                    },
                )
            ]
        )
        client = RemnawaveClient(
            "https://panel.example.invalid", "token", transport=transport
        )
        user = client._user(
            {
                "response": {
                    "id": 52,
                    "username": "tg_123456",
                    "telegramId": 123456,
                    "status": "ACTIVE",
                    "expireAt": "2030-01-01T00:00:00.000Z",
                    "subscriptionUrl": "https://sub.example.invalid/old-id",
                }
            }
        )
        try:
            revoked = await client.revoke_subscription(user)
            self.assertEqual(transport.requests[0].method, "POST")
            self.assertEqual(
                transport.requests[0].url.path,
                "/api/users/52/actions/revoke",
            )
            self.assertEqual(json.loads(transport.requests[0].content), {})
            self.assertNotEqual(revoked.subscription_url, user.subscription_url)
        finally:
            await client.close()

    async def test_revoke_requires_panel_to_return_rotated_subscription_url(self) -> None:
        transport = QueueTransport(
            [
                (
                    200,
                    {
                        "response": {
                            "id": 52,
                            "username": "tg_123456",
                            "telegramId": 123456,
                            "status": "ACTIVE",
                            "expireAt": "2030-01-01T00:00:00.000Z",
                            "subscriptionUrl": "https://sub.example.invalid/old-id",
                        }
                    },
                )
            ]
        )
        client = RemnawaveClient(
            "https://panel.example.invalid", "token", transport=transport
        )
        user = client._user(
            {
                "response": {
                    "id": 52,
                    "username": "tg_123456",
                    "telegramId": 123456,
                    "status": "ACTIVE",
                    "expireAt": "2030-01-01T00:00:00.000Z",
                    "subscriptionUrl": "https://sub.example.invalid/old-id",
                }
            }
        )
        try:
            with self.assertRaises(PanelDataError):
                await client.revoke_subscription(user)
        finally:
            await client.close()

    async def test_panel_session_can_be_closed(self) -> None:
        client = RemnawaveClient(
            "https://panel.example.invalid",
            "token",
            transport=QueueTransport([]),
        )
        await client.close()
        self.assertTrue(client._client.is_closed)

    async def test_subscription_url_must_use_https(self) -> None:
        client = RemnawaveClient(
            "https://panel.example.invalid",
            "token",
            transport=QueueTransport([]),
        )
        try:
            user = client._user(
                {
                    "response": {
                        "id": 52,
                        "username": "tg_123456",
                        "telegramId": 123456,
                        "status": "ACTIVE",
                        "expireAt": "2030-01-01T00:00:00.000Z",
                        "subscriptionUrl": "http://attacker.invalid/secret",
                    }
                }
            )
            with self.assertRaises(PanelDataError):
                client.safe_subscription_url(user)
        finally:
            await client.close()

    async def test_subscription_url_must_not_contain_embedded_credentials(self) -> None:
        client = RemnawaveClient(
            "https://panel.example.invalid",
            "token",
            transport=QueueTransport([]),
        )
        try:
            user = client._user(
                {
                    "response": {
                        "id": 52,
                        "username": "tg_123456",
                        "telegramId": 123456,
                        "status": "ACTIVE",
                        "expireAt": "2030-01-01T00:00:00.000Z",
                        "subscriptionUrl": "https://user:password@sub.example.invalid/id",
                    }
                }
            )
            with self.assertRaises(PanelDataError):
                client.safe_subscription_url(user)
        finally:
            await client.close()

    async def test_disable_requires_panel_confirmation(self) -> None:
        transport = QueueTransport([(200, {"response": {"id": 52}})])
        client = RemnawaveClient(
            "https://panel.example.invalid", "token", transport=transport
        )
        try:
            user = client._user(
                {
                    "response": {
                        "id": 52,
                        "username": "tg_123456",
                        "telegramId": 123456,
                        "status": "ACTIVE",
                        "expireAt": "2030-01-01T00:00:00.000Z",
                    }
                }
            )
            with self.assertRaises(PanelDataError):
                await client.disable_user(user)
        finally:
            await client.close()


    async def test_enable_uses_enable_endpoint_and_checks_binding(self) -> None:
        transport = QueueTransport(
            [
                (
                    200,
                    {
                        "response": {
                            "id": 52,
                            "username": "tg_123456",
                            "telegramId": 123456,
                            "status": "ACTIVE",
                            "expireAt": "2030-01-01T00:00:00.000Z",
                        }
                    },
                ),
                (
                    200,
                    {
                        "response": {
                            "id": 52,
                            "username": "tg_123456",
                            "telegramId": 999999,
                            "status": "ACTIVE",
                            "expireAt": "2030-01-01T00:00:00.000Z",
                        }
                    },
                ),
            ]
        )
        client = RemnawaveClient(
            "https://panel.example.invalid", "token", transport=transport
        )
        try:
            user = PanelUser(
                id=52,
                username="tg_123456",
                telegram_id=123456,
                status="DISABLED",
                expire_at="2030-01-01T00:00:00.000Z",
                subscription_url=None,
            )
            enabled = await client.enable_user(user)
            self.assertEqual(enabled.status, "ACTIVE")
            self.assertEqual(transport.requests[0].url.path, "/api/users/52/actions/enable")
            with self.assertRaises(PanelDataError):
                await client.enable_user(user)
        finally:
            await client.close()

    async def test_enable_rejects_panel_response_that_remains_disabled(self) -> None:
        transport = QueueTransport(
            [
                (
                    200,
                    {
                        "response": {
                            "id": 52,
                            "username": "tg_123456",
                            "telegramId": 123456,
                            "status": "DISABLED",
                            "expireAt": "2030-01-01T00:00:00.000Z",
                        }
                    },
                )
            ]
        )
        client = RemnawaveClient(
            "https://panel.example.invalid", "token", transport=transport
        )
        try:
            user = PanelUser(
                id=52,
                username="tg_123456",
                telegram_id=123456,
                status="DISABLED",
                expire_at="2030-01-01T00:00:00.000Z",
                subscription_url=None,
            )
            with self.assertRaises(PanelDataError):
                await client.enable_user(user)
        finally:
            await client.close()


class PingCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_ping_reports_build_api_latency_and_uptime(self) -> None:
        from unittest.mock import patch

        answers: list[str] = []

        class Bot:
            async def get_me(self):
                return object()

        class Message:
            async def answer(self, text: str) -> None:
                answers.append(text)

        with (
            patch("jopesvpn.bot.PROCESS_STARTED_AT", 10.0),
            patch("jopesvpn.bot.time.monotonic", return_value=93_794.0),
            patch(
                "jopesvpn.bot.time.perf_counter",
                side_effect=(100.0, 100.042),
            ),
        ):
            await ping_command(Message(), Bot())

        self.assertEqual(len(answers), 1)
        self.assertIn("Сборка: JV-PAYMENTS-R6", answers[0])
        self.assertIn("Telegram API: 42 мс", answers[0])
        self.assertIn("Uptime: 1д 02:03:04", answers[0])


class BotAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    def test_expired_callback_query_errors_are_narrowly_recognized(self) -> None:
        stale = TelegramBadRequest(
            method="answerCallbackQuery",
            message="query is too old and response timeout expired",
        )
        unrelated = TelegramBadRequest(
            method="answerCallbackQuery",
            message="message is not modified",
        )
        self.assertTrue(_is_stale_callback_query_error(stale))
        self.assertFalse(_is_stale_callback_query_error(unrelated))
        self.assertFalse(_is_stale_callback_query_error(ValueError("invalid")))

    def test_admin_check_uses_sender_not_command_target(self) -> None:
        settings = Settings(
            bot_token="token",
            panel_api_url="https://panel.example.invalid",
            panel_api_token="panel-token",
            admin_telegram_ids=frozenset({1001}),
        )
        admin_message = SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            from_user=SimpleNamespace(id=1001),
            text="/admin_revoke 2002",
        )
        non_admin_message = SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            from_user=SimpleNamespace(id=2002),
            text="/admin_revoke 1001",
        )
        self.assertTrue(_is_admin(admin_message, settings))
        self.assertFalse(_admin_only(non_admin_message, settings))

    def test_admin_commands_are_private_chat_only(self) -> None:
        settings = Settings(
            bot_token="token",
            panel_api_url="https://panel.example.invalid",
            panel_api_token="panel-token",
            admin_telegram_ids=frozenset({1001}),
        )
        group_message = SimpleNamespace(
            chat=SimpleNamespace(type="group"),
            from_user=SimpleNamespace(id=1001),
        )
        self.assertFalse(_admin_only(group_message, settings))

    def test_settings_reject_non_https_panel_api(self) -> None:
        import os
        from unittest.mock import patch

        env = {
            "BOT_TOKEN": "123:token",
            "PANEL_API_URL": "http://panel.example.invalid",
            "PANEL_API_TOKEN": "panel-token",
            "BOT_ADMIN_IDS": "1001",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ConfigurationError):
                Settings.from_env(env_file="Z:\\missing\\no-dotenv-file")

    def test_free_access_requires_explicit_enablement(self) -> None:
        import os
        from unittest.mock import patch

        env = {
            "BOT_TOKEN": "123:token",
            "PANEL_API_URL": "https://panel.example.com",
            "PANEL_API_TOKEN": "panel-token",
            "BOT_ADMIN_IDS": "1001",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env(env_file="Z:\\missing\\no-dotenv-file")
        self.assertFalse(settings.free_access_enabled)
        with patch.dict(os.environ, {**env, "ENABLE_FREE_ACCESS": "true"}, clear=True):
            settings = Settings.from_env(env_file="Z:\\missing\\no-dotenv-file")
        self.assertTrue(settings.free_access_enabled)

    def test_platega_proxy_setting_requires_single_ip_address(self) -> None:
        import os
        from unittest.mock import patch

        env = {
            "BOT_TOKEN": "123:token",
            "PANEL_API_URL": "https://panel.example.com",
            "PANEL_API_TOKEN": "panel-token",
            "BOT_ADMIN_IDS": "1001",
        }
        for address, expected in (
            ("", None),
            (" 172.30.50.11 ", "172.30.50.11"),
            ("2001:0db8::1", "2001:db8::1"),
        ):
            with self.subTest(address=address), patch.dict(
                os.environ, {**env, "PLATEGA_TRUSTED_PROXY_IP": address}, clear=True
            ):
                settings = Settings.from_env(env_file="Z:\\missing\\no-dotenv-file")
                self.assertEqual(settings.platega_trusted_proxy_ip, expected)
        for address in ("proxy.example.test", "172.30.50.11, 172.30.50.12", "172.30.50.0/24"):
            with self.subTest(address=address), patch.dict(
                os.environ, {**env, "PLATEGA_TRUSTED_PROXY_IP": address}, clear=True
            ), self.assertRaises(ConfigurationError):
                Settings.from_env(env_file="Z:\\missing\\no-dotenv-file")

    def test_settings_reject_panel_url_credentials(self) -> None:
        import os
        from unittest.mock import patch

        env = {
            "BOT_TOKEN": "123:token",
            "PANEL_API_URL": "https://user:password@panel.example.invalid",
            "PANEL_API_TOKEN": "panel-token",
            "BOT_ADMIN_IDS": "1001",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ConfigurationError):
                Settings.from_env(env_file="Z:\\missing\\no-dotenv-file")

    def test_settings_reject_reserved_invalid_panel_host(self) -> None:
        import os
        from unittest.mock import patch

        env = {
            "BOT_TOKEN": "123:bot-token",
            "PANEL_API_URL": "https://panel.example.invalid",
            "PANEL_API_TOKEN": "test-panel-token",
            "BOT_ADMIN_IDS": "1001",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ConfigurationError):
                Settings.from_env(env_file="Z:\\missing\\no-dotenv-file")

    def test_settings_require_real_panel_token_for_bot_run(self) -> None:
        import os
        from unittest.mock import patch

        env = {
            "BOT_TOKEN": "123:bot-token",
            "PANEL_API_URL": "https://panel.example.invalid",
            "PANEL_API_TOKEN": "REPLACE_WITH_SCOPED_REWAVE_API_TOKEN",
            "BOT_ADMIN_IDS": "1001",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ConfigurationError):
                Settings.from_env(env_file="Z:\\missing\\no-dotenv-file")

    def test_settings_allow_telegram_check_without_panel_credentials(self) -> None:
        import os
        from unittest.mock import patch

        env = {
            "BOT_TOKEN": "123:bot-token",
            "BOT_ADMIN_IDS": "1001",
            "PANEL_API_TOKEN": "REPLACE_WITH_SCOPED_REWAVE_API_TOKEN",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env(
                env_file="Z:\\missing\\no-dotenv-file",
                require_panel=False,
            )
        self.assertFalse(settings.panel_api_token)
        self.assertFalse(settings.panel_api_url)

    def test_telegram_check_ignores_panel_placeholders_and_invalid_optional_values(self) -> None:
        import os
        from unittest.mock import patch

        env = {
            "BOT_TOKEN": "123:bot-token",
            "BOT_ADMIN_IDS": "1001",
            "PANEL_API_URL": "https://panel.example.invalid",
            "PANEL_API_TOKEN": "REPLACE_WITH_SCOPED_REWAVE_API_TOKEN",
            "PANEL_INTERNAL_SQUAD_UUID": "not-a-uuid",
            "PANEL_CA_BUNDLE": "missing-ca.pem",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env(
                env_file="Z:\\missing\\no-dotenv-file",
                require_panel=False,
            )
        self.assertFalse(settings.panel_api_url)
        self.assertFalse(settings.panel_api_token)
        self.assertIsNone(settings.panel_internal_squad_uuid)
        self.assertIsNone(settings.panel_ca_bundle)

    def test_settings_reject_empty_or_malformed_admin_list(self) -> None:
        import os
        from unittest.mock import patch

        for admin_ids in ("", "not-a-number", "0"):
            with self.subTest(admin_ids=admin_ids):
                env = {
                    "BOT_TOKEN": "123:bot-token",
                    "PANEL_API_URL": "https://panel.example.invalid",
                    "PANEL_API_TOKEN": "api-token",
                    "BOT_ADMIN_IDS": admin_ids,
                }
                with patch.dict(os.environ, env, clear=True):
                    with self.assertRaises(ConfigurationError):
                        Settings.from_env(env_file="Z:\\missing\\no-dotenv-file")

    def test_settings_parse_trial_channel_and_reject_invalid_days(self) -> None:
        import os
        from unittest.mock import patch

        env = {
            "BOT_TOKEN": "123:bot-token",
            "BOT_ADMIN_IDS": "1001",
            "TRIAL_CHANNEL_USERNAME": "@jopesvpn",
            "TRIAL_DAYS": "3",
            "SUBSCRIPTION_PAGE_URL": "https://page.example.test/",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env(
                env_file="Z:\\missing\\no-dotenv-file",
                require_panel=False,
            )
        self.assertEqual(settings.trial_channel_username, "jopesvpn")
        self.assertEqual(settings.trial_channel_url, "https://t.me/jopesvpn")
        self.assertEqual(settings.trial_days, 3)
        self.assertEqual(settings.subscription_page_url, "https://page.example.test")

        for page_url in ("http://page.example.test", "https://x.test/sub"):
            with self.subTest(page_url=page_url):
                env["SUBSCRIPTION_PAGE_URL"] = page_url
                with patch.dict(os.environ, env, clear=True):
                    with self.assertRaises(ConfigurationError):
                        Settings.from_env(
                            env_file="Z:\\missing\\no-dotenv-file",
                            require_panel=False,
                        )
        env["SUBSCRIPTION_PAGE_URL"] = "https://page.example.test"

        for days in ("0", "366", "not-a-number"):
            with self.subTest(days=days):
                env["TRIAL_DAYS"] = days
                with patch.dict(os.environ, env, clear=True):
                    with self.assertRaises(ConfigurationError):
                        Settings.from_env(
                            env_file="Z:\\missing\\no-dotenv-file",
                            require_panel=False,
                        )

    async def test_home_panel_status_reports_missing_user_honestly(self) -> None:
        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                raise PanelNotFound("missing")

        text = await _home_text("Test", 12345, Panel())
        self.assertIn("Подписки нет", text)
        self.assertNotIn("подключён", text.lower())

    async def test_home_only_offers_unused_trial_for_unlinked_user(self) -> None:
        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                raise PanelNotFound("missing")

        with tempfile.TemporaryDirectory() as directory:
            prefs = PreferencesStore(Path(directory) / "state.sqlite3")
            prefs.register_start(12345)
            _, _, has_access, trial_available = await _home_view(
                "Test", 12345, Panel(), prefs=prefs
            )
            self.assertFalse(has_access)
            self.assertTrue(trial_available)

            prefs.mark_trial_used(12345)
            _, _, has_access, trial_available = await _home_view(
                "Test", 12345, Panel(), prefs=prefs
            )
            self.assertFalse(has_access)
            self.assertFalse(trial_available)


class BotCommandTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def bot():
        async def send_message(*args, **kwargs) -> None:
            return None

        return SimpleNamespace(send_message=send_message)

    def preferences(self) -> PreferencesStore:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return PreferencesStore(Path(directory.name) / "state.sqlite3")

    @staticmethod
    def message(*, telegram_id: int, text: str):
        answers: list[str] = []
        message = SimpleNamespace()

        async def answer(value: str, **kwargs) -> None:
            answers.append(value)
            message.reply_markup = kwargs.get("reply_markup")

        message.chat = SimpleNamespace(type="private")
        message.from_user = SimpleNamespace(id=telegram_id, first_name="Tester")
        message.text = text
        message.answer = answer
        return message, answers

    @staticmethod
    def settings() -> Settings:
        return Settings(
            bot_token="token",
            panel_api_url="https://panel.example.invalid",
            panel_api_token="panel-token",
            admin_telegram_ids=frozenset({1001}),
        )

    async def test_connect_does_not_disclose_a_subscription_for_another_telegram_id(self):
        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                raise PanelNotFound("missing")

        message, answers = self.message(telegram_id=1001, text="/connect")
        await connect_command(message, Panel(), self.settings())
        self.assertEqual(len(answers), 1)
        self.assertNotIn("https://", answers[0])
        self.assertIn("Сначала получите доступ", answers[0])

    async def test_connect_ignores_user_supplied_target_id(self):
        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                raise PanelNotFound("missing")

        message, answers = self.message(
            telegram_id=1001, text="/connect 2002"
        )
        await connect_command(message, Panel(), self.settings())
        self.assertNotIn("https://", answers[0])

    async def test_admin_revoke_calls_panel_revoke_action(self):
        calls: list[str] = []

        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                return SimpleNamespace(id=52)

            async def revoke_subscription(self, user) -> None:
                calls.append("revoke")

            async def disable_user(self, user) -> None:
                calls.append("disable")

        message, answers = self.message(
            telegram_id=1001, text="/admin_revoke 2002"
        )
        await admin_revoke(message, Panel(), self.settings())
        self.assertEqual(calls, [])
        self.assertIn("Замена ссылки подписки", answers[0])
        self.assertEqual(
            message.reply_markup.inline_keyboard[0][0].callback_data,
            "admin:run:revoke:2002",
        )

    async def test_admin_user_row_opens_card_without_preferences_store(self) -> None:
        displays: list[str] = []
        markups = []

        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                return PanelUser(
                    id=52,
                    username=f"tg_{telegram_id}",
                    telegram_id=telegram_id,
                    status="ACTIVE",
                    expire_at=None,
                    subscription_url=None,
                )

            async def get_user_devices(self, user):
                raise PanelUnavailable("device statistics unavailable")

        async def answer_callback(*args, **kwargs) -> None:
            return None

        async def edit_text(text: str, **kwargs) -> None:
            displays.append(text)
            markups.append(kwargs.get("reply_markup"))

        message = SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            edit_text=edit_text,
            answer=edit_text,
        )
        callback = SimpleNamespace(
            data="admin:u:2002:3",
            message=message,
            from_user=SimpleNamespace(id=1001),
            answer=answer_callback,
        )
        await admin_navigation(
            callback,
            self.bot(),
            Panel(),
            self.settings(),
            FakeState(),
            prefs=None,
        )
        self.assertIn("КАРТОЧКА ПОЛЬЗОВАТЕЛЯ", displays[-1])
        callbacks = {
            button.callback_data
            for row in markups[-1].inline_keyboard
            for button in row
            if button.callback_data
        }
        self.assertIn("admin:list:3", callbacks)

    async def test_admin_id_search_keeps_search_available_when_not_found(self) -> None:
        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                raise PanelNotFound("missing")

        state = FakeState()
        state.state = AdminFlow.find_user.state
        await state.update_data(return_page=4)
        message, answers = self.message(telegram_id=1001, text="2002")
        await admin_find_input(message, Panel(), self.settings(), state)
        self.assertEqual(state.state, AdminFlow.find_user.state)
        self.assertIn("не найден", answers[-1].casefold())
        callbacks = {
            button.callback_data
            for row in message.reply_markup.inline_keyboard
            for button in row
            if button.callback_data
        }
        self.assertIn("admin:find:4", callbacks)

    async def test_admin_id_search_opens_found_user_card(self) -> None:
        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                return PanelUser(
                    id=52,
                    username=f"tg_{telegram_id}",
                    telegram_id=telegram_id,
                    status="ACTIVE",
                    expire_at=None,
                    subscription_url=None,
                )

            async def get_user_devices(self, user):
                return []

        state = FakeState()
        state.state = AdminFlow.find_user.state
        await state.update_data(return_page=2)
        message, answers = self.message(telegram_id=1001, text="2002")
        await admin_find_input(message, Panel(), self.settings(), state)
        self.assertIsNone(state.state)
        self.assertTrue(answers)
        self.assertIn("КАРТОЧКА ПОЛЬЗОВАТЕЛЯ", answers[-1])
        callbacks = {
            button.callback_data
            for row in message.reply_markup.inline_keyboard
            for button in row
            if button.callback_data
        }
        self.assertIn("admin:list:2", callbacks)

    async def test_admin_id_search_rejects_invalid_value_without_clearing_state(self) -> None:
        state = FakeState()
        state.state = AdminFlow.find_user.state
        message, answers = self.message(telegram_id=1001, text="-2")
        await admin_find_input(message, object(), self.settings(), state)
        self.assertEqual(state.state, AdminFlow.find_user.state)
        self.assertTrue(answers)

    async def test_admin_disable_calls_disable_action_not_subscription_revoke(self):
        calls: list[str] = []

        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                return SimpleNamespace(id=52, status="ACTIVE")

            async def disable_user(self, user) -> None:
                calls.append("disable")

            async def revoke_subscription(self, user) -> None:
                calls.append("revoke")

        message, answers = self.message(
            telegram_id=1001, text="/admin_disable 2002"
        )
        await admin_disable(message, Panel(), self.settings())
        self.assertEqual(calls, [])
        self.assertIn("DISABLED", answers[0])
        self.assertEqual(
            message.reply_markup.inline_keyboard[0][0].callback_data,
            "admin:run:disable:2002",
        )

    async def test_admin_enable_uses_enable_action(self):
        calls: list[str] = []

        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                return SimpleNamespace(id=52, status="DISABLED")

            async def enable_user(self, user) -> None:
                calls.append("enable")

        message, answers = self.message(
            telegram_id=1001, text="/admin_enable 2002"
        )
        await admin_enable(message, Panel(), self.settings())
        self.assertEqual(calls, [])
        self.assertIn("Включение", answers[0])
        self.assertEqual(
            message.reply_markup.inline_keyboard[0][0].callback_data,
            "admin:run:enable:2002",
        )

    async def test_admin_enable_runs_only_after_callback_confirmation(self):
        actions: list[str] = []
        displays: list[str] = []

        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                actions.append("lookup")
                return SimpleNamespace(
                    id=72,
                    username="tg_12345",
                    telegram_id=12345,
                    status="DISABLED",
                    expire_at="2030-01-01T00:00:00Z",
                    subscription_url=None,
                )

            async def enable_user(self, user):
                actions.append("enable")
                return SimpleNamespace(
                    id=user.id,
                    telegram_id=user.telegram_id,
                    status="ACTIVE",
                    expire_at=user.expire_at,
                )

        state = FakeState()

        async def answer(text: str = "", **kwargs) -> None:
            return None

        async def edit_text(text: str, **kwargs) -> None:
            displays.append(text)

        async def answer(text: str = "", **kwargs) -> None:
            return None

        message = SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            edit_text=edit_text,
            answer=answer,
        )

        def callback(data: str):
            return SimpleNamespace(
                data=data,
                message=message,
                from_user=SimpleNamespace(id=1001),
                answer=answer,
            )

        await admin_navigation(
            callback("admin:run:enable:12345"),
            self.bot(),
            Panel(),
            self.settings(),
            state,
        )
        self.assertEqual(actions, [])
        await admin_navigation(
            callback("admin:confirm:enable:12345"),
            self.bot(),
            Panel(),
            self.settings(),
            state,
        )
        self.assertEqual(actions, [])
        await admin_navigation(
            callback("admin:run:enable:12345"),
            self.bot(),
            Panel(),
            self.settings(),
            state,
        )
        self.assertEqual(actions, ["lookup", "enable"])
        self.assertIn("Статус доступа обновлён", displays[-1])

    async def test_admin_enable_reports_limited_status_without_claiming_access(self):
        displays: list[str] = []
        notifications: list[str] = []

        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                return SimpleNamespace(
                    id=72,
                    username="tg_12345",
                    telegram_id=12345,
                    status="DISABLED",
                    expire_at="2030-01-01T00:00:00Z",
                    subscription_url=None,
                )

            async def enable_user(self, user):
                return SimpleNamespace(
                    id=user.id,
                    telegram_id=user.telegram_id,
                    status="LIMITED",
                    expire_at=user.expire_at,
                )

        state = FakeState()
        await state.update_data(action="enable", telegram_id=12345)
        await state.set_state(AdminFlow.action_confirm)

        async def answer(text: str = "", **kwargs) -> None:
            return None

        async def edit_text(text: str, **kwargs) -> None:
            displays.append(text)

        message = SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            edit_text=edit_text,
            answer=answer,
        )
        callback = SimpleNamespace(
            data="admin:run:enable:12345",
            message=message,
            from_user=SimpleNamespace(id=1001),
            answer=answer,
        )

        async def send_message(_telegram_id: int, text: str, **kwargs) -> None:
            notifications.append(text)

        await admin_navigation(
            callback,
            SimpleNamespace(send_message=send_message),
            Panel(),
            self.settings(),
            state,
        )
        self.assertIn("ДОСТУП ОСТАЁТСЯ ОГРАНИЧЕННЫМ", displays[-1])
        self.assertNotIn("ДОСТУП ВКЛЮЧЁН", displays[-1])
        self.assertIn("ограниченным", notifications[-1])

    async def test_grant_does_not_create_an_existing_panel_user_again(self):
        calls: list[str] = []

        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                return SimpleNamespace(id=52, telegram_id=telegram_id)

            async def create_user(self, *args):
                calls.append("create")
                raise AssertionError("existing user must not be created again")

        settings = Settings(
            bot_token="token",
            panel_api_url="https://panel.example.invalid",
            panel_api_token="panel-token",
            admin_telegram_ids=frozenset({1001}),
            panel_internal_squad_uuid=str(uuid4()),
        )
        message, answers = self.message(
            telegram_id=1001, text="/admin_grant 2002 30"
        )
        class State:
            async def clear(self) -> None:
                return None

            async def update_data(self, **kwargs) -> None:
                return None

            async def set_state(self, state) -> None:
                return None

        await admin_grant(message, Panel(), settings, State())
        self.assertEqual(calls, [])
        self.assertIn("уже существует", answers[0])

    async def test_every_admin_command_rejects_non_admin_sender_before_panel_calls(self):
        calls: list[str] = []

        class Panel:
            async def __getattribute__(self, name: str):
                if name in {"health", "get_nodes", "get_user_by_telegram_id",
                            "create_user", "disable_user", "enable_user",
                            "revoke_subscription"}:
                    calls.append(name)
                    raise AssertionError("Panel must not be called for non-admin")
                return object.__getattribute__(self, name)

        from jopesvpn.bot import (
            admin_extend,
            admin_grant,
            admin_status,
            admin_user,
        )

        commands = (
            (admin_status, "/admin_status"),
            (admin_user, "/admin_user 2002"),
            (admin_grant, "/admin_grant 2002 30"),
            (admin_extend, "/admin_extend 2002 30"),
            (admin_disable, "/admin_disable 2002"),
            (admin_enable, "/admin_enable 2002"),
            (admin_revoke, "/admin_revoke 2002"),
        )
        for handler, text in commands:
            with self.subTest(command=text):
                message, answers = self.message(telegram_id=2002, text=text)
                await handler(message, Panel(), self.settings())
                self.assertEqual(answers, ["Команда недоступна."])
        self.assertEqual(calls, [])

    async def test_admin_callback_rechecks_sender_before_panel_operations(self):
        calls: list[str] = []
        answers: list[tuple[str, bool]] = []

        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                calls.append("lookup")
                raise AssertionError("unauthorized callback must not access Panel")

        async def answer(text: str = "", **kwargs) -> None:
            answers.append((text, kwargs.get("show_alert", False)))

        callback = SimpleNamespace(
            data="admin:run:disable:1001",
            message=SimpleNamespace(chat=SimpleNamespace(type="private")),
            from_user=SimpleNamespace(id=2002),
            answer=answer,
        )
        await admin_navigation(
            callback,
            self.bot(),
            Panel(),
            self.settings(),
            SimpleNamespace(clear=lambda: None),
        )
        self.assertEqual(calls, [])
        self.assertTrue(answers[0][1])

    async def test_admin_action_runs_only_after_callback_confirmation(self):
        actions: list[str] = []
        displays: list[str] = []

        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                actions.append("lookup")
                return SimpleNamespace(
                    id=72,
                    username="tg_12345",
                    telegram_id=12345,
                    status="ACTIVE",
                    expire_at="2030-01-01T00:00:00Z",
                    subscription_url=None,
                )

            async def disable_user(self, user) -> None:
                actions.append("disable")

        state = FakeState()

        async def answer(text: str = "", **kwargs) -> None:
            return None

        async def edit_text(text: str, **kwargs) -> None:
            displays.append(text)

        async def answer(text: str = "", **kwargs) -> None:
            return None

        message = SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            edit_text=edit_text,
            answer=answer,
        )

        def callback(data: str):
            return SimpleNamespace(
                data=data,
                message=message,
                from_user=SimpleNamespace(id=1001),
                answer=answer,
            )

        await admin_navigation(
            callback("admin:confirm:disable:12345"),
            self.bot(),
            Panel(),
            self.settings(),
            state,
        )
        self.assertEqual(actions, [])
        await admin_navigation(
            callback("admin:run:disable:12345"),
            self.bot(),
            Panel(),
            self.settings(),
            state,
        )
        self.assertEqual(actions, ["lookup", "disable"])
        self.assertIn("Disable подтверждён", displays[-1])

    async def test_stale_disable_confirmation_does_not_repeat_the_action(self):
        actions: list[str] = []
        alerts: list[str] = []

        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                actions.append("lookup")
                return SimpleNamespace(
                    id=72,
                    username="tg_12345",
                    telegram_id=12345,
                    status="ACTIVE",
                    expire_at="2030-01-01T00:00:00Z",
                    subscription_url=None,
                )

            async def disable_user(self, user) -> None:
                actions.append("disable")

        state = FakeState()

        async def answer(text: str = "", **kwargs) -> None:
            alerts.append(text)

        async def edit_text(text: str, **kwargs) -> None:
            return None

        message = SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            edit_text=edit_text,
            answer=answer,
        )

        def callback(data: str):
            return SimpleNamespace(
                data=data,
                message=message,
                from_user=SimpleNamespace(id=1001),
                answer=answer,
            )

        await admin_navigation(
            callback("admin:confirm:disable:12345"),
            self.bot(),
            Panel(),
            self.settings(),
            state,
        )
        await admin_navigation(
            callback("admin:run:disable:12345"),
            self.bot(),
            Panel(),
            self.settings(),
            state,
        )
        await admin_navigation(
            callback("admin:run:disable:12345"),
            self.bot(),
            Panel(),
            self.settings(),
            state,
        )
        self.assertEqual(actions, ["lookup", "disable"])
        self.assertIn("устарело", alerts[-1])

    async def test_extend_confirms_and_extends_only_once(self):
        calls: list[tuple[int, int]] = []
        displays: list[str] = []

        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                return SimpleNamespace(
                    id=72,
                    username="tg_12345",
                    telegram_id=12345,
                    status="ACTIVE",
                    expire_at="2030-01-01T00:00:00Z",
                    subscription_url=None,
                )

            async def extend_user(self, user, days: int):
                calls.append((user.id, days))
                return SimpleNamespace(
                    id=user.id,
                    username=user.username,
                    telegram_id=user.telegram_id,
                    status="ACTIVE",
                    expire_at="2030-01-31T00:00:00Z",
                    subscription_url=None,
                )

        state = FakeState()
        await state.update_data(telegram_id=12345, days=30)
        await state.set_state(AdminFlow.extend_confirm)

        async def answer(text: str = "", **kwargs) -> None:
            return None

        async def edit_text(text: str, **kwargs) -> None:
            displays.append(text)

        async def answer(text: str = "", **kwargs) -> None:
            return None

        message = SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            edit_text=edit_text,
            answer=answer,
        )

        def callback(data: str):
            return SimpleNamespace(
                data=data,
                message=message,
                from_user=SimpleNamespace(id=1001),
                answer=answer,
            )

        await admin_navigation(
            callback("admin:run:extend"),
            self.bot(),
            Panel(),
            self.settings(),
            state,
        )
        await admin_navigation(
            callback("admin:run:extend"),
            self.bot(),
            Panel(),
            self.settings(),
            state,
        )
        self.assertEqual(calls, [(72, 30)])
        self.assertIn("СРОК ПРОДЛЁН", displays[-1])


    async def test_active_subscription_view_hides_raw_link_behind_action(self):
        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                return PanelUser(
                    id=7,
                    username="tg_1001",
                    telegram_id=1001,
                    status="ACTIVE",
                    expire_at="2030-01-01T00:00:00Z",
                    subscription_url="https://sub.example.test/secret",
                )

        text, page_url = await _subscription_view(
            1001, Panel(), "https://page.example.test"
        )
        self.assertNotIn("https://sub.example.test/secret", text)
        self.assertIn("01.01.2030", text)
        self.assertIn("страница подписки", text.lower())
        self.assertEqual(page_url, "https://page.example.test/secret")

    async def test_disabled_and_expired_subscriptions_never_show_a_link(self):
        for status in ("DISABLED", "EXPIRED", "LIMITED"):
            with self.subTest(status=status):
                class Panel:
                    async def get_user_by_telegram_id(self, telegram_id: int):
                        return PanelUser(
                            id=7,
                            username="tg_1001",
                            telegram_id=1001,
                            status=status,
                            expire_at="2030-01-01T00:00:00Z",
                            subscription_url="https://sub.example.test/secret",
                        )

                text, page_url = await _subscription_view(
                    1001, Panel(), "https://page.example.test"
                )
                self.assertNotIn("https://", text)
                self.assertIsNone(page_url)
                self.assertIn("01.01.2030", text)

    def test_plan_catalog_shows_only_starting_ruble_prices(self):
        plans = load_plans()
        text = plans_text(plans, category="time")
        self.assertIn("Цена за минимальный срок: 26,66 ₽", text)
        self.assertIn("безлимитный трафик на срок", text.casefold())
        self.assertIn("Минимальный срок: 7 дн.", text)
        self.assertNotIn("⭐", text)


    async def test_trial_tariff_is_shown_in_subscription(self):
        user = PanelUser(
            id=94,
            username="tg_12345",
            telegram_id=12345,
            status="ACTIVE",
            expire_at="2030-01-01T00:00:00Z",
            subscription_url="https://sub.example.invalid/id",
            tag="TRIAL",
        )
        text = subscription_text(user, "https://sub.example.invalid/id")
        self.assertIn("Тариф: Пробный", text)
        self.assertNotIn("🎁", text)

    async def test_trial_creates_three_day_account_with_trial_tag(self):
        created: list[tuple] = []
        squad_uuid = str(uuid4())

        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                raise PanelNotFound("missing")

            def safe_subscription_url(self, user):
                return "https://sub.example.invalid/trial"

            async def create_user(
                self, telegram_id: int, days: int, squad: str, *, tag=None
            ):
                created.append((telegram_id, days, squad, tag))
                return PanelUser(
                    id=91,
                    username=f"tg_{telegram_id}",
                    telegram_id=telegram_id,
                    status="ACTIVE",
                    expire_at="2030-01-01T00:00:00Z",
                    subscription_url=None,
                    tag=tag,
                )

        class Bot:
            async def get_chat_member(self, *, chat_id: str, user_id: int):
                self.request = (chat_id, user_id)
                return SimpleNamespace(status="member")

        displays: list[str] = []

        async def edit_text(text: str, **kwargs) -> None:
            displays.append(text)

        async def answer(text: str = "", **kwargs) -> None:
            return None

        message = SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            edit_text=edit_text,
            answer=answer,
        )
        callback = SimpleNamespace(
            data="trial:check",
            message=message,
            from_user=SimpleNamespace(id=12345),
            answer=answer,
        )
        prefs = self.preferences()
        settings = Settings(
            bot_token="token",
            panel_api_url="https://panel.example.invalid",
            panel_api_token="panel-token",
            admin_telegram_ids=frozenset({1001}),
            panel_internal_squad_uuid=squad_uuid,
        )
        bot = Bot()
        await trial_check(
            callback,
            bot=bot,
            panel=Panel(),
            settings=settings,
            state=None,
            prefs=prefs,
        )
        self.assertEqual(created, [(12345, 3, squad_uuid, "TRIAL")])
        self.assertEqual(bot.request, ("@jopesvpn", 12345))
        self.assertIn("ПРОБНЫЙ ДОСТУП АКТИВИРОВАН", displays[-1])
        self.assertIn("Тариф: Пробный", displays[-1])
        self.assertTrue(prefs.trial_was_used(12345))

    async def test_referral_trial_is_standard_duration_without_day_bonus(self):
        created: list[tuple] = []
        extensions: list[tuple[int, int]] = []

        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                raise PanelNotFound("missing")

            async def create_user(
                self, telegram_id: int, days: int, squad: str, *, tag=None
            ):
                created.append((telegram_id, days, squad, tag))
                return PanelUser(
                    id=91,
                    username=f"tg_{telegram_id}",
                    telegram_id=telegram_id,
                    status="ACTIVE",
                    expire_at="2030-01-06T00:00:00Z",
                    subscription_url=None,
                    tag=tag,
                )

            async def extend_user(self, user: PanelUser, days: int):
                extensions.append((user.telegram_id, days))
                return user

            def safe_subscription_url(self, user: PanelUser) -> str:
                return "https://sub.example.invalid/trial"

        class Bot:
            async def get_chat_member(self, *, chat_id: str, user_id: int):
                return SimpleNamespace(status="member")

            async def send_message(self, **kwargs):
                return None

        async def edit_text(*args, **kwargs):
            return None

        async def answer(*args, **kwargs):
            return None

        with tempfile.TemporaryDirectory() as directory:
            prefs = PreferencesStore(Path(directory) / "state.sqlite3")
            prefs.register_start(12345, 1001)
            callback = SimpleNamespace(
                data="trial:check",
                message=SimpleNamespace(
                    chat=SimpleNamespace(type="private"),
                    edit_text=edit_text,
                    answer=answer,
                ),
                from_user=SimpleNamespace(id=12345),
                answer=answer,
            )
            settings = Settings(
                bot_token="token",
                panel_api_url="https://panel.example.invalid",
                panel_api_token="panel-token",
                admin_telegram_ids=frozenset({1001}),
                panel_internal_squad_uuid=str(uuid4()),
            )
            await trial_check(
                callback,
                bot=Bot(),
                panel=Panel(),
                settings=settings,
                state=None,
                prefs=prefs,
            )
            self.assertEqual(
                created,
                [(12345, settings.trial_days, settings.panel_internal_squad_uuid, "TRIAL")],
            )
            self.assertEqual(extensions, [])
            self.assertEqual(
                prefs.referral_stats(1001),
                {"invited": 1, "activated": 1, "reward_days": 0, "pending": 0},
            )
            self.assertTrue(prefs.trial_was_used(12345))

    async def test_trial_requires_channel_membership(self):
        created: list[tuple] = []

        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                raise PanelNotFound("missing")

            async def create_user(self, *args, **kwargs):
                created.append((args, kwargs))
                raise AssertionError("trial must not be created without membership")

        class Bot:
            async def get_chat_member(self, *, chat_id: str, user_id: int):
                return SimpleNamespace(status="left", is_member=False)

        displays: list[str] = []

        async def edit_text(text: str, **kwargs) -> None:
            displays.append(text)

        async def answer(text: str = "", **kwargs) -> None:
            return None

        message = SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            edit_text=edit_text,
            answer=answer,
        )
        callback = SimpleNamespace(
            data="trial:check",
            message=message,
            from_user=SimpleNamespace(id=12345),
            answer=answer,
        )
        await trial_check(
            callback,
            bot=Bot(),
            panel=Panel(),
            settings=Settings(
                bot_token="token",
                panel_api_url="https://panel.example.invalid",
                panel_api_token="panel-token",
                admin_telegram_ids=frozenset({1001}),
                panel_internal_squad_uuid=str(uuid4()),
            ),
            state=None,
            prefs=self.preferences(),
        )
        self.assertEqual(created, [])
        self.assertIn("ПОДПИСКА НА КАНАЛ НЕ НАЙДЕНА", displays[-1])

    async def test_trial_is_not_issued_twice_for_existing_account(self):
        calls: list[str] = []

        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                calls.append("lookup")
                return PanelUser(
                    id=92,
                    username=f"tg_{telegram_id}",
                    telegram_id=telegram_id,
                    status="EXPIRED",
                    expire_at="2020-01-01T00:00:00Z",
                    subscription_url=None,
                    tag="TRIAL",
                )

            async def create_user(self, *args, **kwargs):
                calls.append("create")
                raise AssertionError("trial must not be reissued")

        class Bot:
            async def get_chat_member(self, **kwargs):
                calls.append("membership")
                raise AssertionError("membership must not matter for an existing account")

        displays: list[str] = []

        async def edit_text(text: str, **kwargs) -> None:
            displays.append(text)

        async def answer(text: str = "", **kwargs) -> None:
            return None

        message = SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            edit_text=edit_text,
            answer=answer,
        )
        callback = SimpleNamespace(
            data="trial:check",
            message=message,
            from_user=SimpleNamespace(id=12345),
            answer=answer,
        )
        await trial_check(
            callback,
            bot=Bot(),
            panel=Panel(),
            settings=self.settings(),
            state=None,
            prefs=self.preferences(),
        )
        self.assertEqual(calls, ["lookup"])
        self.assertEqual(displays[-1], t("trial.unavailable_generic"))

    async def test_trial_does_not_duplicate_after_parallel_lookup(self):
        lookups = 0
        created: list[str] = []

        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                nonlocal lookups
                lookups += 1
                if lookups == 1:
                    raise PanelNotFound("missing")
                return PanelUser(
                    id=93,
                    username=f"tg_{telegram_id}",
                    telegram_id=telegram_id,
                    status="ACTIVE",
                    expire_at="2030-01-01T00:00:00Z",
                    subscription_url=None,
                    tag="TRIAL",
                )

            async def create_user(self, *args, **kwargs):
                created.append("create")
                raise AssertionError("second lookup must stop the duplicate")

        class Bot:
            async def get_chat_member(self, **kwargs):
                return SimpleNamespace(status="member")

        displays: list[str] = []

        async def edit_text(text: str, **kwargs) -> None:
            displays.append(text)

        async def answer(text: str = "", **kwargs) -> None:
            return None

        message = SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            edit_text=edit_text,
            answer=answer,
        )
        callback = SimpleNamespace(
            data="trial:check",
            message=message,
            from_user=SimpleNamespace(id=12345),
            answer=answer,
        )
        await trial_check(
            callback,
            bot=Bot(),
            panel=Panel(),
            settings=Settings(
                bot_token="token",
                panel_api_url="https://panel.example.invalid",
                panel_api_token="panel-token",
                admin_telegram_ids=frozenset({1001}),
                panel_internal_squad_uuid=str(uuid4()),
            ),
            state=None,
            prefs=self.preferences(),
        )
        self.assertEqual(created, [])
        self.assertEqual(displays[-1], t("trial.unavailable_generic"))

    async def test_admin_grant_creates_user_with_admin_tag(self):
        created: list[tuple] = []
        squad_uuid = str(uuid4())

        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                raise PanelNotFound("missing")

            async def create_user(
                self, telegram_id: int, days: int, squad: str, *, tag=None
            ):
                created.append((telegram_id, days, squad, tag))
                return PanelUser(
                    id=95,
                    username=f"tg_{telegram_id}",
                    telegram_id=telegram_id,
                    status="ACTIVE",
                    expire_at="2030-01-01T00:00:00Z",
                    subscription_url=None,
                    tag=tag,
                )

        displays: list[str] = []

        async def edit_text(text: str, **kwargs) -> None:
            displays.append(text)

        async def answer(text: str = "", **kwargs) -> None:
            return None

        message = SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            edit_text=edit_text,
            answer=answer,
        )
        callback = SimpleNamespace(
            data="admin:run:grant",
            message=message,
            from_user=SimpleNamespace(id=1001),
            answer=answer,
        )
        state = FakeState()
        state.state = AdminFlow.grant_confirm.state
        state.data = {"telegram_id": 12345, "days": 30}
        settings = Settings(
            bot_token="token",
            panel_api_url="https://panel.example.invalid",
            panel_api_token="panel-token",
            admin_telegram_ids=frozenset({1001}),
            panel_internal_squad_uuid=squad_uuid,
        )
        await admin_navigation(callback, self.bot(), Panel(), settings, state)
        self.assertEqual(created, [(12345, 30, squad_uuid, "ADMIN")])
        self.assertIn("Основной", displays[-1])

    async def test_admin_tariff_is_shown_in_subscription(self):
        user = PanelUser(
            id=96,
            username="tg_12345",
            telegram_id=12345,
            status="ACTIVE",
            expire_at="2030-01-01T00:00:00Z",
            subscription_url="https://sub.example.invalid/id",
            tag="ADMIN",
        )
        text = subscription_text(user, "https://sub.example.invalid/id")
        self.assertIn("Тариф: Основной", text)

class GuideNavigationTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def callback(*, chat_type: str = "private"):
        edits: list[str] = []
        alerts: list[str] = []
        callback = SimpleNamespace()
        callback.from_user = SimpleNamespace(id=1001, first_name="Tester")
        callback.data = "user:g:android:happ:1"

        async def answer(text=None, **kwargs) -> None:
            alerts.append(text or "")

        async def edit_text(text, **kwargs) -> None:
            edits.append(text)

        callback.answer = answer
        callback.message = SimpleNamespace(
            chat=SimpleNamespace(type=chat_type),
            edit_text=edit_text,
        )
        return callback, edits, alerts

    async def test_guide_step_callback_is_routed_and_rendered(self) -> None:
        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                raise PanelNotFound("missing")

        callback, edits, alerts = self.callback()
        settings = Settings(
            bot_token="token",
            panel_api_url="https://panel.example.invalid",
            panel_api_token="panel-token",
            admin_telegram_ids=frozenset({1001}),
            subscription_page_url="https://page.example.invalid",
        )
        await _user_screen(
            callback, SimpleNamespace(), Panel(), "g:android:happ:1", settings, ()
        )
        self.assertEqual(len(edits), 1)
        self.assertIn("Шаг 2 из 5", edits[0])
        self.assertIn("Happ", edits[0])
        self.assertNotIn("устарела", " ".join(alerts))

    async def test_guide_step_is_rejected_in_a_group_chat(self) -> None:
        class Panel:
            pass

        callback, edits, alerts = self.callback(chat_type="group")
        settings = Settings(
            bot_token="token",
            panel_api_url="https://panel.example.invalid",
            panel_api_token="panel-token",
            admin_telegram_ids=frozenset({1001}),
        )
        await _user_screen(
            callback, SimpleNamespace(), Panel(), "g:android:happ:1", settings, ()
        )
        self.assertEqual(edits, [])
        self.assertIn("личном чате", alerts[0])


class PanelDeviceTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def user_payload() -> dict:
        return {
            "response": {
                "id": 41,
                "username": "tg_123456",
                "telegramId": 123456,
                "status": "ACTIVE",
                "expireAt": "2030-01-01T00:00:00.000Z",
                "subscriptionUrl": "https://sub.example.invalid/short",
                "hwidDeviceLimit": 2,
            }
        }

    async def test_devices_are_parsed_only_for_the_requested_user(self) -> None:
        transport = QueueTransport(
            [
                (200, self.user_payload()),
                (
                    200,
                    {
                        "response": {
                            "total": 1,
                            "devices": [
                                {
                                    "hwid": "device-hwid-1",
                                    "userId": 41,
                                    "platform": "android",
                                    "osVersion": "14",
                                    "deviceModel": "Pixel",
                                    "createdAt": "2026-01-01T00:00:00Z",
                                    "updatedAt": "2026-01-02T00:00:00Z",
                                }
                            ],
                        }
                    },
                ),
            ]
        )
        client = RemnawaveClient(
            "https://panel.example.invalid",
            "test-api-token",
            transport=transport,
        )
        try:
            user = await client.get_user_by_telegram_id(123456)
            devices = await client.get_user_devices(user)
        finally:
            await client.close()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].hwid, "device-hwid-1")
        self.assertEqual(devices[0].platform, "android")
        self.assertEqual(transport.requests[1].url.path, "/api/hwid/devices/41")

    async def test_device_from_another_user_is_rejected(self) -> None:
        transport = QueueTransport(
            [
                (200, self.user_payload()),
                (
                    200,
                    {
                        "response": {
                            "total": 1,
                            "devices": [
                                {"hwid": "foreign", "userId": 42, "platform": "ios"}
                            ],
                        }
                    },
                ),
            ]
        )
        client = RemnawaveClient(
            "https://panel.example.invalid",
            "test-api-token",
            transport=transport,
        )
        try:
            user = await client.get_user_by_telegram_id(123456)
            with self.assertRaises(PanelDataError):
                await client.get_user_devices(user)
        finally:
            await client.close()

    async def test_device_deletion_is_confirmed_by_the_panel(self) -> None:
        transport = QueueTransport(
            [
                (200, self.user_payload()),
                (
                    200,
                    {"response": {"total": 0, "devices": []}},
                ),
            ]
        )
        client = RemnawaveClient(
            "https://panel.example.invalid",
            "test-api-token",
            transport=transport,
        )
        try:
            user = await client.get_user_by_telegram_id(123456)
            remaining = await client.delete_user_device(user, "device-hwid-1")
        finally:
            await client.close()
        self.assertEqual(remaining, [])
        request = transport.requests[1]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url.path, "/api/hwid/devices/delete")
        self.assertEqual(
            json.loads(request.content),
            {"userId": 41, "hwid": "device-hwid-1"},
        )


class GuideKeyboardTests(unittest.TestCase):
    def test_every_guide_button_fits_telegram_callback_limit(self) -> None:
        keyboards = [guide_platform_keyboard()]
        for platform, apps in PLATFORM_APPS.items():
            keyboards.append(guide_apps_keyboard(platform))
            for app in apps:
                for step in range(5):
                    keyboards.append(guide_step_keyboard(platform, app, step))
        for markup in keyboards:
            for row in markup.inline_keyboard:
                for button in row:
                    if button.callback_data is not None:
                        self.assertLessEqual(
                            len(button.callback_data.encode("utf-8")), 64
                        )

    def test_guide_step_buttons_point_to_known_platforms_and_apps(self) -> None:
        for platform, apps in PLATFORM_APPS.items():
            for app in apps:
                markup = guide_step_keyboard(platform, app, 0)
                for row in markup.inline_keyboard:
                    for button in row:
                        data = button.callback_data or ""
                        if data.startswith("user:g:"):
                            parts = data.split(":")
                            self.assertEqual(parts[2], platform)
                            self.assertIn(parts[3], apps)

    def test_device_pages_and_limit_warning_use_real_counts(self) -> None:
        user = PanelUser(
            id=7,
            username="tg_1001",
            telegram_id=1001,
            status="LIMITED",
            expire_at="2030-01-01T00:00:00Z",
            subscription_url="https://sub.example.invalid/id",
            hwid_device_limit=2,
        )
        devices = [
            PanelDevice(
                hwid=f"hwid-{index}",
                platform="android",
                os_version="14",
                device_model=f"Phone {index}",
                created_at=None,
                updated_at=None,
            )
            for index in range(20)
        ]
        text = devices_text(user, devices, page=2)
        self.assertIn("Страница: 3 из 3", text)
        self.assertIn("Всего устройств: 20", text)
        self.assertIn("Лимит устройств достигнут", text)
        markup = devices_keyboard(devices, page=2)
        for row in markup.inline_keyboard:
            for button in row:
                if button.callback_data is not None:
                    self.assertLessEqual(len(button.callback_data.encode()), 64)
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data
        ]
        self.assertIn("user:devices:1", callbacks)
        self.assertIn("user:dev:del:16", callbacks)


class PreferencesTests(unittest.TestCase):
    def test_preferences_survive_reopening_the_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            store = PreferencesStore(path)
            store.remember(1001)
            store.set_reminders(1001, False)
            reopened = PreferencesStore(path)
            self.assertEqual(reopened.known_users(), [1001])
            self.assertFalse(reopened.reminders_enabled(1001))

    def test_remembered_username_and_navigation_survive_reopening(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            store = PreferencesStore(path)
            store.remember(1001, "User", "Name", "public_name")
            store.reset_navigation(1001, "settings")
            store.push_navigation(1001, "timezone")
            reopened = PreferencesStore(path)
            self.assertEqual(reopened.display_usernames(), {1001: "public_name"})
            self.assertEqual(reopened.current_navigation(1001), "timezone")

    def test_unknown_user_defaults_to_enabled_reminders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PreferencesStore(Path(directory) / "state.sqlite3")
            self.assertTrue(store.reminders_enabled(9999))

    def test_setup_tutorial_progress_is_persistent_and_monotonic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            store = PreferencesStore(path)
            self.assertFalse(store.register_start(1001))
            self.assertEqual(store.setup_tutorial_step(1001), 0)
            store.set_setup_tutorial_step(1001, 2)
            store.set_setup_tutorial_step(1001, 1)
            store.set_setup_tutorial_cursor(1001, 1)
            reopened = PreferencesStore(path)
            self.assertEqual(reopened.setup_tutorial_step(1001), 2)
            self.assertEqual(reopened.setup_tutorial_cursor(1001), 1)

    def test_back_navigation_pops_one_screen_and_avoids_duplicate_pages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PreferencesStore(Path(directory) / "state.sqlite3")
            store.reset_navigation(1001, "settings")
            store.push_navigation(1001, "timezone")
            store.push_navigation(1001, "timezone")
            self.assertEqual(store.pop_navigation(1001), "settings")
            self.assertEqual(store.pop_navigation(1001), "settings")

    def test_reminder_marker_is_per_day_and_level(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PreferencesStore(Path(directory) / "state.sqlite3")
            store.remember(1001)
            today = datetime.now(UTC).date()
            self.assertFalse(store.reminder_was_sent(1001, today, 3))
            store.mark_reminder_sent(1001, today, 3)
            self.assertTrue(store.reminder_was_sent(1001, today, 3))
            self.assertFalse(store.reminder_was_sent(1001, today, 1))

    def test_free_daily_rollover_and_report_queue_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PreferencesStore(Path(directory) / "state.sqlite3")
            store.initialize_free_daily(1001, "2026-01-01", 100)
            self.assertTrue(
                store.rollover_free_daily(
                    1001, "2026-01-01", "2026-01-02", 40, 140
                )
            )
            self.assertFalse(
                store.rollover_free_daily(
                    1001, "2026-01-01", "2026-01-02", 40, 140
                )
            )
            self.assertEqual(
                store.free_daily_state(1001), ("2026-01-02", 140)
            )
            self.assertEqual(
                store.pending_free_daily_reports(),
                [(1001, "2026-01-01", 40)],
            )
            store.mark_free_daily_report_sent(1001, "2026-01-01")
            self.assertEqual(store.pending_free_daily_reports(), [])

    def test_referral_link_registers_existing_user_and_keeps_first_referrer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PreferencesStore(Path(directory) / "state.sqlite3")
            self.assertFalse(store.register_start(1002))
            self.assertTrue(store.register_start(1002, 1001))
            self.assertFalse(store.register_start(1002, 2001))
            self.assertEqual(store.referral_for(1002), 1001)
            self.assertFalse(store.register_start(3003, 3003))
            self.assertIsNone(store.referral_for(3003))

    def test_referral_reward_is_idempotent_and_monthly_capped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PreferencesStore(Path(directory) / "state.sqlite3")
            store.register_start(1002, 1001)
            store.register_start(1003, 1001)
            store.mark_referral_activated(1002)
            self.assertTrue(
                store.reserve_referral_reward(
                    1002,
                    reward_month="2026-11",
                    reward_days=5,
                    monthly_cap=5,
                )
            )
            self.assertFalse(
                store.reserve_referral_reward(
                    1002,
                    reward_month="2026-11",
                    reward_days=5,
                    monthly_cap=5,
                )
            )
            store.finish_referral_reward(1002)
            self.assertFalse(
                store.reserve_referral_reward(
                    1003,
                    reward_month="2026-11",
                    reward_days=5,
                    monthly_cap=5,
                )
            )
            self.assertEqual(
                store.referral_stats(1001),
                {"invited": 2, "activated": 2, "reward_days": 5, "pending": 0},
            )

    def test_trial_activity_cancels_one_time_nudge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PreferencesStore(Path(directory) / "state.sqlite3")
            store.register_start(1001)
            store.mark_onboarding_activity(1001)
            connection = store._connect()
            try:
                connection.execute(
                    "UPDATE user_preferences SET onboarding_due_at = datetime('now', '-1 minute') "
                    "WHERE telegram_id = 1001"
                )
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(store.pending_trial_nudges(), [])

    def test_trial_duration_uses_single_number_and_correct_plural(self) -> None:
        text = trial_text(3, "https://t.me/jopesvpn")
        self.assertIn("Срок: 72 часа", text)
        self.assertNotIn("72 72", text)
        self.assertNotIn("3 3", text)
        self.assertIn(
            "Бесплатно на 72 часа",
            plans_text(load_plans(), trial_available=True),
        )

    def test_active_subscription_buttons_use_supported_https_url(self) -> None:
        markup = subscription_keyboard(
            "https://page.example.invalid/user"
        )
        urls = [
            button.url
            for row in markup.inline_keyboard
            for button in row
            if button.url is not None
        ]
        self.assertIn("https://page.example.invalid/user", urls)
        self.assertTrue(all(url.startswith("https://") for url in urls))


class ReminderTests(unittest.IsolatedAsyncioTestCase):
    async def test_reminder_is_sent_once_per_day(self) -> None:
        now = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
        expiry = (now + timedelta(days=3, seconds=60)).isoformat()

        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                return PanelUser(
                    id=7,
                    username="tg_1001",
                    telegram_id=telegram_id,
                    status="ACTIVE",
                    expire_at=expiry,
                    subscription_url="https://sub.example.invalid/id",
                )

        class Bot:
            def __init__(self) -> None:
                self.messages: list[tuple[int, str]] = []

            async def send_message(self, chat_id, text, **kwargs):
                self.messages.append((chat_id, text))

        with tempfile.TemporaryDirectory() as directory:
            store = PreferencesStore(Path(directory) / "state.sqlite3")
            store.remember(1001)
            bot = Bot()
            first = await send_due_reminders(bot, Panel(), store, now=now)
            second = await send_due_reminders(bot, Panel(), store, now=now)
            store.set_reminders(1001, False)
            disabled = await send_due_reminders(bot, Panel(), store, now=now)
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(disabled, 0)
        self.assertEqual(len(bot.messages), 1)
        self.assertEqual(bot.messages[0][0], 1001)
        self.assertIn("3 дня", bot.messages[0][1])

    async def test_trial_nudge_sends_once_only_to_inactive_non_subscriber(self):
        class Panel:
            async def get_user_by_telegram_id(self, telegram_id: int):
                raise PanelNotFound("missing")

        class Bot:
            def __init__(self) -> None:
                self.messages: list[tuple[int, str]] = []

            async def send_message(self, chat_id, text, **kwargs):
                self.messages.append((chat_id, text))

        with tempfile.TemporaryDirectory() as directory:
            store = PreferencesStore(Path(directory) / "state.sqlite3")
            store.register_start(1001)
            connection = store._connect()
            try:
                connection.execute(
                    "UPDATE user_preferences SET onboarding_due_at = datetime('now', '-1 minute') "
                    "WHERE telegram_id = 1001"
                )
                connection.commit()
            finally:
                connection.close()
            bot = Bot()
            self.assertEqual(await send_due_trial_nudges(bot, Panel(), store), 1)
            self.assertEqual(await send_due_trial_nudges(bot, Panel(), store), 0)
        self.assertEqual(len(bot.messages), 1)
        self.assertIn("бесплатный пробный доступ", bot.messages[0][1])


class BotCommandRoutingTests(unittest.TestCase):
    def test_user_commands_precede_ticket_text_state_and_fallback_routes(self) -> None:
        handlers = [handler.callback.__name__ for handler in router.message.handlers]
        user_commands = handlers.index("user_section_command_entry")
        connect = handlers.index("connect_command")
        ticket_text = handlers.index("ticket_message_input")
        unknown = handlers.index("unknown_command")

        self.assertLess(user_commands, ticket_text)
        self.assertLess(user_commands, unknown)
        self.assertLess(connect, ticket_text)

    def test_about_command_is_registered_and_docs_command_handler_is_removed(self) -> None:
        handlers = [handler.callback.__name__ for handler in router.message.handlers]
        self.assertIn("show_about_command", handlers)
        self.assertNotIn("show_documents", handlers)


class FakeState:
    def __init__(self) -> None:
        self.state: str | None = None
        self.data: dict = {}

    async def clear(self) -> None:
        self.state = None
        self.data = {}

    async def get_state(self) -> str | None:
        return self.state

    async def set_state(self, value) -> None:
        self.state = getattr(value, "state", value)

    async def get_data(self) -> dict:
        return dict(self.data)

    async def update_data(self, **kwargs) -> None:
        self.data.update(kwargs)


if __name__ == "__main__":
    unittest.main()
