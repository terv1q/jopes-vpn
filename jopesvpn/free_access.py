from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import UTC, date, datetime, time, timedelta

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramForbiddenError,
    TelegramRetryAfter,
)

from jopesvpn.config import Settings
from jopesvpn.i18n import reset_language, set_language, t
from jopesvpn.panel import (
    FREE_TRAFFIC_BYTES,
    PanelError,
    PanelNotFound,
    PanelUser,
    RemnawaveClient,
)
from jopesvpn.preferences import PreferencesError, PreferencesStore
from jopesvpn.timezones import format_datetime, reset_utc_offset, set_utc_offset
from jopesvpn.ui import format_bytes

logger = logging.getLogger(__name__)

_PERMANENT_DELIVERY_MARKERS: tuple[tuple[str, str], ...] = (
    ("bot was blocked", "blocked"),
    ("bot was kicked", "kicked"),
    ("chat not found", "chat_not_found"),
    ("user is deactivated", "deactivated"),
    ("peer_id_invalid", "invalid_peer"),
    ("bot can't initiate conversation", "chat_not_found"),
    ("user not found", "user_not_found"),
)


def _instant(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _is_expired(user: PanelUser, now: datetime) -> bool:
    expires = _instant(user.expire_at)
    return (
        user.status == "EXPIRED"
        and user.tag == "TRIAL"
        and expires is not None
        and expires <= now
    )


def _delivery_failure(error: TelegramAPIError) -> tuple[str, bool]:
    text = str(error).lower()
    for marker, reason in _PERMANENT_DELIVERY_MARKERS:
        if marker in text:
            return reason, True
    if isinstance(error, TelegramForbiddenError):
        return "forbidden", True
    return type(error).__name__[:64], False


def _forget_free_state(prefs: PreferencesStore, telegram_id: int) -> None:
    try:
        prefs.purge_free_daily_state(telegram_id)
    except (PreferencesError, sqlite3.Error):
        logger.warning("FREE state cleanup failed")


async def process_free_access_once(
    bot: Bot,
    panel: RemnawaveClient,
    prefs: PreferencesStore,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> None:
    if not settings.free_access_enabled:
        return
    current = (now or datetime.now(UTC)).astimezone(UTC)
    current_day = current.date().isoformat()
    try:
        telegram_ids = prefs.free_access_users()
    except (PreferencesError, sqlite3.Error):
        logger.warning("FREE account scan could not read known users")
        return

    reset_unconfirmed = False
    for telegram_id in telegram_ids:
        try:
            user = await panel.get_user_by_telegram_id(telegram_id)
        except PanelNotFound:
            _forget_free_state(prefs, telegram_id)
            continue
        except PanelError as error:
            logger.warning("FREE account lookup failed (%s)", type(error).__name__)
            continue

        if _is_expired(user, current):
            try:
                user = await panel.apply_free_access(user)
                prefs.initialize_free_daily(
                    telegram_id,
                    current_day,
                    user.traffic_lifetime_used_bytes,
                )
            except (PanelError, PreferencesError, sqlite3.Error, ValueError) as error:
                logger.warning(
                    "Expired trial could not be moved to FREE (%s)",
                    type(error).__name__,
                )
                continue

        if user.tag != "FREE":
            _forget_free_state(prefs, telegram_id)
            continue
        if user.status not in {"ACTIVE", "LIMITED"}:
            continue
        try:
            if (
                user.traffic_limit_bytes != FREE_TRAFFIC_BYTES
                or user.traffic_limit_strategy != "NO_RESET"
                or user.expire_at is None
                or not user.expire_at.startswith("2099-")
            ):
                user = await panel.repair_free_access(user)

            state = prefs.free_daily_state(telegram_id)
            if state is None:
                prefs.initialize_free_daily(
                    telegram_id,
                    current_day,
                    user.traffic_lifetime_used_bytes,
                )
                continue
            state_day, baseline = state
            if state_day >= current_day:
                continue
            reset_at = _instant(user.last_traffic_reset_at)
            if reset_at is None or reset_at.date().isoformat() != current_day:
                try:
                    user = await panel.reset_user_traffic(user)
                except PanelError as error:
                    logger.warning(
                        "FREE daily reset request failed (%s)",
                        type(error).__name__,
                    )
                    try:
                        user = await panel.get_user_by_telegram_id(telegram_id)
                    except PanelError as error:
                        logger.warning(
                            "FREE daily reset could not be confirmed (%s)",
                            type(error).__name__,
                        )
                        reset_unconfirmed = True
                        continue
                reset_at = _instant(user.last_traffic_reset_at)
                if reset_at is None or reset_at.date().isoformat() != current_day:
                    reset_unconfirmed = True
                    continue
            if (
                user.status == "LIMITED"
                and user.traffic_used_bytes is not None
                and user.traffic_used_bytes < FREE_TRAFFIC_BYTES
            ):
                try:
                    user = await panel.reactivate_free_after_reset(user)
                except PanelError as error:
                    logger.warning(
                        "FREE account reactivation failed (%s)",
                        type(error).__name__,
                    )
                    reset_unconfirmed = True
                    continue
            if user.status not in {"ACTIVE", "LIMITED"}:
                continue
            measured_day = date.fromisoformat(state_day)
            used = None
            if (
                measured_day == current.date() - timedelta(days=1)
                and baseline is not None
                and user.traffic_lifetime_used_bytes is not None
            ):
                used = max(user.traffic_lifetime_used_bytes - baseline, 0)
            prefs.rollover_free_daily(
                telegram_id,
                state_day,
                current_day,
                used,
                user.traffic_lifetime_used_bytes,
            )
        except PanelError as error:
            logger.warning(
                "FREE account reconciliation failed (%s)",
                type(error).__name__,
            )
        except (PreferencesError, sqlite3.Error, ValueError):
            logger.warning("FREE daily usage state could not be updated")

    if reset_unconfirmed:
        logger.warning("FREE daily reports are waiting for a confirmed Panel reset")

    try:
        reports = prefs.pending_free_daily_reports(
            now=current if now is not None else datetime.now(UTC)
        )
    except (PreferencesError, sqlite3.Error):
        logger.warning("FREE daily report queue could not be read")
        return
    unreachable_users: set[int] = set()
    for telegram_id, report_day, used_bytes in reports:
        if telegram_id in unreachable_users:
            continue
        remaining = (
            max(FREE_TRAFFIC_BYTES - used_bytes, 0)
            if used_bytes is not None
            else None
        )
        language_token = None
        timezone_token = None
        try:
            language = prefs.language(telegram_id)
            offset = prefs.timezone_offset(telegram_id)
            language_token = set_language(language)
            timezone_token = set_utc_offset(offset)
            period_start = datetime.combine(
                date.fromisoformat(report_day), time.min, tzinfo=UTC
            )
            await bot.send_message(
                telegram_id,
                t(
                    "free.daily_report",
                    day=format_datetime(period_start, offset),
                    used=format_bytes(used_bytes),
                    quota=format_bytes(FREE_TRAFFIC_BYTES),
                    remaining=format_bytes(remaining),
                ),
                parse_mode="HTML",
            )
            prefs.mark_free_daily_report_sent(
                telegram_id,
                report_day,
                now=current if now is not None else datetime.now(UTC),
            )
        except TelegramRetryAfter as error:
            try:
                prefs.defer_free_daily_reports(
                    max(1, error.retry_after),
                    now=current if now is not None else datetime.now(UTC),
                )
            except (PreferencesError, sqlite3.Error):
                logger.warning("FREE daily report retry delay could not be saved")
            else:
                logger.debug("FREE daily reports paused for %s seconds", error.retry_after)
            break
        except TelegramAPIError as error:
            reason, permanent = _delivery_failure(error)
            if permanent:
                unreachable_users.add(telegram_id)
            try:
                attempts, abandoned = prefs.record_free_daily_report_failure(
                    telegram_id,
                    report_day,
                    reason=reason,
                    permanent=permanent,
                    now=current if now is not None else datetime.now(UTC),
                )
            except (PreferencesError, sqlite3.Error):
                logger.warning("FREE daily report state could not be saved")
                continue
            if permanent:
                logger.info("FREE daily reports closed: %s", reason)
            elif abandoned:
                logger.warning(
                    "FREE daily report closed after %s attempt(s): %s",
                    attempts,
                    reason,
                )
            elif attempts == 1:
                logger.warning(
                    "FREE daily report delivery failed (%s), will retry", reason
                )
            else:
                logger.debug(
                    "FREE daily report attempt %s failed (%s)", attempts, reason
                )
        except (PreferencesError, sqlite3.Error, ValueError):
            logger.warning("FREE daily report state could not be saved")
        finally:
            if timezone_token is not None:
                reset_utc_offset(timezone_token)
            if language_token is not None:
                reset_language(language_token)


async def free_access_loop(
    bot: Bot,
    panel: RemnawaveClient,
    prefs: PreferencesStore,
    settings: Settings,
    *,
    interval_seconds: int = 300,
) -> None:
    while True:
        await process_free_access_once(bot, panel, prefs, settings)
        current = datetime.now(UTC)
        next_midnight = datetime.combine(
            current.date() + timedelta(days=1), time.min, tzinfo=UTC
        )
        until_midnight = (next_midnight - current).total_seconds()
        await asyncio.sleep(max(1, min(interval_seconds, until_midnight)))
