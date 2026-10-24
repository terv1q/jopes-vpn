from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sqlite3
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from hashlib import sha256
from html import escape
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from aiogram import Bot, Dispatcher, F, Router
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    ErrorEvent,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from aiogram.utils.token import TokenValidationError
from aiohttp import web

from jopesvpn.admin import (
    admin_disable,
    admin_enable,
    admin_extend,
    admin_grant,
    admin_navigation,
    admin_revoke,
    admin_status,
    admin_user,
)
from jopesvpn.admin import (
    router as admin_router,
)
from jopesvpn.announcements import (
    ANNOUNCEMENT_CALLBACK_PREFIX,
    ANNOUNCEMENT_CAMPAIGN,
    ANNOUNCEMENT_TEXT,
    CAMPAIGN_START_PROTOCOLS,
    PROTOCOL_LABELS,
    connection_keyboard,
    image_prompt_keyboard,
    preview_keyboard,
)
from jopesvpn.broadcasts import BroadcastError, BroadcastStore
from jopesvpn.config import ConfigurationError, Settings
from jopesvpn.connection_logs import connection_log_loop
from jopesvpn.free_access import free_access_loop
from jopesvpn.i18n import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    current_language,
    reset_language,
    set_language,
    t,
)
from jopesvpn.notifications import reminder_loop, trial_nudge_loop
from jopesvpn.orders import (
    FULFILLED,
    FULFILLING,
    FULFILLMENT_REVIEW,
    PAID,
    PENDING,
    REFUNDED,
    OrderConflict,
    OrderError,
    OrderNotFound,
    OrderStateError,
    OrderStore,
)
from jopesvpn.panel import (
    FREE_TRAFFIC_BYTES,
    PanelAccessDenied,
    PanelDataError,
    PanelError,
    PanelNotFound,
    PanelUser,
    RemnawaveClient,
)
from jopesvpn.payments import PaymentCheck, PaymentStore
from jopesvpn.plans import (
    Plan,
    PlanTerm,
    format_rubles,
    load_plans,
    stars_for_rub_amount,
    term_savings,
)
from jopesvpn.platega import (
    PlategaClient,
    PlategaError,
    PlategaProtocolError,
    TransactionStatus,
)
from jopesvpn.preferences import PreferencesError, PreferencesStore
from jopesvpn.promotions import (
    PROMO_MAX_EXTRA_BUTTONS,
    PROMO_NEW_DAYS,
    PROMO_POST_TEXT,
    PROMO_RETURNING_DAYS,
    PROMO_START_PREFIX,
    PromotionStore,
    PromotionStoreError,
    promo_builder_buttons_keyboard,
    promo_builder_photo_keyboard,
    promo_builder_text_keyboard,
    promo_preview_keyboard,
    promo_review_photo_keyboard,
)
from jopesvpn.screens import answer_screen, edit_screen
from jopesvpn.screens import configure as configure_screens
from jopesvpn.ticket_ui import (
    ADMIN_TICKETS_PER_PAGE,
    TICKET_MESSAGES_PER_PAGE,
    USER_TICKETS_PER_PAGE,
    category_keyboard,
    category_text,
    rating_keyboard,
    ticket_admin_summary_text,
    ticket_close_prompt_keyboard,
    ticket_detail_keyboard,
    ticket_detail_text,
    ticket_list_keyboard,
    ticket_list_text,
)
from jopesvpn.tickets import TicketRateLimited, TicketStore, TicketStoreError
from jopesvpn.timezones import (
    ADMIN_UTC_OFFSET,
    DEFAULT_UTC_OFFSET,
    current_utc_offset,
    format_datetime,
    reset_utc_offset,
    set_utc_offset,
    utc_label,
)
from jopesvpn.traffic_notifications import traffic_notification_loop
from jopesvpn.ui import (
    DEVICES_PER_PAGE,
    PLATFORM_APPS,
    about_keyboard,
    about_text,
    back_keyboard,
    cabinet_keyboard,
    cabinet_text,
    channel_title,
    connect_keyboard,
    connect_text,
    device_confirm_keyboard,
    device_confirm_text,
    devices_keyboard,
    devices_text,
    documents_keyboard,
    format_bytes,
    guide_apps_keyboard,
    guide_platform_keyboard,
    guide_step_keyboard,
    guide_step_text,
    guide_text,
    help_keyboard,
    help_text,
    home_keyboard,
    home_text,
    language_keyboard,
    language_page_count,
    language_text,
    more_keyboard,
    more_text,
    no_subscription_text,
    payment_method_text,
    payment_methods_keyboard,
    payment_receipt_text,
    plan_terms_keyboard,
    plans_keyboard,
    plans_text,
    platega_payment_keyboard,
    referral_keyboard,
    referral_share_url,
    referral_text,
    settings_keyboard,
    settings_text,
    setup_keyboard,
    stats_keyboard,
    stats_text,
    status_text,
    subscription_keyboard,
    subscription_text,
    support_url,
    tariff_text,
    timezone_keyboard,
    timezone_text,
    topup_amount_keyboard,
    topup_custom_keyboard,
    topup_methods_keyboard,
    topup_text,
    trial_keyboard,
    trial_text,
    troubleshooting_text,
    wallet_keyboard,
    wallet_text,
    withdrawal_action_keyboard,
    withdrawal_confirm_keyboard,
    withdrawal_text,
)
from jopesvpn.wallet import (
    MAX_TOPUP_MINOR,
    MIN_TOPUP_MINOR,
    MIN_WITHDRAWAL_MINOR,
    TOPUP_PRESET_AMOUNTS,
    InsufficientFunds,
    PaymentTransaction,
    TopupConflict,
    TopupNotFound,
    WalletConflict,
    WalletError,
    WalletStore,
    WithdrawalPending,
    WithdrawalUnavailable,
)

logger = logging.getLogger(__name__)
router = Router(name="user")
BUILD_CODE = "JV-PAYMENTS-R6"
PROCESS_STARTED_AT = time.monotonic()


class AnnouncementFlow(StatesGroup):
    awaiting_image = State()


class PromoPostFlow(StatesGroup):
    choosing_text = State()
    configuring_buttons = State()
    awaiting_text = State()
    awaiting_photo = State()
    awaiting_button_label = State()
    awaiting_button_url = State()
    awaiting_review_photo = State()


@router.message(
    Command(
        "account",
        "subscription",
        "devices",
        "stats",
        "settings",
        "guides",
        "plans",
        "referrals",
        "cooperation",
    )
)
async def user_section_command_entry(
    message: Message,
    bot: Bot,
    panel: RemnawaveClient,
    settings: Settings,
    plans: tuple[Plan, ...],
    prefs: PreferencesStore | None = None,
    wallet: WalletStore | None = None,
    state: FSMContext | None = None,
) -> None:
    await user_section_command(
        message,
        bot,
        panel,
        settings,
        plans,
        prefs=prefs,
        wallet=wallet,
        state=state,
    )


@router.message(Command("ping"))
async def ping_command(message: Message, bot: Bot) -> None:
    api_started_at = time.perf_counter()
    await bot.get_me()
    api_latency_ms = max(0, round((time.perf_counter() - api_started_at) * 1000))
    uptime_seconds = max(0, int(time.monotonic() - PROCESS_STARTED_AT))
    days, remainder = divmod(uptime_seconds, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes, seconds = divmod(remainder, 60)
    await message.answer(
        "🏓 Pong\n"
        f"Сборка: {BUILD_CODE}\n"
        f"Telegram API: {api_latency_ms} мс\n"
        f"Uptime: {days}д {hours:02}:{minutes:02}:{seconds:02}"
    )


def _announcement_owner_id(settings: Settings) -> int:
    return settings.owner_telegram_id or min(settings.admin_telegram_ids)


async def _send_announcement_preview(
    bot: Bot, settings: Settings, prefs: PreferencesStore
) -> None:
    owner_id = _announcement_owner_id(settings)
    try:
        prefs.recover_announcement_image_state(ANNOUNCEMENT_CAMPAIGN)
        if not prefs.begin_announcement_preview(ANNOUNCEMENT_CAMPAIGN, owner_id):
            return
        bot_user = await bot.get_me()
        if not bot_user.username:
            raise RuntimeError("Bot username is unavailable")
        await bot.send_message(
            chat_id=owner_id,
            text="Предпросмотр поста для официального канала:",
        )
        preview = await bot.send_message(
            chat_id=owner_id,
            text=ANNOUNCEMENT_TEXT,
            reply_markup=preview_keyboard(bot_user.username),
        )
        prefs.finish_announcement_preview(
            ANNOUNCEMENT_CAMPAIGN, preview.message_id
        )
    except (TelegramAPIError, PreferencesError, RuntimeError):
        logger.exception("Protocol announcement preview could not be delivered")
        try:
            prefs.retry_announcement_preview(ANNOUNCEMENT_CAMPAIGN)
        except PreferencesError:
            logger.exception("Protocol announcement preview state could not be reset")


@router.callback_query(
    F.data.startswith(f"{ANNOUNCEMENT_CALLBACK_PREFIX}:")
)
async def protocol_announcement_approval(
    callback: CallbackQuery,
    bot: Bot,
    settings: Settings,
    prefs: PreferencesStore,
    state: FSMContext | None = None,
) -> None:
    owner_id = _announcement_owner_id(settings)
    if callback.from_user.id != owner_id:
        await callback.answer(
            "Одобрить публикацию может только владелец.", show_alert=True
        )
        return
    action = (callback.data or "").removeprefix(
        f"{ANNOUNCEMENT_CALLBACK_PREFIX}:"
    )
    if action == "image":
        if state is None:
            await callback.answer(
                "Не удалось открыть загрузку изображения.", show_alert=True
            )
            return
        try:
            requested = prefs.begin_announcement_image(
                ANNOUNCEMENT_CAMPAIGN, owner_id
            )
        except PreferencesError:
            logger.exception("Announcement image upload could not be started")
            await callback.answer(
                "Не удалось открыть загрузку изображения.", show_alert=True
            )
            return
        if not requested:
            await callback.answer("Предпросмотр уже обработан.", show_alert=True)
            return
        await state.set_state(AnnouncementFlow.awaiting_image)
        try:
            await bot.send_message(
                owner_id,
                "Пришлите изображение одним сообщением. После загрузки появится "
                "предпросмотр поста с картинкой.",
                reply_markup=image_prompt_keyboard(),
            )
        except TelegramAPIError:
            await state.clear()
            try:
                prefs.cancel_announcement_image(
                    ANNOUNCEMENT_CAMPAIGN, owner_id
                )
            except PreferencesError:
                logger.exception("Announcement image request could not be cancelled")
            await callback.answer(
                "Не удалось запросить изображение.", show_alert=True
            )
            return
        await callback.answer()
        return
    if action == "image_cancel":
        try:
            cancelled = prefs.cancel_announcement_image(
                ANNOUNCEMENT_CAMPAIGN, owner_id
            )
        except PreferencesError:
            logger.exception("Announcement image request could not be cancelled")
            await callback.answer("Не удалось отменить загрузку.", show_alert=True)
            return
        if not cancelled:
            await callback.answer("Загрузка уже обработана.", show_alert=True)
            return
        if state is not None:
            await state.clear()
        await callback.answer("Продолжаем без картинки.")
        if isinstance(callback.message, Message):
            await callback.message.edit_reply_markup(reply_markup=None)
        return
    if action == "cancel":
        try:
            cancelled = prefs.cancel_announcement(ANNOUNCEMENT_CAMPAIGN, owner_id)
        except PreferencesError:
            logger.exception("Protocol announcement could not be cancelled")
            await callback.answer("Не удалось сохранить отмену.", show_alert=True)
            return
        if not cancelled:
            await callback.answer("Предпросмотр уже обработан.", show_alert=True)
            return
        await callback.answer("Публикация отменена.")
        if isinstance(callback.message, Message):
            await callback.message.edit_reply_markup(reply_markup=None)
        if state is not None:
            await state.clear()
        return
    if action != "publish":
        await callback.answer("Кнопка устарела.", show_alert=True)
        return
    try:
        claimed = prefs.claim_announcement_publication(
            ANNOUNCEMENT_CAMPAIGN, owner_id
        )
    except PreferencesError:
        logger.exception("Protocol announcement could not be claimed for publication")
        await callback.answer("Не удалось подготовить публикацию.", show_alert=True)
        return
    if not claimed:
        await callback.answer("Публикация уже обработана.", show_alert=True)
        return
    try:
        bot_user = await bot.get_me()
        if not bot_user.username:
            raise RuntimeError("Bot username is unavailable")
        photo_file_id = prefs.announcement_photo(ANNOUNCEMENT_CAMPAIGN)
    except (TelegramAPIError, PreferencesError, RuntimeError):
        logger.exception("Protocol announcement publication failed")
        try:
            prefs.retry_announcement_publication(ANNOUNCEMENT_CAMPAIGN)
        except PreferencesError:
            logger.exception("Protocol announcement publication state is uncertain")
        await callback.answer(
            "Не удалось опубликовать. Проверьте права бота в канале.",
            show_alert=True,
        )
        return
    try:
        if photo_file_id is None:
            posted = await bot.send_message(
                chat_id=f"@{settings.trial_channel_username}",
                text=ANNOUNCEMENT_TEXT,
                reply_markup=connection_keyboard(bot_user.username),
            )
        else:
            posted = await bot.send_photo(
                chat_id=f"@{settings.trial_channel_username}",
                photo=photo_file_id,
                caption=ANNOUNCEMENT_TEXT,
                reply_markup=connection_keyboard(bot_user.username),
            )
    except (TelegramBadRequest, TelegramForbiddenError):
        logger.exception("Telegram rejected protocol announcement publication")
        try:
            prefs.retry_announcement_publication(ANNOUNCEMENT_CAMPAIGN)
        except PreferencesError:
            logger.exception("Protocol announcement publication state is uncertain")
        await callback.answer(
            "Telegram отклонил публикацию. Проверьте права бота в канале.",
            show_alert=True,
        )
        return
    except TelegramAPIError:
        logger.exception("Protocol announcement publication result is uncertain")
        await callback.answer(
            "Проверьте канал: Telegram не подтвердил отправку. "
            "Не нажимайте повторно.",
            show_alert=True,
        )
        return
    try:
        prefs.finish_announcement_publication(
            ANNOUNCEMENT_CAMPAIGN, posted.message_id
        )
    except PreferencesError:
        logger.exception("Published announcement status could not be saved")
        await callback.answer(
            "Пост отправлен, но его статус не сохранился. Не нажимайте повторно.",
            show_alert=True,
        )
        return
    await callback.answer("Пост опубликован.")
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
    if state is not None:
        await state.clear()
    try:
        await bot.send_message(owner_id, "Пост опубликован в официальном канале.")
    except TelegramAPIError:
        logger.warning("Publication confirmation could not be delivered to owner")


def _channel_post_keyboard(
    post_id: int,
    buttons: list[list[dict[str, str]]],
    bot_username: str,
    *,
    approval: bool,
    post_type: str = "standard",
) -> InlineKeyboardMarkup:
    flat_buttons = [button for row in buttons for button in row]
    if post_type == "promo":
        return promo_preview_keyboard(
            post_id,
            bot_username,
            flat_buttons,
            approval=approval,
        )
    markup = []
    button_index = 0
    for row in buttons:
        markup_row = []
        for button in row:
            markup_row.append(
                InlineKeyboardButton(
                    text=button["text"],
                    url=f"https://t.me/{bot_username}?start=cp_{post_id}_{button_index}",
                    style="success",
                )
            )
            button_index += 1
        if markup_row:
            markup.append(markup_row)
    if approval:
        markup.append(
            [
                InlineKeyboardButton(
                    text="Добавить фото",
                    callback_data=f"channelpost:{post_id}:photo",
                    style="primary",
                )
            ]
        )
        markup.append(
            [
                InlineKeyboardButton(
                    text="Опубликовать в ТГК",
                    callback_data=f"channelpost:{post_id}:approve",
                    style="success",
                ),
                InlineKeyboardButton(
                    text="Отклонить",
                    callback_data=f"channelpost:{post_id}:cancel",
                    style="danger",
                ),
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=markup)


async def channel_post_preview_loop(
    bot: Bot,
    tickets: TicketStore,
    promotions: PromotionStore | None = None,
) -> None:
    try:
        recovered = tickets.recover_channel_post_previews()
        if recovered:
            logger.warning("Recovered stale channel post previews")
    except TicketStoreError:
        logger.exception("Stale channel post previews could not be recovered")
    while True:
        try:
            posts = tickets.pending_channel_posts()
            bot_username = ""
            if posts:
                bot_user = await bot.get_me()
                if not bot_user.username:
                    raise RuntimeError("Bot username is unavailable")
                bot_username = bot_user.username
            for post in posts:
                post_id = int(post["post_id"])
                if not tickets.claim_channel_post_preview(post_id):
                    continue
                reviewer_id = int(post["reviewer_id"] or post["owner_id"])
                try:
                    buttons = json.loads(post["buttons_json"])
                    markup = _channel_post_keyboard(
                        post_id,
                        buttons,
                        bot_username,
                        approval=True,
                        post_type=str(post["post_type"]),
                    )
                    if post["photo_file_id"] is not None:
                        posted = await bot.send_photo(
                            chat_id=reviewer_id,
                            photo=post["photo_file_id"],
                            caption=post["body_html"] or None,
                            parse_mode="HTML" if post["body_html"] else None,
                            reply_markup=markup,
                        )
                    elif post["photo_data"] is not None:
                        posted = await bot.send_photo(
                            chat_id=reviewer_id,
                            photo=BufferedInputFile(
                                bytes(post["photo_data"]),
                                filename={
                                    "image/png": "post.png",
                                    "image/jpeg": "post.jpg",
                                    "image/webp": "post.webp",
                                }.get(post["photo_mime"], "post.jpg"),
                            ),
                            caption=post["body_html"] or None,
                            parse_mode="HTML" if post["body_html"] else None,
                            reply_markup=markup,
                        )
                    else:
                        posted = await bot.send_message(
                            chat_id=reviewer_id,
                            text=post["body_html"],
                            parse_mode="HTML",
                            reply_markup=markup,
                        )
                    tickets.finish_channel_post_preview(post_id, posted.message_id)
                    if post["post_type"] == "promo":
                        logs = (
                            promotions.preparation_log(post_id)
                            if promotions is not None
                            else []
                        )
                        log_text = "\n".join(
                            f"{entry['created_at']} · ID {entry['actor_id']} · "
                            f"{entry['action']}: {entry['detail']}"
                            for entry in logs
                        ) or "Нет записей"
                        try:
                            await bot.send_message(
                                chat_id=reviewer_id,
                                text=t(
                                    "promo.review_details",
                                    post_id=post_id,
                                    creator_id=post["owner_id"],
                                    creator=f"ID {post['owner_id']}",
                                    photo=(
                                        "есть"
                                        if post["photo_file_id"] is not None
                                        or post["photo_data"] is not None
                                        else "нет"
                                    ),
                                    log=escape(log_text),
                                ),
                                parse_mode="HTML",
                            )
                        except TelegramAPIError:
                            logger.exception(
                                "Promotion review details could not be delivered"
                            )
                except (
                    TelegramAPIError,
                    TicketStoreError,
                    PromotionStoreError,
                    ValueError,
                    TypeError,
                ):
                    logger.exception("Channel post preview delivery failed")
                    try:
                        tickets.retry_channel_post_preview(post_id)
                    except TicketStoreError:
                        logger.exception("Channel post preview retry could not be saved")
        except (
            TelegramAPIError,
            TicketStoreError,
            PromotionStoreError,
            RuntimeError,
        ):
            logger.exception("Channel post queue could not be processed")
        await asyncio.sleep(5)


@router.callback_query(F.data.startswith("channelpost:"))
async def channel_post_approval(
    callback: CallbackQuery,
    bot: Bot,
    settings: Settings,
    tickets: TicketStore,
    state: FSMContext | None = None,
    promotions: PromotionStore | None = None,
) -> None:
    match = re.fullmatch(
        r"channelpost:([1-9][0-9]{0,18}):(approve|cancel|photo|photo_cancel)",
        callback.data or "",
    )
    if match is None:
        await callback.answer("Кнопка устарела.", show_alert=True)
        return
    post_id = int(match.group(1))
    owner_id = _announcement_owner_id(settings)
    if callback.from_user.id != owner_id:
        await callback.answer("Одобрить пост может только владелец.", show_alert=True)
        return
    action = match.group(2)
    if action == "photo_cancel":
        if state is not None and (await state.get_data()).get("post_id") == post_id:
            await state.clear()
        await callback.answer("Загрузка фото отменена.")
        return
    if action == "photo":
        if state is None:
            await callback.answer("Загрузка фото недоступна.", show_alert=True)
            return
        try:
            post = tickets.get_channel_post(post_id)
        except TicketStoreError:
            logger.exception("Channel post image request could not be loaded")
            await callback.answer("Не удалось открыть загрузку фото.", show_alert=True)
            return
        if (
            post is None
            or post["status"] != "awaiting_approval"
            or int(post["reviewer_id"] or post["owner_id"]) != owner_id
        ):
            await callback.answer("Этот пост уже обработан.", show_alert=True)
            return
        await state.clear()
        await state.update_data(post_id=post_id)
        await state.set_state(PromoPostFlow.awaiting_review_photo)
        try:
            await bot.send_message(
                owner_id,
                t("promo.builder.review_photo_prompt"),
                reply_markup=promo_review_photo_keyboard(post_id),
            )
        except TelegramAPIError:
            await state.clear()
            logger.exception("Promotion photo upload prompt could not be sent")
            await callback.answer("Не удалось запросить фото.", show_alert=True)
            return
        await callback.answer()
        return
    if action == "cancel":
        try:
            cancelled = tickets.cancel_channel_post(post_id, owner_id)
        except TicketStoreError:
            logger.exception("Channel post cancellation could not be saved")
            await callback.answer("Не удалось отменить пост.", show_alert=True)
            return
        if not cancelled:
            await callback.answer("Пост уже обработан.", show_alert=True)
            return
        try:
            post = tickets.get_channel_post(post_id)
            if post is not None and post["post_type"] == "promo":
                if promotions is None:
                    raise PromotionStoreError("Promotion storage is unavailable")
                promotions.cancel_campaign(post_id)
        except (TicketStoreError, PromotionStoreError):
            logger.exception("Promotion cancellation state could not be saved")
        await callback.answer("Пост отменён.")
        if isinstance(callback.message, Message):
            await callback.message.edit_reply_markup(reply_markup=None)
        return
    claimed = False
    try:
        if not tickets.claim_channel_post_publication(post_id, owner_id):
            await callback.answer("Пост уже обработан.", show_alert=True)
            return
        claimed = True
        post = tickets.get_channel_post(post_id)
        bot_user = await bot.get_me()
        if post is None or not bot_user.username:
            raise RuntimeError("Channel post data is unavailable")
        buttons = json.loads(post["buttons_json"])
        markup = _channel_post_keyboard(
            post_id,
            buttons,
            bot_user.username,
            approval=False,
            post_type=str(post["post_type"]),
        )
    except (TelegramAPIError, TicketStoreError, RuntimeError, ValueError, TypeError):
        if claimed:
            try:
                tickets.retry_channel_post_publication(post_id)
            except TicketStoreError:
                logger.exception("Channel post retry state could not be saved")
        logger.exception("Channel post could not be prepared")
        await callback.answer("Не удалось подготовить пост.", show_alert=True)
        return
    try:
        if post["photo_file_id"] is not None:
            published = await bot.send_photo(
                chat_id=f"@{settings.trial_channel_username}",
                photo=post["photo_file_id"],
                caption=post["body_html"] or None,
                parse_mode="HTML" if post["body_html"] else None,
                reply_markup=markup if buttons or post["post_type"] == "promo" else None,
            )
        elif post["photo_data"] is not None:
            published = await bot.send_photo(
                chat_id=f"@{settings.trial_channel_username}",
                photo=BufferedInputFile(
                    bytes(post["photo_data"]),
                    filename={
                        "image/png": "post.png",
                        "image/jpeg": "post.jpg",
                        "image/webp": "post.webp",
                    }.get(post["photo_mime"], "post.jpg"),
                ),
                caption=post["body_html"] or None,
                parse_mode="HTML" if post["body_html"] else None,
                reply_markup=markup if buttons or post["post_type"] == "promo" else None,
            )
        else:
            published = await bot.send_message(
                chat_id=f"@{settings.trial_channel_username}",
                text=post["body_html"],
                parse_mode="HTML",
                reply_markup=markup if buttons or post["post_type"] == "promo" else None,
            )
    except (TelegramBadRequest, TelegramForbiddenError):
        try:
            tickets.retry_channel_post_publication(post_id)
        except TicketStoreError:
            logger.exception("Rejected channel post could not be reopened")
        logger.exception("Telegram rejected channel post publication")
        await callback.answer(
            "Telegram отклонил пост. Проверьте права бота и попробуйте снова.",
            show_alert=True,
        )
        return
    except TelegramAPIError:
        logger.exception("Channel post publication result is uncertain")
        await callback.answer(
            "Не удалось подтвердить результат. Проверьте канал и не нажимайте повторно.",
            show_alert=True,
        )
        return
    try:
        if post["post_type"] == "promo":
            if promotions is None:
                raise PromotionStoreError("Promotion storage is unavailable")
            finished = promotions.finish_publication(post_id, published.message_id)
        else:
            finished = tickets.finish_channel_post_publication(
                post_id, published.message_id
            )
        if not finished:
            raise TicketStoreError("Published post status could not be saved")
    except (PromotionStoreError, TicketStoreError):
        logger.exception("Published channel post status could not be saved")
        await callback.answer(
            "Пост отправлен, но статус не сохранился. Не нажимайте повторно.",
            show_alert=True,
        )
        return
    await callback.answer("Пост опубликован.")
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)


def _promo_builder_allowed(callback: CallbackQuery, settings: Settings) -> bool:
    return (
        isinstance(callback.message, Message)
        and callback.message.chat.type == "private"
        and callback.from_user.id in settings.admin_telegram_ids
    )


def _promo_builder_buttons(data: dict[str, object]) -> list[dict[str, str]]:
    raw_buttons = data.get("buttons", [])
    if not isinstance(raw_buttons, list):
        return []
    return [
        button
        for button in raw_buttons
        if isinstance(button, dict)
        and isinstance(button.get("text"), str)
        and isinstance(button.get("url"), str)
    ]


async def _send_promo_builder_preview(
    bot: Bot, chat_id: int, data: dict[str, object]
) -> None:
    bot_user = await bot.get_me()
    text = data.get("text")
    if not bot_user.username or not isinstance(text, str) or not text.strip():
        raise ValueError("Promo preview data is incomplete")
    keyboard = promo_preview_keyboard(
        None, bot_user.username, _promo_builder_buttons(data)
    )
    photo_file_id = data.get("photo_file_id")
    if isinstance(photo_file_id, str):
        await bot.send_photo(
            chat_id=chat_id,
            photo=photo_file_id,
            caption=text,
            reply_markup=keyboard,
        )
    else:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("promo-builder:"))
async def promo_post_builder_callback(
    callback: CallbackQuery,
    bot: Bot,
    settings: Settings,
    state: FSMContext,
    tickets: TicketStore,
    promotions: PromotionStore,
) -> None:
    if not _promo_builder_allowed(callback, settings):
        await callback.answer(t("admin.command_unavailable"), show_alert=True)
        return
    assert isinstance(callback.message, Message)
    action = (callback.data or "").removeprefix("promo-builder:")
    if action == "start":
        await state.clear()
        await state.update_data(actor_id=callback.from_user.id, buttons=[], preparation=[])
        await state.set_state(PromoPostFlow.choosing_text)
        await callback.answer()
        await callback.message.answer(
            t("promo.builder.choose_text", body=escape(PROMO_POST_TEXT)),
            reply_markup=promo_builder_text_keyboard(),
            parse_mode="HTML",
        )
        return
    data = await state.get_data()
    if data.get("actor_id") != callback.from_user.id:
        await callback.answer(
            "Черновик не найден. Начните создание заново.", show_alert=True
        )
        return
    if action == "cancel":
        await state.clear()
        await callback.answer(t("promo.builder.cancelled"))
        await callback.message.answer(t("promo.builder.cancelled"))
        return
    if action == "text:default":
        await state.update_data(text=PROMO_POST_TEXT)
        await state.set_state(PromoPostFlow.awaiting_photo)
        await callback.answer()
        await callback.message.answer(
            t("promo.builder.photo_prompt"),
            reply_markup=promo_builder_photo_keyboard(),
        )
        return
    if action == "text:edit":
        await state.set_state(PromoPostFlow.awaiting_text)
        await callback.answer()
        await callback.message.answer(t("promo.builder.edit_text"))
        return
    if action in {"photo:skip", "photo:replace"}:
        if "text" not in data:
            await callback.answer("Сначала задайте текст.", show_alert=True)
            return
        preparation = list(data.get("preparation", []))
        if action == "photo:skip":
            await state.update_data(photo_file_id=None)
            preparation.append({"action": "photo", "detail": "не добавлено"})
            await state.set_state(PromoPostFlow.configuring_buttons)
        else:
            preparation.append({"action": "photo", "detail": "запрошена замена"})
            await state.set_state(PromoPostFlow.awaiting_photo)
        await state.update_data(preparation=preparation)
        await callback.answer()
        if action == "photo:skip":
            await callback.message.answer(
                t("promo.builder.buttons_prompt"),
                reply_markup=promo_builder_buttons_keyboard(
                    len(_promo_builder_buttons(data))
                ),
            )
        else:
            await callback.message.answer(
                t("promo.builder.photo_prompt"),
                reply_markup=promo_builder_photo_keyboard(),
            )
        return
    if action == "button:add":
        if len(_promo_builder_buttons(data)) >= PROMO_MAX_EXTRA_BUTTONS:
            await callback.answer("Достигнут лимит дополнительных кнопок.", show_alert=True)
            return
        await state.set_state(PromoPostFlow.awaiting_button_label)
        await callback.answer()
        await callback.message.answer(t("promo.builder.button_label"))
        return
    if action == "preview":
        try:
            await _send_promo_builder_preview(bot, callback.from_user.id, data)
        except (TelegramAPIError, ValueError):
            logger.exception("Promo post preview could not be sent")
            await callback.answer("Не удалось показать предпросмотр.", show_alert=True)
            return
        await callback.answer()
        await callback.message.answer(
            t("promo.builder.controls"),
            reply_markup=promo_builder_buttons_keyboard(
                len(_promo_builder_buttons(data))
            ),
        )
        return
    if action == "submit":
        text = data.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 1024:
            await callback.answer(t("promo.builder.text_invalid"), show_alert=True)
            return
        post_id: int | None = None
        try:
            post_id = tickets.create_channel_post(
                callback.from_user.id,
                escape(text.strip()),
                [[button] for button in _promo_builder_buttons(data)],
                None,
                None,
                reviewer_id=_announcement_owner_id(settings),
                photo_file_id=(
                    data.get("photo_file_id")
                    if isinstance(data.get("photo_file_id"), str)
                    else None
                ),
                post_type="promo",
            )
            promotions.create_campaign(post_id)
            preparations = data.get("preparation", [])
            promotions.log_preparation(
                post_id,
                callback.from_user.id,
                "created",
                f"creator={callback.from_user.full_name}",
            )
            for entry in preparations if isinstance(preparations, list) else []:
                if isinstance(entry, dict):
                    promotions.log_preparation(
                        post_id,
                        callback.from_user.id,
                        str(entry.get("action", "update")),
                        str(entry.get("detail", "")),
                    )
            promotions.log_preparation(
                post_id,
                callback.from_user.id,
                "sent_for_approval",
                f"buttons={len(_promo_builder_buttons(data)) + 1}",
            )
        except (TicketStoreError, PromotionStoreError, ValueError):
            logger.exception("Promo post could not be submitted for approval")
            if post_id is not None:
                try:
                    tickets.cancel_queued_promo_post(
                        post_id, callback.from_user.id
                    )
                    promotions.cancel_campaign(post_id)
                except (TicketStoreError, PromotionStoreError):
                    logger.exception("Incomplete promo post could not be cancelled")
            await callback.answer(t("promo.builder.unavailable"), show_alert=True)
            return
        await state.clear()
        await callback.answer()
        await callback.message.answer(t("promo.builder.submitted"))
        return
    await callback.answer("Кнопка устарела.", show_alert=True)


