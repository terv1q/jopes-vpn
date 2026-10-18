from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from jopesvpn.i18n import reset_language, set_language, t
from jopesvpn.panel import PanelError, PanelNotFound, PanelUser, RemnawaveClient
from jopesvpn.preferences import PreferencesStore


logger = logging.getLogger(__name__)

REMINDER_DAYS = (3, 1, 0)
CHECK_INTERVAL_SECONDS = 6 * 60 * 60


def _days_left(user: PanelUser, now: datetime) -> int | None:
    if not user.expire_at:
        return None
    try:
        expires = datetime.fromisoformat(user.expire_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if expires.tzinfo is None:
        return None
    return int((expires.astimezone(UTC) - now).total_seconds() // 86400)


def _reminder_text(user: PanelUser, days_left: int) -> str:
    if days_left <= 0:
        if user.status == "EXPIRED":
            title, first, second = (
                t("reminder.expired_title"),
                t("reminder.expired_line_1"),
                t("reminder.expired_line_2"),
            )
        else:
            title, first, second = (
                t("reminder.today_title"),
                t("reminder.today_line_1"),
                t("reminder.today_line_2"),
            )
    elif days_left == 1:
        title, first, second = (
            t("reminder.tomorrow_title"),
            t("reminder.tomorrow_line_1"),
            t("reminder.tomorrow_line_2"),
        )
    else:
        title = t("reminder.days_title", days=days_left)
        first = t("reminder.days_line_1", days=days_left)
        second = t("reminder.days_line_2")
    return (
        f"<b>{title}</b>\n\n"
        f"<blockquote>┌ <b>{t('common.status')}</b>\n"
        f"├ {first}\n"
        f"└ {second}</blockquote>"
    )


async def send_due_reminders(
    bot: Bot,
    panel: RemnawaveClient,
    prefs: PreferencesStore,
    *,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(UTC)
    today = now.date()
    sent = 0
    for telegram_id in prefs.known_users():
        if not prefs.reminders_enabled(telegram_id):
            continue
        try:
            user = await panel.get_user_by_telegram_id(telegram_id)
        except PanelNotFound:
            continue
        except PanelError as error:
            logger.warning("Reminder lookup failed (%s)", type(error).__name__)
            continue
        left = _days_left(user, now)
        if left is None or left not in REMINDER_DAYS:
            continue
        if user.status not in {"ACTIVE", "EXPIRED"}:
            continue
        if prefs.reminder_was_sent(telegram_id, today, left):
            continue
        token = set_language(prefs.language(telegram_id))
        try:
            await bot.send_message(
                telegram_id,
                _reminder_text(user, left),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=t("reminder.button_subscription"),
                                callback_data="user:subscription",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text=t("reminder.button_support"),
                                callback_data="user:help",
                            )
                        ],
                    ]
                ),
                parse_mode="HTML",
            )
        except TelegramAPIError:
            logger.warning("Reminder could not be delivered")
            continue
        finally:
            reset_language(token)
        prefs.mark_reminder_sent(telegram_id, today, left)
        sent += 1
    return sent


async def reminder_loop(
    bot: Bot,
    panel: RemnawaveClient,
    prefs: PreferencesStore,
    *,
    interval: int = CHECK_INTERVAL_SECONDS,
) -> None:
    await asyncio.sleep(30)
    while True:
        try:
            await send_due_reminders(bot, panel, prefs)
        except Exception:  # noqa: BLE001 - keep the reminder loop alive
            logger.exception("Reminder loop iteration failed")
        await asyncio.sleep(interval)


async def send_due_trial_nudges(
    bot: Bot,
    panel: RemnawaveClient,
    prefs: PreferencesStore,
    *,
    limit: int = 25,
) -> int:
    sent = 0
    for telegram_id in prefs.pending_trial_nudges(limit):
        try:
            await panel.get_user_by_telegram_id(telegram_id)
        except PanelNotFound:
            pass
        except PanelError as error:
            logger.warning("Trial nudge lookup failed (%s)", type(error).__name__)
            continue
        else:
            prefs.mark_trial_nudge_sent(telegram_id)
            continue

        token = set_language(prefs.language(telegram_id))
        try:
            await bot.send_message(
                telegram_id,
                t("trial.nudge"),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=t("trial.nudge_button"),
                                callback_data="user:trial",
                            )
                        ]
                    ]
                ),
                parse_mode="HTML",
            )
        except TelegramAPIError:
            logger.warning("Trial nudge could not be delivered")
            continue
        finally:
            reset_language(token)
        prefs.mark_trial_nudge_sent(telegram_id)
        sent += 1
    return sent


async def trial_nudge_loop(
    bot: Bot,
    panel: RemnawaveClient,
    prefs: PreferencesStore,
    *,
    interval: int = CHECK_INTERVAL_SECONDS,
) -> None:
    await asyncio.sleep(60)
    while True:
        try:
            await send_due_trial_nudges(bot, panel, prefs)
        except Exception:  # noqa: BLE001 - keep the reminder loop alive
            logger.exception("Trial nudge iteration failed")
        await asyncio.sleep(interval)
