from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from jopesvpn.plans import TRAFFIC_RESET_STRATEGIES


class PanelError(Exception):
    pass


class PanelUnavailable(PanelError):
    pass


class PanelAccessDenied(PanelError):
    pass


class PanelNotFound(PanelError):
    pass


class PanelDataError(PanelError):
    pass


FREE_TRAFFIC_BYTES = 200 * 1024**2


def _is_active_free_user(user: PanelUser | None, telegram_id: int) -> bool:
    return (
        user is not None
        and user.telegram_id == telegram_id
        and user.status == "ACTIVE"
        and user.tag == "FREE"
        and user.traffic_limit_bytes == FREE_TRAFFIC_BYTES
        and user.traffic_limit_strategy == "NO_RESET"
        and user.expire_at is not None
        and user.expire_at.startswith("2099-")
    )


@dataclass(frozen=True)
class PanelUser:
    id: int
    username: str
    telegram_id: int | None
    status: str
    expire_at: str | None
    subscription_url: str | None
    tag: str | None = None
    short_uuid: str | None = None
    traffic_used_bytes: int | None = None
    traffic_lifetime_used_bytes: int | None = None
    traffic_limit_bytes: int | None = None
    hwid_device_limit: int | None = None
    online_at: str | None = None
    last_connected_at: str | None = None
    last_node_uuid: str | None = None
    created_at: str | None = None
    last_traffic_reset_at: str | None = None
    traffic_limit_strategy: str | None = None


@dataclass(frozen=True)
class PanelUserPage:
    users: list[PanelUser]
    total: int


@dataclass(frozen=True)
class PanelDevice:
    hwid: str
    platform: str | None
    os_version: str | None
    device_model: str | None
    created_at: str | None
    updated_at: str | None
    request_ip: str | None = None
    user_agent: str | None = None