@router.message(PromoPostFlow.awaiting_text, F.text)
async def receive_promo_post_text(
    message: Message,
    settings: Settings,
    state: FSMContext,
) -> None:
    if not _private_chat(message) or not _is_admin(message, settings):
        await state.clear()
        await message.answer(t("admin.command_unavailable"))
        return
    text = (message.text or "").strip()
    if text.casefold() in {"/cancel", "отмена"}:
        await state.clear()
        await message.answer(t("promo.builder.cancelled"))
        return
    if not text or len(text) > 1024:
        await message.answer(t("promo.builder.text_invalid"))
        return
    data = await state.get_data()
    preparation = list(data.get("preparation", []))
    preparation.append({"action": "text", "detail": "изменён администратором"})
    await state.update_data(text=text, preparation=preparation)
    await state.set_state(PromoPostFlow.awaiting_photo)
    await message.answer(
        t("promo.builder.photo_prompt"),
        reply_markup=promo_builder_photo_keyboard(),
    )


@router.message(PromoPostFlow.awaiting_photo, F.photo)
async def receive_promo_post_photo(
    message: Message,
    settings: Settings,
    state: FSMContext,
) -> None:
    if not _private_chat(message) or not _is_admin(message, settings):
        await state.clear()
        await message.answer(t("admin.command_unavailable"))
        return
    assert message.from_user is not None
    data = await state.get_data()
    if data.get("actor_id") != message.from_user.id:
        await state.clear()
        await message.answer(t("promo.builder.cancelled"))
        return
    assert message.photo is not None
    preparation = list(data.get("preparation", []))
    preparation.append({"action": "photo", "detail": "добавлено администратором"})
    await state.update_data(
        photo_file_id=message.photo[-1].file_id,
        preparation=preparation,
    )
    await state.set_state(PromoPostFlow.configuring_buttons)
    await message.answer(t("promo.builder.photo_saved"))
    await message.answer(
        t("promo.builder.buttons_prompt"),
        reply_markup=promo_builder_buttons_keyboard(
            len(_promo_builder_buttons(data))
        ),
    )


@router.message(PromoPostFlow.awaiting_button_label, F.text)
async def receive_promo_post_button_label(
    message: Message,
    settings: Settings,
    state: FSMContext,
) -> None:
    if not _private_chat(message) or not _is_admin(message, settings):
        await state.clear()
        await message.answer(t("admin.command_unavailable"))
        return
    label = (message.text or "").strip()
    if label.casefold() in {"/cancel", "отмена"}:
        await state.set_state(PromoPostFlow.configuring_buttons)
        await message.answer(
            t("promo.builder.buttons_prompt"),
            reply_markup=promo_builder_buttons_keyboard(0),
        )
        return
    if not label or len(label) > 64:
        await message.answer(t("promo.builder.button_label"))
        return
    await state.update_data(pending_button_label=label)
    await state.set_state(PromoPostFlow.awaiting_button_url)
    await message.answer(t("promo.builder.button_url"))


@router.message(PromoPostFlow.awaiting_button_url, F.text)
async def receive_promo_post_button_url(
    message: Message,
    settings: Settings,
    state: FSMContext,
) -> None:
    if not _private_chat(message) or not _is_admin(message, settings):
        await state.clear()
        await message.answer(t("admin.command_unavailable"))
        return
    url = (message.text or "").strip()
    if url.casefold() in {"/cancel", "отмена"}:
        await state.set_state(PromoPostFlow.configuring_buttons)
        await message.answer(
            t("promo.builder.buttons_prompt"),
            reply_markup=promo_builder_buttons_keyboard(0),
        )
        return
    try:
        parsed = urlsplit(url)
        parsed_port = parsed.port
    except ValueError:
        await message.answer(t("promo.builder.button_invalid"))
        return
    if (
        len(url) > 512
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed_port == 0
        or any(character.isspace() for character in url)
    ):
        await message.answer(t("promo.builder.button_invalid"))
        return
    data = await state.get_data()
    buttons = _promo_builder_buttons(data)
    if len(buttons) >= PROMO_MAX_EXTRA_BUTTONS:
        await state.set_state(PromoPostFlow.configuring_buttons)
        await message.answer("Достигнут лимит дополнительных кнопок.")
        return
    label = data.get("pending_button_label")
    if not isinstance(label, str):
        await state.set_state(PromoPostFlow.configuring_buttons)
        await message.answer(t("promo.builder.button_invalid"))
        return
    buttons.append({"text": label, "url": url})
    preparation = list(data.get("preparation", []))
    preparation.append(
        {"action": "button_added", "detail": f"{label} · {parsed.hostname}"}
    )
    await state.update_data(
        buttons=buttons,
        preparation=preparation,
        pending_button_label=None,
    )
    await state.set_state(PromoPostFlow.configuring_buttons)
    await message.answer(
        t("promo.builder.controls"),
        reply_markup=promo_builder_buttons_keyboard(len(buttons)),
    )


@router.message(PromoPostFlow.awaiting_review_photo, F.photo)
async def receive_review_post_photo(
    message: Message,
    bot: Bot,
    settings: Settings,
    state: FSMContext,
    tickets: TicketStore,
    promotions: PromotionStore,
) -> None:
    owner_id = _announcement_owner_id(settings)
    if not _private_chat(message) or message.from_user is None:
        await state.clear()
        return
    if message.from_user.id != owner_id:
        await state.clear()
        await message.answer(t("admin.command_unavailable"))
        return
    data = await state.get_data()
    post_id = data.get("post_id")
    if not isinstance(post_id, int):
        await state.clear()
        await message.answer(t("promo.builder.review_photo_expired"))
        return
    new_preview_id: int | None = None
    try:
        post = tickets.get_channel_post(post_id)
        bot_user = await bot.get_me()
        if post is None or post["status"] != "awaiting_approval":
            raise TicketStoreError("Post is no longer awaiting approval")
        if not bot_user.username:
            raise RuntimeError("Bot username is unavailable")
        markup = _channel_post_keyboard(
            post_id,
            json.loads(post["buttons_json"]),
            bot_user.username,
            approval=True,
            post_type=str(post["post_type"]),
        )
        assert message.photo is not None
        preview = await bot.send_photo(
            chat_id=owner_id,
            photo=message.photo[-1].file_id,
            caption=post["body_html"] or None,
            parse_mode="HTML" if post["body_html"] else None,
            reply_markup=markup,
        )
        new_preview_id = preview.message_id
        previous_message_id = tickets.update_channel_post_preview_photo(
            post_id,
            owner_id,
            message.photo[-1].file_id,
            new_preview_id,
        )
        if previous_message_id is None:
            await bot.delete_message(owner_id, new_preview_id)
            new_preview_id = None
            await state.clear()
            await message.answer(t("promo.builder.review_photo_expired"))
            return
    except (TelegramAPIError, TicketStoreError, PromotionStoreError, RuntimeError, ValueError):
        logger.exception("Review photo could not be added to channel post")
        if new_preview_id is not None:
            try:
                await bot.delete_message(owner_id, new_preview_id)
            except TelegramAPIError:
                logger.warning("Temporary photo preview could not be removed")
        await message.answer(t("promo.builder.unavailable"))
        return
    if post["post_type"] == "promo":
        try:
            promotions.log_preparation(
                post_id, owner_id, "review_photo_added", "разработчик добавил фото"
            )
        except PromotionStoreError:
            logger.exception("Review photo action could not be added to preparation log")
    if previous_message_id is not None:
        try:
            await bot.delete_message(owner_id, previous_message_id)
        except TelegramAPIError:
            logger.warning("Previous channel post preview could not be removed")
    await state.clear()
    await message.answer(t("promo.builder.review_photo_saved"))


@router.message(PromoPostFlow.awaiting_photo, F.text)
async def promo_post_photo_expected(
    message: Message, state: FSMContext
) -> None:
    if (message.text or "").strip().casefold() in {"/cancel", "отмена"}:
        await state.clear()
        await message.answer(t("promo.builder.cancelled"))
        return
    await message.answer(t("promo.builder.photo_prompt"))


@router.message(PromoPostFlow.awaiting_review_photo, F.text)
async def promo_review_photo_expected(message: Message) -> None:
    await message.answer(t("promo.builder.review_photo_prompt"))


@router.message(AnnouncementFlow.awaiting_image, F.photo)
async def receive_announcement_image(
    message: Message,
    bot: Bot,
    settings: Settings,
    prefs: PreferencesStore,
    state: FSMContext,
) -> None:
    owner_id = _announcement_owner_id(settings)
    if not _private_chat(message):
        await state.clear()
        return
    assert message.from_user is not None
    if message.from_user.id != owner_id:
        await state.clear()
        return
    assert message.photo is not None
    try:
        old_preview_id = prefs.claim_announcement_image(
            ANNOUNCEMENT_CAMPAIGN, owner_id, message.photo[-1].file_id
        )
    except PreferencesError:
        logger.exception("Announcement image could not be saved")
        await message.answer("Не удалось сохранить изображение. Попробуйте ещё раз.")
        return
    if old_preview_id is None:
        await state.clear()
        await message.answer("Загрузка изображения уже завершена или устарела.")
        return
    try:
        bot_user = await bot.get_me()
        if not bot_user.username:
            raise RuntimeError("Bot username is unavailable")
        photo_preview = await bot.send_photo(
            chat_id=owner_id,
            photo=message.photo[-1].file_id,
            caption=ANNOUNCEMENT_TEXT,
            reply_markup=preview_keyboard(bot_user.username),
        )
    except (TelegramAPIError, RuntimeError):
        logger.exception("Announcement image preview could not be sent")
        try:
            prefs.retry_announcement_image(ANNOUNCEMENT_CAMPAIGN)
        except PreferencesError:
            logger.exception("Announcement image state could not be reset")
        await message.answer(
            "Не удалось показать картинку. Попробуйте отправить её ещё раз."
        )
        return
    try:
        prefs.finish_announcement_image(
            ANNOUNCEMENT_CAMPAIGN, photo_preview.message_id
        )
    except PreferencesError:
        logger.exception("Announcement image preview state could not be saved")
        await message.answer(
            "Картинка отправлена, но предпросмотр не сохранился. "
            "Не одобряйте пост до проверки."
        )
        return
    await state.clear()
    try:
        await bot.delete_message(owner_id, old_preview_id)
    except TelegramAPIError:
        logger.warning("Old text-only announcement preview could not be removed")
    await message.answer("Картинка добавлена. Проверьте предпросмотр и одобрите пост.")


@router.message(AnnouncementFlow.awaiting_image, F.text)
async def announcement_image_expected(message: Message) -> None:
    await message.answer(
        "Отправьте картинку как фото или нажмите «Не добавлять картинку»."
    )


@router.message(Command("about"))
async def show_about_command(
    message: Message,
    plans: tuple[Plan, ...],
    settings: Settings,
    prefs: PreferencesStore | None = None,
    wallet: WalletStore | None = None,
) -> None:
    if not _private_chat(message):
        await message.answer(t("alert.private_use_start"))
        return
    privacy_url, agreement_url = (
        _document_urls(prefs) if prefs is not None else (None, None)
    )
    await answer_screen(
        message,
        about_text(
            plans,
            traffic_plans_enabled=settings.traffic_plans_enabled,
            free_access_enabled=settings.free_access_enabled,
            documents_configured=privacy_url is not None and agreement_url is not None,
        ),
        about_keyboard(privacy_url, agreement_url),
        banner="support",
    )


class TicketFlow(StatesGroup):
    awaiting_new_message = State()
    awaiting_user_reply = State()
    awaiting_admin_reply = State()


class TopUpFlow(StatesGroup):
    awaiting_amount = State()
    choose_payment = State()


class LanguageMiddleware(BaseMiddleware):
    """Apply the stored interface language for the duration of one update."""

    def __init__(self, prefs: PreferencesStore | None) -> None:
        self._prefs = prefs
        super().__init__()

    async def __call__(self, handler, event, data):
        language = DEFAULT_LANGUAGE
        from_user = getattr(event, "from_user", None)
        if self._prefs is not None and from_user is not None:
            try:
                language = self._prefs.language(from_user.id, DEFAULT_LANGUAGE)
            except PreferencesError:
                logger.warning("Preferences language read failed")
        token = set_language(language)
        timezone_token = None
        if self._prefs is not None and from_user is not None:
            try:
                offset = self._prefs.timezone_offset(
                    from_user.id, DEFAULT_UTC_OFFSET
                )
                timezone_token = set_utc_offset(offset)
            except (PreferencesError, ValueError):
                logger.warning("User timezone read failed")
        try:
            return await handler(event, data)
        finally:
            if timezone_token is not None:
                reset_utc_offset(timezone_token)
            reset_language(token)


class OnboardingActivityMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        super().__init__()

    async def __call__(self, handler, event, data):
        prefs = data.get("prefs")
        from_user = getattr(event, "from_user", None)
        is_start = (
            isinstance(event, Message)
            and event.text is not None
            and re.match(r"^/start(?:@\w+)?(?:\s|$)", event.text) is not None
        )
        is_private = (
            isinstance(event, Message) and event.chat.type == "private"
        ) or (
            isinstance(event, CallbackQuery)
            and event.message is not None
            and event.message.chat.type == "private"
        )
        if (
            isinstance(prefs, PreferencesStore)
            and from_user is not None
            and is_private
        ):
            try:
                prefs.remember(
                    from_user.id,
                    from_user.first_name,
                    from_user.last_name,
                    from_user.username,
                )
            except (PreferencesError, ValueError):
                logger.warning("User profile could not be stored")
        if isinstance(prefs, PreferencesStore) and from_user is not None and not is_start:
            try:
                prefs.mark_onboarding_activity(from_user.id)
            except (PreferencesError, ValueError):
                logger.warning("Onboarding activity could not be stored")
        return await handler(event, data)


router.message.outer_middleware(OnboardingActivityMiddleware())
router.callback_query.outer_middleware(OnboardingActivityMiddleware())


class DocumentsConsentMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if not isinstance(event, (Message, CallbackQuery, PreCheckoutQuery)):
            return await handler(event, data)
        user = getattr(event, "from_user", None)
        if user is None:
            return await handler(event, data)
        settings = data.get("settings")
        is_admin = (
            isinstance(settings, Settings)
            and user.id in settings.admin_telegram_ids
        )
        if isinstance(event, Message) and event.chat.type != "private":
            return await handler(event, data)
        if (
            isinstance(event, CallbackQuery)
            and (
                event.message is None
                or event.message.chat.type != "private"
            )
        ):
            return await handler(event, data)

        prefs = data.get("prefs")
        if not isinstance(prefs, PreferencesStore):
            logger.error("Document consent storage is unavailable")
            if isinstance(event, Message):
                await event.answer(t("docs.accept_failed"))
            elif isinstance(event, CallbackQuery):
                await event.answer(t("docs.accept_failed"), show_alert=True)
            else:
                await event.answer(
                    ok=False,
                    error_message=t("docs.accept_failed"),
                )
            return
        try:
            privacy_url = prefs.document_url("privacy")
            agreement_url = prefs.document_url("agreement")
        except (PreferencesError, ValueError):
            logger.warning("Document links could not be loaded")
            if isinstance(event, Message):
                await event.answer(t("docs.accept_failed"))
            elif isinstance(event, CallbackQuery):
                await event.answer(t("docs.accept_failed"), show_alert=True)
            else:
                await event.answer(
                    ok=False,
                    error_message=t("docs.accept_failed"),
                )
            return
        if privacy_url is None or agreement_url is None:
            return await handler(event, data)
        try:
            accepted = prefs.documents_accepted(
                user.id, prefs.documents_version()
            )
        except (PreferencesError, ValueError):
            logger.warning("Document consent could not be checked")
            if isinstance(event, Message):
                await event.answer(t("docs.accept_failed"))
            elif isinstance(event, CallbackQuery):
                await event.answer(t("docs.accept_failed"), show_alert=True)
            else:
                await event.answer(
                    ok=False,
                    error_message=t("docs.accept_failed"),
                )
            return
        if accepted:
            return await handler(event, data)

        if isinstance(event, Message):
            text = event.text or ""
            if event.successful_payment is not None or event.refunded_payment is not None:
                return await handler(event, data)
            if re.match(
                r"^/(?:start|help|paysupport)(?:@\w+)?(?:\s|$)",
                text,
            ):
                return await handler(event, data)
            if is_admin and re.match(
                r"^/(?:admin|refund_star)(?:@\w+)?(?:\s|$)",
                text,
            ):
                return await handler(event, data)
            state = data.get("state")
            if isinstance(state, FSMContext):
                current_state = await state.get_state()
                if current_state and current_state.endswith(
                    ":awaiting_new_message"
                ):
                    return await handler(event, data)
                if is_admin and current_state == "DocumentsFlow:url":
                    return await handler(event, data)
                if is_admin and current_state and current_state.startswith(
                    "PromoPostFlow:"
                ):
                    return await handler(event, data)
            await event.answer(
                _documents_consent_text(prefs),
                reply_markup=_documents_consent_keyboard(prefs),
                parse_mode="HTML",
            )
            return
        if isinstance(event, CallbackQuery):
            action = event.data or ""
            if action.startswith("docs:") or (
                not is_admin and action.startswith("ticket:")
            ):
                return await handler(event, data)
            if is_admin and action.startswith(
                ("promo-builder:", "channelpost:")
            ):
                return await handler(event, data)
            if is_admin and action in {"admin:home", "admin:docs"}:
                return await handler(event, data)
            if is_admin and action.startswith("admin:docs:"):
                return await handler(event, data)
            await event.answer(t("docs.accept_required"), show_alert=True)
            if event.message is not None:
                await event.message.answer(
                    _documents_consent_text(prefs),
                    reply_markup=_documents_consent_keyboard(prefs),
                    parse_mode="HTML",
                )
            return
        await event.answer(
            ok=False,
            error_message=t("docs.accept_required"),
        )


@router.callback_query(F.data.startswith("about:missing:"))
async def missing_about_document(callback: CallbackQuery) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer(t("alert.private_chat"), show_alert=True)
        return
    document = (callback.data or "").removeprefix("about:missing:")
    if document not in {"privacy", "agreement"}:
        await callback.answer(t("alert.stale_button_short"), show_alert=True)
        return
    await callback.answer(t("about.document_missing"), show_alert=True)


def _document_urls(
    prefs: PreferencesStore,
) -> tuple[str | None, str | None]:
    try:
        return prefs.document_url("privacy"), prefs.document_url("agreement")
    except (PreferencesError, ValueError):
        logger.warning("Document links could not be loaded")
        return None, None


def _documents_consent_text(prefs: PreferencesStore) -> str:
    privacy_url, agreement_url = _document_urls(prefs)
    message = f"<b>{t('docs.title')}</b>\n\n{t('docs.accept_required')}"
    message += "\n\n" + t("docs.version", version=prefs.documents_version())
    if privacy_url is None or agreement_url is None:
        message += "\n\n" + t("docs.unconfigured")
    else:
        message += "\n\n" + t("docs.intro")
    return message


def _documents_consent_keyboard(prefs: PreferencesStore):
    privacy_url, agreement_url = _document_urls(prefs)
    return documents_keyboard(
        privacy_url,
        agreement_url,
        acceptance_required=True,
        can_accept=privacy_url is not None and agreement_url is not None,
        version=prefs.documents_version(),
    )


router.message.outer_middleware(DocumentsConsentMiddleware())
router.callback_query.outer_middleware(DocumentsConsentMiddleware())
router.pre_checkout_query.outer_middleware(DocumentsConsentMiddleware())


TRIAL_TAG = "TRIAL"
_trial_lock = asyncio.Lock()
_device_lock = asyncio.Lock()
_admin_plan_lock = asyncio.Lock()
_free_access_lock = asyncio.Lock()
_balance_purchase_lock = asyncio.Lock()
_platega_link_lock = asyncio.Lock()
_device_delete_attempts: dict[int, list[float]] = {}
DEVICE_DELETE_LIMIT = 5
DEVICE_DELETE_WINDOW_SECONDS = 60.0


def _private_chat(message: Message) -> bool:
    return message.chat.type == "private" and message.from_user is not None


def _is_admin(message: Message, settings: Settings) -> bool:
    return (
        message.from_user is not None
        and message.from_user.id in settings.admin_telegram_ids
    )


def _is_stale_callback_query_error(error: BaseException) -> bool:
    if not isinstance(error, TelegramBadRequest):
        return False
    message = error.message.casefold()
    return "query is too old" in message or "query id is invalid" in message


async def _handle_stale_callback_query_error(event: ErrorEvent) -> bool:
    if _is_stale_callback_query_error(event.exception):
        logger.info("Ignored an expired Telegram callback query")
        return True
    raise event.exception


def _admin_only(message: Message, settings: Settings) -> bool:
    return _private_chat(message) and _is_admin(message, settings)


def _expiry_text(user: PanelUser) -> str:
    if not user.expire_at:
        return t("time.unknown")
    try:
        expires = datetime.fromisoformat(user.expire_at.replace("Z", "+00:00"))
        if expires.tzinfo is None:
            return t("time.unknown")
        return format_datetime(expires, current_utc_offset())
    except ValueError:
        return t("time.unknown")


def _page_url(user: PanelUser | None, settings: Settings) -> str | None:
    if user is None or user.status != "ACTIVE":
        return None
    try:
        return RemnawaveClient.subscription_page_url(
            user, settings.subscription_page_url
        )
    except PanelDataError:
        logger.warning("Panel subscription page URL could not be derived")
        return None


async def _clear_state(state: FSMContext | None) -> None:
    if state is not None:
        await state.clear()


async def _get_owned_user(message: Message, panel: RemnawaveClient) -> PanelUser:
    assert message.from_user is not None
    user = await panel.get_user_by_telegram_id(message.from_user.id)
    if user.telegram_id != message.from_user.id:
        raise PanelNotFound("No Panel user is linked to this Telegram account")
    return user


def _panel_error_text(error: PanelError) -> str:
    if isinstance(error, PanelNotFound):
        return t("alert.panel_not_found")
    if isinstance(error, PanelAccessDenied):
        return t("alert.panel_access")
    if isinstance(error, PanelDataError):
        return t("alert.panel_data")
    return t("alert.panel_unavailable")


async def _panel_error(
    message: Message,
    error: PanelError,
    *,
    not_found_text: str | None = None,
) -> None:
    logger.warning("Panel request failed (%s)", type(error).__name__)
    await message.answer(
        not_found_text or _panel_error_text(error),
        reply_markup=back_keyboard(),
    )


async def _home_view(
    first_name: str,
    telegram_id: int,
    panel: RemnawaveClient,
    subscription_page_base: str | None = None,
    prefs: PreferencesStore | None = None,
) -> tuple[str, str | None, bool, bool]:
    page_url = None
    user: PanelUser | None = None
    panel_available = False
    try:
        user = await panel.get_user_by_telegram_id(telegram_id)
        if user.telegram_id != telegram_id:
            user = None
            raise PanelNotFound("No Panel user is linked to this Telegram account")
        panel_available = True
        if user.status == "ACTIVE":
            try:
                page_url = RemnawaveClient.subscription_page_url(
                    user, subscription_page_base
                )
            except PanelDataError:
                logger.warning("Subscription page URL could not be derived")
        rows = [t("cabinet.status_row", status=status_text(user))]
        tariff = tariff_text(user)
        if tariff is not None:
            rows.append(t("cabinet.tariff_row", tariff=tariff))
        rows.append(t("cabinet.expires_row", expires=_expiry_text(user)))
        summary = (
            f"<blockquote>┌ <b>{t('home.access')}</b>\n"
            + "\n".join(
                f"{'└' if index == len(rows) - 1 else '├'} {row}"
                for index, row in enumerate(rows)
            )
            + "</blockquote>"
        )
    except PanelNotFound:
        panel_available = True
        summary = (
            f"<blockquote>┌ <b>{t('home.access')}</b>\n"
            f"└ {t('cabinet.status_row', status=t('status.none'))}</blockquote>"
        )
    except PanelError as error:
        logger.warning("Panel request failed (%s)", type(error).__name__)
        summary = (
            f"<blockquote>┌ <b>{t('home.access')}</b>\n"
            f"└ {t('cabinet.status_row', status=t('status.no_connection'))}</blockquote>"
        )
    has_access = user is not None and user.status == "ACTIVE"
    trial_available = False
    if panel_available and user is None and prefs is not None:
        try:
            trial_available = not prefs.trial_was_used(telegram_id)
        except PreferencesError:
            logger.warning("Trial usage state could not be read")
    return (
        home_text(
            first_name,
            summary,
            has_access=has_access,
            trial_available=trial_available,
        ),
        page_url,
        has_access,
        trial_available,
    )


async def _home_text(
    first_name: str, telegram_id: int, panel: RemnawaveClient
) -> str:
    text, _page_url, _has_access, _trial_available = await _home_view(
        first_name, telegram_id, panel
    )
    return text


async def _edit_or_send(
    callback: CallbackQuery,
    text: str,
    markup,
) -> None:
    if callback.message is None:
        return
    await edit_screen(
        callback.message,
        text,
        markup,
        data=callback.data,
    )


def _setup_step(prefs: PreferencesStore | None, telegram_id: int) -> int:
    if prefs is None:
        return 3
    try:
        return prefs.setup_tutorial_step(telegram_id)
    except PreferencesError:
        logger.warning("Setup tutorial progress could not be read")
        return 3


def _record_navigation(
    prefs: PreferencesStore | None, telegram_id: int, screen: str, *, reset: bool = False
) -> None:
    if prefs is None:
        return
    try:
        if reset:
            prefs.reset_navigation(telegram_id, screen)
        else:
            prefs.push_navigation(telegram_id, screen)
    except (PreferencesError, ValueError):
        logger.warning("User navigation history could not be updated")


async def _trial_eligible(
    telegram_id: int,
    panel: RemnawaveClient,
    prefs: PreferencesStore | None,
) -> bool:
    if prefs is None:
        return False
    try:
        if await _load_user(telegram_id, panel) is not None:
            return False
        return not prefs.trial_was_used(telegram_id)
    except (PanelError, PreferencesError):
        logger.warning("Trial eligibility could not be confirmed")
        return False


async def _load_user(
    telegram_id: int, panel: RemnawaveClient
) -> PanelUser | None:
    try:
        user = await panel.get_user_by_telegram_id(telegram_id)
    except PanelNotFound:
        return None
    if user.telegram_id != telegram_id:
        return None
    return user


async def _free_access_available(
    telegram_id: int,
    panel: RemnawaveClient,
    settings: Settings,
) -> bool:
    if not settings.free_access_enabled:
        return False
    try:
        user = await _load_user(telegram_id, panel)
    except PanelError as error:
        logger.warning("FREE eligibility check failed (%s)", type(error).__name__)
        return False
    if user is None:
        return settings.panel_internal_squad_uuid is not None
    return user.status == "EXPIRED" and user.tag != "FREE"


