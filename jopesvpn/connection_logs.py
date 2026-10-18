from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from jopesvpn.config import Settings
from jopesvpn.panel import (
    PanelDataError,
    PanelDevice,
    PanelError,
    PanelNotFound,
    PanelUser,
    RemnawaveClient,
)
from jopesvpn.preferences import PreferencesError, PreferencesStore
from jopesvpn.ui import admin_log_keyboard, connection_log_text


logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60
EVENTS_PER_CYCLE = 3


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


async def baseline(panel: RemnawaveClient, prefs: PreferencesStore) -> int:
    """Remember the current state silently so enabling logs does not flood."""
    now = _now()
    now_iso = _iso(now)
    tracked = 0
    try:
        telegram_ids = prefs.known_users()
    except PreferencesError:
        logger.warning("Connection log baseline cannot read users")
        raise
    for telegram_id in telegram_ids:
        try:
            user = await panel.get_user_by_telegram_id(telegram_id)
        except PanelNotFound:
            continue
        except PanelError:
            logger.warning("Connection log baseline user read failed")
            raise
        try:
            devices = await panel.get_user_devices(user)
        except (PanelError, ValueError):
            logger.warning("Connection log baseline device read failed")
            raise
        for device in devices:
            try:
                prefs.register_endpoint(
                    user.id,
                    telegram_id,
                    device.hwid,
                    device.request_ip or "",
                    now=now_iso,
                )
                prefs.mark_endpoint_notified(
                    user.id,
                    device.hwid,
                    device.request_ip or "",
                    notified_at=now_iso,
                )
            except PreferencesError:
                logger.warning("Connection log endpoint write failed")
                raise
        tracked += 1
    logger.info("Connection log baseline stored for %s account(s)", tracked)
    return tracked


async def _username(bot: Bot, telegram_id: int, cache: dict[int, str | None]) -> str | None:
    if telegram_id in cache:
        return cache[telegram_id]
    try:
        chat = await bot.get_chat(telegram_id)
    except TelegramAPIError:
        cache[telegram_id] = None
        return None
    username = getattr(chat, "username", None)
    cache[telegram_id] = username if isinstance(username, str) else None
    return cache[telegram_id]


def _page_url(user: PanelUser, settings: Settings) -> str | None:
    if user.status != "ACTIVE":
        return None
    try:
        return RemnawaveClient.subscription_page_url(
            user, settings.subscription_page_url
        )
    except PanelDataError:
        return None


async def _notify(
    bot: Bot,
    settings: Settings,
    prefs: PreferencesStore,
    user: PanelUser,
    device: PanelDevice,
    *,
    telegram_id: int,
    usernames: dict[int, str | None],
) -> bool:
    name = None
    try:
        name = prefs.display_name(telegram_id)
    except PreferencesError:
        logger.warning("Connection log name read failed")
    username = await _username(bot, telegram_id, usernames)
    ip = device.request_ip or ""
    same_ip_count = 0
    if ip:
        try:
            same_ip_count = len(prefs.accounts_on_ip(ip))
        except PreferencesError:
            logger.warning("Connection log IP reuse read failed")
    text = connection_log_text(
        user,
        device,
        name=name,
        username=username,
        same_ip_count=same_ip_count,
    )
    markup = admin_log_keyboard(
        telegram_id,
        user.status,
        _page_url(user, settings),
    )
    delivered = True
    for admin_id in settings.admin_telegram_ids:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=text,
                reply_markup=markup,
                parse_mode="HTML",
            )
        except TelegramAPIError:
            logger.warning("Connection log delivery failed for admin")
            delivered = False
    return delivered


async def check_once(
    bot: Bot,
    settings: Settings,
    panel: RemnawaveClient,
    prefs: PreferencesStore,
) -> int:
    """Poll known accounts once and notify for pending endpoint metadata."""
    try:
        telegram_ids = prefs.known_users()
    except PreferencesError:
        logger.warning("Connection log cannot read users")
        return 0
    usernames: dict[int, str | None] = {}
    sent = 0
    for telegram_id in telegram_ids:
        try:
            user = await panel.get_user_by_telegram_id(telegram_id)
        except PanelNotFound:
            continue
        except PanelError as error:
            logger.warning(
                "Connection log user read failed (%s)", type(error).__name__
            )
            continue
        try:
            devices = await panel.get_user_devices(user)
        except (PanelError, ValueError) as error:
            logger.warning(
                "Connection log device read failed (%s)", type(error).__name__
            )
            continue
        if not devices:
            continue
        now = _now()
        now_iso = _iso(now)
        new_devices: list[PanelDevice] = []
        for device in devices:
            try:
                is_new = prefs.register_endpoint(
                    user.id,
                    telegram_id,
                    device.hwid,
                    device.request_ip or "",
                    now=now_iso,
                )
            except PreferencesError:
                logger.warning("Connection log endpoint write failed")
                continue
            if is_new:
                new_devices.append(device)
        for device in new_devices[:EVENTS_PER_CYCLE]:
            delivered = await _notify(
                bot,
                settings,
                prefs,
                user,
                device,
                telegram_id=telegram_id,
                usernames=usernames,
            )
            if not delivered:
                continue
            try:
                prefs.mark_endpoint_notified(
                    user.id,
                    device.hwid,
                    device.request_ip or "",
                    notified_at=now_iso,
                )
            except PreferencesError:
                logger.warning("Connection log delivery state write failed")
                continue
            sent += 1
    return sent


async def connection_log_loop(
    bot: Bot,
    settings: Settings,
    panel: RemnawaveClient,
    prefs: PreferencesStore,
) -> None:
    while True:
        started = asyncio.get_running_loop().time()
        try:
            enabled = prefs.admin_logs_enabled()
            if enabled:
                await check_once(bot, settings, panel, prefs)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # pragma: no cover - defensive loop guard
            logger.warning(
                "Connection log cycle failed (%s)", type(error).__name__
            )
        elapsed = asyncio.get_running_loop().time() - started
        await asyncio.sleep(max(0.0, POLL_INTERVAL_SECONDS - elapsed))