class RemnawaveClient:
    def __init__(
        self,
        api_url: str,
        api_token: str,
        *,
        ca_bundle: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=api_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {api_token}",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(10.0, connect=5.0),
            verify=ca_bundle or True,
            follow_redirects=False,
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(
        self, method: str, path: str, *, json_body: dict[str, Any] | None = None
    ) -> dict[str, Any] | list[Any]:
        try:
            response = await self._client.request(method, path, json=json_body)
        except httpx.RequestError as error:
            raise PanelUnavailable("Panel request failed") from error

        if response.status_code == 404:
            raise PanelNotFound("Panel resource was not found")
        if response.status_code in (401, 403):
            raise PanelAccessDenied("Panel rejected the API token or scope")
        if not response.is_success:
            raise PanelUnavailable(f"Panel returned HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as error:
            raise PanelDataError("Panel returned invalid JSON") from error
        if not isinstance(data, (dict, list)):
            raise PanelDataError("Panel returned an unexpected response")
        return data

    @staticmethod
    def _response(data: dict[str, Any] | list[Any]) -> Any:
        return data.get("response", data) if isinstance(data, dict) else data

    @classmethod
    def _user(cls, data: dict[str, Any] | list[Any]) -> PanelUser:
        payload = cls._response(data)
        if not isinstance(payload, dict):
            raise PanelDataError("Panel returned an unexpected user")
        try:
            raw_id = payload["id"]
            raw_username = payload["username"]
            raw_status = payload["status"]
            raw_expire_at = payload.get("expireAt")
            telegram_id_value = payload.get("telegramId")
            tag_value = payload.get("tag")
            if (
                isinstance(raw_id, bool)
                or not isinstance(raw_username, str)
                or not raw_username
                or not isinstance(raw_status, str)
                or not raw_status
            ):
                raise TypeError
            user_id = int(raw_id)
            telegram_id = (
                telegram_id_value
                if isinstance(telegram_id_value, int)
                and not isinstance(telegram_id_value, bool)
                and telegram_id_value > 0
                else None
            )
            if user_id <= 0 or (telegram_id is not None and telegram_id <= 0):
                raise ValueError

            def optional_non_negative_int(value: Any) -> int | None:
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    return None
                return value

            def optional_str(value: Any) -> str | None:
                return value if isinstance(value, str) and value else None

            traffic = payload.get("userTraffic")
            traffic = traffic if isinstance(traffic, dict) else {}
            return PanelUser(
                id=user_id,
                username=raw_username,
                telegram_id=telegram_id,
                status=raw_status,
                expire_at=optional_str(raw_expire_at),
                subscription_url=optional_str(payload.get("subscriptionUrl")),
                tag=optional_str(tag_value),
                short_uuid=optional_str(payload.get("shortUuid")),
                traffic_used_bytes=optional_non_negative_int(
                    traffic.get("usedTrafficBytes")
                ),
                traffic_lifetime_used_bytes=optional_non_negative_int(
                    traffic.get("lifetimeUsedTrafficBytes")
                ),
                traffic_limit_bytes=optional_non_negative_int(
                    payload.get("trafficLimitBytes")
                ),
                hwid_device_limit=optional_non_negative_int(
                    payload.get("hwidDeviceLimit")
                ),
                online_at=optional_str(traffic.get("onlineAt")),
                last_connected_at=optional_str(traffic.get("firstConnectedAt")),
                last_node_uuid=optional_str(traffic.get("lastConnectedNodeUuid")),
                created_at=optional_str(payload.get("createdAt")),
                last_traffic_reset_at=optional_str(
                    payload.get("lastTrafficResetAt")
                ),
                traffic_limit_strategy=optional_str(
                    payload.get("trafficLimitStrategy")
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise PanelDataError("Panel user response is missing required fields") from error

    @staticmethod
    def username_for_telegram_id(telegram_id: int) -> str:
        if telegram_id <= 0:
            raise ValueError("Telegram ID must be positive")
        return f"tg_{telegram_id}"

    async def get_user_by_telegram_id(self, telegram_id: int) -> PanelUser:
        username = self.username_for_telegram_id(telegram_id)
        data = await self._request(
            "GET", f"/api/users/by-username/{quote(username, safe='')}"
        )
        user = self._user(data)
        if user.telegram_id is not None and user.telegram_id != telegram_id:
            raise PanelNotFound("No Panel user is linked to this Telegram account")
        return user

    async def list_users(self, start: int = 0, size: int = 50) -> PanelUserPage:
        if start < 0:
            raise ValueError("Start offset must not be negative")
        if not 1 <= size <= 1000:
            raise ValueError("Page size must be between 1 and 1000")
        data = await self._request("GET", f"/api/users?start={start}&size={size}")
        payload = self._response(data)
        if not isinstance(payload, dict):
            raise PanelDataError("Panel returned an unexpected user list")
        raw_users = payload.get("users")
        raw_total = payload.get("total")
        if (
            not isinstance(raw_users, list)
            or isinstance(raw_total, bool)
            or not isinstance(raw_total, int)
            or raw_total < 0
        ):
            raise PanelDataError("Panel returned an unexpected user list")
        return PanelUserPage(
            users=[self._user(item) for item in raw_users],
            total=raw_total,
        )

    async def get_nodes(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/api/nodes")
        nodes = self._response(data)
        if isinstance(nodes, dict):
            nodes = nodes.get("nodes")
        if not isinstance(nodes, list) or any(
            not isinstance(node, dict)
            or not isinstance(node.get("name"), str)
            or not isinstance(node.get("isConnected"), bool)
            for node in nodes
        ):
            raise PanelDataError("Panel returned an unexpected node list")
        return nodes

    async def create_user(
        self,
        telegram_id: int,
        days: int,
        internal_squad_uuid: str | None,
        *,
        tag: str | None = None,
        traffic_limit_gb: int | None = None,
        permanent_traffic: bool = False,
        traffic_limit_strategy: str = "NO_RESET",
    ) -> PanelUser:
        if not 1 <= days <= 365:
            raise ValueError("Access duration must be between 1 and 365 days")
        if tag is not None and not re.fullmatch(r"[A-Z0-9_]{1,16}", tag):
            raise ValueError("Panel user tag must match [A-Z0-9_]{1,16}")
        username = self.username_for_telegram_id(telegram_id)
        expire_at = (datetime.now(UTC) + timedelta(days=days)).isoformat()
        body: dict[str, Any] = {
            "username": username,
            "telegramId": telegram_id,
            "expireAt": (
                "2099-01-01T00:00:00+00:00"
                if permanent_traffic
                else expire_at
            ),
            "trafficLimitBytes": 0,
            "status": "ACTIVE",
        }
        if traffic_limit_gb is not None:
            if isinstance(traffic_limit_gb, bool) or traffic_limit_gb <= 0:
                raise ValueError("Traffic limit must be positive")
            if traffic_limit_strategy not in TRAFFIC_RESET_STRATEGIES:
                raise ValueError("Traffic limit strategy is invalid")
            if permanent_traffic and traffic_limit_strategy != "NO_RESET":
                raise ValueError("Permanent traffic plans cannot reset their quota")
            body["trafficLimitBytes"] = traffic_limit_gb * 1024**3
            body["trafficLimitStrategy"] = traffic_limit_strategy
        elif permanent_traffic:
            raise ValueError("A permanent access plan requires a traffic limit")
        if tag is not None:
            body["tag"] = tag
        if internal_squad_uuid is not None:
            body["activeInternalSquads"] = [internal_squad_uuid]
        await self._request("POST", "/api/users", json_body=body)
        try:
            user = await self.get_user_by_telegram_id(telegram_id)
        except PanelError as error:
            raise PanelUnavailable(
                "Panel may have created the user, but confirmation failed; inspect Panel before retrying"
            ) from error
        if user.status != "ACTIVE":
            raise PanelDataError("Panel created a user that is not active")
        if tag is not None and user.tag != tag:
            raise PanelDataError("Panel did not confirm the requested user tag")
        if traffic_limit_gb is not None and user.traffic_limit_bytes != body["trafficLimitBytes"]:
            raise PanelDataError("Panel did not confirm the requested traffic limit")
        if (
            traffic_limit_gb is not None
            and user.traffic_limit_strategy != traffic_limit_strategy
        ):
            raise PanelDataError("Panel did not confirm the traffic reset strategy")
        if permanent_traffic and (
            user.expire_at is None or user.expire_at[:4] != "2099"
        ):
            raise PanelDataError("Panel did not confirm the package expiration")
        return user

    async def apply_paid_plan(
        self,
        user: PanelUser,
        days: int,
        traffic_limit_gb: int | None,
        *,
        permanent_traffic: bool = False,
        tag: str | None = None,
        traffic_limit_strategy: str = "NO_RESET",
    ) -> PanelUser:
        if not 1 <= days <= 365:
            raise ValueError("Plan duration must be between 1 and 365 days")
        try:
            if user.expire_at is None:
                raise ValueError
            current_expiry = datetime.fromisoformat(
                user.expire_at.replace("Z", "+00:00")
            )
        except ValueError as error:
            raise PanelDataError("Panel returned an invalid expiration date") from error
        if current_expiry.tzinfo is None:
            raise PanelDataError("Panel returned an expiration date without a timezone")
        if permanent_traffic and traffic_limit_gb is None:
            raise ValueError("A permanent access plan requires a traffic limit")
        if traffic_limit_strategy not in TRAFFIC_RESET_STRATEGIES:
            raise ValueError("Traffic limit strategy is invalid")
        if traffic_limit_gb is None and traffic_limit_strategy != "NO_RESET":
            raise ValueError("Unlimited plans cannot have a traffic reset strategy")
        if permanent_traffic and traffic_limit_strategy != "NO_RESET":
            raise ValueError("Permanent traffic plans cannot reset their quota")
        if tag is not None and not re.fullmatch(r"[A-Z0-9_]{1,16}", tag):
            raise ValueError("Panel user tag must match [A-Z0-9_]{1,16}")
        expiry_base: datetime | None = None
        if permanent_traffic:
            expire_at = "2099-01-01T00:00:00+00:00"
        else:
            expiry_base = current_expiry.astimezone(UTC)
            if expiry_base.year == 2099:
                expiry_base = datetime.now(UTC)
            else:
                expiry_base = max(expiry_base, datetime.now(UTC))
            expire_at = (expiry_base + timedelta(days=days)).isoformat()

        if traffic_limit_gb is None:
            traffic_limit_bytes = 0
        else:
            if isinstance(traffic_limit_gb, bool) or traffic_limit_gb <= 0:
                raise ValueError("Traffic limit must be positive")
            if user.traffic_used_bytes is None or user.traffic_limit_bytes is None:
                raise PanelDataError("Panel did not return traffic usage and limit")
            current_cap = user.traffic_limit_bytes
            current_used = user.traffic_used_bytes
            if traffic_limit_strategy == "NO_RESET":
                traffic_limit_bytes = (
                    max(current_cap, current_used) if current_cap else current_used
                ) + traffic_limit_gb * 1024**3
            else:
                traffic_limit_bytes = traffic_limit_gb * 1024**3

        data = await self._request(
            "PATCH",
            "/api/users",
            json_body={
                "id": user.id,
                "status": "ACTIVE",
                "expireAt": expire_at,
                "trafficLimitBytes": traffic_limit_bytes,
                "trafficLimitStrategy": traffic_limit_strategy,
                **({"tag": tag} if tag is not None else {}),
            },
        )
        updated = self._user(data)
        if (
            updated.id != user.id
            or updated.telegram_id != user.telegram_id
            or updated.status != "ACTIVE"
            or updated.traffic_limit_bytes != traffic_limit_bytes
            or (
                traffic_limit_gb is not None
                and updated.traffic_limit_strategy != traffic_limit_strategy
            )
            or (tag is not None and updated.tag != tag)
        ):
            raise PanelDataError("Panel did not confirm the paid plan")
        try:
            if updated.expire_at is None:
                raise ValueError
            confirmed_expiry = datetime.fromisoformat(
                updated.expire_at.replace("Z", "+00:00")
            )
        except ValueError as error:
            raise PanelDataError("Panel returned an invalid expiration date") from error
        if confirmed_expiry.tzinfo is None:
            raise PanelDataError("Panel did not confirm the paid plan duration")
        if permanent_traffic:
            if confirmed_expiry.year != 2099:
                raise PanelDataError("Panel did not confirm the package expiration")
        elif expiry_base is not None and confirmed_expiry <= expiry_base:
            raise PanelDataError("Panel did not confirm the paid plan duration")
        return updated

    async def apply_free_access(self, user: PanelUser) -> PanelUser:
        if user.status != "EXPIRED" or user.tag == "FREE":
            raise ValueError("Only expired non-FREE users can be moved to FREE")
        await self.reset_user_traffic(user)

        try:
            data = await self._request(
                "PATCH",
                "/api/users",
                json_body={
                    "id": user.id,
                    "status": "ACTIVE",
                    "expireAt": "2099-01-01T00:00:00+00:00",
                    "trafficLimitBytes": FREE_TRAFFIC_BYTES,
                    "trafficLimitStrategy": "NO_RESET",
                    "tag": "FREE",
                },
            )
        except PanelError as error:
            if user.telegram_id is None:
                raise PanelDataError(
                    "Panel user has no Telegram ID; FREE activation cannot be confirmed"
                ) from error
            try:
                confirmed = await self.get_user_by_telegram_id(user.telegram_id)
            except PanelError as verification_error:
                raise PanelUnavailable(
                    "Panel may have changed the user, but FREE activation could not be confirmed"
                ) from verification_error
            if (
                confirmed is not None
                and confirmed.id == user.id
                and _is_active_free_user(confirmed, user.telegram_id)
            ):
                return confirmed
            raise PanelUnavailable("Panel did not confirm FREE activation") from error
        updated = self._user(data)
        if (
            updated.id != user.id
            or user.telegram_id is None
            or not _is_active_free_user(updated, user.telegram_id)
        ):
            raise PanelDataError("Panel did not confirm FREE activation")
        return updated

    async def create_free_user(
        self, telegram_id: int, internal_squad_uuid: str
    ) -> PanelUser:
        if telegram_id <= 0 or not internal_squad_uuid:
            raise ValueError("A Telegram ID and internal squad are required")
        username = self.username_for_telegram_id(telegram_id)
        try:
            await self._request(
                "POST",
                "/api/users",
                json_body={
                    "username": username,
                    "telegramId": telegram_id,
                    "expireAt": "2099-01-01T00:00:00+00:00",
                    "trafficLimitBytes": FREE_TRAFFIC_BYTES,
                    "trafficLimitStrategy": "NO_RESET",
                    "tag": "FREE",
                    "status": "ACTIVE",
                    "activeInternalSquads": [internal_squad_uuid],
                },
            )
        except PanelError as request_error:
            try:
                confirmed = await self.get_user_by_telegram_id(telegram_id)
            except PanelNotFound:
                raise request_error
            except PanelError as verification_error:
                raise PanelUnavailable(
                    "Panel may have created FREE access, but confirmation failed"
                ) from verification_error
            if _is_active_free_user(confirmed, telegram_id):
                return confirmed
            raise PanelUnavailable("Panel did not confirm FREE access") from request_error

        try:
            confirmed = await self.get_user_by_telegram_id(telegram_id)
        except PanelError as error:
            raise PanelUnavailable(
                "Panel created FREE access, but confirmation failed"
            ) from error
        if not _is_active_free_user(confirmed, telegram_id):
            raise PanelDataError("Panel did not confirm the FREE account")
        return confirmed

    async def repair_free_access(self, user: PanelUser) -> PanelUser:
        if user.tag != "FREE" or user.status not in {"ACTIVE", "LIMITED"}:
            raise ValueError("Only an enabled FREE account can be repaired")
        data = await self._request(
            "PATCH",
            "/api/users",
            json_body={
                "id": user.id,
                "expireAt": "2099-01-01T00:00:00+00:00",
                "trafficLimitBytes": FREE_TRAFFIC_BYTES,
                "trafficLimitStrategy": "NO_RESET",
                "tag": "FREE",
            },
        )
        updated = self._user(data)
        if (
            updated.id != user.id
            or updated.telegram_id != user.telegram_id
            or updated.tag != "FREE"
            or updated.traffic_limit_bytes != FREE_TRAFFIC_BYTES
            or updated.traffic_limit_strategy != "NO_RESET"
            or updated.expire_at is None
            or not updated.expire_at.startswith("2099-")
            or updated.status != user.status
        ):
            raise PanelDataError("Panel did not confirm the FREE account settings")
        return updated

    async def reactivate_free_after_reset(self, user: PanelUser) -> PanelUser:
        if user.tag != "FREE" or user.status != "LIMITED":
            raise ValueError("Only a limited FREE account can be reactivated")
        data = await self._request(
            "PATCH",
            "/api/users",
            json_body={"id": user.id, "status": "ACTIVE"},
        )
        updated = self._user(data)
        if (
            updated.id != user.id
            or updated.telegram_id != user.telegram_id
            or updated.tag != "FREE"
            or updated.status != "ACTIVE"
            or updated.traffic_limit_bytes != FREE_TRAFFIC_BYTES
            or updated.traffic_used_bytes is None
            or updated.traffic_used_bytes >= FREE_TRAFFIC_BYTES
        ):
            raise PanelDataError("Panel did not confirm FREE account reactivation")
        return updated

    async def reset_user_traffic(self, user: PanelUser) -> PanelUser:
        data = await self._request(
            "POST",
            f"/api/users/{user.id}/actions/reset-traffic",
        )
        updated = self._user(data)
        if updated.id != user.id or updated.telegram_id != user.telegram_id:
            raise PanelDataError("Panel reset traffic for an unexpected user")
        return updated

    async def extend_user(self, user: PanelUser, days: int) -> PanelUser:
        if not 1 <= days <= 365:
            raise ValueError("Extension must be between 1 and 365 days")
        data = await self._request(
            "POST",
            f"/api/users/{user.id}/actions/extend",
            json_body={"days": days},
        )
        updated = self._user(data)
        if updated.id != user.id or updated.telegram_id != user.telegram_id:
            raise PanelDataError("Panel did not confirm the extended user")
        try:
            if user.expire_at is None or updated.expire_at is None:
                raise ValueError
            before = datetime.fromisoformat(user.expire_at.replace("Z", "+00:00"))
            after = datetime.fromisoformat(updated.expire_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise PanelDataError("Panel returned an invalid expiration date") from error
        if before.tzinfo is None or after.tzinfo is None:
            raise PanelDataError("Panel returned an expiration date without a timezone")
        if after <= before:
            raise PanelDataError("Panel did not extend the expiration date")
        if user.status == "EXPIRED" and updated.status != "ACTIVE":
            raise PanelDataError("Panel did not reactivate the expired user")
        return updated

    async def disable_user(self, user: PanelUser) -> None:
        data = await self._request(
            "POST", f"/api/users/{user.id}/actions/disable"
        )
        result = self._user(data)
        if (
            result.id != user.id
            or result.telegram_id != user.telegram_id
            or result.status != "DISABLED"
        ):
            raise PanelDataError("Panel did not confirm that the user was disabled")

    async def enable_user(self, user: PanelUser) -> PanelUser:
        data = await self._request(
            "POST", f"/api/users/{user.id}/actions/enable"
        )
        result = self._user(data)
        if (
            result.id != user.id
            or result.telegram_id != user.telegram_id
            or result.status not in {"ACTIVE", "EXPIRED", "LIMITED"}
        ):
            raise PanelDataError("Panel did not confirm that the user was enabled")
        return result

    async def revoke_subscription(self, user: PanelUser) -> PanelUser:
        data = await self._request(
            "POST",
            f"/api/users/{user.id}/actions/revoke",
            json_body={},
        )
        revoked_user = self._user(data)
        if (
            revoked_user.id != user.id
            or revoked_user.telegram_id != user.telegram_id
            or revoked_user.username != user.username
            or user.subscription_url is None
            or revoked_user.subscription_url is None
            or user.subscription_url == revoked_user.subscription_url
        ):
            raise PanelDataError(
                "Panel did not confirm that the subscription URL was rotated"
            )
        return revoked_user

    @classmethod
    def _devices(cls, data: dict[str, Any] | list[Any], user: PanelUser) -> list[PanelDevice]:
        payload = cls._response(data)
        if not isinstance(payload, dict):
            raise PanelDataError("Panel returned an unexpected device list")
        raw_devices = payload.get("devices")
        if not isinstance(raw_devices, list):
            raise PanelDataError("Panel returned an unexpected device list")
        devices: list[PanelDevice] = []
        for item in raw_devices:
            if not isinstance(item, dict):
                raise PanelDataError("Panel returned an unexpected device")
            hwid = item.get("hwid")
            raw_user_id = item.get("userId")
            if (
                not isinstance(hwid, str)
                or not hwid
                or isinstance(raw_user_id, bool)
                or not isinstance(raw_user_id, int)
                or raw_user_id != user.id
            ):
                raise PanelDataError("Panel returned a device for another user")
            devices.append(
                PanelDevice(
                    hwid=hwid,
                    platform=item.get("platform")
                    if isinstance(item.get("platform"), str)
                    else None,
                    os_version=item.get("osVersion")
                    if isinstance(item.get("osVersion"), str)
                    else None,
                    device_model=item.get("deviceModel")
                    if isinstance(item.get("deviceModel"), str)
                    else None,
                    created_at=item.get("createdAt")
                    if isinstance(item.get("createdAt"), str)
                    else None,
                    updated_at=item.get("updatedAt")
                    if isinstance(item.get("updatedAt"), str)
                    else None,
                    request_ip=item.get("requestIp")
                    if isinstance(item.get("requestIp"), str)
                    else None,
                    user_agent=item.get("userAgent")
                    if isinstance(item.get("userAgent"), str)
                    else None,
                )
            )
        return devices

    async def get_user_devices(self, user: PanelUser) -> list[PanelDevice]:
        data = await self._request("GET", f"/api/hwid/devices/{user.id}")
        return self._devices(data, user)

    async def delete_user_device(self, user: PanelUser, hwid: str) -> list[PanelDevice]:
        if not hwid:
            raise ValueError("HWID is required")
        data = await self._request(
            "POST",
            "/api/hwid/devices/delete",
            json_body={"userId": user.id, "hwid": hwid},
        )
        devices = self._devices(data, user)
        if any(device.hwid == hwid for device in devices):
            raise PanelDataError("Panel did not confirm the device deletion")
        return devices

    async def health(self) -> bool:
        try:
            response = await self._client.get("/health")
        except httpx.RequestError as error:
            raise PanelUnavailable("Panel health request failed") from error
        return response.status_code == 200

    @staticmethod
    def safe_subscription_url(user: PanelUser) -> str:
        if user.subscription_url is None:
            raise PanelDataError("Panel did not return a subscription URL")
        try:
            parsed = urlsplit(user.subscription_url)
            parsed.port
        except ValueError as error:
            raise PanelDataError("Panel returned an invalid subscription URL") from error
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise PanelDataError("Panel returned an unsafe subscription URL")
        return user.subscription_url

    @classmethod
    def subscription_page_url(
        cls, user: PanelUser, base_url: str | None
    ) -> str | None:
        if base_url is None:
            return None
        subscription_url = cls.safe_subscription_url(user)
        short_uuid = urlsplit(subscription_url).path.rstrip("/").rsplit("/", 1)[-1]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", short_uuid):
            raise PanelDataError("Panel returned an invalid subscription short UUID")
        return f"{base_url.rstrip('/')}/{short_uuid}"