async def _grant_user_free_access(
    callback: CallbackQuery,
    panel: RemnawaveClient,
    settings: Settings,
    prefs: PreferencesStore | None,
) -> None:
    telegram_id = callback.from_user.id
    if not settings.free_access_enabled or prefs is None:
        await callback.answer(t("plans.free_failed"), show_alert=True)
        return

    async with _free_access_lock:
        try:
            user = await _load_user(telegram_id, panel)
        except PanelError as error:
            logger.warning("FREE account lookup failed (%s)", type(error).__name__)
            await callback.answer(_panel_error_text(error), show_alert=True)
            return

        if user is not None and user.tag == "FREE":
            await callback.answer(
                t("plans.free_already_active")
                if user.status in {"ACTIVE", "LIMITED"}
                else t("plans.free_blocked"),
                show_alert=True,
            )
            return
        if user is not None and user.status != "EXPIRED":
            await callback.answer(t("plans.free_blocked"), show_alert=True)
            return

        try:
            if user is None:
                if settings.panel_internal_squad_uuid is None:
                    await callback.answer(t("plans.free_failed"), show_alert=True)
                    return
                user = await panel.create_free_user(
                    telegram_id, settings.panel_internal_squad_uuid
                )
            else:
                user = await panel.apply_free_access(user)
        except (PanelError, ValueError) as error:
            logger.warning("FREE access grant failed (%s)", type(error).__name__)
            await callback.answer(t("plans.free_failed"), show_alert=True)
            return

        try:
            prefs.initialize_free_daily(
                telegram_id,
                datetime.now(UTC).date().isoformat(),
                user.traffic_lifetime_used_bytes,
            )
        except (PreferencesError, sqlite3.Error):
            logger.warning("FREE daily baseline could not be initialized")

    page_url = _page_url(user, settings)
    await callback.answer()
    await _edit_or_send(
        callback,
        t("plans.free_success", quota=format_bytes(FREE_TRAFFIC_BYTES)),
        subscription_keyboard(
            page_url,
            has_access=user.status == "ACTIVE",
            support=support_url(settings.support_contact),
        ),
    )


async def _subscription_view(
    telegram_id: int,
    panel: RemnawaveClient,
    subscription_page_base: str | None = None,
    *,
    support: str | None = None,
) -> tuple[str, str | None]:
    try:
        user = await _load_user(telegram_id, panel)
        page_url = None
        if user is not None and user.status == "ACTIVE":
            page_url = RemnawaveClient.subscription_page_url(
                user, subscription_page_base
            )
    except PanelNotFound:
        return no_subscription_text(), None
    except PanelError as error:
        logger.warning("Panel request failed (%s)", type(error).__name__)
        return _panel_error_text(error), None
    return subscription_text(user, page_url=page_url, support=support), page_url


def _has_access(user: PanelUser | None) -> bool:
    return user is not None and user.status == "ACTIVE"


async def _devices(
    panel: RemnawaveClient, user: PanelUser
) -> tuple[list | None, str | None]:
    try:
        return await panel.get_user_devices(user), None
    except PanelError as error:
        logger.warning("Panel device request failed (%s)", type(error).__name__)
        return None, _panel_error_text(error)
    except ValueError:
        return None, t("alert.panel_bad_devices")


async def _render_home(
    callback: CallbackQuery,
    panel: RemnawaveClient,
    settings: Settings,
    prefs: PreferencesStore | None,
) -> None:
    text, page_url, has_access, trial_available = await _home_view(
        callback.from_user.first_name or t("common.friend"),
        callback.from_user.id,
        panel,
        settings.subscription_page_url,
        prefs,
    )
    onboarding = _setup_step(prefs, callback.from_user.id) < 3
    if onboarding:
        text += "\n\n" + t("setup.intro")
    await _edit_or_send(
        callback,
        text,
        home_keyboard(
            callback.from_user.id in settings.admin_telegram_ids,
            page_url,
            has_access=has_access,
            trial_available=trial_available,
            onboarding=onboarding,
        ),
    )


async def _render_setup(
    callback: CallbackQuery,
    panel: RemnawaveClient,
    settings: Settings,
    prefs: PreferencesStore | None,
) -> None:
    if prefs is None:
        await _edit_or_send(
            callback, t("setup.storage_unavailable"), back_keyboard()
        )
        return
    try:
        step = prefs.setup_tutorial_cursor(callback.from_user.id)
        step = min(max(step, 0), 3)
        user = await _load_user(callback.from_user.id, panel)
        trial_available = (
            user is None and not prefs.trial_was_used(callback.from_user.id)
        )
        if step == 0 and _has_access(user):
            prefs.set_setup_tutorial_step(callback.from_user.id, 1)
            step = 1
    except PreferencesError:
        logger.warning("Setup tutorial state could not be loaded")
        await _edit_or_send(
            callback, t("setup.storage_unavailable"), back_keyboard()
        )
        return
    except PanelError as error:
        logger.warning("Panel request failed (%s)", type(error).__name__)
        await _edit_or_send(callback, _panel_error_text(error), back_keyboard())
        return

    display_step = step if step == 0 or _has_access(user) else 0
    title_key = (
        "setup.access_title",
        "setup.install_title",
        "setup.add_title",
        "setup.connect_title",
    )[display_step]
    note_key = (
        "setup.access_note",
        "setup.install_note",
        "setup.add_note",
        "setup.connect_note",
    )[display_step]
    page_url = _page_url(user, settings)
    text = f"{t(title_key)}\n\n{t(note_key)}"
    if step > 0 and not _has_access(user):
        text += "\n\n" + t("setup.access_required")
    await _edit_or_send(
        callback,
        text,
        setup_keyboard(
            display_step,
            trial_available=trial_available,
            page_url=page_url,
        ),
    )


async def _render_cabinet(
    callback: CallbackQuery,
    panel: RemnawaveClient,
    settings: Settings,
    prefs: PreferencesStore | None = None,
    wallet: WalletStore | None = None,
) -> None:
    support = support_url(settings.support_contact)
    try:
        user = await _load_user(callback.from_user.id, panel)
    except PanelError as error:
        logger.warning("Panel request failed (%s)", type(error).__name__)
        await _edit_or_send(
            callback,
            cabinet_text(
                None,
                telegram_id=callback.from_user.id,
                balance_minor=(
                    wallet.account(callback.from_user.id).balance_minor
                    if wallet is not None
                    else None
                ),
                support=settings.support_contact,
                panel_error=True,
            ),
            cabinet_keyboard(),
        )
        return
    devices_count = None
    page_url = None
    if user is not None:
        page_url = _page_url(user, settings)
        try:
            devices_count = len(await panel.get_user_devices(user))
        except (PanelError, ValueError) as error:
            logger.warning("Panel device request failed (%s)", type(error).__name__)
    await _edit_or_send(
        callback,
        cabinet_text(
            user,
            telegram_id=callback.from_user.id,
            balance_minor=(
                wallet.account(callback.from_user.id).balance_minor
                if wallet is not None
                else None
            ),
            devices_count=devices_count,
            support=settings.support_contact,
            trial_available=(
                user is None
                and await _trial_eligible(callback.from_user.id, panel, prefs)
            ),
        ),
        cabinet_keyboard(page_url),
    )


async def _render_connect(
    callback: CallbackQuery,
    panel: RemnawaveClient,
    settings: Settings,
    prefs: PreferencesStore | None = None,
) -> None:
    try:
        user = await _load_user(callback.from_user.id, panel)
    except PanelError as error:
        logger.warning("Panel request failed (%s)", type(error).__name__)
        await _edit_or_send(
            callback,
            connect_text(
                None,
                None,
                support=settings.support_contact,
                panel_error=True,
            ),
            connect_keyboard(support=support_url(settings.support_contact)),
        )
        return
    page_url = _page_url(user, settings)
    trial_available = (
        user is None
        and await _trial_eligible(callback.from_user.id, panel, prefs)
    )
    await _edit_or_send(
        callback,
        connect_text(
            user,
            page_url,
            support=settings.support_contact,
            trial_available=trial_available,
        ),
        connect_keyboard(
            page_url,
            has_access=_has_access(user),
            support=support_url(settings.support_contact),
            trial_available=trial_available,
        ),
    )


async def _render_devices(
    callback: CallbackQuery,
    panel: RemnawaveClient,
    settings: Settings,
    page: int = 0,
) -> None:
    try:
        user = await _load_user(callback.from_user.id, panel)
    except PanelError as error:
        logger.warning("Panel request failed (%s)", type(error).__name__)
        await _edit_or_send(
            callback,
            devices_text(user=None, devices=None, error=_panel_error_text(error)),
            devices_keyboard([]),
        )
        return
    if user is None:
        await _edit_or_send(
            callback,
            devices_text(None, None),
            devices_keyboard([]),
        )
        return
    devices, error = await _devices(panel, user)
    await _edit_or_send(
        callback,
        devices_text(user, devices, error=error, page=page),
        devices_keyboard(devices or [], page),
    )


async def _render_stats(
    callback: CallbackQuery, panel: RemnawaveClient
) -> None:
    try:
        user = await _load_user(callback.from_user.id, panel)
    except PanelError as error:
        logger.warning("Panel request failed (%s)", type(error).__name__)
        await _edit_or_send(callback, stats_text(None, panel_error=True), stats_keyboard())
        return
    devices_count = None
    if user is not None:
        try:
            devices_count = len(await panel.get_user_devices(user))
        except (PanelError, ValueError) as error:
            logger.warning("Panel device request failed (%s)", type(error).__name__)
    await _edit_or_send(
        callback,
        stats_text(user, devices_count=devices_count),
        stats_keyboard(),
    )


async def _render_settings(
    callback: CallbackQuery,
    panel: RemnawaveClient,
    settings: Settings,
    prefs: PreferencesStore | None,
) -> None:
    if prefs is None:
        await _edit_or_send(
            callback,
            t("settings.storage_unavailable"),
            back_keyboard("user:cabinet"),
        )
        return
    try:
        enabled = prefs.reminders_enabled(callback.from_user.id)
        timezone_offset = prefs.timezone_offset(
            callback.from_user.id, DEFAULT_UTC_OFFSET
        )
    except PreferencesError:
        logger.warning("Preferences database is unavailable")
        await _edit_or_send(
            callback,
            t("settings.storage_temporary"),
            back_keyboard("user:cabinet"),
        )
        return
    try:
        user = await _load_user(callback.from_user.id, panel)
    except PanelError as error:
        logger.warning("Panel request failed (%s)", type(error).__name__)
        user = None
    await _edit_or_send(
        callback,
        settings_text(
            telegram_id=callback.from_user.id,
            reminders_enabled=enabled,
            user=user,
            support=settings.support_contact,
            timezone_offset=timezone_offset,
        ),
        settings_keyboard(enabled),
    )


async def _user_screen(
    callback: CallbackQuery,
    bot: Bot,
    panel: RemnawaveClient,
    screen: str,
    settings: Settings,
    plans: tuple[Plan, ...],
    prefs: PreferencesStore | None = None,
    wallet: WalletStore | None = None,
) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer(t("alert.private_chat"), show_alert=True)
        return
    if screen == "home":
        await callback.answer()
        await _render_home(callback, panel, settings, prefs)
    elif screen == "setup":
        await callback.answer()
        await _render_setup(callback, panel, settings, prefs)
    elif screen == "more":
        await callback.answer()
        await _edit_or_send(callback, more_text(), more_keyboard())
    elif screen == "about":
        privacy_url, agreement_url = (
            _document_urls(prefs) if prefs is not None else (None, None)
        )
        await callback.answer()
        await _edit_or_send(
            callback,
            about_text(
                plans,
                traffic_plans_enabled=settings.traffic_plans_enabled,
                free_access_enabled=settings.free_access_enabled,
                documents_configured=(
                    privacy_url is not None and agreement_url is not None
                ),
            ),
            about_keyboard(privacy_url, agreement_url),
        )
    elif screen in {"cabinet", "subscription"}:
        await callback.answer()
        await _render_cabinet(callback, panel, settings, prefs, wallet)
    elif screen == "connect":
        await callback.answer()
        await _render_connect(callback, panel, settings, prefs)
    elif screen == "subscription":
        await callback.answer()
        try:
            user = await _load_user(callback.from_user.id, panel)
        except PanelError as error:
            logger.warning("Panel request failed (%s)", type(error).__name__)
            await _edit_or_send(
                callback,
                _panel_error_text(error),
                back_keyboard(),
            )
            return
        page_url = _page_url(user, settings)
        devices_count = None
        if user is not None:
            try:
                devices_count = len(await panel.get_user_devices(user))
            except (PanelError, ValueError) as error:
                logger.warning(
                    "Panel device request failed (%s)", type(error).__name__
                )
        await _edit_or_send(
            callback,
            subscription_text(
                user,
                page_url=page_url,
                support=settings.support_contact,
                devices_count=devices_count,
            ),
            subscription_keyboard(
                page_url,
                has_access=_has_access(user),
                support=support_url(settings.support_contact),
                trial_available=(
                    user is None
                    and await _trial_eligible(
                        callback.from_user.id, panel, prefs
                    )
                ),
            ),
        )
    elif screen == "devices" or screen.startswith("devices:"):
        await callback.answer()
        raw_page = screen.partition(":")[2]
        page = _device_index(raw_page) if raw_page else 0
        if page is None:
            await callback.answer(t("alert.stale_button_short"), show_alert=True)
            return
        await _render_devices(callback, panel, settings, page)
    elif screen == "stats":
        await callback.answer()
        await _render_stats(callback, panel)
    elif screen == "referrals":
        if prefs is None or wallet is None:
            await callback.answer(t("referral.unavailable"), show_alert=True)
            return
        try:
            summary = wallet.referral_summary(callback.from_user.id)
            bot_user = await bot.get_me()
        except (WalletError, TelegramAPIError):
            logger.warning("Referral statistics could not be loaded")
            await callback.answer(t("referral.unavailable"), show_alert=True)
            return
        if not bot_user.username:
            await callback.answer(t("referral.bot_username_missing"), show_alert=True)
            return
        invite_url = (
            f"https://t.me/{bot_user.username}?start=ref_{callback.from_user.id}"
        )
        stats = {
            "invited": summary.invited,
            "activated": summary.activated,
            "paying": summary.paying,
            "purchases": summary.purchases,
            "earned_minor": summary.earned_minor,
            "available_minor": summary.available_minor,
        }
        await callback.answer()
        await _edit_or_send(
            callback,
            referral_text(stats, invite_url),
            referral_keyboard(
                referral_share_url(invite_url),
                withdraw_available=(
                    summary.available_minor >= MIN_WITHDRAWAL_MINOR
                    and summary.pending_withdrawal_id is None
                ),
            ),
        )
    elif screen == "wallet":
        if wallet is None:
            await callback.answer(t("wallet.unavailable"), show_alert=True)
            return
        try:
            account = wallet.account(callback.from_user.id)
            payments = wallet.payment_history(callback.from_user.id)
            pending = wallet.pending_withdrawal(callback.from_user.id)
        except WalletError:
            logger.warning("Wallet screen could not be loaded")
            await callback.answer(t("wallet.unavailable"), show_alert=True)
            return
        await callback.answer()
        await _edit_or_send(
            callback,
            wallet_text(payments, pending),
            wallet_keyboard(
                withdraw_available=(
                    pending is None
                    and account.referral_available_minor
                    >= MIN_WITHDRAWAL_MINOR
                ),
                withdrawal_pending=pending is not None,
            ),
        )
    elif screen == "topup":
        if wallet is None:
            await callback.answer(t("wallet.unavailable"), show_alert=True)
            return
        stars_for: dict[int, int] = {}
        for amount in TOPUP_PRESET_AMOUNTS:
            try:
                stars_for[amount] = stars_for_rub_amount(amount, plans)
            except ValueError:
                stars_for[amount] = 0
        await callback.answer()
        await _edit_or_send(
            callback, topup_text(plans), topup_amount_keyboard(stars_for)
        )
    elif screen == "settings":
        await callback.answer()
        await _render_settings(callback, panel, settings, prefs)
    elif screen == "timezone":
        if prefs is None:
            await callback.answer(t("settings.timezone_unavailable"), show_alert=True)
            return
        try:
            offset = prefs.timezone_offset(
                callback.from_user.id, DEFAULT_UTC_OFFSET
            )
        except PreferencesError:
            logger.warning("User timezone read failed")
            await callback.answer(t("settings.timezone_unavailable"), show_alert=True)
            return
        await callback.answer()
        await _edit_or_send(
            callback,
            timezone_text(offset),
            timezone_keyboard(offset),
        )
    elif screen == "languages" or screen.startswith("languages:"):
        page = 0
        if screen.startswith("languages:"):
            try:
                page = int(screen.removeprefix("languages:"))
            except ValueError:
                await callback.answer(t("alert.stale_button_short"), show_alert=True)
                return
        current = current_language()
        pages = language_page_count(current)
        if not 0 <= page < pages:
            await callback.answer(t("alert.stale_button_short"), show_alert=True)
            return
        await callback.answer()
        await _edit_or_send(
            callback,
            language_text(current, page, pages),
            language_keyboard(current, page),
        )
    elif screen.startswith("set:lang:"):
        language = screen.partition("set:lang:")[2]
        if language not in SUPPORTED_LANGUAGES:
            await callback.answer(t("alert.language_unavailable"), show_alert=True)
            return
        if prefs is None:
            await callback.answer(t("alert.language_failed"), show_alert=True)
            return
        try:
            prefs.set_language(callback.from_user.id, language)
        except (PreferencesError, ValueError):
            logger.warning("Preferences language write failed")
            await callback.answer(t("alert.language_failed"), show_alert=True)
            return
        set_language(language)
        await callback.answer(t("alert.language_saved"))
        await _render_settings(callback, panel, settings, prefs)
    elif screen.startswith("set:tz:"):
        raw_offset = screen.removeprefix("set:tz:")
        try:
            offset = int(raw_offset)
        except ValueError:
            await callback.answer(t("alert.stale_button_short"), show_alert=True)
            return
        if prefs is None:
            await callback.answer(t("settings.timezone_unavailable"), show_alert=True)
            return
        try:
            prefs.set_timezone_offset(callback.from_user.id, offset)
            prefs.pop_navigation(callback.from_user.id)
        except (PreferencesError, ValueError):
            logger.warning("User timezone write failed")
            await callback.answer(t("settings.timezone_failed"), show_alert=True)
            return
        set_utc_offset(offset)
        await callback.answer(
            t("settings.timezone_saved", offset=utc_label(offset))
        )
        await _render_settings(callback, panel, settings, prefs)
    elif screen == "plans":
        trial_available = await _trial_eligible(
            callback.from_user.id, panel, prefs
        )
        free_available = await _free_access_available(
            callback.from_user.id, panel, settings
        )
        await callback.answer()
        await _edit_or_send(
            callback,
            plans_text(
                plans,
                settings.trial_days,
                trial_available=trial_available,
                traffic_plans_enabled=settings.traffic_plans_enabled,
                free_for_admin=callback.from_user.id in settings.admin_telegram_ids,
                free_available=free_available,
            ),
            plans_keyboard(
                plans,
                trial_available=trial_available,
                traffic_plans_enabled=settings.traffic_plans_enabled,
                free_for_admin=callback.from_user.id in settings.admin_telegram_ids,
                free_available=free_available,
            ),
        )
    elif screen.startswith("plans:"):
        parts = screen.split(":")
        category = parts[1] if len(parts) > 1 else ""
        traffic_family: str | None = None
        if category == "free" and len(parts) == 3 and parts[2] == "confirm":
            await _grant_user_free_access(callback, panel, settings, prefs)
            return
        if (
            category == "traffic"
            and len(parts) == 4
            and parts[2] == "family"
        ):
            traffic_family = parts[3]
        elif len(parts) != 2:
            await callback.answer(t("alert.stale_button"), show_alert=True)
            return
        if category not in {"time", "traffic", "free"}:
            await callback.answer(t("alert.stale_button"), show_alert=True)
            return
        if traffic_family is not None and traffic_family not in {
            "NO_RESET", "DAY", "WEEK", "MONTH", "MONTH_ROLLING"
        }:
            await callback.answer(t("alert.stale_button"), show_alert=True)
            return
        trial_available = await _trial_eligible(
            callback.from_user.id, panel, prefs
        )
        free_available = await _free_access_available(
            callback.from_user.id, panel, settings
        )
        await callback.answer()
        await _edit_or_send(
            callback,
            plans_text(
                plans,
                settings.trial_days,
                trial_available=trial_available,
                traffic_plans_enabled=settings.traffic_plans_enabled,
                category=category,
                traffic_family=traffic_family,
                free_for_admin=callback.from_user.id in settings.admin_telegram_ids,
                free_available=free_available,
            ),
            plans_keyboard(
                plans,
                trial_available=trial_available,
                traffic_plans_enabled=settings.traffic_plans_enabled,
                category=category,
                traffic_family=traffic_family,
                free_for_admin=callback.from_user.id in settings.admin_telegram_ids,
                free_available=free_available,
            ),
        )
    elif screen == "trial":
        await callback.answer()
        await _show_trial(callback, panel, settings, prefs)
    elif screen == "guides":
        await callback.answer()
        try:
            user = await _load_user(callback.from_user.id, panel)
        except PanelError as error:
            logger.warning("Panel request failed (%s)", type(error).__name__)
            user = None
        page_url = _page_url(user, settings)
        await _edit_or_send(
            callback,
            guide_text(page_url=page_url),
            guide_platform_keyboard(
                page_url=page_url,
            ),
        )
    elif screen.startswith("guide:"):
        platform = screen.partition(":")[2]
        await callback.answer()
        if platform in PLATFORM_APPS:
            await _edit_or_send(
                callback, guide_text(platform), guide_apps_keyboard(platform)
            )
        else:
            await callback.answer(t("alert.stale_button_short"), show_alert=True)
    elif screen.startswith("g:"):
        await _render_guide_step(callback, panel, settings, screen)
    elif screen == "help":
        await callback.answer()
        await _edit_or_send(
            callback,
            help_text(settings.support_contact),
            help_keyboard(support_url(settings.support_contact)),
        )
    elif screen == "troubleshoot":
        await callback.answer()
        await _edit_or_send(
            callback,
            troubleshooting_text(),
            help_keyboard(support_url(settings.support_contact)),
        )
    elif screen.startswith("platform:"):
        platform = screen.partition(":")[2]
        await callback.answer()
        if platform == "desktop":
            await _edit_or_send(callback, guide_text(), guide_platform_keyboard())
        elif platform in PLATFORM_APPS:
            await _edit_or_send(
                callback, guide_text(platform), guide_apps_keyboard(platform)
            )
        else:
            await callback.answer(t("alert.stale_button_short"), show_alert=True)
    elif screen == "profile":
        await callback.answer()
        await _render_cabinet(callback, panel, settings)
    else:
        await callback.answer(t("alert.stale_button"), show_alert=True)


async def _show_trial(
    callback: CallbackQuery,
    panel: RemnawaveClient,
    settings: Settings,
    prefs: PreferencesStore | None = None,
) -> None:
    try:
        user = await _load_user(callback.from_user.id, panel)
    except PanelError as error:
        logger.warning("Panel request failed (%s)", type(error).__name__)
        await _edit_or_send(callback, _panel_error_text(error), back_keyboard())
        return
    if prefs is None:
        await _edit_or_send(callback, t("trial.unavailable_generic"), back_keyboard())
        return
    try:
        if prefs.trial_was_used(callback.from_user.id):
            await _edit_or_send(
                callback,
                t("trial.unavailable_generic"),
                back_keyboard(),
            )
            return
    except PreferencesError:
        logger.warning("Trial usage state could not be read")
        await _edit_or_send(
            callback,
            t("trial.unavailable_generic"),
            back_keyboard(),
        )
        return
    if user is not None:
        await _edit_or_send(callback, t("trial.unavailable_generic"), back_keyboard())
        return
    await _edit_or_send(
        callback,
        trial_text(settings.trial_days, settings.trial_channel_url),
        trial_keyboard(settings.trial_channel_url),
    )


async def _render_guide_step(
    callback: CallbackQuery,
    panel: RemnawaveClient,
    settings: Settings,
    screen: str,
) -> None:
    parts = screen.split(":")
    if len(parts) != 4:
        await callback.answer(t("alert.stale_button_short"), show_alert=True)
        return
    _, platform, app, raw_step = parts
    if platform not in PLATFORM_APPS or app not in PLATFORM_APPS[platform]:
        await callback.answer(t("alert.stale_button_short"), show_alert=True)
        return
    try:
        step = int(raw_step)
    except ValueError:
        await callback.answer(t("alert.stale_button_short"), show_alert=True)
        return
    if step < 0 or step > 4:
        await callback.answer(t("alert.stale_button_short"), show_alert=True)
        return
    await callback.answer()
    page_url = None
    user = None
    if step == 1:
        try:
            user = await _load_user(callback.from_user.id, panel)
            page_url = _page_url(user, settings)
        except PanelError as error:
            logger.warning("Panel request failed (%s)", type(error).__name__)
    await _edit_or_send(
        callback,
        guide_step_text(platform, app, step, page_url=page_url),
        guide_step_keyboard(
            platform,
            app,
            step,
            page_url=page_url,
        ),
    )


async def _notify_referrer(bot: Bot, telegram_id: int, text: str) -> bool:
    try:
        await bot.send_message(chat_id=telegram_id, text=text)
        return True
    except TelegramAPIError:
        logger.warning("Referral notification delivery failed")
        return False


async def _show_campaign_connection(
    target: Message | CallbackQuery,
    panel: RemnawaveClient,
    settings: Settings,
    prefs: PreferencesStore,
) -> bool:
    user = target.from_user
    if user is None:
        return False
    try:
        pending = prefs.pending_campaign_click(user.id)
    except PreferencesError:
        logger.warning("Campaign connection choice could not be loaded")
        return False
    if pending is None:
        return False
    click_id, protocol = pending
    try:
        panel_user = await _load_user(user.id, panel)
    except PanelError as error:
        logger.warning("Panel request failed (%s)", type(error).__name__)
        panel_user = None
        panel_error = True
    else:
        panel_error = False
    page_url = _page_url(panel_user, settings)
    trial_available = (
        panel_user is None
        and not panel_error
        and await _trial_eligible(user.id, panel, prefs)
    )
    _record_navigation(prefs, user.id, "connect")
    text = connect_text(
        panel_user,
        page_url,
        support=settings.support_contact,
        panel_error=panel_error,
        trial_available=trial_available,
    )
    label = PROTOCOL_LABELS.get(protocol)
    if label is not None:
        if panel_error:
            text = (
                f"<b>Подключение: {label}</b>\n\n"
                "Не получилось проверить подписку. Попробуйте ещё раз позже."
            )
        elif _has_access(panel_user):
            text = (
                f"<b>Вы выбрали {label}</b>\n\n"
                "Нажмите «🌐 Открыть страницу подписки» и добавьте подписку "
                f"в приложение. Затем выберите сервер {label}."
            )
        else:
            text = (
                f"<b>Вы выбрали {label}</b>\n\n"
                "Чтобы подключиться, сначала получите доступ. Затем добавьте "
                "подписку в приложение и выберите сервер "
                f"{label}."
            )
    markup = connect_keyboard(
        page_url,
        has_access=_has_access(panel_user),
        support=support_url(settings.support_contact),
        trial_available=trial_available,
    )
    if isinstance(target, CallbackQuery):
        await _edit_or_send(target, text, markup)
    else:
        await answer_screen(target, text, markup, banner="connect")
    try:
        prefs.finish_campaign_click(click_id)
    except PreferencesError:
        logger.warning("Campaign connection choice could not be marked complete")
    return True


