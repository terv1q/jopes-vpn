from __future__ import annotations

import asyncio
import hashlib
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from jopesvpn.i18n import reset_language, set_language, t
from jopesvpn.panel import PanelError, PanelNotFound, PanelUser, RemnawaveClient
from jopesvpn.preferences import PreferencesError, PreferencesStore
from jopesvpn.timezones import reset_utc_offset, set_utc_offset
from jopesvpn.ui import format_bytes


logger = logging.getLogger(__name__)
CHECK_INTERVAL_SECONDS = 5 * 60
TRAFFIC_THRESHOLDS = (50, 75, 100)


def _cycle_key(user: PanelUser) -> str:
    identity = "\x1f".join(
        (
            user.tag or "",
            str(user.traffic_limit_bytes),
            user.traffic_limit_strategy or "",
            user.last_traffic_reset_at or "",
            user.expire_at or "",
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


async def process_traffic_notifications_once(
    bot: Bot,
    panel: RemnawaveClient,
    prefs: PreferencesStore,
) -> int:
    try:
        telegram_ids = prefs.known_users()
    except PreferencesError:
        logger.warning("Traffic notification scan could not read known users")
        return 0

    sent = 0
    for telegram_id in telegram_ids:
        try:
            user = await panel.get_user_by_telegram_id(telegram_id)
        except PanelNotFound:
            continue
        except PanelError as error:
            logger.warning("Traffic notification lookup failed (%s)", type(error).__name__)
            continue
        limit = user.traffic_limit_bytes
        used = user.traffic_used_bytes
        if (
            user.telegram_id != telegram_id
            or limit is None
            or limit <= 0
            or used is None
            or used < 0
        ):
            continue

        cycle_key = _cycle_key(user)
        for threshold in TRAFFIC_THRESHOLDS:
            if used * 100 < limit * threshold:
                continue
            try:
                reserved = prefs.claim_traffic_notification(
                    telegram_id, user.id, cycle_key, threshold
                )
            except (PreferencesError, ValueError):
                logger.warning("Traffic notification could not be reserved")
                break
            if not reserved:
                continue

            language_token = None
            timezone_token = None
            try:
                language = prefs.language(telegram_id)
                offset = prefs.timezone_offset(telegram_id)
                language_token = set_language(language)
                timezone_token = set_utc_offset(offset)
            except (PreferencesError, ValueError):
                logger.warning("Traffic notification preferences are invalid")
                if timezone_token is not None:
                    reset_utc_offset(timezone_token)
                if language_token is not None:
                    reset_language(language_token)
                try:
                    prefs.release_traffic_notification(
                        telegram_id, user.id, cycle_key, threshold
                    )
                except PreferencesError:
                    logger.warning("Traffic notification reservation could not be released")
                continue

            percent = min(used * 100 // limit, 100)
            values = {
                "used": format_bytes(used),
                "limit": format_bytes(limit),
                "remaining": format_bytes(max(limit - used, 0)),
                "percent": percent,
            }
            if threshold == 100:
                key = (
                    "traffic.alert_exhausted_limited"
                    if user.status == "LIMITED"
                    else "traffic.alert_exhausted_pending"
                )
                text = t(key, **values)
            else:
                text = t(f"traffic.alert_{threshold}", **values)
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t("common.plans"),
                            callback_data="user:plans",
                        ),
                        InlineKeyboardButton(
                            text=t("common.subscription"),
                            callback_data="user:subscription",
                        ),
                    ]
                ]
            )
            try:
                await bot.send_message(telegram_id, text, reply_markup=markup)
            except TelegramAPIError:
                logger.warning("Traffic notification delivery failed")
                try:
                    prefs.release_traffic_notification(
                        telegram_id, user.id, cycle_key, threshold
                    )
                except PreferencesError:
                    logger.warning("Traffic notification reservation could not be released")
                continue
            finally:
                if timezone_token is not None:
                    reset_utc_offset(timezone_token)
                if language_token is not None:
                    reset_language(language_token)

            try:
                prefs.mark_traffic_notification_sent(
                    telegram_id, user.id, cycle_key, threshold
                )
            except PreferencesError:
                logger.error(
                    "Traffic notification was delivered but its state could not be saved"
                )
            sent += 1
    return sent


async def traffic_notification_loop(
    bot: Bot,
    panel: RemnawaveClient,
    prefs: PreferencesStore,
    *,
    interval_seconds: int = CHECK_INTERVAL_SECONDS,
) -> None:
    while True:
        try:
            await process_traffic_notifications_once(bot, panel, prefs)
        except Exception:  # noqa: BLE001 - keep the notification loop alive
            logger.exception("Traffic notification iteration failed")
        await asyncio.sleep(interval_seconds)