async def _promo_reply(
    target: Message | CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if isinstance(target, Message):
        await target.answer(text, reply_markup=reply_markup)
    elif isinstance(target.message, Message):
        await target.message.answer(text, reply_markup=reply_markup)
    else:
        await target.answer(text, show_alert=True)


async def _process_pending_promo_start(
    target: Message | CallbackQuery,
    bot: Bot,
    panel: RemnawaveClient,
    settings: Settings,
    plans: tuple[Plan, ...] | None,
    prefs: PreferencesStore,
    orders: OrderStore | None,
    promotions: PromotionStore | None,
) -> bool:
    user = target.from_user
    if user is None or promotions is None:
        return False
    try:
        post_id = promotions.pending_start_request(user.id)
    except PromotionStoreError:
        logger.exception("Pending promotion request could not be loaded")
        await _promo_reply(target, t("promo.claim_unavailable"))
        return True
    if post_id is None:
        return False
    reserved = False
    try:
        panel_user = await _load_user(user.id, panel)
        trial_used = prefs.trial_was_used(user.id)
        if orders is None:
            raise OrderError("Purchase history storage is unavailable")
        had_purchase = orders.user_has_subscription_purchase(user.id)
        category = (
            "returning"
            if panel_user is not None or trial_used or had_purchase
            else "new"
        )
        outcome, previous_category = promotions.reserve_reward(
            post_id, user.id, category
        )
        if outcome == "inactive":
            promotions.clear_start_request(user.id)
            await _promo_reply(target, t("promo.claim_inactive"))
            return True
        if outcome == "full":
            promotions.clear_start_request(user.id)
            await _promo_reply(target, t("promo.claim_full"))
            return True
        if outcome == "applied":
            promotions.clear_start_request(user.id)
            await _promo_reply(target, t("promo.claim_duplicate"))
            return True
        if outcome in {"review", "reserved"} and previous_category is not None:
            promotions.clear_start_request(user.id)
            await _promo_reply(
                target,
                t(
                    "promo.claim_processing"
                    if outcome == "reserved"
                    else "promo.claim_status"
                ),
            )
            return True
        if outcome != "claimed":
            promotions.clear_start_request(user.id)
            await _promo_reply(target, t("promo.claim_unavailable"))
            return True
        reserved = True
        if plans is None:
            raise RuntimeError("Plan catalog is unavailable")
        unlimited = next(
            (
                plan
                for plan in plans
                if plan.code == "unlimited-7"
                and plan.traffic_limit_gb is None
                and plan.panel_tag is not None
            ),
            None,
        )
        if unlimited is None or settings.panel_internal_squad_uuid is None:
            raise RuntimeError("Unlimited plan or Panel squad is unavailable")
        days = PROMO_NEW_DAYS if category == "new" else PROMO_RETURNING_DAYS
        permanent_tags = {
            plan.panel_tag for plan in plans if plan.permanent_traffic
        }
        if panel_user is None:
            updated = await panel.create_user(
                user.id,
                days,
                settings.panel_internal_squad_uuid,
                tag=unlimited.panel_tag,
            )
        elif panel_user.status not in {"ACTIVE", "EXPIRED", "LIMITED"}:
            raise PanelAccessDenied("Existing subscription cannot be extended")
        elif panel_user.tag == "FREE":
            updated = await panel.apply_paid_plan(
                panel_user,
                days,
                None,
                tag=unlimited.panel_tag,
                traffic_limit_strategy="NO_RESET",
            )
        elif panel_user.tag in permanent_tags:
            raise PanelAccessDenied(
                "Permanent subscription has no expiration to extend"
            )
        else:
            updated = await panel.extend_user(panel_user, days)
        if not promotions.finish_reward(user.id, post_id, applied=True):
            raise PromotionStoreError("Promotion reward state could not be committed")
        reserved = False
        promotions.clear_start_request(user.id)
    except (PanelError, PreferencesError, OrderError, PromotionStoreError, RuntimeError, ValueError) as error:
        logger.exception("Promotion reward could not be safely completed")
        if reserved:
            try:
                promotions.finish_reward(
                    user.id,
                    post_id,
                    applied=False,
                    error_code=type(error).__name__,
                )
            except PromotionStoreError:
                logger.exception("Promotion reward review state could not be saved")
            try:
                promotions.clear_start_request(user.id)
            except PromotionStoreError:
                logger.exception("Promotion start request could not be cleared")
            try:
                await bot.send_message(
                    _announcement_owner_id(settings),
                    t(
                        "promo.claim_review_admin",
                        user_id=user.id,
                        post_id=post_id,
                        error=type(error).__name__,
                    ),
                )
            except TelegramAPIError:
                logger.warning("Promotion review notification could not be delivered")
            await _promo_reply(target, t("promo.claim_status"))
        else:
            await _promo_reply(target, t("promo.claim_unavailable"))
        return True
    try:
        promotions.clear_start_request(user.id)
        await _promo_reply(
            target,
            t(
                "promo.claim_new"
                if category == "new"
                else "promo.claim_returning"
            ),
            connect_keyboard(
                _page_url(updated, settings),
                has_access=_has_access(updated),
                support=support_url(settings.support_contact),
                trial_available=False,
            ),
        )
    except (PromotionStoreError, TelegramAPIError):
        logger.exception("Promotion reward confirmation could not be delivered")
    try:
        await bot.send_message(
            _announcement_owner_id(settings),
            (
                "Промоподарок выдан\n"
                f"Пост: #{post_id}\nКатегория: {category}\n"
                f"Пользователь: {user.id}\nДней: {days}"
            ),
        )
    except TelegramAPIError:
        logger.warning("Promotion reward notification could not be delivered")
    return True


def _start_text(first_name: str, body: str) -> str:
    return t("start.welcome", name=escape(first_name)) + "\n\n" + body


def _start_keyboard(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    # Decorate a copy: the same keyboards are used on other screens.
    markup = markup.model_copy(deep=True)
    for row_index, row in enumerate(markup.inline_keyboard):
        for button in row:
            action = button.callback_data or ""
            button.style = "primary"
            # Unicode text symbols remain visible without owner Premium.
            button.icon_custom_emoji_id = None
            icon = "≡"
            if action.startswith("docs:accept:") or (
                row_index == 0 and not action.startswith("docs:")
            ):
                button.style = "success"
                icon = "✓" if action.startswith("docs:accept:") else "ϟ"
            elif action in {"user:help", "user:about", "ticket:open"}:
                icon = "?" if action != "user:about" else "ⓘ"
            elif action == "user:cabinet":
                icon = "⌂"
            elif action == "admin:home":
                button.style = "danger"
                icon = "⚙\ufe0e"
            elif button.url:
                icon = "↗"
            if button.text and not any(char.isalnum() for char in button.text.split()[0]):
                button.text = button.text.partition(" ")[2] or button.text
            button.text = f"{icon} {button.text}"
    return markup


async def _answer_start(
    message: Message,
    text: str,
    markup: InlineKeyboardMarkup,
    *,
    prefs: PreferencesStore,
    banner: str,
) -> None:
    animation = Path(__file__).with_name("assets") / "banners" / "start-welcome.gif"
    if animation.is_file() and len(text) <= 1024:
        # A new asset must never reuse the Telegram ID of an earlier GIF.
        cache_key = f"start-animation-v3:{sha256(animation.read_bytes()).hexdigest()}"
        cached = None
        try:
            cached = prefs.banner_file_id(cache_key)
        except PreferencesError:
            logger.warning("Start animation cache is unavailable")
        sources = [cached, FSInputFile(animation)] if cached else [FSInputFile(animation)]
        for source in sources:
            try:
                sent = await message.answer_animation(
                    animation=source,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=markup,
                    disable_notification=True,
                )
            except TelegramAPIError:
                logger.warning("Start animation delivery failed; trying fallback")
                continue
            if sent.animation is not None:
                try:
                    prefs.set_banner_file_id(cache_key, sent.animation.file_id)
                except PreferencesError:
                    logger.warning("Start animation cache could not be saved")
            return
    await answer_screen(message, text, markup, prefs=prefs, banner=banner)


@router.message(CommandStart())
async def start(
    message: Message,
    bot: Bot,
    panel: RemnawaveClient,
    settings: Settings,
    state: FSMContext | None = None,
    prefs: PreferencesStore | None = None,
    wallet: WalletStore | None = None,
    tickets: TicketStore | None = None,
    orders: OrderStore | None = None,
    promotions: PromotionStore | None = None,
    plans: tuple[Plan, ...] | None = None,
) -> None:
    await _clear_state(state)
    if not _private_chat(message):
        await message.answer(t("alert.private_start"))
        return
    assert message.from_user is not None
    if prefs is None:
        await message.answer(t("docs.accept_failed"))
        return
    referral_registered = False
    referrer_id: int | None = None
    campaign_protocol: str | None = None
    channel_post_click: tuple[int, int] | None = None
    promo_post_id: int | None = None
    try:
        prefs.reset_navigation(message.from_user.id)
        command_parts = (message.text or "").split(maxsplit=1)
        if len(command_parts) == 2:
            start_parameter = command_parts[1].strip()
            campaign_protocol = CAMPAIGN_START_PROTOCOLS.get(start_parameter)
            post_click = re.fullmatch(
                r"cp_([1-9][0-9]{0,18})_([0-7])", start_parameter
            )
            if post_click:
                channel_post_click = (
                    int(post_click.group(1)),
                    int(post_click.group(2)),
                )
            promo_match = re.fullmatch(
                rf"{re.escape(PROMO_START_PREFIX)}([1-9][0-9]{{0,18}})",
                start_parameter,
            )
            if promo_match is not None:
                promo_post_id = int(promo_match.group(1))
        if len(command_parts) == 2 and re.fullmatch(
            r"ref_[1-9][0-9]{0,18}", command_parts[1].strip()
        ):
            candidate = int(command_parts[1][4:])
            if candidate <= 9_223_372_036_854_775_807:
                referrer_id = candidate
        referral_registered = prefs.register_start(
            message.from_user.id, referrer_id
        )
        prefs.remember(
            message.from_user.id,
            message.from_user.first_name,
            message.from_user.last_name,
            message.from_user.username,
        )
        if campaign_protocol is not None:
            prefs.log_campaign_click(
                ANNOUNCEMENT_CAMPAIGN,
                campaign_protocol,
                message.from_user.id,
                message.from_user.first_name or "",
                message.from_user.last_name,
                message.from_user.username,
            )
    except (PreferencesError, ValueError):
        logger.warning("Preferences database is unavailable")
        campaign_protocol = None
    if referral_registered and referrer_id is not None:
        await _notify_referrer(bot, referrer_id, t("referral.new_visit"))
    if campaign_protocol is not None:
        person = (
            f"@{message.from_user.username}"
            if message.from_user.username
            else message.from_user.full_name
        )
        try:
            await bot.send_message(
                _announcement_owner_id(settings),
                (
                    "Переход по кнопке подключения из канала\n"
                    f"Вариант: {PROTOCOL_LABELS[campaign_protocol]}\n"
                    f"Пользователь: {person}\n"
                    f"Telegram ID: {message.from_user.id}"
                ),
            )
        except TelegramAPIError:
            logger.warning("Protocol button click notification could not be delivered")
    try:
        consented = prefs.documents_accepted(
            message.from_user.id, prefs.documents_version()
        )
    except (PreferencesError, ValueError):
        logger.warning("Document consent state could not be loaded")
        await message.answer(t("docs.accept_failed"))
        return
    privacy_url, agreement_url = _document_urls(prefs)
    if promo_post_id is not None:
        if promotions is None:
            await message.answer(t("promo.claim_unavailable"))
            return
        try:
            if not promotions.store_start_request(
                message.from_user.id, promo_post_id
            ):
                await message.answer(t("promo.claim_inactive"))
                return
        except PromotionStoreError:
            logger.exception("Promotion button click could not be recorded")
            await message.answer(t("promo.claim_unavailable"))
            return
    if (
        not consented
        and privacy_url is not None
        and agreement_url is not None
    ):
        await _answer_start(
            message,
            _start_text(
                message.from_user.first_name or t("common.friend"),
                _documents_consent_text(prefs),
            ),
            _start_keyboard(_documents_consent_keyboard(prefs)),
            prefs=prefs,
            banner="support",
        )
        return
    if await _process_pending_promo_start(
        message,
        bot,
        panel,
        settings,
        plans,
        prefs,
        orders,
        promotions,
    ):
        return
    if await _show_campaign_connection(message, panel, settings, prefs):
        return
    if channel_post_click is not None and tickets is not None:
        post_id, button_index = channel_post_click
        try:
            button = tickets.record_channel_post_click(
                post_id,
                button_index,
                message.from_user.id,
                message.from_user.full_name,
            )
        except TicketStoreError:
            logger.exception("Channel post button click could not be recorded")
            button = None
        if button is not None:
            await message.answer(
                f"Кнопка: {button['text']}\nНажмите ниже, чтобы открыть ссылку.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=f"Открыть: {button['text']}",
                                url=button["url"],
                                style="success",
                            )
                        ]
                    ]
                ),
            )
            try:
                await bot.send_message(
                    _announcement_owner_id(settings),
                    (
                        "Переход по кнопке поста\n"
                        f"Пост: #{post_id}\n"
                        f"Кнопка: {button['text']}\n"
                        f"Пользователь: {message.from_user.full_name}\n"
                        f"Telegram ID: {message.from_user.id}"
                    ),
                )
            except TelegramAPIError:
                logger.warning("Channel post click notification could not be delivered")
            return
    text, page_url, has_access, trial_available = await _home_view(
        message.from_user.first_name or t("common.friend"),
        message.from_user.id,
        panel,
        settings.subscription_page_url,
        prefs,
    )
    onboarding = _setup_step(prefs, message.from_user.id) < 3
    if onboarding:
        text += "\n\n" + t("setup.intro")
    text = _start_text(
        message.from_user.first_name or t("common.friend"),
        text.removeprefix(
            t(
                "home.greeting",
                name=escape(message.from_user.first_name or t("common.friend")),
            )
            + "\n\n"
        ),
    )
    await _answer_start(
        message,
        text,
        _start_keyboard(
            home_keyboard(
                _is_admin(message, settings),
                page_url,
                has_access=has_access,
                trial_available=trial_available,
                onboarding=onboarding,
            )
        ),
        prefs=prefs,
        banner="home",
    )


@router.callback_query(F.data.startswith("docs:"))
async def documents_navigation(
    callback: CallbackQuery,
    bot: Bot,
    panel: RemnawaveClient,
    settings: Settings,
    prefs: PreferencesStore | None = None,
    orders: OrderStore | None = None,
    promotions: PromotionStore | None = None,
    plans: tuple[Plan, ...] | None = None,
) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer(t("alert.private_chat"), show_alert=True)
        return
    if prefs is None:
        await callback.answer(t("docs.accept_failed"), show_alert=True)
        return
    action = callback.data or ""
    if action.startswith("docs:missing:"):
        await callback.answer(t("docs.missing_link"), show_alert=True)
        return
    try:
        privacy_url, agreement_url = _document_urls(prefs)
        current_version = prefs.documents_version()
    except (PreferencesError, ValueError):
        logger.warning("Document settings could not be loaded")
        await callback.answer(t("docs.accept_failed"), show_alert=True)
        return

    if action.startswith("docs:accept:"):
        requested_version = action.removeprefix("docs:accept:")
        if requested_version != current_version:
            await callback.answer(t("alert.stale_button_short"), show_alert=True)
            return
        if privacy_url is None or agreement_url is None:
            await callback.answer(t("docs.unconfigured"), show_alert=True)
            return
        try:
            prefs.accept_documents(callback.from_user.id, current_version)
        except (PreferencesError, ValueError):
            logger.warning("Document consent could not be stored")
            await callback.answer(t("docs.accept_failed"), show_alert=True)
            return
        await callback.answer(t("docs.accepted", version=current_version))
        if await _process_pending_promo_start(
            callback,
            bot,
            panel,
            settings,
            plans,
            prefs,
            orders,
            promotions,
        ):
            return
        if await _show_campaign_connection(callback, panel, settings, prefs):
            return
        text, page_url, has_access, trial_available = await _home_view(
            callback.from_user.first_name or t("common.friend"),
            callback.from_user.id,
            panel,
            settings.subscription_page_url,
            prefs,
        )
        await _edit_or_send(
            callback,
            text,
            home_keyboard(
                callback.from_user.id in settings.admin_telegram_ids,
                page_url,
                has_access=has_access,
                trial_available=trial_available,
                onboarding=_setup_step(prefs, callback.from_user.id) < 3,
            ),
        )
        return
    await callback.answer(t("alert.stale_button_short"), show_alert=True)


@router.message(Command("help"))
async def help_command(
    message: Message,
    settings: Settings,
    state: FSMContext | None = None,
    prefs: PreferencesStore | None = None,
) -> None:
    await _clear_state(state)
    if not _private_chat(message):
        await message.answer(t("alert.private_help"))
        return
    assert message.from_user is not None
    _record_navigation(prefs, message.from_user.id, "help")
    await answer_screen(
        message,
        help_text(settings.support_contact),
        help_keyboard(support_url(settings.support_contact)),
        banner="support",
    )


@router.message(Command("status"))
async def status_command(
    message: Message,
    panel: RemnawaveClient,
    settings: Settings,
    state: FSMContext | None = None,
    prefs: PreferencesStore | None = None,
    wallet: WalletStore | None = None,
) -> None:
    await _clear_state(state)
    if not _private_chat(message):
        await message.answer(t("alert.private_status"))
        return
    assert message.from_user is not None
    _record_navigation(prefs, message.from_user.id, "cabinet")
    try:
        user = await _get_owned_user(message, panel)
    except PanelError as error:
        await _panel_error(message, error)
        return
    await answer_screen(
        message,
        cabinet_text(
            user,
            telegram_id=message.from_user.id,
            balance_minor=(
                wallet.account(message.from_user.id).balance_minor
                if wallet is not None
                else None
            ),
            support=settings.support_contact,
        ),
        cabinet_keyboard(_page_url(user, settings)),
        banner="cabinet",
    )


@router.message(Command("connect"))
async def connect_command(
    message: Message,
    panel: RemnawaveClient,
    settings: Settings,
    state: FSMContext | None = None,
    prefs: PreferencesStore | None = None,
) -> None:
    await _clear_state(state)
    if not _private_chat(message):
        await message.answer(t("alert.private_connect"))
        return
    assert message.from_user is not None
    _record_navigation(prefs, message.from_user.id, "connect")
    try:
        user = await _load_user(message.from_user.id, panel)
    except PanelError as error:
        logger.warning("Panel request failed (%s)", type(error).__name__)
        await answer_screen(
            message,
            connect_text(
                None, None, support=settings.support_contact, panel_error=True
            ),
            connect_keyboard(
                support=support_url(settings.support_contact)
            ),
            banner="connect",
        )
        return
    page_url = _page_url(user, settings)
    trial_available = (
        user is None
        and await _trial_eligible(message.from_user.id, panel, prefs)
    )
    await answer_screen(
        message,
        connect_text(
            user,
            page_url,
            support=settings.support_contact,
            trial_available=trial_available,
        ),
        connect_keyboard(
            page_url,
            has_access=_has_access(user),
            support=support_url(settings.support_contact),
            trial_available=trial_available,
        ),
        banner="connect",
    )


@router.callback_query(F.data.startswith("user:"))
async def user_navigation(
    callback: CallbackQuery,
    bot: Bot,
    panel: RemnawaveClient,
    settings: Settings,
    state: FSMContext,
    plans: tuple[Plan, ...],
    prefs: PreferencesStore | None = None,
    wallet: WalletStore | None = None,
) -> None:
    assert callback.data is not None
    action = callback.data.removeprefix("user:")
    if action == "key":
        action = "subscription"
    elif action == "profile":
        action = "cabinet"
    elif action == "subscription":
        action = "cabinet"
    screens = {
        "home",
        "setup",
        "more",
        "about",
        "cabinet",
        "connect",
        "subscription",
        "referrals",
        "wallet",
        "topup",
        "devices",
        "stats",
        "settings",
        "plans",
        "trial",
        "guides",
        "help",
        "troubleshoot",
        "timezone",
        "languages",
    }
    valid_action = (
        action == "back"
        or action in screens
        or (
            action.startswith("devices:")
            and _device_index(action.partition(":")[2]) is not None
        )
        or action.startswith(
            (
                "guide:",
                "g:",
                "platform:",
                "plans:",
                "set:tz:",
                "set:lang:",
                "set:rem:",
                "languages:",
                "dev:del:",
                "dev:rm:",
            )
        )
    )
    if not valid_action:
        await callback.answer(t("alert.stale_button"), show_alert=True)
        return
    if prefs is not None:
        try:
            prefs.remember(
                callback.from_user.id,
                callback.from_user.first_name,
                callback.from_user.last_name,
                callback.from_user.username,
            )
        except (PreferencesError, ValueError):
            logger.warning("Preferences database is unavailable")
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer(t("alert.private_chat"), show_alert=True)
        return
    if prefs is not None:
        try:
            if action == "back":
                action = prefs.pop_navigation(callback.from_user.id)
            elif action == "home":
                prefs.reset_navigation(callback.from_user.id)
            elif not action.startswith(("set:", "languages:")):
                navigation_screen = (
                    "devices"
                    if action.startswith("devices:")
                    else action
                )
                prefs.push_navigation(
                    callback.from_user.id, navigation_screen
                )
        except (PreferencesError, ValueError):
            logger.warning("User navigation history could not be updated")
            if action == "back":
                action = "home"

    if action.startswith(("dev:del:", "dev:rm:")):
        if action.startswith("dev:del:"):
            await _confirm_device_delete(callback, panel, action, state)
        else:
            await _delete_device(callback, panel, action, state)
        return
    if action.startswith("set:rem:"):
        await _toggle_reminders(callback, panel, settings, prefs, action)
        return
    if action.startswith("set:lang:"):
        await _user_screen(
            callback, bot, panel, action, settings, plans, prefs, wallet
        )
        return

    if action in screens or action.startswith(
        (
            "guide:",
            "g:",
            "platform:",
            "plans:",
            "set:tz:",
            "languages:",
        )
    ):
        await _user_screen(
            callback, bot, panel, action, settings, plans, prefs, wallet
        )
    else:
        await callback.answer(t("alert.stale_button"), show_alert=True)


@router.callback_query(F.data.in_({"setup:next", "setup:back", "setup:finish"}))
async def setup_navigation(
    callback: CallbackQuery,
    panel: RemnawaveClient,
    settings: Settings,
    prefs: PreferencesStore | None = None,
) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer(t("alert.private_chat"), show_alert=True)
        return
    if prefs is None:
        await callback.answer(t("setup.storage_unavailable"), show_alert=True)
        return
    try:
        step = prefs.setup_tutorial_cursor(callback.from_user.id)
        user = await _load_user(callback.from_user.id, panel)
        if not _has_access(user):
            await callback.answer(t("setup.access_required"), show_alert=True)
            await _render_setup(callback, panel, settings, prefs)
            return
        if callback.data == "setup:finish":
            prefs.set_setup_tutorial_step(callback.from_user.id, 3)
            prefs.set_setup_tutorial_cursor(callback.from_user.id, 3)
        elif callback.data == "setup:back":
            prefs.set_setup_tutorial_cursor(callback.from_user.id, max(0, step - 1))
        elif step < 3:
            prefs.set_setup_tutorial_step(callback.from_user.id, step + 1)
            prefs.set_setup_tutorial_cursor(callback.from_user.id, step + 1)
    except PreferencesError:
        logger.warning("Setup tutorial progress could not be updated")
        await callback.answer(t("setup.storage_unavailable"), show_alert=True)
        return
    except PanelError as error:
        logger.warning("Panel request failed (%s)", type(error).__name__)
        await callback.answer(_panel_error_text(error), show_alert=True)
        return
    await callback.answer()
    await _render_setup(callback, panel, settings, prefs)


def _ticket_message_content(message: Message) -> tuple[str, str | None]:
    body = (message.text or message.caption or "").strip()
    photo_file_id = message.photo[-1].file_id if message.photo else None
    if len(body) > 4000:
        raise ValueError("Ticket message is too long")
    if not body and photo_file_id is None:
        raise ValueError("Ticket message cannot be empty")
    return body, photo_file_id


async def _send_ticket_update(
    bot: Bot,
    recipient_id: int,
    header: str,
    body: str,
    photo_file_id: str | None,
    callback_data: str,
    portal_url: str | None = None,
) -> bool:
    rows = [
        [
            InlineKeyboardButton(
                text=t("ticket.view_button"),
                callback_data=callback_data,
            )
        ]
    ]
    if portal_url is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("ticket.portal_button"),
                    url=portal_url,
                )
            ]
        )
    markup = InlineKeyboardMarkup(
        inline_keyboard=rows
    )
    content = escape(body[:4000])
    try:
        if photo_file_id is not None:
            await bot.send_photo(
                chat_id=recipient_id,
                photo=photo_file_id,
                caption=header[:1024],
                reply_markup=markup,
                parse_mode="HTML",
            )
            if content:
                await bot.send_message(
                    chat_id=recipient_id,
                    text=content,
                    parse_mode="HTML",
                )
        elif len(header) + len(content) + 2 <= 4096:
            await bot.send_message(
                chat_id=recipient_id,
                text=header + ("\n\n" + content if content else ""),
                reply_markup=markup,
                parse_mode="HTML",
            )
        else:
            await bot.send_message(
                chat_id=recipient_id,
                text=header,
                reply_markup=markup,
                parse_mode="HTML",
            )
            await bot.send_message(
                chat_id=recipient_id,
                text=content,
                parse_mode="HTML",
            )
        return True
    except TelegramAPIError:
        logger.warning("Support ticket notification delivery failed")
        return False


async def _notify_ticket_admins(
    bot: Bot,
    settings: Settings,
    ticket: dict,
    body: str,
    photo_file_id: str | None,
    *,
    first_message: bool = False,
) -> bool:
    key = "ticket.new_admin" if first_message else "ticket.new_message_admin"
    header = t(
        key,
        ticket_id=ticket["ticket_id"],
        category=category_text(str(ticket["category"])),
        name=escape(str(ticket["user_name"] or t("common.unknown"))),
        user_id=ticket["user_id"],
    )
    delivered = True
    for admin_id in settings.admin_telegram_ids:
        delivered = (
            await _send_ticket_update(
                bot,
                admin_id,
                header,
                body,
                photo_file_id,
                f"ticket:admin:view:{ticket['ticket_id']}",
                (
                    f"{settings.ticket_portal_url}/admin/ticket/"
                    f"{ticket['ticket_id']}"
                    if settings.ticket_portal_url is not None
                    else None
                ),
            )
            and delivered
        )
    return delivered


async def ticket_close_prompt_loop(
    bot: Bot,
    tickets: TicketStore,
    prefs: PreferencesStore,
    *,
    interval_seconds: int = 300,
) -> None:
    while True:
        try:
            pending = tickets.pending_close_prompts()
        except TicketStoreError:
            logger.warning("Ticket follow-up scan failed")
            await asyncio.sleep(interval_seconds)
            continue
        for ticket in pending:
            ticket_id = int(ticket["ticket_id"])
            message_id = int(ticket["message_id"])
            try:
                reserved = tickets.mark_close_prompt_attempted(
                    ticket_id, message_id
                )
            except TicketStoreError:
                logger.warning("Ticket follow-up could not be reserved")
                continue
            if not reserved:
                continue
            language = DEFAULT_LANGUAGE
            try:
                language = prefs.language(
                    int(ticket["user_id"]), DEFAULT_LANGUAGE
                )
            except PreferencesError:
                logger.warning("Ticket follow-up language could not be loaded")
            try:
                language_token = set_language(language)
            except ValueError:
                logger.warning("Ticket follow-up language is invalid")
                language_token = set_language(DEFAULT_LANGUAGE)
            try:
                await bot.send_message(
                    int(ticket["user_id"]),
                    t("ticket.close_prompt", ticket_id=ticket_id),
                    reply_markup=ticket_close_prompt_keyboard(ticket_id),
                )
            except TelegramAPIError:
                logger.warning("Ticket follow-up delivery failed")
                continue
            finally:
                reset_language(language_token)
            try:
                tickets.mark_close_prompt_sent(ticket_id, message_id)
            except TicketStoreError:
                logger.warning("Ticket follow-up delivery could not be recorded")
        await asyncio.sleep(interval_seconds)


async def ticket_notification_loop(
    bot: Bot,
    tickets: TicketStore,
    settings: Settings,
    *,
    interval_seconds: int = 5,
) -> None:
    while True:
        try:
            pending = tickets.pending_notifications()
        except TicketStoreError:
            logger.warning("Support ticket notification scan failed")
            await asyncio.sleep(interval_seconds)
            continue
        for notification in pending:
            notification_id = int(notification["notification_id"])
            ticket_id = int(notification["ticket_id"])
            recipient_id = int(notification["recipient_id"])
            admin = recipient_id in settings.admin_telegram_ids
            callback_data = (
                f"ticket:admin:view:{ticket_id}"
                if admin
                else f"ticket:view:{ticket_id}"
            )
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t("ticket.view_button"),
                            callback_data=callback_data,
                        )
                    ],
                    *(
                        [
                            [
                                InlineKeyboardButton(
                                    text=t("ticket.portal_button"),
                                    url=(
                                        f"{settings.ticket_portal_url}/admin/ticket/"
                                        f"{ticket_id}"
                                    ),
                                )
                            ]
                        ]
                        if admin and settings.ticket_portal_url is not None
                        else []
                    )
                ]
            )
            delivered = False
            try:
                await bot.send_message(
                    recipient_id,
                    (
                        f"Новое сообщение в обращении #{ticket_id}."
                        if admin
                        else t("ticket.new_message_user", ticket_id=ticket_id)
                    ),
                    reply_markup=markup,
                )
                delivered = True
            except TelegramAPIError:
                logger.warning("Support ticket notification delivery failed")
            try:
                tickets.mark_notification_attempt(
                    notification_id, delivered=delivered
                )
            except TicketStoreError:
                logger.warning("Support ticket notification state could not be saved")
        await asyncio.sleep(interval_seconds)


async def _show_ticket(
    callback: CallbackQuery,
    bot: Bot,
    tickets: TicketStore,
    ticket_id: int,
    *,
    admin: bool,
    portal_enabled: bool = False,
    answer_callback: bool = True,
    history_page: int = 0,
) -> None:
    assert callback.message is not None
    try:
        ticket = tickets.get(ticket_id)
        message_count = (
            tickets.message_count(ticket_id) if ticket is not None else 0
        )
        pages = max(
            1,
            (message_count + TICKET_MESSAGES_PER_PAGE - 1)
            // TICKET_MESSAGES_PER_PAGE,
        )
        history_page = min(max(history_page, 0), pages - 1)
        messages = (
            tickets.messages(
                ticket_id,
                limit=TICKET_MESSAGES_PER_PAGE,
                offset=history_page * TICKET_MESSAGES_PER_PAGE,
            )
            if ticket is not None
            else []
        )
        has_admin_reply = (
            tickets.has_admin_reply(ticket_id) if ticket is not None else False
        )
    except TicketStoreError:
        logger.warning("Support ticket details could not be read")
        await callback.answer(t("ticket.unavailable"), show_alert=True)
        return
    if ticket is None or (
        not admin and int(ticket["user_id"]) != callback.from_user.id
    ):
        await callback.answer(t("ticket.not_found"), show_alert=True)
        return
    if answer_callback:
        await callback.answer()
    offset = ADMIN_UTC_OFFSET if admin else current_utc_offset()
    await _edit_or_send(
        callback,
        ticket_detail_text(
            ticket,
            messages,
            utc_offset=offset,
            admin=admin,
        ),
        ticket_detail_keyboard(
            ticket,
            admin=admin,
            has_admin_reply=has_admin_reply,
            portal_enabled=portal_enabled and admin,
            history_page=history_page,
            has_older_messages=history_page < pages - 1,
        ),
    )
    for item in messages:
        photo_file_id = item.get("photo_file_id")
        if not isinstance(photo_file_id, str) or not photo_file_id:
            continue
        role = t(
            "ticket.sender_admin"
            if item["sender_role"] == "admin"
            else "ticket.sender_user"
        )
        caption = (
            f"<b>{role}</b> · "
            f"{format_datetime(item['created_at'], offset)}"
        )
        try:
            await bot.send_photo(
                callback.message.chat.id,
                photo_file_id,
                caption=caption,
                parse_mode="HTML",
            )
        except TelegramAPIError:
            logger.warning("Support ticket attachment could not be displayed")


def _ticket_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("ticket.cancel_button"),
                    callback_data="ticket:cancel",
                )
            ]
        ]
    )


@router.callback_query(F.data.startswith("ticket:"))
async def ticket_navigation(
    callback: CallbackQuery,
    bot: Bot,
    settings: Settings,
    state: FSMContext,
    tickets: TicketStore,
) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer(t("alert.private_chat"), show_alert=True)
        return
    await state.clear()
    assert callback.data is not None
    parts = callback.data.split(":")
    is_admin_action = len(parts) > 1 and parts[1] == "admin"
    if is_admin_action and callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer(t("ticket.admin_required"), show_alert=True)
        return
    await state.clear()

    if callback.data == "ticket:open":
        await state.clear()
        await callback.answer()
        await _edit_or_send(
            callback,
            f"<b>{t('ticket.open_title')}</b>\n\n{t('ticket.enter_message')}",
            category_keyboard(),
        )
        return
    if callback.data == "ticket:cancel":
        await state.clear()
        await callback.answer()
        await _edit_or_send(
            callback,
            t("ticket.cancelled"),
            help_keyboard(),
        )
        return
    if len(parts) == 3 and parts[1:] and parts[1] == "category":
        category = parts[2]
        if category not in {"connection", "account", "other"}:
            await callback.answer(t("alert.stale_button_short"), show_alert=True)
            return
        await state.clear()
        await state.update_data(ticket_category=category)
        await state.set_state(TicketFlow.awaiting_new_message)
        await callback.answer()
        await _edit_or_send(
            callback,
            f"<b>{t('ticket.open_title')}</b>\n\n{t('ticket.enter_message')}",
            _ticket_cancel_keyboard(),
        )
        return
    if callback.data == "ticket:mine" or callback.data.startswith(
        "ticket:mine:"
    ):
        if len(parts) > 3:
            await callback.answer(t("alert.stale_button_short"), show_alert=True)
            return
        try:
            page = int(parts[2]) if len(parts) == 3 else 0
        except ValueError:
            await callback.answer(t("alert.stale_button_short"), show_alert=True)
            return
        if page < 0:
            await callback.answer(t("alert.stale_button_short"), show_alert=True)
            return
        await state.clear()
        try:
            total = tickets.user_ticket_count(callback.from_user.id)
            pages = max(1, (total + USER_TICKETS_PER_PAGE - 1) // USER_TICKETS_PER_PAGE)
            page = min(page, pages - 1)
            user_tickets = tickets.user_tickets(
                callback.from_user.id,
                limit=USER_TICKETS_PER_PAGE,
                offset=page * USER_TICKETS_PER_PAGE,
            )
        except TicketStoreError:
            logger.warning("Support ticket list could not be read")
            await callback.answer(t("ticket.unavailable"), show_alert=True)
            return
        await callback.answer()
        await _edit_or_send(
            callback,
            ticket_list_text(user_tickets)
            + "\n\n"
            + t("ticket.user_page", page=page + 1, pages=pages),
            ticket_list_keyboard(
                user_tickets,
                page=page,
                total=total,
            ),
        )
        return
    if callback.data == "ticket:admin:list" or callback.data.startswith(
        "ticket:admin:list:"
    ):
        if len(parts) > 4:
            await callback.answer(t("alert.stale_button_short"), show_alert=True)
            return
        try:
            page = int(parts[3]) if len(parts) == 4 else 0
        except ValueError:
            await callback.answer(t("alert.stale_button_short"), show_alert=True)
            return
        if page < 0:
            await callback.answer(t("alert.stale_button_short"), show_alert=True)
            return
        await state.clear()
        try:
            summary = tickets.summary()
            pages = max(
                1,
                (int(summary["open"]) + ADMIN_TICKETS_PER_PAGE - 1)
                // ADMIN_TICKETS_PER_PAGE,
            )
            page = min(page, pages - 1)
            open_tickets = tickets.open_tickets(
                limit=ADMIN_TICKETS_PER_PAGE,
                offset=page * ADMIN_TICKETS_PER_PAGE,
            )
        except TicketStoreError:
            logger.warning("Admin support ticket queue could not be read")
            await callback.answer(t("ticket.unavailable"), show_alert=True)
            return
        text = ticket_admin_summary_text(summary)
        text += "\n\n" + t("ticket.admin_page", page=page + 1, pages=pages)
        if open_tickets:
            text += "\n\n" + "\n".join(
                (
                    f"#{item['ticket_id']} · "
                    f"{escape(str(item['user_name'] or t('common.unknown')))} · "
                    f"{category_text(str(item['category']))}"
                )
                for item in open_tickets
            )
        else:
            text += f"\n\n{t('ticket.admin_empty')}"
        await callback.answer()
        await _edit_or_send(
            callback,
            text,
            ticket_list_keyboard(
                open_tickets,
                admin=True,
                page=page,
                total=int(summary["open"]),
            ),
        )
        return

    action = ":".join(parts[1:])
    if action.startswith("portal:"):
        if callback.from_user.id not in settings.admin_telegram_ids:
            await callback.answer(t("ticket.admin_required"), show_alert=True)
            return
        if settings.ticket_portal_url is None:
            await callback.answer(t("ticket.unavailable"), show_alert=True)
            return
        try:
            ticket_id = int(action.removeprefix("portal:"))
            ticket = tickets.get(ticket_id)
        except (ValueError, TicketStoreError):
            ticket = None
        if ticket is None:
            await callback.answer(t("ticket.not_found"), show_alert=True)
            return
        await callback.answer()
        await callback.message.answer(
            t("ticket.portal_button"),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t("ticket.portal_button"),
                            url=(
                                f"{settings.ticket_portal_url}/admin/ticket/"
                                f"{ticket_id}"
                            ),
                        )
                    ]
                ]
            ),
        )
        return
    if action.startswith("admin:view:") or action.startswith("view:"):
        is_admin_view = action.startswith("admin:view:")
        raw_id = action.removeprefix("admin:view:").removeprefix("view:")
        raw_id, _, raw_page = raw_id.partition(":")
        try:
            ticket_id = int(raw_id)
            history_page = int(raw_page) if raw_page else 0
            if history_page < 0:
                raise ValueError
        except ValueError:
            await callback.answer(t("ticket.not_found"), show_alert=True)
            return
        await _show_ticket(
            callback,
            bot,
            tickets,
            ticket_id,
            admin=is_admin_view,
            portal_enabled=settings.ticket_portal_url is not None,
            history_page=history_page,
        )
        return
    if action.startswith(("reply:", "admin:reply:")):
        admin = action.startswith("admin:reply:")
        raw_id = action.removeprefix("admin:reply:").removeprefix("reply:")
        try:
            ticket_id = int(raw_id)
            ticket = tickets.get(ticket_id)
        except (ValueError, TicketStoreError):
            ticket = None
        if (
            ticket is None
            or ticket["status"] != "open"
            or (not admin and int(ticket["user_id"]) != callback.from_user.id)
        ):
            await callback.answer(t("ticket.no_open"), show_alert=True)
            return
        await state.clear()
        await state.update_data(ticket_id=ticket_id)
        await state.set_state(
            TicketFlow.awaiting_admin_reply
            if admin
            else TicketFlow.awaiting_user_reply
        )
        await callback.answer()
        await _edit_or_send(
            callback,
            t("ticket.enter_reply"),
            _ticket_cancel_keyboard(),
        )
        return
    if action.startswith(("close:", "admin:close:")):
        admin = action.startswith("admin:close:")
        raw_id = action.removeprefix("admin:close:").removeprefix("close:")
        try:
            ticket_id = int(raw_id)
            ticket = tickets.get(ticket_id)
            if (
                ticket is None
                or (not admin and int(ticket["user_id"]) != callback.from_user.id)
                or not tickets.close(ticket_id, "admin" if admin else "user")
            ):
                await callback.answer(t("ticket.not_found"), show_alert=True)
                return
        except (ValueError, TicketStoreError):
            await callback.answer(t("ticket.unavailable"), show_alert=True)
            return
        await state.clear()
        try:
            ticket = tickets.get(ticket_id)
            if ticket is None:
                raise TicketStoreError("Closed ticket disappeared")
            messages = tickets.messages(ticket_id)
            has_admin_reply = tickets.has_admin_reply(ticket_id)
        except TicketStoreError:
            logger.warning("Closed support ticket details could not be read")
            await callback.answer(
                t("ticket.closed_details_unavailable"),
                show_alert=True,
            )
            return
        if admin:
            if has_admin_reply:
                try:
                    await bot.send_message(
                        int(ticket["user_id"]),
                        t("ticket.rating_request", ticket_id=ticket_id),
                        reply_markup=rating_keyboard(ticket_id),
                    )
                except TelegramAPIError:
                    logger.warning("Support ticket rating request delivery failed")
            else:
                await _send_ticket_update(
                    bot,
                    int(ticket["user_id"]),
                    t("ticket.closed_admin", ticket_id=ticket_id),
                    "",
                    None,
                    f"ticket:view:{ticket_id}",
                )
        else:
            for admin_id in settings.admin_telegram_ids:
                await _send_ticket_update(
                    bot,
                    admin_id,
                    t("ticket.closed_notice_admin", ticket_id=ticket_id),
                    "",
                    None,
                    f"ticket:admin:view:{ticket_id}",
                )
            if has_admin_reply:
                try:
                    await bot.send_message(
                        int(ticket["user_id"]),
                        t("ticket.rating_request", ticket_id=ticket_id),
                        reply_markup=rating_keyboard(ticket_id),
                    )
                except TelegramAPIError:
                    logger.warning("Support ticket rating request delivery failed")
        await callback.answer()
        await _show_ticket(
            callback,
            bot,
            tickets,
            ticket_id,
            admin=admin,
            portal_enabled=settings.ticket_portal_url is not None,
            answer_callback=False,
        )
        return
    if action.startswith("reopen:"):
        await callback.answer(t("ticket.no_open"), show_alert=True)
        return
    if action.startswith("rate_menu:"):
        try:
            ticket_id = int(action.removeprefix("rate_menu:"))
            ticket = tickets.get(ticket_id)
            has_admin_reply = tickets.has_admin_reply(ticket_id)
        except (ValueError, TicketStoreError):
            ticket = None
            has_admin_reply = False
        if (
            ticket is None
            or int(ticket["user_id"]) != callback.from_user.id
            or ticket["status"] != "closed"
            or not has_admin_reply
        ):
            await callback.answer(t("ticket.rating_not_available"), show_alert=True)
            return
        await callback.answer()
        await _edit_or_send(
            callback,
            t("ticket.rate_prompt"),
            rating_keyboard(ticket_id),
        )
        return
    if action.startswith("rate:"):
        try:
            _, raw_id, raw_rating = action.split(":")
            ticket_id = int(raw_id)
            rating = int(raw_rating)
            if not tickets.rate(ticket_id, callback.from_user.id, rating):
                await callback.answer(t("ticket.rating_not_available"), show_alert=True)
                return
        except (ValueError, TicketStoreError):
            await callback.answer(t("ticket.unavailable"), show_alert=True)
            return
        for admin_id in settings.admin_telegram_ids:
            try:
                await bot.send_message(
                    admin_id,
                    t("ticket.rating_admin", rating=rating, ticket_id=ticket_id),
                )
            except TelegramAPIError:
                logger.warning("Support ticket rating notification failed")
        await callback.answer(t("ticket.rating_thanks"))
        await _show_ticket(
            callback,
            bot,
            tickets,
            ticket_id,
            admin=False,
            portal_enabled=settings.ticket_portal_url is not None,
            answer_callback=False,
        )
        return
    await callback.answer(t("alert.stale_button"), show_alert=True)


async def _ticket_message_input(
    message: Message,
    bot: Bot,
    settings: Settings,
    state: FSMContext,
    tickets: TicketStore,
) -> None:
    if not _private_chat(message):
        await message.answer(t("alert.private_chat"))
        return
    assert message.from_user is not None
    if message.text and message.text.strip().casefold() == "/cancel":
        await state.clear()
        await message.answer(t("ticket.cancelled"), reply_markup=help_keyboard())
        return
    try:
        body, photo_file_id = _ticket_message_content(message)
    except ValueError as error:
        key = "ticket.message_too_long" if "long" in str(error) else "ticket.message_empty"
        await message.answer(t(key))
        return

    current_state = await state.get_state()
    data = await state.get_data()
    if current_state == TicketFlow.awaiting_new_message.state:
        category = str(data.get("ticket_category", ""))
        try:
            ticket_id = tickets.create(
                message.from_user.id,
                " ".join(
                    part
                    for part in (
                        message.from_user.first_name,
                        message.from_user.last_name,
                    )
                    if part
                ),
                category,
                body,
                photo_file_id,
            )
        except TicketRateLimited as error:
            await message.answer(
                t(
                    "ticket.rate_limited",
                    wait=(
                        f"{max(1, (error.retry_after_seconds + 3599) // 3600)} ч"
                        if error.retry_after_seconds >= 3600
                        else f"{max(1, (error.retry_after_seconds + 59) // 60)} мин."
                    ),
                )
            )
            return
        except (TicketStoreError, ValueError):
            logger.warning("Support ticket creation failed")
            await message.answer(t("ticket.unavailable"))
            return
        await state.clear()
        ticket = tickets.get(ticket_id)
        assert ticket is not None
        delivered = await _notify_ticket_admins(
            bot,
            settings,
            ticket,
            body,
            photo_file_id,
            first_message=True,
        )
        response = t("ticket.created", ticket_id=ticket_id)
        if not delivered:
            response += "\n\n" + t("ticket.admin_notify_failed")
        ticket_rows = [
            [
                InlineKeyboardButton(
                    text=t("ticket.view_button"),
                    callback_data=f"ticket:view:{ticket_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("ticket.my_button"),
                    callback_data="ticket:mine",
                )
            ],
        ]
        await message.answer(
            response,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=ticket_rows),
        )
        return

    try:
        ticket_id = int(data.get("ticket_id", 0))
        ticket = tickets.get(ticket_id)
        is_admin_reply = current_state == TicketFlow.awaiting_admin_reply.state
        if (
            ticket is None
            or ticket["status"] != "open"
            or (
                not is_admin_reply
                and int(ticket["user_id"]) != message.from_user.id
            )
            or (
                is_admin_reply
                and message.from_user.id not in settings.admin_telegram_ids
            )
            or not tickets.add_message(
                ticket_id,
                message.from_user.id,
                "admin" if is_admin_reply else "user",
                body,
                photo_file_id,
            )
        ):
            await state.clear()
            await message.answer(t("ticket.no_open"))
            return
    except TicketRateLimited as error:
        await message.answer(
            t(
                "ticket.rate_limited",
                wait=(
                    f"{max(1, (error.retry_after_seconds + 3599) // 3600)} ч"
                    if error.retry_after_seconds >= 3600
                    else f"{max(1, (error.retry_after_seconds + 59) // 60)} мин."
                ),
            )
        )
        return
    except (TicketStoreError, ValueError):
        logger.warning("Support ticket reply could not be stored")
        await message.answer(t("ticket.unavailable"))
        return
    await state.clear()
    ticket = tickets.get(ticket_id)
    assert ticket is not None
    if is_admin_reply:
        delivered = await _send_ticket_update(
            bot,
            int(ticket["user_id"]),
            t("ticket.new_message_user", ticket_id=ticket_id),
            body,
            photo_file_id,
            f"ticket:view:{ticket_id}",
        )
        response = t("ticket.reply_saved", ticket_id=ticket_id)
        if not delivered:
            response += "\n\n" + t("ticket.admin_reply_failed")
        await message.answer(response)
    else:
        delivered = await _notify_ticket_admins(
            bot,
            settings,
            ticket,
            body,
            photo_file_id,
        )
        response = t("ticket.reply_saved", ticket_id=ticket_id)
        if not delivered:
            response += "\n\n" + t("ticket.admin_notify_failed")
        await message.answer(
            response,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t("ticket.view_button"),
                            callback_data=f"ticket:view:{ticket_id}",
                        )
                    ]
                ]
            ),
        )


@router.message(TicketFlow.awaiting_new_message)
@router.message(TicketFlow.awaiting_user_reply)
@router.message(TicketFlow.awaiting_admin_reply)
async def ticket_message_input(
    message: Message,
    bot: Bot,
    settings: Settings,
    state: FSMContext,
    tickets: TicketStore,
) -> None:
    await _ticket_message_input(message, bot, settings, state, tickets)


def _device_index(raw: str) -> int | None:
    try:
        index = int(raw)
    except ValueError:
        return None
    return index if index >= 0 else None


async def _confirm_device_delete(
    callback: CallbackQuery,
    panel: RemnawaveClient,
    action: str,
    state: FSMContext,
) -> None:
    index = _device_index(action.removeprefix("dev:del:"))
    if index is None:
        await callback.answer(t("alert.stale_button_short"), show_alert=True)
        return
    try:
        user = await _load_user(callback.from_user.id, panel)
        if user is None:
            await callback.answer(t("alert.subscription_missing"), show_alert=True)
            return
        devices = await panel.get_user_devices(user)
    except PanelError as error:
        logger.warning("Panel device request failed (%s)", type(error).__name__)
        await callback.answer(_panel_error_text(error), show_alert=True)
        return
    if index >= len(devices):
        await callback.answer(
            t("alert.device_list_changed"), show_alert=True
        )
        return
    device = devices[index]
    await state.update_data(
        device_hwid=device.hwid,
        device_page=index // DEVICES_PER_PAGE,
    )
    await callback.answer()
    await _edit_or_send(
        callback,
        device_confirm_text(device),
        device_confirm_keyboard(index),
    )


async def _delete_device(
    callback: CallbackQuery,
    panel: RemnawaveClient,
    action: str,
    state: FSMContext,
) -> None:
    if _device_index(action.removeprefix("dev:rm:")) is None:
        await callback.answer(t("alert.stale_button_short"), show_alert=True)
        return
    data = await state.get_data()
    expected_hwid = data.get("device_hwid")
    raw_page = data.get("device_page", 0)
    page = raw_page if isinstance(raw_page, int) and raw_page >= 0 else 0
    if not isinstance(expected_hwid, str) or not expected_hwid:
        await callback.answer(
            t("alert.device_confirm_stale"),
            show_alert=True,
        )
        return
    async with _device_lock:
        now = time.monotonic()
        recent = [
            stamp
            for stamp in _device_delete_attempts.get(callback.from_user.id, [])
            if now - stamp < DEVICE_DELETE_WINDOW_SECONDS
        ]
        _device_delete_attempts[callback.from_user.id] = recent
        if len(recent) >= DEVICE_DELETE_LIMIT:
            await callback.answer(
                t("alert.device_delete_limited"),
                show_alert=True,
            )
            return
        try:
            user = await _load_user(callback.from_user.id, panel)
            if user is None:
                await callback.answer(t("alert.subscription_missing"), show_alert=True)
                return
            devices = await panel.get_user_devices(user)
            device = next(
                (item for item in devices if item.hwid == expected_hwid), None
            )
            if device is None:
                await callback.answer(
                    t("alert.device_gone"), show_alert=True
                )
                await state.clear()
                return
            remaining = await panel.delete_user_device(user, device.hwid)
        except PanelError as error:
            logger.warning("Panel device deletion failed (%s)", type(error).__name__)
            await callback.answer(_panel_error_text(error), show_alert=True)
            return
        recent.append(time.monotonic())
    await state.clear()
    await callback.answer(t("alert.device_deleted"))
    await _edit_or_send(
        callback,
        devices_text(user, remaining, page=page),
        devices_keyboard(remaining, page),
    )


async def _toggle_reminders(
    callback: CallbackQuery,
    panel: RemnawaveClient,
    settings: Settings,
    prefs: PreferencesStore | None,
    action: str,
) -> None:
    if prefs is None:
        await callback.answer(t("alert.settings_unavailable"), show_alert=True)
        return
    value = action.removeprefix("set:rem:")
    if value not in {"0", "1"}:
        await callback.answer(t("alert.stale_button_short"), show_alert=True)
        return
    try:
        prefs.set_reminders(callback.from_user.id, value == "1")
    except (PreferencesError, ValueError):
        logger.warning("Preferences write failed")
        await callback.answer(t("alert.settings_failed"), show_alert=True)
        return
    await callback.answer(t("alert.reminders_on") if value == "1" else t("alert.reminders_off"))
    await _render_settings(callback, panel, settings, prefs)


def _channel_membership(member) -> bool:
    status = getattr(member, "status", "")
    value = getattr(status, "value", status)
    if value in {"creator", "administrator", "member"}:
        return True
    return bool(getattr(member, "is_member", False))


async def _run_trial(
    callback: CallbackQuery,
    bot: Bot,
    panel: RemnawaveClient,
    settings: Settings,
    prefs: PreferencesStore | None = None,
) -> None:
    assert callback.from_user is not None
    telegram_id = callback.from_user.id
    if prefs is None:
        await _edit_or_send(callback, t("trial.unavailable_generic"), back_keyboard())
        return
    try:
        if prefs.trial_was_used(telegram_id):
            await _edit_or_send(callback, t("trial.unavailable_generic"), back_keyboard())
            return
    except PreferencesError:
        logger.warning("Trial usage state could not be read")
        await _edit_or_send(callback, t("trial.not_confirmed"), back_keyboard())
        return
    try:
        existing = await panel.get_user_by_telegram_id(telegram_id)
    except PanelNotFound:
        existing = None
    except PanelError as error:
        logger.warning("Panel trial lookup failed (%s)", type(error).__name__)
        await _edit_or_send(callback, _panel_error_text(error), back_keyboard())
        return
    if existing is not None:
        await _edit_or_send(callback, t("trial.unavailable_generic"), back_keyboard())
        return
    if settings.panel_internal_squad_uuid is None:
        await _edit_or_send(
            callback,
            t("alert.trial_not_configured"),
            back_keyboard(),
        )
        return

    try:
        member = await bot.get_chat_member(
            chat_id=f"@{settings.trial_channel_username}", user_id=telegram_id
        )
    except TelegramBadRequest:
        logger.warning("Trial channel membership check failed (TelegramBadRequest)")
        await _edit_or_send(
            callback,
            t("trial.channel_check_failed"),
            back_keyboard(),
        )
        return
    except TelegramAPIError:
        logger.warning("Trial channel membership check failed (TelegramAPIError)")
        await _edit_or_send(
            callback,
            t("trial.telegram_unavailable"),
            back_keyboard(),
        )
        return

    if not _channel_membership(member):
        await _edit_or_send(
            callback,
            f"<b>{t('trial.channel_missing_title')}</b>\n\n"
            f"{t('alert.trial_channel_missing_channel', channel=escape(channel_title()))}\n\n"
            f"{t('trial.channel_missing_note')}",
            trial_keyboard(settings.trial_channel_url),
        )
        return

    referrer_id = None
    if prefs is not None:
        try:
            referrer_id = prefs.referral_for(telegram_id)
        except PreferencesError:
            logger.warning("Referral attribution could not be read")

    async with _trial_lock:
        try:
            existing = await panel.get_user_by_telegram_id(telegram_id)
        except PanelNotFound:
            existing = None
        except PanelError as error:
            logger.warning("Panel trial lookup failed (%s)", type(error).__name__)
            await _edit_or_send(callback, _panel_error_text(error), back_keyboard())
            return
        if existing is not None:
            await _edit_or_send(callback, t("trial.unavailable_generic"), back_keyboard())
            return
        try:
            created = await panel.create_user(
                telegram_id,
                settings.trial_days,
                settings.panel_internal_squad_uuid,
                tag=TRIAL_TAG,
            )
        except PanelError as error:
            logger.warning("Panel trial grant failed (%s)", type(error).__name__)
            await _edit_or_send(
                callback,
                t("trial.not_confirmed"),
                back_keyboard(),
            )
            return

    if prefs is not None:
        try:
            prefs.mark_trial_used(telegram_id)
            if referrer_id is not None:
                prefs.mark_referral_activated(telegram_id)
        except PreferencesError:
            logger.warning("Trial usage state could not be saved")

    page_url = _page_url(created, settings)
    try:
        setup_step = prefs.setup_tutorial_step(telegram_id)
        if setup_step == 0:
            prefs.set_setup_tutorial_step(telegram_id, 1)
            setup_step = 1
    except PreferencesError:
        logger.warning("Setup tutorial progress could not be updated")
        setup_step = 3
    if setup_step < 3:
        setup_titles = (
            "setup.access_title",
            "setup.install_title",
            "setup.add_title",
            "setup.connect_title",
        )
        setup_notes = (
            "setup.access_note",
            "setup.install_note",
            "setup.add_note",
            "setup.connect_note",
        )
        setup_text = (
            f"\n\n{t(setup_titles[setup_step])}\n\n"
            f"{t(setup_notes[setup_step])}"
        )
        setup_markup = setup_keyboard(
            setup_step,
            trial_available=False,
            page_url=page_url,
        )
    else:
        setup_text = ""
        setup_markup = subscription_keyboard(
            page_url,
            has_access=True,
            support=support_url(settings.support_contact),
        )
    await _edit_or_send(
        callback,
        f"<b>{t('trial.activated')}</b>\n\n"
        f"<blockquote>┌ <b>{t('common.parameters')}</b>\n"
        f"├ {t('common.telegram_id')}: <code>{telegram_id}</code>\n"
        f"├ {t('common.tariff')}: {escape(tariff_text(created) or TRIAL_TAG)}\n"
        f"└ {t('common.expires')}: {_expiry_text(created)}</blockquote>\n\n"
        f"{t('trial.activated_note')}"
        f"{setup_text}",
        setup_markup,
    )


@router.callback_query(F.data == "trial:check")
async def trial_check(
    callback: CallbackQuery,
    bot: Bot,
    panel: RemnawaveClient,
    settings: Settings,
    state: FSMContext | None = None,
    prefs: PreferencesStore | None = None,
) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer(t("alert.private_chat"), show_alert=True)
        return
    await _clear_state(state)
    await callback.answer()
    await _run_trial(callback, bot, panel, settings, prefs)


router.include_router(admin_router)


async def user_section_command(
    message: Message,
    bot: Bot,
    panel: RemnawaveClient,
    settings: Settings,
    plans: tuple[Plan, ...],
    prefs: PreferencesStore | None = None,
    wallet: WalletStore | None = None,
    state: FSMContext | None = None,
) -> None:
    await _clear_state(state)
    if not _private_chat(message):
        await message.answer(t("alert.private_use_start"))
        return
    assert message.from_user is not None
    command = (message.text or "").split(maxsplit=1)[0].split("@", maxsplit=1)[0]
    screen = command.removeprefix("/").lower()
    if screen in {"account", "subscription"}:
        screen = "cabinet"
    if screen == "cooperation":
        screen = "referrals"
    _record_navigation(prefs, message.from_user.id, screen)
    try:
        user = await _load_user(message.from_user.id, panel)
    except PanelError as error:
        logger.warning("Panel request failed (%s)", type(error).__name__)
        await message.answer(_panel_error_text(error), reply_markup=back_keyboard())
        return

    if screen == "subscription":
        page_url = _page_url(user, settings)
        devices_count = None
        if user is not None:
            try:
                devices_count = len(await panel.get_user_devices(user))
            except (PanelError, ValueError) as error:
                logger.warning("Panel device request failed (%s)", type(error).__name__)
        await answer_screen(
            message,
            subscription_text(
                user,
                page_url=page_url,
                support=settings.support_contact,
                devices_count=devices_count,
            ),
            subscription_keyboard(
                page_url,
                has_access=_has_access(user),
                support=support_url(settings.support_contact),
                trial_available=(
                    user is None
                    and await _trial_eligible(
                        message.from_user.id, panel, prefs
                    )
                ),
            ),
            banner="subscription",
        )
    elif screen in {"cabinet", "devices", "stats"}:
        if screen == "devices":
            devices, error = (None, None)
            if user is not None:
                devices, error = await _devices(panel, user)
            await answer_screen(
                message,
                devices_text(user, devices, error=error),
                devices_keyboard(devices or []),
                banner="devices",
            )
        elif screen == "stats":
            devices_count = None
            if user is not None:
                try:
                    devices_count = len(await panel.get_user_devices(user))
                except (PanelError, ValueError) as error:
                    logger.warning("Panel device request failed (%s)", type(error).__name__)
            await answer_screen(
                message,
                stats_text(user, devices_count=devices_count),
                stats_keyboard(),
                banner="stats",
            )
        else:
            page_url = _page_url(user, settings)
            devices_count = None
            if user is not None:
                try:
                    devices_count = len(await panel.get_user_devices(user))
                except (PanelError, ValueError) as error:
                    logger.warning("Panel device request failed (%s)", type(error).__name__)
            await answer_screen(
                message,
                cabinet_text(
                    user,
                    telegram_id=message.from_user.id,
                    balance_minor=(
                        wallet.account(message.from_user.id).balance_minor
                        if wallet is not None
                        else None
                    ),
                    devices_count=devices_count,
                    support=settings.support_contact,
                    trial_available=(
                        user is None
                        and await _trial_eligible(
                            message.from_user.id, panel, prefs
                        )
                    ),
                ),
                cabinet_keyboard(page_url),
                banner="cabinet",
            )
    elif screen == "settings":
        if prefs is None:
            await message.answer(t("settings.storage_unavailable"))
            return
        try:
            enabled = prefs.reminders_enabled(message.from_user.id)
            timezone_offset = prefs.timezone_offset(
                message.from_user.id, DEFAULT_UTC_OFFSET
            )
        except PreferencesError:
            logger.warning("Preferences database is unavailable")
            await message.answer(t("settings.storage_temporary"))
            return
        await answer_screen(
            message,
            settings_text(
                telegram_id=message.from_user.id,
                reminders_enabled=enabled,
                user=user,
                support=settings.support_contact,
                timezone_offset=timezone_offset,
            ),
            settings_keyboard(enabled),
            banner="settings",
        )
    elif screen == "guides":
        page_url = _page_url(user, settings)
        await answer_screen(
            message,
            guide_text(page_url=page_url),
            guide_platform_keyboard(
                page_url=page_url,
            ),
            banner="guides",
        )
    elif screen == "plans":
        trial_available = await _trial_eligible(
            message.from_user.id, panel, prefs
        )
        await answer_screen(
            message,
            plans_text(
                plans,
                settings.trial_days,
                trial_available=trial_available,
                traffic_plans_enabled=settings.traffic_plans_enabled,
                free_for_admin=message.from_user.id in settings.admin_telegram_ids,
            ),
            plans_keyboard(
                plans,
                trial_available=trial_available,
                traffic_plans_enabled=settings.traffic_plans_enabled,
                free_for_admin=message.from_user.id in settings.admin_telegram_ids,
            ),
            banner="plans",
        )
    elif screen == "referrals":
        if prefs is None or wallet is None:
            await message.answer(t("referral.unavailable"))
            return
        try:
            summary = wallet.referral_summary(message.from_user.id)
            bot_user = await bot.get_me()
        except (PreferencesError, WalletError, TelegramAPIError):
            logger.warning("Referral statistics could not be loaded")
            await message.answer(t("referral.unavailable"))
            return
        if not bot_user.username:
            await message.answer(t("referral.bot_username_missing"))
            return
        invite_url = (
            f"https://t.me/{bot_user.username}?start=ref_{message.from_user.id}"
        )
        stats = {
            "invited": summary.invited,
            "activated": summary.activated,
            "paying": summary.paying,
            "purchases": summary.purchases,
            "earned_minor": summary.earned_minor,
            "available_minor": summary.available_minor,
        }
        await answer_screen(
            message,
            referral_text(stats, invite_url),
            referral_keyboard(
                referral_share_url(invite_url),
                withdraw_available=(
                    summary.available_minor >= MIN_WITHDRAWAL_MINOR
                    and summary.pending_withdrawal_id is None
                ),
            ),
            banner="stats",
        )


def _order_is_current(order) -> bool:
    try:
        created = datetime.fromisoformat(
            order.created_at.replace("Z", "+00:00")
        )
    except ValueError:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    age = datetime.now(UTC) - created.astimezone(UTC)
    return timedelta(0) <= age <= timedelta(minutes=30)


def _order_is_recent(order, seconds: int = 45) -> bool:
    try:
        created = datetime.fromisoformat(
            order.created_at.replace("Z", "+00:00")
        )
    except (AttributeError, ValueError):
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    age = datetime.now(UTC) - created.astimezone(UTC)
    return timedelta(0) <= age <= timedelta(seconds=seconds)


def _order_plan_term(plan: Plan, order) -> tuple[int, PlanTerm | None] | None:
    days = order.term_days or plan.days
    term = plan.term_for(days)
    if plan.sellable and term is None:
        return None
    expected_stars = term.stars_amount if term is not None else plan.price_minor
    expected_amount = (
        order.rub_price_minor
        if getattr(order, "provider", None) == "platega"
        else expected_stars
    )
    if order.amount_minor != expected_amount:
        return None
    if term is None:
        if order.rub_price_minor is not None:
            return None
    else:
        if (
            order.rub_price_minor is None
            or order.rub_price_minor <= 0
            or order.discount_rub_minor is None
            or order.discount_rub_minor < 0
            or order.discount_rub_minor > order.rub_price_minor
        ):
            return None
    return days, term


def _stars_plan(
    plans: tuple[Plan, ...],
    code: str,
    *,
    include_traffic: bool = False,
    sellable_only: bool = False,
) -> Plan | None:
    return next(
        (
            plan
            for plan in plans
            if plan.code == code
            and plan.currency == "XTR"
            and plan.price_minor is not None
            and (not sellable_only or plan.sellable)
            and (plan.traffic_limit_gb is None or include_traffic)
        ),
        None,
    )


def _plan_term_payment_text(
    plan: Plan, term: PlanTerm,
) -> str:
    return t(
        "plans.confirm_order_text",
        plan=escape(plan.title),
        days=term.days,
    )


def _admin_free_plan(
    plans: tuple[Plan, ...],
    code: str,
    *,
    include_traffic: bool,
) -> Plan | None:
    return next(
        (
            plan
            for plan in plans
            if plan.code == code
            and plan.sellable
            and (plan.traffic_limit_gb is None or include_traffic)
        ),
        None,
    )


async def _grant_admin_plan(
    callback: CallbackQuery,
    panel: RemnawaveClient,
    settings: Settings,
    plan: Plan,
    prefs: PreferencesStore | None,
    operation_key: str,
) -> None:
    if callback.message is None or callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer(t("admin.command_unavailable"), show_alert=True)
        return
    if prefs is None:
        await callback.answer(t("docs.accept_failed"), show_alert=True)
        return
    try:
        claimed = prefs.claim_admin_plan_operation(
            operation_key, callback.from_user.id, plan.code
        )
    except (PreferencesError, ValueError):
        logger.warning("Administrator plan operation could not be claimed")
        await callback.answer(t("plans.admin_free_unavailable"), show_alert=True)
        return
    if not claimed:
        await callback.answer(t("plans.admin_free_duplicate"), show_alert=True)
        return

    async def require_review() -> None:
        try:
            prefs.complete_admin_plan_operation(
                operation_key, review_required=True
            )
        except PreferencesError:
            logger.warning(
                "Administrator plan operation remains unrecorded"
            )

    async with _admin_plan_lock:
        try:
            user = await _load_user(callback.from_user.id, panel)
        except PanelError as error:
            await require_review()
            logger.warning(
                "Administrator plan lookup failed (%s)", type(error).__name__
            )
            await callback.answer(t("plans.purchase_blocked"), show_alert=True)
            return
        if user is None:
            if settings.panel_internal_squad_uuid is None:
                await require_review()
                await callback.answer(t("plans.purchase_blocked"), show_alert=True)
                return
            try:
                user = await panel.create_user(
                    callback.from_user.id,
                    plan.days,
                    settings.panel_internal_squad_uuid,
                    tag=plan.panel_tag,
                    traffic_limit_gb=plan.traffic_limit_gb,
                    permanent_traffic=plan.permanent_traffic,
                    traffic_limit_strategy=plan.traffic_limit_strategy,
                )
            except PanelError as error:
                await require_review()
                logger.warning(
                    "Administrator plan creation failed (%s)",
                    type(error).__name__,
                )
                await callback.answer(t("plans.purchase_blocked"), show_alert=True)
                return
        else:
            if user.status not in {"ACTIVE", "LIMITED"}:
                await require_review()
                await callback.answer(t("plans.purchase_blocked"), show_alert=True)
                return
            if user.status == "LIMITED" and not plan.permanent_traffic:
                await require_review()
                await callback.answer(t("plans.purchase_blocked"), show_alert=True)
                return
            try:
                user = await panel.apply_paid_plan(
                    user,
                    plan.days,
                    plan.traffic_limit_gb,
                    permanent_traffic=plan.permanent_traffic,
                    tag=plan.panel_tag,
                    traffic_limit_strategy=plan.traffic_limit_strategy,
                )
            except PanelError as error:
                await require_review()
                logger.warning(
                    "Administrator plan update failed (%s)", type(error).__name__
                )
                await callback.answer(t("plans.purchase_blocked"), show_alert=True)
                return
    try:
        prefs.complete_admin_plan_operation(operation_key)
    except PreferencesError:
        logger.error(
            "Administrator plan was applied but its idempotency state is not final"
        )
        await callback.answer(t("plans.admin_free_unrecorded"), show_alert=True)
        return
    if plan.permanent_traffic:
        details = t("plans.traffic_volume", gb=plan.traffic_limit_gb)
    else:
        details = t(
            "plans.duration", days=plan.days
        ) + " · " + t("common.expires") + ": " + escape(_expiry_text(user))
    await callback.answer()
    await _edit_or_send(
        callback,
        t(
            "plans.admin_free_success",
            plan=escape(plan.title),
            details=escape(details),
        ),
        back_keyboard("user:plans"),
    )


async def _notify_payment_admins(
    bot: Bot, settings: Settings, order_id: str
) -> None:
    for admin_id in settings.admin_telegram_ids:
        try:
            await bot.send_message(
                admin_id,
                t("plans.payment_issue_admin", order_id=escape(order_id)),
                parse_mode="HTML",
            )
        except TelegramAPIError:
            logger.warning("Payment review notification delivery failed")


async def payment_review_loop(
    bot: Bot,
    settings: Settings,
    orders: OrderStore,
    wallet: WalletStore | None = None,
    *,
    interval_seconds: int = 60,
) -> None:
    while True:
        try:
            pending = orders.pending_review_notifications()
        except (OrderError, sqlite3.Error):
            logger.warning("Paid order review scan failed")
            await asyncio.sleep(interval_seconds)
            continue
        for order in pending:
            delivered = True
            for admin_id in settings.admin_telegram_ids:
                try:
                    await bot.send_message(
                        admin_id,
                        t(
                            "plans.payment_review_admin",
                            order_id=escape(order.id),
                            telegram_id=order.telegram_id,
                            plan=escape(order.plan_code),
                            amount=order.amount_minor,
                        ),
                        parse_mode="HTML",
                    )
                except TelegramAPIError:
                    delivered = False
                    logger.warning("Paid order review notification delivery failed")
            if delivered:
                try:
                    orders.mark_review_notified(order.id)
                except (OrderError, sqlite3.Error):
                    logger.warning("Paid order review notice could not be recorded")
        if wallet is not None and settings.admin_telegram_ids:
            try:
                withdrawals = wallet.pending_withdrawal_notifications()
            except (WalletError, sqlite3.Error):
                logger.warning("Withdrawal review scan failed")
                withdrawals = []
            for request in withdrawals:
                delivered = True
                for admin_id in settings.admin_telegram_ids:
                    try:
                        await bot.send_message(
                            admin_id,
                            t(
                                "wallet.withdraw_admin",
                                request_id=escape(request.id),
                                telegram_id=request.telegram_id,
                                amount=format_rubles(request.amount_minor),
                                name=str(request.telegram_id),
                            ),
                            parse_mode="HTML",
                            reply_markup=withdrawal_action_keyboard(request.id),
                        )
                    except TelegramAPIError:
                        delivered = False
                        logger.warning(
                            "Withdrawal review notification delivery failed"
                        )
                if delivered:
                    try:
                        wallet.mark_withdrawal_notified(request.id)
                    except (WalletError, sqlite3.Error):
                        logger.warning(
                            "Withdrawal review notification state could not be saved"
                        )
        await asyncio.sleep(interval_seconds)


def _parse_rub_amount(raw: str) -> int | None:
    cleaned = (
        raw.strip()
        .replace(" ", "")
        .replace(" ", "")
        .replace(",", ".")
        .replace("₽", "")
        .casefold()
    )
    for suffix in ("рублей", "рубля", "рубль", "руб"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
            break
    if len(cleaned) > 32 or re.fullmatch(r"\d+(?:\.\d{1,2})?", cleaned) is None:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not value.is_finite() or value <= 0:
        return None
    minor = int((value * 100).to_integral_value(rounding=ROUND_HALF_UP))
    return minor if minor > 0 else None


async def _topup_callback_amount(
    callback: CallbackQuery,
    state: FSMContext,
    raw_amount: str | None,
) -> int | None:
    if raw_amount is not None:
        amount = (
            int(raw_amount)
            if raw_amount.isascii() and raw_amount.isdigit() and len(raw_amount) <= 10
            else None
        )
    else:
        amount = None
        if isinstance(callback.message, Message):
            match = re.search(
                r"Сумма пополнения:\s*([\d\s\u00a0]+(?:[.,]\d{1,2})?)",
                callback.message.text or callback.message.caption or "",
            )
            amount = None if match is None else _parse_rub_amount(match[1])
        if amount is None:
            amount = (await state.get_data()).get("topup_amount_minor")
    if isinstance(amount, bool) or not isinstance(amount, int):
        return None
    return amount if MIN_TOPUP_MINOR <= amount <= MAX_TOPUP_MINOR else None


async def _restore_topup_amounts(
    callback: CallbackQuery,
    state: FSMContext,
    plans: tuple[Plan, ...],
) -> None:
    await state.clear()
    await callback.answer(t("payments.select_amount"))
    await _edit_or_send(
        callback,
        topup_text(plans),
        topup_amount_keyboard(
            {
                amount: stars_for_rub_amount(amount, plans)
                for amount in TOPUP_PRESET_AMOUNTS
            }
        ),
    )


async def _payment_return_url(bot: Bot) -> str:
    username = (await bot.me()).username
    if not username:
        raise ValueError("Bot username is unavailable")
    return f"https://t.me/{username}"


async def _send_platega_link(
    callback: CallbackQuery,
    bot: Bot,
    payments: PaymentStore,
    payment_id: str,
    payment_url: str,
    kind: str,
    local_id: str,
    amount_minor: int,
) -> None:
    assert isinstance(callback.message, Message)
    payments.track(payment_id)
    text = t(
        "platega.payment_link",
        amount=format_rubles(amount_minor),
        payment_id=escape(payment_id),
    )
    markup = platega_payment_keyboard(payment_url, kind, local_id)
    screen = payments.screen(payment_id)
    if screen is not None and screen[0] == callback.from_user.id:
        try:
            await bot.edit_message_text(
                text,
                chat_id=screen[0],
                message_id=screen[1],
                reply_markup=markup,
                parse_mode="HTML",
            )
        except TelegramBadRequest as error:
            if "message is not modified" in error.message.casefold():
                payments.record_screen(payment_id, screen[0], screen[1])
                return
        else:
            payments.record_screen(payment_id, screen[0], screen[1])
            return
    sent = await callback.message.answer(
        text,
        reply_markup=markup,
        parse_mode="HTML",
    )
    payments.record_screen(payment_id, callback.from_user.id, sent.message_id)


async def _send_topup_invoice(
    message: Message,
    wallet: WalletStore,
    plans: tuple[Plan, ...],
    telegram_id: int,
    amount_minor: int,
) -> bool:
    try:
        stars = stars_for_rub_amount(amount_minor, plans)
        topup = wallet.create_topup(telegram_id, amount_minor, stars)
        await message.answer_invoice(
            title=t("wallet.topup_invoice_title"),
            description=t(
                "wallet.topup_invoice_description",
                amount=format_rubles(amount_minor),
            ),
            payload=f"jvtop_{topup.id}",
            provider_token="",
            currency="XTR",
            prices=[
                LabeledPrice(
                    label=t("wallet.topup_invoice_title"),
                    amount=topup.stars_amount,
                )
            ],
        )
    except (ValueError, WalletError, TelegramAPIError):
        logger.warning("Top-up invoice could not be created")
        return False
    return True


@router.callback_query(F.data.startswith("topup:"))
async def topup_navigation(
    callback: CallbackQuery,
    plans: tuple[Plan, ...],
    state: FSMContext,
    settings: Settings,
    bot: Bot,
    wallet: WalletStore | None = None,
    platega: PlategaClient | None = None,
    payments: PaymentStore | None = None,
) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer(t("alert.private_chat"), show_alert=True)
        return
    assert isinstance(callback.message, Message)
    if wallet is None:
        await callback.answer(t("wallet.unavailable"), show_alert=True)
        return
    action = (callback.data or "").removeprefix("topup:")
    if action == "cancel":
        await state.clear()
        await callback.answer()
        account = wallet.account(callback.from_user.id)
        payments = wallet.payment_history(callback.from_user.id)
        pending = wallet.pending_withdrawal(callback.from_user.id)
        await _edit_or_send(
            callback,
            wallet_text(payments, pending),
            wallet_keyboard(
                withdraw_available=(
                    pending is None
                    and account.referral_available_minor >= MIN_WITHDRAWAL_MINOR
                ),
                withdrawal_pending=pending is not None,
            ),
        )
        return
    if action == "pay:stars" or action.startswith("pay:stars:"):
        raw_amount = (
            None if action == "pay:stars" else action.removeprefix("pay:stars:")
        )
        amount_minor = await _topup_callback_amount(callback, state, raw_amount)
        if amount_minor is None:
            await _restore_topup_amounts(callback, state, plans)
            return
        await state.clear()
        await callback.answer()
        if not await _send_topup_invoice(
            callback.message, wallet, plans, callback.from_user.id, amount_minor
        ):
            await callback.message.answer(t("wallet.unavailable"))
        return
    if action.startswith("pay:platega:"):
        parts = action.split(":")
        method_value = parts[2] if len(parts) in {3, 4} else ""
        if (
            not settings.platega_enabled
            or platega is None
            or not method_value.isdigit()
            or int(method_value) not in PlategaClient.PAYMENT_METHODS
        ):
            await callback.answer(t("wallet.unavailable"), show_alert=True)
            return
        amount_minor = await _topup_callback_amount(
            callback, state, parts[3] if len(parts) == 4 else None
        )
        if amount_minor is None:
            await _restore_topup_amounts(callback, state, plans)
            return
        await state.clear()
        await callback.answer()
        payments = payments or PaymentStore(wallet.database_path)
        try:
            async with _platega_link_lock:
                topup = wallet.create_topup(
                    callback.from_user.id,
                    amount_minor,
                    stars_for_rub_amount(amount_minor, plans),
                    provider="platega",
                    payment_method=int(method_value),
                )
                if topup.provider_payment_id and topup.provider_payment_url:
                    payment_id, payment_url = (
                        topup.provider_payment_id,
                        topup.provider_payment_url,
                    )
                else:
                    return_url = await _payment_return_url(bot)
                    link = await platega.create_payment(
                        amount=Decimal(amount_minor) / 100,
                        currency="RUB",
                        payment_method=int(method_value),
                        description=f"Jopes VPN wallet top-up {topup.id}",
                        return_url=return_url,
                        failed_url=return_url,
                        order_id=topup.id,
                        user_id=callback.from_user.id,
                        user_name=callback.from_user.username or "",
                    )
                    topup = wallet.save_topup_payment_link(
                        topup.id,
                        link.transaction_id,
                        link.url,
                        expires_at=link.expires_at(),
                    )
                    payment_id, payment_url = link.transaction_id, link.url
                wallet.record_pending_payment(
                    payment_id=topup.id,
                    telegram_id=callback.from_user.id,
                    amount_minor=amount_minor,
                    reference=f"topup_payment:{topup.id}",
                    external_payment_id=payment_id,
                    purpose="topup",
                )
                payments.track(payment_id)
        except PlategaError as error:
            logger.warning("Platega top-up link could not be created")
            await callback.message.answer(
                t(
                    "payments.method_unavailable"
                    if error.status_code == 400
                    else "wallet.unavailable"
                )
            )
            return
        except (ValueError, WalletError, sqlite3.Error):
            logger.warning("Platega top-up link could not be saved")
            await callback.message.answer(t("wallet.unavailable"))
            return
        await _send_platega_link(
            callback,
            bot,
            payments,
            payment_id,
            payment_url,
            "topup",
            topup.id,
            amount_minor,
        )
        return
    if action == "custom":
        await state.set_state(TopUpFlow.awaiting_amount)
        await callback.answer()
        await _edit_or_send(
            callback,
            t("wallet.topup_prompt", minimum=format_rubles(MIN_TOPUP_MINOR)),
            topup_custom_keyboard(),
        )
        return
    if action.startswith("amount:"):
        raw_amount = action.removeprefix("amount:")
        if not raw_amount.isdigit():
            await callback.answer(t("alert.stale_button_short"), show_alert=True)
            return
        amount_minor = int(raw_amount)
        if not MIN_TOPUP_MINOR <= amount_minor <= MAX_TOPUP_MINOR:
            await callback.answer(
                t("wallet.topup_invalid", minimum=format_rubles(MIN_TOPUP_MINOR)),
                show_alert=True,
            )
            return
        try:
            stars = stars_for_rub_amount(amount_minor, plans)
        except ValueError:
            await callback.answer(t("wallet.unavailable"), show_alert=True)
            return
        await state.update_data(topup_amount_minor=amount_minor)
        await state.set_state(TopUpFlow.choose_payment)
        await callback.answer()
        await _edit_or_send(
            callback,
            t(
                "wallet.payment_method_prompt",
                amount=format_rubles(amount_minor),
                stars=stars,
            ),
            topup_methods_keyboard(
                stars,
                amount_minor=amount_minor,
                platega_enabled=settings.platega_enabled,
            ),
        )
        return
    await callback.answer(t("alert.stale_button_short"), show_alert=True)


@router.message(TopUpFlow.awaiting_amount)
async def topup_amount_input(
    message: Message,
    settings: Settings,
    plans: tuple[Plan, ...],
    state: FSMContext,
    wallet: WalletStore | None = None,
) -> None:
    if not _private_chat(message):
        await message.answer(t("alert.private_chat"))
        return
    assert message.from_user is not None
    if (message.text or "").strip().casefold() in {"/cancel", "отмена"}:
        await state.clear()
        await message.answer(t("common.cancel"), reply_markup=back_keyboard())
        return
    if wallet is None:
        await state.clear()
        await message.answer(t("wallet.unavailable"))
        return
    amount_minor = _parse_rub_amount(message.text or "")
    if amount_minor is None:
        await message.answer(
            t("wallet.topup_invalid", minimum=format_rubles(MIN_TOPUP_MINOR))
        )
        return
    if amount_minor > MAX_TOPUP_MINOR:
        await message.answer(
            t("wallet.topup_too_large", maximum=format_rubles(MAX_TOPUP_MINOR))
        )
        return
    if amount_minor < MIN_TOPUP_MINOR:
        await message.answer(
            t("wallet.topup_invalid", minimum=format_rubles(MIN_TOPUP_MINOR))
        )
        return
    try:
        stars = stars_for_rub_amount(amount_minor, plans)
    except ValueError:
        await message.answer(t("wallet.unavailable"))
        return
    await state.update_data(topup_amount_minor=amount_minor)
    await state.set_state(TopUpFlow.choose_payment)
    await message.answer(
        t(
            "wallet.payment_method_prompt",
            amount=format_rubles(amount_minor),
            stars=stars,
        ),
        reply_markup=topup_methods_keyboard(
            stars, amount_minor=amount_minor, platega_enabled=settings.platega_enabled
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("withdraw:"))
async def withdraw_navigation(
    callback: CallbackQuery,
    bot: Bot,
    settings: Settings,
    wallet: WalletStore | None = None,
) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer(t("alert.private_chat"), show_alert=True)
        return
    if wallet is None:
        await callback.answer(t("wallet.unavailable"), show_alert=True)
        return
    action = (callback.data or "").removeprefix("withdraw:")
    try:
        account = wallet.account(callback.from_user.id)
        pending = wallet.pending_withdrawal(callback.from_user.id)
    except WalletError:
        await callback.answer(t("wallet.unavailable"), show_alert=True)
        return
    if action == "status":
        if pending is None:
            await callback.answer(t("wallet.withdraw_unavailable"), show_alert=True)
        else:
            await callback.answer(
                t(
                    "wallet.pending_row",
                    amount=format_rubles(pending.amount_minor),
                    request_id=pending.id[:8],
                ),
                show_alert=True,
            )
        return
    if action == "request":
        if pending is not None:
            await callback.answer(
                t("wallet.withdraw_pending_error"), show_alert=True
            )
            return
        if account.referral_available_minor < MIN_WITHDRAWAL_MINOR:
            await callback.answer(
                t(
                    "wallet.withdraw_minimum",
                    amount=format_rubles(account.referral_available_minor),
                ),
                show_alert=True,
            )
            return
        await callback.answer()
        await _edit_or_send(
            callback,
            withdrawal_text(
                account.referral_available_minor, settings.support_contact
            ),
            withdrawal_confirm_keyboard(account.referral_available_minor),
        )
        return
    if action == "confirm":
        if pending is not None:
            await callback.answer(
                t("wallet.withdraw_pending_error"), show_alert=True
            )
            return
        try:
            request = wallet.request_withdrawal(
                callback.from_user.id, account.referral_available_minor
            )
        except WithdrawalPending:
            await callback.answer(
                t("wallet.withdraw_pending_error"), show_alert=True
            )
            return
        except (WithdrawalUnavailable, InsufficientFunds, WalletError):
            logger.warning("Withdrawal request could not be created")
            await callback.answer(
                t("wallet.withdraw_unavailable"), show_alert=True
            )
            return
        await callback.answer()
        await _edit_or_send(
            callback,
            t(
                "wallet.withdraw_created",
                amount=format_rubles(request.amount_minor),
                request_id=request.id,
            ),
            back_keyboard("user:wallet"),
        )
        return
    await callback.answer(t("alert.stale_button_short"), show_alert=True)


@router.callback_query(F.data.startswith("withdrawal:"))
async def settle_withdrawal_request(
    callback: CallbackQuery,
    bot: Bot,
    settings: Settings,
    wallet: WalletStore | None = None,
) -> None:
    if (
        callback.message is None
        or callback.message.chat.type != "private"
        or callback.from_user.id not in settings.admin_telegram_ids
    ):
        await callback.answer(t("admin.command_unavailable"), show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 3 or parts[1] not in {"paid", "reject"}:
        await callback.answer(t("alert.stale_button_short"), show_alert=True)
        return
    if wallet is None:
        await callback.answer(t("wallet.unavailable"), show_alert=True)
        return
    try:
        request, changed = wallet.settle_withdrawal(
            parts[2], paid=parts[1] == "paid"
        )
    except (WalletError, sqlite3.Error):
        logger.warning("Withdrawal request could not be settled")
        await callback.answer(t("wallet.withdraw_unavailable"), show_alert=True)
        return
    if not changed:
        await callback.answer(
            t("wallet.withdraw_already_settled"), show_alert=True
        )
        return
    if parts[1] == "paid":
        notice = t(
            "wallet.withdraw_paid_user",
            amount=format_rubles(request.amount_minor),
        )
        try:
            transaction = wallet.payment_transaction(request.id)
            if transaction is not None:
                notice += _receipt_for_transaction(
                    transaction, operation=t("wallet.receipt_withdrawal")
                )
        except WalletError:
            logger.warning("Withdrawal receipt could not be loaded")
        result = t("wallet.withdraw_settled")
    else:
        notice = t(
            "wallet.withdraw_rejected_user",
            amount=format_rubles(request.amount_minor),
        )
        result = t("wallet.withdraw_rejected")
    try:
        await bot.send_message(request.telegram_id, notice)
    except TelegramAPIError:
        logger.warning("Withdrawal status notification failed")
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramAPIError:
            logger.warning("Withdrawal admin controls could not be removed")
    await callback.answer(result)


@router.callback_query(F.data.startswith("pay:balance:"))
async def pay_plan_with_balance(
    callback: CallbackQuery,
    bot: Bot,
    panel: RemnawaveClient,
    settings: Settings,
    plans: tuple[Plan, ...],
    orders: OrderStore,
    wallet: WalletStore | None = None,
) -> None:
    async with _balance_purchase_lock:
        await _pay_plan_with_balance(
            callback, bot, panel, settings, plans, orders, wallet
        )


async def _pay_plan_with_balance(
    callback: CallbackQuery,
    bot: Bot,
    panel: RemnawaveClient,
    settings: Settings,
    plans: tuple[Plan, ...],
    orders: OrderStore,
    wallet: WalletStore | None,
) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer(t("alert.private_chat"), show_alert=True)
        return
    assert isinstance(callback.message, Message)
    if callback.from_user.id in settings.admin_telegram_ids:
        await callback.answer(t("alert.stale_button_short"), show_alert=True)
        return
    if wallet is None:
        await callback.answer(t("wallet.unavailable"), show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or not parts[3].isdigit():
        await callback.answer(t("plans.invalid_invoice"), show_alert=True)
        return
    plan = _stars_plan(
        plans,
        parts[2],
        include_traffic=settings.traffic_plans_enabled,
        sellable_only=True,
    )
    term = plan.term_for(int(parts[3])) if plan is not None else None
    if plan is None or term is None:
        await callback.answer(t("plans.invalid_invoice"), show_alert=True)
        return
    try:
        user = await _load_user(callback.from_user.id, panel)
        account = wallet.account(callback.from_user.id)
    except (PanelError, WalletError) as error:
        logger.warning(
            "Balance purchase pre-check failed (%s)", type(error).__name__
        )
        await callback.answer(t("wallet.payment_blocked"), show_alert=True)
        return
    if (
        user is not None and user.status not in {"ACTIVE", "LIMITED"}
    ) or (user is None and settings.panel_internal_squad_uuid is None):
        await callback.answer(t("plans.purchase_blocked"), show_alert=True)
        return
    if account.balance_minor < term.price_rub_minor:
        await callback.answer(
            t(
                "wallet.insufficient",
                required=format_rubles(term.price_rub_minor),
                balance=format_rubles(account.balance_minor),
            ),
            show_alert=True,
        )
        return
    try:
        latest = orders.latest_for_user(callback.from_user.id)
        if (
            latest is not None
            and latest.plan_code == plan.code
            and latest.term_days == term.days
            and latest.status
            in {PAID, FULFILLING, FULFILLED, FULFILLMENT_REVIEW}
            and _order_is_recent(latest)
        ):
            await callback.answer(
                t("wallet.payment_applied"), show_alert=True
            )
            return
        order = orders.create_order(
            callback.from_user.id, plan, term
        )
        changed = wallet.pay_order(
            order.id, callback.from_user.id, term.price_rub_minor
        )
        if not changed:
            await callback.answer(
                t("wallet.payment_applied"), show_alert=True
            )
            return
        if not orders.claim_fulfillment(order.id):
            raise WalletConflict("Order fulfillment is unavailable")
    except InsufficientFunds:
        await callback.answer(
            t(
                "wallet.insufficient",
                required=format_rubles(term.price_rub_minor),
                balance=format_rubles(account.balance_minor),
            ),
            show_alert=True,
        )
        return
    except (OrderError, WalletError, sqlite3.Error):
        logger.warning("Balance order could not be recorded")
        await callback.answer(t("wallet.payment_blocked"), show_alert=True)
        return
    order_term = _order_plan_term(plan, order)
    try:
        if order_term is None:
            raise OrderConflict("Paid order no longer matches the catalog")
        updated = await _fulfill_order(
            callback.from_user.id, plan, order_term, panel, settings
        )
        if not orders.mark_fulfilled(
            order.id,
            payment_completer=wallet.complete_order_payment_locked,
        ):
            raise OrderStateError("Balance order fulfillment state changed")
        transaction = wallet.payment_transaction(order.id)
        if transaction is None:
            raise WalletError("Successful order payment is missing from history")
    except (PanelError, OrderError, WalletError, sqlite3.Error):
        logger.warning("Balance order requires manual fulfillment review")
        try:
            orders.mark_fulfillment_review(order.id)
        except (OrderError, sqlite3.Error):
            logger.warning("Balance order review state could not be saved")
        try:
            wallet.complete_order_payment(order.id, status="REVIEW")
        except WalletError:
            logger.warning("Balance payment review state could not be saved")
        await callback.answer()
        await _edit_or_send(
            callback, t("plans.paid_review"), back_keyboard("user:wallet")
        )
        return
    if order.rub_price_minor is not None:
        try:
            reward = wallet.reward_referral_purchase(
                order.id, callback.from_user.id, order.rub_price_minor
            )
            await _notify_referral_reward(bot, reward, topup=False)
        except (WalletError, sqlite3.Error):
            logger.warning("Referral purchase reward could not be recorded")
    await callback.answer()
    await answer_screen(
        callback.message,
        _payment_success_text(plan, order_term, updated)
        + _receipt_for_transaction(
            transaction, operation=t("wallet.receipt_subscription")
        ),
        back_keyboard("user:cabinet"),
        banner="purchase_success",
    )


@router.callback_query(F.data.startswith("checkout:"))
async def choose_payment_method(
    callback: CallbackQuery,
    plans: tuple[Plan, ...],
    settings: Settings,
) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer(t("alert.private_chat"), show_alert=True)
        return
    if callback.from_user.id in settings.admin_telegram_ids:
        await callback.answer(t("alert.stale_button_short"), show_alert=True)
        return
    code = (callback.data or "").removeprefix("checkout:")
    plan = _stars_plan(
        plans,
        code,
        include_traffic=settings.traffic_plans_enabled,
        sellable_only=True,
    )
    if plan is None:
        await callback.answer(t("plans.invalid_invoice"), show_alert=True)
        return
    await callback.answer()
    await _edit_or_send(
        callback,
        t(
            "plans.choose_term_text",
            plan=escape(plan.title),
            minimum=plan.days,
            conditions=escape(plan.description),
            cycle_note=(
                t(
                    "plans.calendar_cycle_note"
                    if plan.traffic_limit_strategy != "NO_RESET"
                    else "plans.no_reset_cycle_note"
                )
                if plan.traffic_limit_gb is not None
                else ""
            ),
        ),
        plan_terms_keyboard(plan),
    )


@router.callback_query(F.data.startswith("term:"))
async def choose_plan_term(
    callback: CallbackQuery,
    plans: tuple[Plan, ...],
    settings: Settings,
) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer(t("alert.private_chat"), show_alert=True)
        return
    if callback.from_user.id in settings.admin_telegram_ids:
        await callback.answer(t("alert.stale_button_short"), show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer(t("plans.invalid_invoice"), show_alert=True)
        return
    plan = _stars_plan(
        plans,
        parts[1],
        include_traffic=settings.traffic_plans_enabled,
        sellable_only=True,
    )
    if plan is None or not parts[2].isdigit():
        await callback.answer(t("plans.invalid_invoice"), show_alert=True)
        return
    term = plan.term_for(int(parts[2]))
    if term is None:
        await callback.answer(t("plans.invalid_invoice"), show_alert=True)
        return
    await callback.answer()
    await _edit_or_send(
        callback,
        _plan_term_payment_text(plan, term),
        payment_methods_keyboard(
            plan, term, platega_enabled=settings.platega_enabled
        ),
    )


@router.callback_query(F.data.startswith("pay:"))
async def create_stars_invoice(
    callback: CallbackQuery,
    bot: Bot,
    panel: RemnawaveClient,
    settings: Settings,
    plans: tuple[Plan, ...],
    orders: OrderStore,
    prefs: PreferencesStore | None = None,
    wallet: WalletStore | None = None,
    platega: PlategaClient | None = None,
    payments: PaymentStore | None = None,
) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer(t("alert.private_chat"), show_alert=True)
        return
    is_admin = callback.from_user.id in settings.admin_telegram_ids
    raw_action = (callback.data or "").removeprefix("pay:")
    parts = raw_action.split(":")
    if parts and parts[0] == "unavailable":
        if (
            is_admin
            or len(parts) != 4
            or parts[1] not in {"sbp", "card", "crypto"}
            or not parts[3].isdigit()
        ):
            await callback.answer(t("alert.stale_button_short"), show_alert=True)
            return
        plan = _stars_plan(
            plans,
            parts[2],
            include_traffic=settings.traffic_plans_enabled,
            sellable_only=True,
        )
        term = plan.term_for(int(parts[3])) if plan is not None else None
        if plan is None or term is None:
            await callback.answer(t("alert.stale_button_short"), show_alert=True)
            return
        await callback.answer(t("plans.payment_unavailable"), show_alert=True)
        return
    operation_key: str | None = None
    selected_days: int | None = None
    platega_method: int | None = None
    if is_admin:
        if len(parts) != 2 or not re.fullmatch(r"[A-Za-z0-9_-]{16}", parts[1]):
            await callback.answer(t("plans.invalid_invoice"), show_alert=True)
            return
        code, operation_key = parts
    elif len(parts) == 4 and parts[0] == "platega":
        if (
            not settings.platega_enabled
            or platega is None
            or not parts[2].isdigit()
            or not parts[3].isdigit()
            or int(parts[3]) not in PlategaClient.PAYMENT_METHODS
        ):
            await callback.answer(t("plans.payment_unavailable"), show_alert=True)
            return
        code = parts[1]
        selected_days = int(parts[2])
        platega_method = int(parts[3])
    else:
        if len(parts) == 3 and parts[0] == "stars":
            code = parts[1]
            if not parts[2].isdigit():
                await callback.answer(t("plans.invalid_invoice"), show_alert=True)
                return
            selected_days = int(parts[2])
        else:
            await callback.answer(t("plans.invalid_invoice"), show_alert=True)
            return
    plan = (
        _admin_free_plan(
            plans,
            code,
            include_traffic=(
                settings.traffic_plans_enabled
                or callback.from_user.id in settings.admin_telegram_ids
            ),
        )
        if is_admin
        else _stars_plan(
            plans,
            code,
            include_traffic=settings.traffic_plans_enabled,
            sellable_only=True,
        )
    )
    if plan is None:
        await callback.answer(t("plans.invalid_invoice"), show_alert=True)
        return
    selected_term = plan.term_for(selected_days) if selected_days is not None else None
    if not is_admin and (not plan.sellable or selected_term is None):
        await callback.answer(t("plans.invalid_invoice"), show_alert=True)
        return
    if not is_admin:
        await callback.answer()
    try:
        user = await _load_user(callback.from_user.id, panel)
    except PanelError as error:
        logger.warning(
            "Subscription pre-purchase check failed (%s)", type(error).__name__
        )
        if is_admin:
            await callback.answer(t("plans.purchase_blocked"), show_alert=True)
        else:
            await callback.message.answer(t("plans.purchase_blocked"))
        return
    if (
        user is not None
        and user.status not in {"ACTIVE", "EXPIRED"}
        and not (
            user.status == "LIMITED"
            and (
                settings.traffic_plans_enabled
                or callback.from_user.id in settings.admin_telegram_ids
                or plan.traffic_limit_gb is None
            )
        )
    ) or (user is None and settings.panel_internal_squad_uuid is None):
        if is_admin:
            await callback.answer(t("plans.purchase_blocked"), show_alert=True)
        else:
            await callback.message.answer(t("plans.purchase_blocked"))
        return
    if is_admin:
        assert operation_key is not None
        await _grant_admin_plan(callback, panel, settings, plan, prefs, operation_key)
        return
    if selected_term is None:
        await callback.message.answer(t("plans.invalid_invoice"))
        return
    if platega_method is not None:
        assert platega is not None
        if wallet is None:
            await callback.message.answer(t("wallet.unavailable"))
            return
        payments = payments or PaymentStore(wallet.database_path)
        try:
            async with _platega_link_lock:
                order = orders.create_order(
                    callback.from_user.id,
                    plan,
                    selected_term,
                    provider="platega",
                    payment_method=platega_method,
                )
                if order.provider_payment_id and order.provider_payment_url:
                    payment_id, payment_url = (
                        order.provider_payment_id,
                        order.provider_payment_url,
                    )
                else:
                    return_url = await _payment_return_url(bot)
                    link = await platega.create_payment(
                        amount=Decimal(order.amount_minor) / 100,
                        currency=order.currency,
                        payment_method=platega_method,
                        description=f"Jopes VPN {plan.title} · {selected_term.days} days",
                        return_url=return_url,
                        failed_url=return_url,
                        order_id=order.id,
                        user_id=callback.from_user.id,
                        user_name=callback.from_user.username or "",
                    )
                    order = orders.save_payment_link(
                        order.id,
                        link.transaction_id,
                        link.url,
                        expires_at=link.expires_at(),
                    )
                    payment_id, payment_url = link.transaction_id, link.url
                wallet.record_pending_payment(
                    payment_id=order.id,
                    telegram_id=order.telegram_id,
                    amount_minor=order.amount_minor,
                    reference=f"order_payment:{order.id}",
                    external_payment_id=payment_id,
                    purpose="subscription",
                )
                payments.track(payment_id)
        except PlategaError as error:
            logger.warning("Platega subscription link could not be created")
            await callback.message.answer(
                t(
                    "payments.method_unavailable"
                    if error.status_code == 400
                    else "plans.payment_unavailable"
                )
            )
            return
        except (OrderError, WalletError, sqlite3.Error, ValueError):
            logger.warning("Platega subscription link could not be saved")
            await callback.message.answer(t("plans.payment_unavailable"))
            return
        await _send_platega_link(
            callback,
            bot,
            payments,
            payment_id,
            payment_url,
            "order",
            order.id,
            order.amount_minor,
        )
        return
    try:
        order = orders.create_order(callback.from_user.id, plan, selected_term)
    except (OrderError, sqlite3.Error):
        logger.warning("Stars order could not be created")
        await callback.message.answer(t("plans.invalid_invoice"))
        return
    try:
        await callback.message.answer_invoice(
            title=t("plans.invoice_title", plan=plan.title),
            description=t(
                "plans.invoice_description",
                plan=plan.title,
                days=selected_term.days,
            ),
            payload=f"jvpay_{order.id}",
            provider_token="",
            currency="XTR",
            prices=[
                LabeledPrice(
                    label=plan.title,
                    amount=selected_term.stars_amount,
                )
            ],
        )
    except TelegramAPIError:
        logger.warning("Stars invoice delivery failed")
        await callback.message.answer(t("plans.purchase_blocked"))


@router.pre_checkout_query()
async def validate_stars_pre_checkout(
    query: PreCheckoutQuery,
    panel: RemnawaveClient,
    settings: Settings,
    plans: tuple[Plan, ...],
    orders: OrderStore,
    wallet: WalletStore | None = None,
) -> None:
    valid = False
    error_message = t("plans.invalid_invoice")
    payload = query.invoice_payload
    if payload.startswith("jvtop_"):
        topup_id = payload.removeprefix("jvtop_")
        try:
            if wallet is not None:
                topup = wallet.topup(topup_id)
                valid = (
                    topup.telegram_id == query.from_user.id
                    and topup.status == "PENDING"
                    and topup.provider_payment_id is None
                    and query.currency == "XTR"
                    and query.total_amount == topup.stars_amount
                )
        except (TopupNotFound, WalletError, sqlite3.Error):
            logger.warning("Top-up pre-checkout could not be validated")
    elif payload.startswith("jvpay_"):
        order_id = payload.removeprefix("jvpay_")
        try:
            order = orders.get(order_id)
            plan = _stars_plan(
                plans,
                order.plan_code,
                include_traffic=settings.traffic_plans_enabled,
            )
            order_term = (
                None if plan is None else _order_plan_term(plan, order)
            )
            if (
                plan is not None
                and order_term is not None
                and order.telegram_id == query.from_user.id
                and order.status == PENDING
                and _order_is_current(order)
                and order.currency == "XTR"
                and query.currency == "XTR"
                and query.total_amount == order.amount_minor
            ):
                user = await _load_user(query.from_user.id, panel)
                valid = (
                    user is None
                    and settings.panel_internal_squad_uuid is not None
                ) or (
                    user is not None
                    and (
                        user.status == "ACTIVE"
                        or user.status == "LIMITED"
                    )
                )
                if not valid:
                    error_message = t("plans.purchase_blocked")
        except PanelError as error:
            logger.warning(
                "Stars pre-checkout Panel validation failed (%s)",
                type(error).__name__,
            )
            error_message = t("plans.purchase_blocked")
        except (OrderNotFound, OrderError, sqlite3.Error):
            logger.warning("Stars pre-checkout order could not be validated")
    if not valid:
        try:
            await query.answer(ok=False, error_message=error_message)
        except TelegramAPIError:
            logger.warning("Stars pre-checkout rejection could not be delivered")
        return
    try:
        await query.answer(ok=True)
    except TelegramAPIError:
        logger.warning("Stars pre-checkout approval could not be delivered")


async def _fulfill_order(
    telegram_id: int,
    plan: Plan,
    order_term: tuple[int, PlanTerm | None],
    panel: RemnawaveClient,
    settings: Settings,
) -> PanelUser:
    duration_days, _ = order_term
    user = await _load_user(telegram_id, panel)
    if user is None:
        if settings.panel_internal_squad_uuid is None:
            raise PanelAccessDenied("Panel squad is not configured")
        return await panel.create_user(
            telegram_id,
            duration_days,
            settings.panel_internal_squad_uuid,
            tag=plan.panel_tag,
            traffic_limit_gb=plan.traffic_limit_gb,
            permanent_traffic=plan.permanent_traffic,
            traffic_limit_strategy=plan.traffic_limit_strategy,
        )
    if user.status not in {"ACTIVE", "LIMITED"}:
        raise OrderConflict("Subscription is no longer eligible for extension")
    return await panel.apply_paid_plan(
        user,
        duration_days,
        plan.traffic_limit_gb,
        permanent_traffic=plan.permanent_traffic,
        tag=plan.panel_tag,
        traffic_limit_strategy=plan.traffic_limit_strategy,
    )


def _payment_success_text(
    plan: Plan, order_term: tuple[int, PlanTerm | None], updated: PanelUser
) -> str:
    duration_days, _ = order_term
    if plan.permanent_traffic:
        return t(
            "plans.payment_success_traffic",
            gb=plan.traffic_limit_gb,
        )
    if plan.traffic_limit_gb is not None:
        return t(
            "plans.payment_success_traffic_limited",
            gb=plan.traffic_limit_gb,
            expires=_expiry_text(updated),
        )
    return t(
        "plans.payment_success",
        days=duration_days,
        expires=_expiry_text(updated),
    )


def _receipt_amount(transaction: PaymentTransaction) -> str:
    if transaction.currency == "XTR":
        return (
            f"{transaction.amount_paid_minor} ⭐ "
            f"({format_rubles(transaction.rub_amount_minor)})"
        )
    if transaction.amount_paid_minor:
        return format_rubles(transaction.amount_paid_minor)
    return format_rubles(transaction.rub_amount_minor)


def _receipt_for_transaction(transaction: PaymentTransaction, *, operation: str) -> str:
    method = payment_method_text(transaction.method)
    return payment_receipt_text(
        operation_id=transaction.id,
        operation=operation,
        amount=_receipt_amount(transaction),
        method=method,
        result=t("wallet.receipt_result_success"),
    )


async def _notify_referral_reward(
    bot: Bot, reward, *, topup: bool
) -> None:
    if reward is None or reward.amount_minor <= 0:
        return
    event = t(
        "referral.reward_topup_event" if topup else "referral.reward_purchase_event",
        percent=reward.percent,
    )
    await _notify_referrer(
        bot,
        reward.referrer_id,
        t(
            "referral.reward_notice",
            amount=format_rubles(reward.amount_minor),
            event=event,
        ),
    )


@router.message(F.successful_payment)
async def process_stars_payment(
    message: Message,
    bot: Bot,
    panel: RemnawaveClient,
    settings: Settings,
    plans: tuple[Plan, ...],
    orders: OrderStore,
    wallet: WalletStore | None = None,
) -> None:
    if message.from_user is None or message.successful_payment is None:
        return
    payment = message.successful_payment
    payload = payment.invoice_payload
    if payload.startswith("jvtop_"):
        await _process_topup_payment(message, bot, wallet, settings, payload)
        return
    order_id = payload.removeprefix("jvpay_") if payload.startswith("jvpay_") else ""
    if not order_id:
        logger.warning("Successful Stars payment had an unknown invoice payload")
        await _notify_payment_admins(bot, settings, "unknown")
        await message.answer(t("plans.paid_review"))
        return
    try:
        order = orders.get(order_id)
        if order.telegram_id != message.from_user.id:
            raise OrderConflict("Payment owner does not match order")
        order, changed = orders.confirm_payment(
            order.id,
            provider="telegram_stars",
            provider_payment_id=payment.telegram_payment_charge_id,
            amount_minor=payment.total_amount,
            currency=payment.currency,
            payment_recorder=(
                None
                if wallet is None
                else lambda connection, paid_order: (
                    wallet.record_order_payment_locked(
                        connection,
                        order_id=paid_order.id,
                        telegram_id=paid_order.telegram_id,
                        amount_paid_minor=paid_order.amount_minor,
                        currency=paid_order.currency,
                        method="telegram_stars",
                        external_payment_id=paid_order.provider_payment_id or "",
                    )
                )
            ),
        )
        if not changed or order.status != PAID:
            return
        if wallet is None:
            raise WalletError("Payment history is unavailable")
        transaction = wallet.payment_transaction(order.id)
        if transaction is None:
            raise WalletError("Payment history is unavailable")
        if not orders.claim_fulfillment(order.id):
            return
    except (OrderError, WalletError, sqlite3.Error):
        logger.warning("Confirmed Stars payment could not be recorded")
        try:
            orders.mark_fulfillment_review(order_id)
        except (OrderError, sqlite3.Error):
            logger.warning("Paid order review state could not be saved")
        await _notify_payment_admins(bot, settings, order_id)
        await message.answer(t("plans.paid_review"))
        return

    plan = _stars_plan(
        plans, order.plan_code, include_traffic=settings.traffic_plans_enabled
    )
    order_term = None if plan is None else _order_plan_term(plan, order)
    try:
        if (
            plan is None
            or order_term is None
            or plan.currency != order.currency
            or payment.currency != "XTR"
            or payment.total_amount != order.amount_minor
        ):
            raise OrderConflict("Paid order no longer matches the catalog")
        updated = await _fulfill_order(
            message.from_user.id, plan, order_term, panel, settings
        )
        if not orders.mark_fulfilled(
            order.id,
            payment_completer=wallet.complete_order_payment_locked,
        ):
            raise OrderStateError("Stars order fulfillment state changed")
        transaction = wallet.payment_transaction(order.id)
        if transaction is None:
            raise WalletError("Successful order payment is missing from history")
    except (PanelError, OrderError, WalletError, sqlite3.Error):
        logger.warning("Paid Stars order requires manual fulfillment review")
        try:
            orders.mark_fulfillment_review(order.id)
        except (OrderError, sqlite3.Error):
            logger.warning("Paid Stars order review state could not be saved")
        if wallet is not None:
            try:
                wallet.complete_order_payment(order.id, status="REVIEW")
            except WalletError:
                logger.warning("Stars payment review state could not be saved")
        await message.answer(t("plans.paid_review"))
        return
    if wallet is not None and order.rub_price_minor is not None:
        try:
            reward = wallet.reward_referral_purchase(
                order.id, message.from_user.id, order.rub_price_minor
            )
            await _notify_referral_reward(bot, reward, topup=False)
        except (WalletError, sqlite3.Error):
            logger.warning("Referral purchase reward could not be recorded")
    await answer_screen(
        message,
        _payment_success_text(plan, order_term, updated)
        + _receipt_for_transaction(
            transaction, operation=t("wallet.receipt_subscription")
        ),
        back_keyboard("user:cabinet"),
        banner="purchase_success",
    )


async def _process_topup_payment(
    message: Message,
    bot: Bot,
    wallet: WalletStore | None,
    settings: Settings,
    payload: str,
) -> None:
    if message.from_user is None or message.successful_payment is None:
        return
    payment = message.successful_payment
    topup_id = payload.removeprefix("jvtop_")
    if wallet is None or not topup_id:
        logger.warning("Top-up payment storage is unavailable")
        await _notify_payment_admins(bot, settings, "topup")
        await message.answer(t("wallet.topup_review"))
        return
    try:
        existing = wallet.topup(topup_id)
        if existing.telegram_id != message.from_user.id:
            raise TopupConflict("Payment owner does not match top-up")
        topup, changed, reward = wallet.confirm_topup(
            topup_id,
            provider_payment_id=payment.telegram_payment_charge_id,
            amount_minor=payment.total_amount,
            currency=payment.currency,
        )
    except (TopupNotFound, TopupConflict, WalletError, sqlite3.Error):
        logger.warning("Confirmed top-up could not be recorded")
        await _notify_payment_admins(bot, settings, topup_id)
        await message.answer(t("wallet.topup_review"))
        return
    if not changed:
        return
    try:
        account = wallet.account(message.from_user.id)
        bonus_minor = wallet.topup_bonus_minor(topup.id)
    except (WalletError, sqlite3.Error):
        logger.warning("Confirmed top-up details could not be loaded")
        await _notify_payment_admins(bot, settings, topup_id)
        await message.answer(t("wallet.topup_review"))
        return
    account_balance = account.balance_minor
    transaction = wallet.payment_transaction(topup.id)
    if transaction is None:
        logger.warning("Successful top-up is missing from payment history")
        await _notify_payment_admins(bot, settings, topup.id)
        await message.answer(t("wallet.topup_review"))
        return
    await message.answer(
        t(
            "wallet.topup_success",
            amount=format_rubles(topup.amount_minor),
            bonus=format_rubles(bonus_minor),
            balance=format_rubles(account_balance),
        )
        + _receipt_for_transaction(
            transaction, operation=t("wallet.receipt_topup")
        ),
        parse_mode="HTML",
    )
    try:
        await _notify_referral_reward(bot, reward, topup=True)
    except TelegramAPIError:
        logger.warning("Top-up referral notification delivery failed")


def _platega_minor_units(amount: Decimal) -> int:
    minor = amount * 100
    if not amount.is_finite() or minor != minor.to_integral_value():
        raise PlategaProtocolError("Platega amount has unsupported precision")
    return int(minor)


async def _apply_platega_status(
    payment: TransactionStatus,
    *,
    bot: Bot,
    panel: RemnawaveClient,
    settings: Settings,
    plans: tuple[Plan, ...],
    orders: OrderStore,
    wallet: WalletStore,
    payments: PaymentStore | None = None,
) -> str:
    payments = payments or PaymentStore(wallet.database_path)
    payments.track(payment.transaction_id)
    async with payments.processing_lock(payment.transaction_id):
        result = await _apply_platega_status_locked(
            payment,
            bot=bot,
            panel=panel,
            settings=settings,
            plans=plans,
            orders=orders,
            wallet=wallet,
            payments=payments,
        )
        if result != "pending":
            payments.queue_screen(
                payment.transaction_id,
                result,
                t(
                    "payments.status_screen",
                    status=t(f"platega.status_{result}"),
                    amount=format_rubles(_platega_minor_units(payment.amount)),
                    payment_id=escape(payment.transaction_id),
                ),
            )
        return result


def _queue_payment_referral(
    payments: PaymentStore,
    reward,
    reference: str,
    *,
    topup: bool,
) -> None:
    if reward is None or reward.amount_minor <= 0:
        return
    payments.queue_notice(
        f"referral:{reference}",
        reward.referrer_id,
        t(
            "referral.reward_notice",
            amount=format_rubles(reward.amount_minor),
            event=t(
                "referral.reward_topup_event"
                if topup
                else "referral.reward_purchase_event",
                percent=reward.percent,
            ),
        ),
    )


def _queue_platega_review(
    payments: PaymentStore,
    settings: Settings,
    orders: OrderStore,
    order_id: str,
    telegram_id: int,
) -> None:
    payments.queue_notice(f"review:{order_id}", telegram_id, t("plans.paid_review"))
    for admin_id in settings.admin_telegram_ids:
        payments.queue_notice(
            f"review:{order_id}:{admin_id}",
            admin_id,
            t("plans.payment_issue_admin", order_id=escape(order_id)),
        )
    orders.mark_review_notified(order_id)


async def _apply_platega_status_locked(
    payment: TransactionStatus,
    *,
    bot: Bot,
    panel: RemnawaveClient,
    settings: Settings,
    plans: tuple[Plan, ...],
    orders: OrderStore,
    wallet: WalletStore,
    payments: PaymentStore,
) -> str:
    topup = wallet.topup_by_payment_id(payment.transaction_id)
    order = orders.get_by_payment_id(payment.transaction_id, provider="platega")
    if topup is not None and topup.provider != "platega":
        raise PlategaProtocolError("Payment provider does not match")
    if topup is None and order is None and payment.payload is not None:
        try:
            local_id = UUID(payment.payload).hex
        except (ValueError, TypeError, AttributeError):
            raise PlategaProtocolError("Platega payload is invalid") from None
        try:
            candidate_topup = wallet.topup(local_id)
            if (
                candidate_topup.provider == "platega"
                and candidate_topup.provider_payment_id is None
            ):
                topup = candidate_topup
        except TopupNotFound:
            pass
        try:
            candidate_order = orders.get(local_id)
            if (
                candidate_order.provider == "platega"
                and candidate_order.provider_payment_id is None
            ):
                order = candidate_order
        except OrderNotFound:
            pass
    if (topup is None) == (order is None):
        raise PlategaProtocolError("Platega transaction has no unique local payment")
    if topup is not None:
        local_id = topup.id
        expected_minor = topup.amount_minor
    else:
        assert order is not None
        local_id = order.id
        expected_minor = order.amount_minor
    if payment.payload is not None:
        try:
            if UUID(payment.payload).hex != UUID(local_id).hex:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise PlategaProtocolError(
                "Platega payload does not match the local payment"
            ) from None
    if (
        payment.currency != "RUB"
        or _platega_minor_units(payment.amount) != expected_minor
    ):
        raise PlategaProtocolError("Platega amount or currency does not match")
    if topup is not None and topup.provider_payment_id is None:
        topup = wallet.save_topup_payment_link(topup.id, payment.transaction_id)
    elif order is not None and order.provider_payment_id is None:
        order = orders.save_payment_link(order.id, payment.transaction_id)
    wallet.record_pending_payment(
        payment_id=local_id,
        telegram_id=topup.telegram_id if topup is not None else order.telegram_id,
        amount_minor=expected_minor,
        reference=f"topup_payment:{local_id}"
        if topup is not None
        else f"order_payment:{local_id}",
        external_payment_id=payment.transaction_id,
        purpose="topup" if topup is not None else "subscription",
    )
    if payment.status == "PENDING":
        return "pending"
    if payment.status == "CANCELED":
        local_status = topup.status if topup is not None else order.status
        if local_status == "REFUNDED":
            return "refunded"
        if local_status == FULFILLMENT_REVIEW:
            return "review"
        if local_status in {"CREDITED", "REFUNDING", PAID, FULFILLING, FULFILLED}:
            return "confirmed"
        if topup is not None:
            wallet.cancel_topup(topup.id)
        elif order is not None:
            try:
                orders.cancel(order.id)
            except OrderStateError:
                pass
        wallet.update_provider_payment_status(payment.transaction_id, "CANCELED")
        payments.queue_notice(
            f"canceled:{local_id}",
            topup.telegram_id if topup is not None else order.telegram_id,
            t("platega.status_canceled"),
        )
        return "canceled"
    if payment.status == "CHARGEBACKED":
        if topup is not None:
            wallet.refund_topup(topup.id)
            target_id, telegram_id = topup.id, topup.telegram_id
        else:
            assert order is not None
            wallet.refund_order(order.id)
            target_id, telegram_id = order.id, order.telegram_id
        wallet.update_provider_payment_status(payment.transaction_id, "REFUNDED")
        for admin_id in settings.admin_telegram_ids:
            payments.queue_notice(
                f"refund:{target_id}:{admin_id}",
                admin_id,
                t("platega.refund_admin", payment_id=escape(target_id)),
            )
        payments.queue_notice(
            f"refund:{target_id}", telegram_id, t("plans.refund_user_notice")
        )
        return "refunded"
    if payment.status != "CONFIRMED":
        raise PlategaProtocolError("Unsupported Platega status")

    if topup is not None:
        if topup.status == "REFUNDED":
            return "refunded"
        if topup.status == "REFUNDING":
            return "confirmed"
        topup, _changed, reward = wallet.confirm_topup(
            topup.id,
            provider_payment_id=payment.transaction_id,
            amount_minor=expected_minor,
            currency=payment.currency,
            provider="platega",
        )
        account = wallet.account(topup.telegram_id)
        transaction = wallet.payment_transaction(topup.id)
        if transaction is None:
            raise WalletError("Confirmed Platega top-up is missing from history")
        payments.queue_notice(
            f"topup:{topup.id}",
            topup.telegram_id,
            t(
                "wallet.topup_success",
                amount=format_rubles(topup.amount_minor),
                bonus=format_rubles(wallet.topup_bonus_minor(topup.id)),
                balance=format_rubles(account.balance_minor),
            )
            + _receipt_for_transaction(
                transaction, operation=t("wallet.receipt_topup")
            ),
        )
        _queue_payment_referral(payments, reward, topup.id, topup=True)
        return "confirmed"

    assert order is not None
    order, _changed = orders.confirm_payment(
        order.id,
        provider="platega",
        provider_payment_id=payment.transaction_id,
        amount_minor=expected_minor,
        currency=payment.currency,
        payment_recorder=lambda connection, paid_order: (
            wallet.record_order_payment_locked(
                connection,
                order_id=paid_order.id,
                telegram_id=paid_order.telegram_id,
                amount_paid_minor=paid_order.amount_minor,
                currency=paid_order.currency,
                method="platega",
                external_payment_id=payment.transaction_id,
            )
        ),
    )
    if order.status == REFUNDED:
        return "refunded"
    if order.status == FULFILLMENT_REVIEW:
        _queue_platega_review(payments, settings, orders, order.id, order.telegram_id)
        return "review"
    if order.status == FULFILLED:
        transaction = wallet.payment_transaction(order.id)
        if transaction is None:
            raise WalletError("Successful Platega order is missing from history")
        if order.rub_price_minor is not None:
            reward = wallet.reward_referral_purchase(
                order.id, order.telegram_id, order.rub_price_minor
            )
            _queue_payment_referral(payments, reward, order.id, topup=False)
        payments.queue_notice(
            f"order:{order.id}",
            order.telegram_id,
            t("payments.subscription_success")
            + _receipt_for_transaction(
                transaction, operation=t("wallet.receipt_subscription")
            ),
        )
        return "confirmed"
    if order.status != PAID:
        return "confirmed"
    if not orders.claim_fulfillment(order.id):
        return "confirmed"
    try:
        plan = _stars_plan(
            plans, order.plan_code, include_traffic=settings.traffic_plans_enabled
        )
        order_term = None if plan is None else _order_plan_term(plan, order)
        if plan is None or order_term is None:
            raise OrderConflict("Paid Platega order no longer matches the catalog")
        updated = await _fulfill_order(
            order.telegram_id, plan, order_term, panel, settings
        )
        if not orders.mark_fulfilled(
            order.id, payment_completer=wallet.complete_order_payment_locked
        ):
            raise OrderStateError("Platega order fulfillment state changed")
    except (PanelError, OrderError, WalletError, sqlite3.Error):
        logger.warning("Paid Platega order requires fulfillment review")
        try:
            orders.mark_fulfillment_review(order.id)
            wallet.complete_order_payment(order.id, status="REVIEW")
        except (OrderError, WalletError, sqlite3.Error):
            logger.warning("Platega fulfillment review state could not be saved")
        _queue_platega_review(payments, settings, orders, order.id, order.telegram_id)
        return "review"
    transaction = wallet.payment_transaction(order.id)
    if transaction is None:
        raise WalletError("Successful Platega order is missing from history")
    if order.rub_price_minor is not None:
        reward = wallet.reward_referral_purchase(
            order.id, order.telegram_id, order.rub_price_minor
        )
        _queue_payment_referral(payments, reward, order.id, topup=False)
    payments.queue_notice(
        f"order:{order.id}",
        order.telegram_id,
        _payment_success_text(plan, order_term, updated)
        + _receipt_for_transaction(
            transaction, operation=t("wallet.receipt_subscription")
        ),
    )
    return "confirmed"


async def _verify_platega_status(
    client: PlategaClient, callback_status: TransactionStatus
) -> TransactionStatus:
    actual = await client.get_transaction_status(callback_status.transaction_id)
    if (
        actual.transaction_id != callback_status.transaction_id
        or actual.amount != callback_status.amount
        or actual.currency != callback_status.currency
        or (
            callback_status.payload is not None
            and actual.payload != callback_status.payload
        )
    ):
        raise PlategaProtocolError("Platega callback differs from transaction status")
    return actual


async def _process_platega_check(
    check: PaymentCheck,
    *,
    bot: Bot,
    panel: RemnawaveClient,
    settings: Settings,
    plans: tuple[Plan, ...],
    orders: OrderStore,
    wallet: WalletStore,
    payments: PaymentStore,
    client: PlategaClient,
) -> None:
    try:
        status = (
            await _verify_platega_status(client, check.callback)
            if check.callback is not None
            else await client.get_transaction_status(check.transaction_id)
        )
        result = await _apply_platega_status(
            status,
            bot=bot,
            panel=panel,
            settings=settings,
            plans=plans,
            orders=orders,
            wallet=wallet,
            payments=payments,
        )
        awaiting_refund = status.status == "CONFIRMED" and (
            payments.awaiting_refund(check.transaction_id)
            or (check.callback is not None and check.callback.status == "CHARGEBACKED")
        )
        payments.finish_check(check, result, retry=awaiting_refund)
    except (
        PlategaError,
        OrderError,
        WalletError,
        PanelError,
        sqlite3.Error,
        ValueError,
    ) as error:
        logger.warning(
            "Automatic payment verification deferred for %s (%s)",
            check.transaction_id, type(error).__name__,
        )
        payments.retry_check(check)


async def _deliver_payment_notices(bot: Bot, payments: PaymentStore) -> None:
    for notice in payments.pending_notices():
        try:
            if notice.message_id is not None:
                await bot.edit_message_text(
                    notice.text,
                    chat_id=notice.telegram_id,
                    message_id=notice.message_id,
                    reply_markup=back_keyboard("user:wallet"),
                    parse_mode="HTML",
                )
            else:
                await bot.send_message(
                    notice.telegram_id, notice.text, parse_mode="HTML"
                )
        except TelegramRetryAfter as error:
            payments.retry_notice(notice, delay=error.retry_after)
        except TelegramForbiddenError:
            payments.mark_notice_delivered(notice.id)
        except TelegramBadRequest:
            if notice.message_id is not None:
                payments.mark_notice_delivered(notice.id)
            else:
                payments.retry_notice(notice)
        except TelegramAPIError:
            logger.warning("Payment notification delivery deferred")
            payments.retry_notice(notice)
        else:
            payments.mark_notice_delivered(notice.id)


async def platega_payment_iteration(
    *,
    bot: Bot,
    panel: RemnawaveClient,
    settings: Settings,
    plans: tuple[Plan, ...],
    orders: OrderStore,
    wallet: WalletStore,
    payments: PaymentStore,
    client: PlategaClient,
    deliver_notices: bool = True,
) -> None:
    payments.synchronize()
    semaphore = asyncio.Semaphore(5)

    async def process(check: PaymentCheck) -> None:
        async with semaphore:
            await _process_platega_check(
                check,
                bot=bot,
                panel=panel,
                settings=settings,
                plans=plans,
                orders=orders,
                wallet=wallet,
                payments=payments,
                client=client,
            )

    results = await asyncio.gather(
        *(process(check) for check in payments.pending_checks()), return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result
    if deliver_notices:
        await _deliver_payment_notices(bot, payments)


async def payment_notice_loop(
    bot: Bot, payments: PaymentStore, *, interval_seconds: float = 1,
) -> None:
    while True:
        payments.notices_changed.clear()
        try:
            await _deliver_payment_notices(bot, payments)
        except sqlite3.Error:
            logger.warning("Payment notification queue is unavailable")
        try:
            await asyncio.wait_for(payments.notices_changed.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass


async def platega_payment_loop(
    *,
    bot: Bot,
    panel: RemnawaveClient,
    settings: Settings,
    plans: tuple[Plan, ...],
    orders: OrderStore,
    wallet: WalletStore,
    payments: PaymentStore,
    client: PlategaClient,
    interval_seconds: float = 10,
) -> None:
    while True:
        payments.changed.clear()
        try:
            await platega_payment_iteration(
                bot=bot,
                panel=panel,
                settings=settings,
                plans=plans,
                orders=orders,
                wallet=wallet,
                payments=payments,
                client=client,
                deliver_notices=False,
            )
        except (OrderError, WalletError, sqlite3.Error):
            logger.warning("Automatic payment reconciliation deferred")
        try:
            await asyncio.wait_for(payments.changed.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass


@router.callback_query(F.data.startswith("platega:check:"))
async def check_platega_payment(
    callback: CallbackQuery,
    bot: Bot,
    panel: RemnawaveClient,
    settings: Settings,
    plans: tuple[Plan, ...],
    orders: OrderStore,
    wallet: WalletStore | None = None,
    platega: PlategaClient | None = None,
    payments: PaymentStore | None = None,
) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer(t("alert.private_chat"), show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if (
        len(parts) != 4
        or parts[2] not in {"order", "topup"}
        or wallet is None
        or platega is None
    ):
        await callback.answer(t("wallet.unavailable"), show_alert=True)
        return
    try:
        if parts[2] == "topup":
            topup = wallet.topup(parts[3])
            if topup.telegram_id != callback.from_user.id:
                raise WalletConflict("Payment owner does not match")
            payment_id = topup.provider_payment_id
        else:
            order = orders.get(parts[3])
            if order.telegram_id != callback.from_user.id:
                raise OrderConflict("Payment owner does not match")
            payment_id = order.provider_payment_id
        if not payment_id:
            await callback.answer(t("platega.payment_pending"), show_alert=True)
            return
        payments = payments or PaymentStore(wallet.database_path)
        payments.wake_check(payment_id)
    except (OrderError, WalletError, PlategaError, sqlite3.Error):
        logger.warning("Platega payment status could not be checked")
        await callback.answer(t("wallet.unavailable"), show_alert=True)
        return
    await callback.answer(t("payments.check_queued"))


def _platega_web_application(
    *,
    bot: Bot,
    panel: RemnawaveClient,
    settings: Settings,
    plans: tuple[Plan, ...],
    orders: OrderStore,
    wallet: WalletStore,
    client: PlategaClient,
    payments: PaymentStore | None = None,
) -> web.Application:
    payments = payments or PaymentStore(wallet.database_path)
    application = web.Application(client_max_size=64 * 1024)

    async def callback_handler(request: web.Request) -> web.Response:
        try:
            body = await request.json()
            payment = client.validate_callback(request.headers, body)
        except (PlategaProtocolError, ValueError, web.HTTPBadRequest):
            raise web.HTTPBadRequest(text="Invalid payment callback") from None
        except PlategaError:
            logger.warning("Platega callback validation failed")
            raise web.HTTPUnauthorized(text="Invalid payment callback") from None
        try:
            payments.request_check(payment)
        except sqlite3.Error:
            logger.warning("Platega callback could not be persisted")
            raise web.HTTPServiceUnavailable(text="Payment queue unavailable") from None
        return web.Response(status=200)

    application.router.add_post("/platega/callback", callback_handler)

    async def health_handler(_request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    application.router.add_get("/platega/healthz", health_handler)
    return application


@router.message(F.refunded_payment)
async def process_stars_refund_update(
    message: Message,
    bot: Bot,
    settings: Settings,
    orders: OrderStore,
    wallet: WalletStore | None = None,
) -> None:
    payment = message.refunded_payment
    if payment is None:
        return
    if wallet is not None:
        try:
            topup = wallet.topup_by_payment_id(
                payment.telegram_payment_charge_id
            )
        except (WalletError, sqlite3.Error):
            logger.warning("Top-up refund lookup failed")
            topup = None
        if topup is not None:
            if (
                payment.currency != "XTR"
                or payment.total_amount != topup.stars_amount
            ):
                logger.warning("Telegram Stars top-up refund details do not match")
                return
            try:
                changed = wallet.refund_topup(topup.id)
            except (WalletError, sqlite3.Error):
                logger.warning("Telegram Stars top-up refund could not be recorded")
                return
            if changed:
                for admin_id in settings.admin_telegram_ids:
                    try:
                        await bot.send_message(
                            admin_id,
                            t("wallet.refund_admin", request_id=escape(topup.id)),
                            parse_mode="HTML",
                        )
                    except TelegramAPIError:
                        logger.warning("Top-up refund notification failed")
                try:
                    await bot.send_message(
                        topup.telegram_id, t("wallet.refund_user_notice")
                    )
                except TelegramAPIError:
                    logger.warning("Top-up refund notification failed")
            return
    try:
        order = orders.get_by_payment_id(payment.telegram_payment_charge_id)
        if order is None:
            logger.warning("Telegram Stars refund has no matching order")
            return
        if (
            payment.currency != "XTR"
            or payment.total_amount != order.amount_minor
        ):
            logger.warning("Telegram Stars refund details do not match the order")
            return
        changed = (
            wallet.refund_order(order.id)
            if wallet is not None
            else orders.mark_refunded(order.id)
        )
    except (OrderError, WalletError, sqlite3.Error):
        logger.warning("Telegram Stars refund could not be recorded")
        return
    if changed:
        for admin_id in settings.admin_telegram_ids:
            try:
                await bot.send_message(
                    admin_id,
                    t("plans.refund_admin", order_id=escape(order.id)),
                    parse_mode="HTML",
                )
            except TelegramAPIError:
                logger.warning("Stars refund notification delivery failed")
        try:
            await bot.send_message(
                order.telegram_id,
                t("plans.refund_user_notice"),
            )
        except TelegramAPIError:
            logger.warning("Stars refund notification delivery failed")


@router.message(Command("paysupport"))
async def payment_support(
    message: Message,
    settings: Settings,
    orders: OrderStore,
    state: FSMContext | None = None,
) -> None:
    await _clear_state(state)
    if not _private_chat(message):
        await message.answer(t("alert.private_use_start"))
        return
    assert message.from_user is not None
    try:
        latest_order = orders.latest_for_user(message.from_user.id)
    except (OrderError, sqlite3.Error):
        logger.warning("Payment support could not load the user's order")
        await message.answer(t("plans.payment_support_unavailable"))
        return
    order_ref = latest_order.id if latest_order is not None else t("common.dash")
    await message.answer(
        f"<b>{t('plans.payment_support_title')}</b>\n\n"
        f"{t('plans.payment_support', order_id=escape(order_ref))}",
        reply_markup=help_keyboard(support_url(settings.support_contact)),
        parse_mode="HTML",
    )


@router.message(Command("refund_star"))
async def refund_stars_payment(
    message: Message,
    bot: Bot,
    panel: RemnawaveClient,
    settings: Settings,
    orders: OrderStore,
    wallet: WalletStore | None = None,
) -> None:
    if not _is_admin(message, settings):
        await message.answer(t("admin.command_unavailable"))
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2]:
        await message.answer("/refund_star <telegram_id> <charge_id>")
        return
    telegram_id = int(parts[1])
    if telegram_id <= 0:
        await message.answer("/refund_star <telegram_id> <charge_id>")
        return
    try:
        order = orders.get_by_payment_id(parts[2])
        if order is None or order.telegram_id != telegram_id:
            await message.answer(t("plans.invalid_invoice"))
            return
        user = await _load_user(telegram_id, panel)
        if user is not None and user.status == "ACTIVE":
            await message.answer(t("plans.refund_active_user"))
            return
        if not orders.claim_refund(order.id):
            await message.answer(t("plans.refund_unavailable"))
            return
    except (OrderError, PanelError, sqlite3.Error):
        logger.warning("Stars refund pre-check failed")
        await message.answer(t("plans.refund_unavailable"))
        return
    try:
        refunded = await bot.refund_star_payment(
            user_id=telegram_id,
            telegram_payment_charge_id=parts[2],
        )
        if not refunded:
            await message.answer(t("plans.refund_unavailable"))
            return
        changed = (
            wallet.refund_order(order.id)
            if wallet is not None
            else orders.mark_refunded(order.id)
        )
    except (TelegramAPIError, OrderError, WalletError, sqlite3.Error):
        logger.warning("Stars refund requires manual review")
        await message.answer(t("plans.refund_unavailable"))
        return
    await message.answer(
        t("plans.refund_done", order_id=escape(order.id)),
        parse_mode="HTML",
    )
    if changed:
        try:
            await bot.send_message(
                telegram_id,
                t("plans.refund_user_notice"),
            )
        except TelegramAPIError:
            logger.warning("Stars refund notification delivery failed")


@router.message(Command("recover_order"))
async def recover_paid_order(
    message: Message,
    bot: Bot,
    panel: RemnawaveClient,
    settings: Settings,
    plans: tuple[Plan, ...],
    orders: OrderStore,
    wallet: WalletStore | None = None,
) -> None:
    if not _is_admin(message, settings) or not _private_chat(message):
        await message.answer(t("admin.command_unavailable"))
        return
    parts = (message.text or "").split(maxsplit=3)
    if len(parts) < 3:
        await message.answer(t("plans.recovery_usage"))
        return
    order_id, action = parts[1], parts[2]
    try:
        order = orders.get(order_id)
    except (OrderError, sqlite3.Error):
        await message.answer(t("plans.recovery_unavailable"))
        return
    if (
        order.status != FULFILLMENT_REVIEW
        or order.provider != "telegram_stars"
        or not order.provider_payment_id
    ):
        await message.answer(t("plans.recovery_unavailable"))
        return
    if action == "refund" and len(parts) == 3:
        try:
            user = await _load_user(order.telegram_id, panel)
            if user is not None and user.status == "ACTIVE":
                await message.answer(t("plans.refund_active_user"))
                return
            if not orders.claim_refund(order.id):
                await message.answer(t("plans.recovery_unavailable"))
                return
            refunded = await bot.refund_star_payment(
                user_id=order.telegram_id,
                telegram_payment_charge_id=order.provider_payment_id,
            )
            if not refunded:
                await message.answer(t("plans.refund_unavailable"))
                return
            changed = (
                wallet.refund_order(order.id)
                if wallet is not None
                else orders.mark_refunded(order.id)
            )
        except (OrderError, PanelError, WalletError, TelegramAPIError, sqlite3.Error):
            logger.warning("Stars recovery refund requires manual reconciliation")
            await message.answer(t("plans.refund_unavailable"))
            return
        if not changed:
            await message.answer(t("plans.recovery_unavailable"))
            return
        await message.answer(
            t("plans.refund_done", order_id=escape(order.id)),
            parse_mode="HTML",
        )
        try:
            await bot.send_message(
                order.telegram_id,
                t("plans.refund_user_notice"),
            )
        except TelegramAPIError:
            logger.warning("Stars refund notification delivery failed")
        return
    if (
        action != "retry"
        or len(parts) != 4
        or parts[3] != "NOT_FULFILLED"
        or wallet is None
    ):
        await message.answer(t("plans.recovery_usage"))
        return
    plan = _stars_plan(
        plans,
        order.plan_code,
        include_traffic=settings.traffic_plans_enabled,
    )
    order_term = None if plan is None else _order_plan_term(plan, order)
    if (
        plan is None
        or order_term is None
        or plan.currency != order.currency
        or wallet.payment_transaction(order.id) is None
    ):
        await message.answer(t("plans.recovery_unavailable"))
        return
    await message.answer(t("plans.recovery_retry_warning"))
    if not orders.claim_recovery_retry(order.id):
        await message.answer(t("plans.recovery_unavailable"))
        return
    try:
        updated = await _fulfill_order(
            order.telegram_id, plan, order_term, panel, settings
        )
        if not orders.mark_fulfilled(
            order.id,
            payment_completer=wallet.complete_order_payment_locked,
        ):
            raise OrderStateError("Stars order fulfillment state changed")
        transaction = wallet.payment_transaction(order.id)
        if transaction is None:
            raise WalletError("Successful Stars payment is missing")
    except (OrderError, PanelError, WalletError, sqlite3.Error):
        logger.warning("Paid order recovery requires manual review")
        try:
            orders.mark_fulfillment_review(order.id)
        except (OrderError, sqlite3.Error):
            logger.warning("Paid order review state could not be saved")
        try:
            wallet.complete_order_payment(order.id, status="REVIEW")
        except (WalletError, sqlite3.Error):
            logger.warning("Recovered payment review state could not be saved")
        await message.answer(t("plans.recovery_retry_failed"))
        return
    if order.rub_price_minor is not None:
        try:
            reward = wallet.reward_referral_purchase(
                order.id, order.telegram_id, order.rub_price_minor
            )
        except (WalletError, sqlite3.Error):
            logger.warning("Recovered referral reward requires reconciliation")
        else:
            try:
                await _notify_referral_reward(bot, reward, topup=False)
            except TelegramAPIError:
                logger.warning("Recovered referral reward notification failed")
    try:
        await bot.send_message(
            order.telegram_id,
            _payment_success_text(plan, order_term, updated)
            + _receipt_for_transaction(
                transaction, operation=t("wallet.receipt_subscription")
            ),
            parse_mode="HTML",
        )
    except TelegramAPIError:
        logger.warning("Recovered purchase notification delivery failed")
    await message.answer(
        t("plans.recovery_retry_done", order_id=escape(order.id)),
        parse_mode="HTML",
    )


@router.message(F.text.startswith("/"))
async def unknown_command(message: Message, settings: Settings) -> None:
    if _private_chat(message):
        await message.answer(
            t("alert.unknown_command"),
            reply_markup=home_keyboard(_is_admin(message, settings)),
        )
    else:
        await message.answer(t("alert.private_use_start"))


def _new_bot(token: str) -> Bot:
    try:
        return Bot(token=token)
    except TokenValidationError:
        logger.error("BOT_TOKEN has an invalid format")
        raise SystemExit(2) from None


async def main() -> None:
    settings = Settings.from_env()
    plans = load_plans(settings.plans_file)
    bot = _new_bot(settings.bot_token)
    panel = RemnawaveClient(
        settings.panel_api_url,
        settings.panel_api_token,
        ca_bundle=settings.panel_ca_bundle,
    )
    try:
        prefs = PreferencesStore(settings.state_db_path or "bot-state.sqlite3")
    except PreferencesError as error:
        logger.error("Cannot start without a writable state database: %s", error)
        await panel.close()
        await bot.session.close()
        raise SystemExit(2) from error
    try:
        tickets = TicketStore(settings.state_db_path or "bot-state.sqlite3")
    except TicketStoreError as error:
        logger.error("Cannot start without support ticket storage: %s", error)
        await panel.close()
        await bot.session.close()
        raise SystemExit(2) from error
    try:
        promotions = PromotionStore(settings.state_db_path or "bot-state.sqlite3")
    except PromotionStoreError as error:
        logger.error("Cannot start without promotion storage: %s", error)
        await panel.close()
        await bot.session.close()
        raise SystemExit(2) from error
    try:
        orders = OrderStore(settings.state_db_path or "bot-state.sqlite3")
    except (OSError, sqlite3.Error) as error:
        logger.error("Cannot start without payment order storage: %s", error)
        await panel.close()
        await bot.session.close()
        raise SystemExit(2) from error
    try:
        wallet = WalletStore(settings.state_db_path or "bot-state.sqlite3")
    except (OSError, WalletError, sqlite3.Error) as error:
        logger.error("Cannot start without wallet storage: %s", error)
        await panel.close()
        await bot.session.close()
        raise SystemExit(2) from error
    try:
        broadcasts = BroadcastStore(settings.state_db_path or "bot-state.sqlite3")
    except (OSError, BroadcastError, sqlite3.Error) as error:
        logger.error("Cannot start without broadcast storage: %s", error)
        await panel.close()
        await bot.session.close()
        raise SystemExit(2) from error
    platega = (
        PlategaClient(settings.platega_merchant_id, settings.platega_secret)
        if settings.platega_enabled
        and settings.platega_merchant_id is not None
        and settings.platega_secret is not None
        else None
    )
    try:
        payments = PaymentStore(settings.state_db_path or "bot-state.sqlite3")
    except sqlite3.Error as error:
        logger.error("Cannot start without payment tracking storage")
        if platega is not None:
            await platega.close()
        await panel.close()
        await bot.session.close()
        raise SystemExit(2) from error
    platega_runner: web.AppRunner | None = None
    platega_payment_task: asyncio.Task | None = None
    payment_notice_task: asyncio.Task | None = None
    configure_screens(prefs)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.errors.register(_handle_stale_callback_query_error)
    dispatcher.update.outer_middleware(LanguageMiddleware(prefs))
    dispatcher.include_router(router)
    reminder_task = asyncio.create_task(
        reminder_loop(bot, panel, prefs), name="expiry-reminders"
    )
    trial_nudge_task = asyncio.create_task(
        trial_nudge_loop(bot, panel, prefs), name="trial-onboarding-reminders"
    )
    connection_task = asyncio.create_task(
        connection_log_loop(bot, settings, panel, prefs),
        name="connection-logs",
    )
    ticket_followup_task = asyncio.create_task(
        ticket_close_prompt_loop(bot, tickets, prefs),
        name="ticket-close-prompts",
    )
    ticket_notification_task = asyncio.create_task(
        ticket_notification_loop(bot, tickets, settings),
        name="ticket-notifications",
    )
    payment_review_task = asyncio.create_task(
        payment_review_loop(bot, settings, orders, wallet),
        name="paid-order-review",
    )
    free_access_task = asyncio.create_task(
        free_access_loop(bot, panel, prefs, settings),
        name="free-daily-access",
    )
    traffic_notification_task = asyncio.create_task(
        traffic_notification_loop(bot, panel, prefs),
        name="traffic-threshold-notifications",
    )
    channel_post_task = asyncio.create_task(
        channel_post_preview_loop(bot, tickets, promotions),
        name="channel-post-previews",
    )
    try:
        if platega is not None:
            platega_app = _platega_web_application(
                bot=bot,
                panel=panel,
                settings=settings,
                plans=plans,
                orders=orders,
                wallet=wallet,
                client=platega,
                payments=payments,
            )
            platega_runner = web.AppRunner(platega_app, access_log=None)
            await platega_runner.setup()
            await web.TCPSite(platega_runner, "0.0.0.0", 8081).start()
            platega_payment_task = asyncio.create_task(
                platega_payment_loop(
                    bot=bot,
                    panel=panel,
                    settings=settings,
                    plans=plans,
                    orders=orders,
                    wallet=wallet,
                    payments=payments,
                    client=platega,
                ),
                name="platega-payment-reconciliation",
            )
            payment_notice_task = asyncio.create_task(
                payment_notice_loop(bot, payments), name="platega-payment-notifications",
            )
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Главное меню"),
                BotCommand(command="connect", description="Подключить VPN"),
                BotCommand(command="status", description="Проверить доступ"),
                BotCommand(command="account", description="Личный кабинет"),
                BotCommand(command="ping", description="Состояние бота"),
                BotCommand(command="guides", description="Инструкции"),
                BotCommand(command="plans", description="Тарифы"),
                BotCommand(command="devices", description="Мои устройства"),
                BotCommand(command="stats", description="Статистика"),
                BotCommand(command="settings", description="Настройки"),
                BotCommand(command="referrals", description="Приглашения"),
                BotCommand(command="cooperation", description="Сотрудничество"),
                BotCommand(command="about", description="О сервисе"),
                BotCommand(command="paysupport", description="Помощь с оплатой"),
                BotCommand(command="help", description="Помощь"),
            ]
        )
        await bot.get_me()
        await dispatcher.start_polling(
            bot,
            panel=panel,
            settings=settings,
            plans=plans,
            prefs=prefs,
            tickets=tickets,
            promotions=promotions,
            orders=orders,
            wallet=wallet,
            broadcasts=broadcasts,
            platega=platega,
            payments=payments,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    except TelegramAPIError as error:
        logger.error("Telegram API operation failed (%s)", type(error).__name__)
        raise SystemExit(1) from None
    finally:
        reminder_task.cancel()
        connection_task.cancel()
        ticket_followup_task.cancel()
        ticket_notification_task.cancel()
        payment_review_task.cancel()
        free_access_task.cancel()
        traffic_notification_task.cancel()
        channel_post_task.cancel()
        trial_nudge_task.cancel()
        if platega_payment_task is not None:
            platega_payment_task.cancel()
        if payment_notice_task is not None:
            payment_notice_task.cancel()
        for task in (
            reminder_task,
            trial_nudge_task,
            connection_task,
            ticket_followup_task,
            ticket_notification_task,
            payment_review_task,
            free_access_task,
            traffic_notification_task,
            channel_post_task,
            *((platega_payment_task,) if platega_payment_task is not None else ()),
            *((payment_notice_task,) if payment_notice_task is not None else ()),
        ):
            try:
                await task
            except asyncio.CancelledError:
                pass
        if platega_runner is not None:
            await platega_runner.cleanup()
        if platega is not None:
            await platega.close()
        await panel.close()
        await bot.session.close()


async def check_telegram() -> None:
    settings = Settings.from_env(require_panel=False)
    plans = load_plans(settings.plans_file)
    bot = _new_bot(settings.bot_token)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)
    try:
        await bot.get_me()
        handlers = (
            len(router.message.handlers)
            + len(router.callback_query.handlers)
            + len(admin_router.message.handlers)
            + len(admin_router.callback_query.handlers)
        )
        print(
            "Telegram Bot API: OK; "
            f"handlers registered: {handlers}; plans: {len(plans)}; "
            "polling not started."
        )
    except TelegramAPIError as error:
        logger.error("Telegram API operation failed (%s)", type(error).__name__)
        raise SystemExit(1) from None
    finally:
        await bot.session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check-telegram",
        action="store_true",
        help="validate the Telegram token and handlers without starting polling",
    )
    arguments = parser.parse_args()
    try:
        asyncio.run(check_telegram() if arguments.check_telegram else main())
    except ConfigurationError as error:
        logger.error("Configuration error: %s", error)
        raise SystemExit(2) from error
