from __future__ import annotations

import asyncio
import logging
import shlex
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html import escape
from typing import cast
from urllib.parse import urlsplit

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from jopesvpn.config import Settings
from jopesvpn.connection_logs import POLL_INTERVAL_SECONDS, baseline
from jopesvpn.i18n import t, tn
from jopesvpn.panel import (
    PanelAccessDenied,
    PanelDataError,
    PanelError,
    PanelNotFound,
    PanelUser,
    RemnawaveClient,
)
from jopesvpn.plans import format_rubles
from jopesvpn.preferences import PreferencesError, PreferencesStore
from jopesvpn.screens import answer_screen, edit_screen
from jopesvpn.timezones import (
    ADMIN_UTC_OFFSET,
    DEFAULT_UTC_OFFSET,
    format_datetime,
)
from jopesvpn.ui import (
    ADMIN_USERS_PER_PAGE,
    DEVICES_PER_PAGE,
    admin_devices_keyboard,
    admin_devices_text,
    admin_documents_keyboard,
    admin_keyboard,
    admin_logs_keyboard,
    admin_logs_text,
    admin_user_card_text,
    admin_user_keyboard,
    admin_users_keyboard,
    admin_users_text,
    back_keyboard,
    broadcast_keyboard,
    confirm_extend_keyboard,
    confirm_grant_keyboard,
    confirm_keyboard,
    format_bytes,
    status_text,
    tariff_text,
    wallet_admin_confirm_keyboard,
    wallet_admin_keyboard,
)
from jopesvpn.wallet import MAX_TOPUP_MINOR, WalletError, WalletStore

logger = logging.getLogger(__name__)
router = Router(name="admin")
_mutation_lock = asyncio.Lock()
ADMIN_TAG = "ADMIN"


class AdminFlow(StatesGroup):
    find_user = State()
    grant_user = State()
    grant_days = State()
    grant_confirm = State()
    extend_user = State()
    extend_days = State()
    extend_confirm = State()
    action_confirm = State()


class BroadcastFlow(StatesGroup):
    message = State()


class DocumentsFlow(StatesGroup):
    url = State()


class WalletAdminFlow(StatesGroup):
    adjust_input = State()
    adjust_confirm = State()
    bonus_input = State()
    bonus_confirm = State()
    payment_lookup = State()


def _private_admin(message: Message, settings: Settings) -> bool:
    return (
        message.chat.type == "private"
        and message.from_user is not None
        and message.from_user.id in settings.admin_telegram_ids
    )


def _admin_callback(callback: CallbackQuery, settings: Settings) -> bool:
    return (
        callback.message is not None
        and callback.message.chat.type == "private"
        and callback.from_user.id in settings.admin_telegram_ids
    )


def _parse_admin_rubles(raw: str) -> int | None:
    try:
        amount = Decimal(raw.strip().replace(",", "."))
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount * 100 != (amount * 100).to_integral_value():
        return None
    return int(amount * 100)


@router.message(WalletAdminFlow.adjust_input)
async def receive_wallet_adjustment(
    message: Message,
    settings: Settings,
    state: FSMContext,
) -> None:
    if not _private_admin(message, settings):
        await state.clear()
        await message.answer(t("admin.command_unavailable"))
        return
    if (message.text or "").strip().casefold() in {"/cancel", "отмена"}:
        await state.clear()
        await message.answer(t("admin.wallet_cancelled"), reply_markup=admin_keyboard())
        return
    parts = (message.text or "").strip().split(maxsplit=2)
    if len(parts) < 3:
        await message.answer(t("admin.wallet_adjust_prompt"))
        return
    try:
        telegram_id = int(parts[0])
    except ValueError:
        await message.answer(t("admin.wallet_adjust_invalid"))
        return
    amount_minor = _parse_admin_rubles(parts[1])
    reason = parts[2].strip()
    if telegram_id <= 0 or amount_minor in (None, 0) or not reason:
        await message.answer(t("admin.wallet_adjust_invalid"))
        return
    if abs(amount_minor) > MAX_TOPUP_MINOR or len(reason) > 300:
        await message.answer(t("admin.wallet_adjust_invalid"))
        return
    assert message.from_user is not None
    operation_id = uuid.uuid4().hex
    await state.update_data(
        admin_id=message.from_user.id,
        adjustment_user_id=telegram_id,
        adjustment_amount_minor=amount_minor,
        adjustment_reason=reason,
        operation_id=operation_id,
    )
    await state.set_state(WalletAdminFlow.adjust_confirm)
    await message.answer(
        t(
            "admin.wallet_adjust_preview",
            telegram_id=telegram_id,
            amount=format_rubles(abs(amount_minor)),
            direction="зачислить" if amount_minor > 0 else "списать",
            reason=escape(reason),
        ),
        reply_markup=wallet_admin_confirm_keyboard("adjust"),
        parse_mode="HTML",
    )


@router.message(WalletAdminFlow.bonus_input)
async def receive_wallet_bonus(
    message: Message,
    settings: Settings,
    state: FSMContext,
) -> None:
    if not _private_admin(message, settings):
        await state.clear()
        await message.answer(t("admin.command_unavailable"))
        return
    if (message.text or "").strip().casefold() in {"/cancel", "отмена"}:
        await state.clear()
        await message.answer(t("admin.wallet_cancelled"), reply_markup=admin_keyboard())
        return
    parts = (message.text or "").strip().split()
    if len(parts) != 2 or parts[0].casefold() not in {"fixed", "percent"}:
        await message.answer(t("admin.wallet_bonus_prompt"))
        return
    mode = parts[0].casefold()
    if mode == "fixed":
        value = _parse_admin_rubles(parts[1])
        if value is None:
            await message.answer(t("admin.wallet_bonus_invalid"))
            return
        description = format_rubles(value)
    else:
        try:
            value = int(parts[1])
        except ValueError:
            await message.answer(t("admin.wallet_bonus_invalid"))
            return
        description = f"{value}%"
    if value <= 0 or (mode == "percent" and value > 100):
        await message.answer(t("admin.wallet_bonus_invalid"))
        return
    assert message.from_user is not None
    await state.update_data(
        admin_id=message.from_user.id,
        bonus_mode=mode,
        bonus_value=value,
        bonus_description=description,
    )
    await state.set_state(WalletAdminFlow.bonus_confirm)
    await message.answer(
        t("admin.wallet_bonus_preview", value=description),
        reply_markup=wallet_admin_confirm_keyboard("bonus"),
    )


@router.message(WalletAdminFlow.payment_lookup)
async def receive_wallet_payment_lookup(
    message: Message,
    settings: Settings,
    state: FSMContext,
    wallet: WalletStore | None = None,
) -> None:
    if not _private_admin(message, settings):
        await state.clear()
        await message.answer(t("admin.command_unavailable"))
        return
    payment_id = (message.text or "").strip()
    if payment_id.casefold() in {"/cancel", "отмена"}:
        await state.clear()
        await message.answer(t("admin.wallet_cancelled"), reply_markup=admin_keyboard())
        return
    if wallet is None or not payment_id:
        await message.answer(t("wallet.unavailable"))
        return
    try:
        payment = wallet.payment_transaction(payment_id)
    except WalletError:
        logger.warning("Admin payment lookup failed")
        await message.answer(t("admin.wallet_operation_failed"))
        return
    if payment is None:
        await message.answer(t("admin.wallet_payment_not_found"))
        return
    amount = (
        f"{payment.amount_paid_minor} ⭐ "
        f"({format_rubles(payment.rub_amount_minor)})"
        if payment.currency == "XTR"
        else format_rubles(payment.rub_amount_minor)
    )
    await message.answer(
        t(
            "admin.wallet_payment_found",
            operation_id=escape(payment.id),
            telegram_id=payment.telegram_id,
            amount=amount,
            method=escape(payment.method),
            purpose=escape(payment.purpose),
            status=escape(payment.status),
            created_at=escape(payment.created_at),
        ),
        parse_mode="HTML",
    )


@router.callback_query(
    F.data.in_(
        {
            "admin:wallet:confirm-adjust",
            "admin:wallet:confirm-bonus",
            "admin:wallet:cancel",
        }
    )
)
async def confirm_wallet_admin_action(
    callback: CallbackQuery,
    settings: Settings,
    state: FSMContext,
    wallet: WalletStore | None = None,
) -> None:
    if not _admin_callback(callback, settings):
        await callback.answer(t("admin.command_unavailable"), show_alert=True)
        return
    if callback.data == "admin:wallet:cancel":
        await state.clear()
        await callback.answer()
        await _edit(callback, t("admin.wallet_cancelled"), admin_keyboard())
        return
    if wallet is None:
        await callback.answer(t("wallet.unavailable"), show_alert=True)
        return
    data = await state.get_data()
    if data.get("admin_id") != callback.from_user.id:
        await state.clear()
        await callback.answer(t("admin.wallet_invalid"), show_alert=True)
        return
    try:
        if callback.data == "admin:wallet:confirm-adjust":
            if await state.get_state() != WalletAdminFlow.adjust_confirm.state:
                await callback.answer(t("admin.wallet_invalid"), show_alert=True)
                return
            applied = wallet.adjust_balance(
                operation_id=data["operation_id"],
                admin_telegram_id=callback.from_user.id,
                telegram_id=data["adjustment_user_id"],
                amount_minor=data["adjustment_amount_minor"],
                reason=data["adjustment_reason"],
            )
            result = t(
                "admin.wallet_adjust_done",
                telegram_id=data["adjustment_user_id"],
                amount=format_rubles(abs(data["adjustment_amount_minor"])),
                operation_id=data["operation_id"],
            ) if applied else t("admin.wallet_already_applied")
        else:
            if await state.get_state() != WalletAdminFlow.bonus_confirm.state:
                await callback.answer(t("admin.wallet_invalid"), show_alert=True)
                return
            wallet.configure_bonus(
                callback.from_user.id, data["bonus_mode"], data["bonus_value"]
            )
            result = t(
                "admin.wallet_bonus_done", value=data["bonus_description"]
            )
    except (WalletError, ValueError, KeyError, TypeError):
        logger.warning("Admin wallet operation could not be applied")
        await callback.answer(t("admin.wallet_operation_failed"), show_alert=True)
        return
    await state.clear()
    await callback.answer()
    await _edit(callback, result, admin_keyboard())


@router.message(BroadcastFlow.message)
async def receive_broadcast_message(
    message: Message,
    bot: Bot,
    settings: Settings,
    state: FSMContext,
) -> None:
    if not _private_admin(message, settings):
        await state.clear()
        await message.answer(t("admin.command_unavailable"))
        return
    if message.text and message.text.strip().lower() in {"/cancel", "отмена"}:
        await state.clear()
        await message.answer(t("admin.broadcast_cancelled"))
        return
    await state.update_data(
        broadcast_chat_id=message.chat.id,
        broadcast_message_id=message.message_id,
    )
    try:
        await bot.copy_message(
            chat_id=message.chat.id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            reply_markup=broadcast_keyboard(),
        )
    except TelegramAPIError:
        await state.clear()
        logger.warning("Broadcast preview could not be sent")
        await message.answer(t("admin.broadcast_preview_failed"))


@router.message(DocumentsFlow.url)
async def receive_document_url(
    message: Message,
    settings: Settings,
    state: FSMContext,
    prefs: PreferencesStore | None = None,
) -> None:
    if not _private_admin(message, settings):
        await state.clear()
        await message.answer(t("admin.command_unavailable"))
        return
    data = await state.get_data()
    document = data.get("document")
    if document not in {"privacy", "agreement"}:
        await state.clear()
        await message.answer(t("admin.stale_button"))
        return
    value = (message.text or "").strip()
    if value.lower() in {"/cancel", "отмена"}:
        await state.clear()
        await message.answer(t("docs.admin_cancelled"))
        return
    if value.lower() == "/clear":
        url = None
    else:
        try:
            parsed = urlsplit(value)
            parsed.port
            valid = (
                value.isascii()
                and len(value) <= 2048
                and parsed.scheme == "https"
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and not any(char.isspace() for char in value)
            )
        except ValueError:
            valid = False
        if not valid:
            await message.answer(t("docs.admin_bad_url"))
            return
        url = value
    if prefs is None:
        await state.clear()
        await message.answer(t("settings.storage_temporary"))
        return
    try:
        prefs.set_document_url(document, url)
    except (PreferencesError, ValueError):
        logger.warning("Document link could not be saved")
        await state.clear()
        await message.answer(t("settings.storage_temporary"))
        return
    await state.clear()
    await message.answer(t("docs.admin_saved"), reply_markup=admin_keyboard())


async def _send_broadcast(
    callback: CallbackQuery,
    bot: Bot,
    settings: Settings,
    state: FSMContext,
    prefs: PreferencesStore | None,
) -> None:
    data = await state.get_data()
    source_chat_id = data.get("broadcast_chat_id")
    source_message_id = data.get("broadcast_message_id")
    await state.clear()
    if (
        not isinstance(source_chat_id, int)
        or not isinstance(source_message_id, int)
        or callback.message is None
        or prefs is None
    ):
        await callback.answer(t("admin.broadcast_unavailable"), show_alert=True)
        return
    try:
        recipients = [
            telegram_id
            for telegram_id in prefs.known_users()
            if telegram_id not in settings.admin_telegram_ids
        ]
    except PreferencesError:
        logger.warning("Broadcast recipients could not be loaded")
        await callback.answer(t("admin.broadcast_unavailable"), show_alert=True)
        return

    await callback.answer()
    delivered = 0
    failed = 0
    for telegram_id in recipients:
        try:
            await bot.copy_message(
                chat_id=telegram_id,
                from_chat_id=source_chat_id,
                message_id=source_message_id,
            )
        except TelegramRetryAfter as error:
            await asyncio.sleep(error.retry_after)
            try:
                await bot.copy_message(
                    chat_id=telegram_id,
                    from_chat_id=source_chat_id,
                    message_id=source_message_id,
                )
            except TelegramAPIError:
                failed += 1
            else:
                delivered += 1
        except TelegramAPIError:
            failed += 1
        else:
            delivered += 1
        await asyncio.sleep(0.05)
    await callback.message.answer(
        t(
            "admin.broadcast_result",
            delivered=delivered,
            failed=failed,
            total=len(recipients),
        )
    )


def _arguments(message: Message) -> list[str]:
    if not message.text:
        return []
    try:
        return shlex.split(message.text.partition(" ")[2])
    except ValueError:
        return []


def _expiry_text(
    user: PanelUser, offset: int = ADMIN_UTC_OFFSET
) -> str:
    if not user.expire_at:
        return t("time.unknown")
    try:
        expires = datetime.fromisoformat(user.expire_at.replace("Z", "+00:00"))
        if expires.tzinfo is None:
            return t("time.unknown")
        return format_datetime(expires, offset)
    except ValueError:
        return t("time.unknown")


def _user_offset(prefs: PreferencesStore | None, telegram_id: int) -> int:
    if prefs is not None:
        try:
            return prefs.timezone_offset(telegram_id, DEFAULT_UTC_OFFSET)
        except PreferencesError:
            logger.warning("User timezone for subscription notice is unavailable")
    return DEFAULT_UTC_OFFSET


async def _notify_user(bot: Bot, telegram_id: int, text: str) -> None:
    try:
        await bot.send_message(
            telegram_id,
            text,
            parse_mode="HTML",
        )
    except TelegramAPIError:
        logger.warning("Subscription change notification delivery failed")


def _panel_error_text(error: PanelError, not_found: str | None = None) -> str:
    if isinstance(error, PanelNotFound):
        return not_found or t("admin.user_not_found")
    if isinstance(error, PanelAccessDenied):
        return t("admin.api_denied")
    if isinstance(error, PanelDataError):
        return t("admin.data_unexpected")
    return t("admin.service_unavailable")


def _page_url(user: PanelUser, settings: Settings | None) -> str | None:
    if settings is None or user.status != "ACTIVE":
        return None
    try:
        return RemnawaveClient.subscription_page_url(
            user, settings.subscription_page_url
        )
    except PanelDataError:
        logger.warning("Subscription page URL could not be derived")
        return None


async def _report_panel_error(
    message: Message,
    error: PanelError,
    not_found: str | None = None,
) -> None:
    logger.warning("Panel request failed (%s)", type(error).__name__)
    await message.answer(
        _panel_error_text(error, not_found),
        reply_markup=back_keyboard("admin:home"),
    )


async def _edit(
    callback: CallbackQuery,
    text: str,
    markup,
) -> None:
    if callback.message is None:
        return
    await edit_screen(
        cast(Message, callback.message), text, markup, data=callback.data
    )


def _result_text(title: str, rows: list[str]) -> str:
    lines = [f"<b>{title}</b>", "", f"<blockquote>┌ <b>{t('common.summary')}</b>"]
    for index, row in enumerate(rows):
        branch = "└" if index == len(rows) - 1 else "├"
        lines.append(f"{branch} {row}")
    lines.append("</blockquote>")
    return "\n".join(lines)


def _menu_text() -> str:
    return (
        f"<b>{t('admin.title')}</b>\n\n"
        f"<blockquote>└ {t('admin.note')}</blockquote>"
    )


async def _menu(message: Message) -> None:
    await answer_screen(
        message,
        _menu_text(),
        admin_keyboard(),
        banner="admin",
    )


@router.message(Command("admin"))
async def admin_menu_command(
    message: Message,
    settings: Settings,
    state: FSMContext | None = None,
) -> None:
    if state is not None:
        await state.clear()
    if not _private_admin(message, settings):
        await message.answer(t("admin.command_unavailable"))
        return
    await _menu(message)


async def _fallback_users(
    panel: RemnawaveClient,
    prefs: PreferencesStore | None,
) -> list[PanelUser]:
    if prefs is None:
        raise PanelAccessDenied("No preference store for user fallback")
    try:
        telegram_ids = prefs.known_users()
    except PreferencesError as error:
        raise PanelAccessDenied("Preferences are unavailable") from error
    users: list[PanelUser] = []
    for telegram_id in telegram_ids:
        try:
            users.append(await panel.get_user_by_telegram_id(telegram_id))
        except PanelNotFound:
            continue
    return users


async def _collect_users(
    panel: RemnawaveClient,
    prefs: PreferencesStore | None = None,
) -> tuple[list[PanelUser], int]:
    try:
        first = await panel.list_users(0, 1000)
    except PanelAccessDenied:
        users = await _fallback_users(panel, prefs)
        return users, len(users)
    users = list(first.users)
    total = first.total
    start = len(users)
    guard = 0
    while start < total and guard < 9:
        guard += 1
        page = await panel.list_users(start, 1000)
        if not page.users:
            break
        users.extend(page.users)
        start += len(page.users)
    return users, total


def _service_status_text(
    panel_healthy: bool,
    nodes: list[dict],
    *,
    users: list[PanelUser] | None,
    users_total: int | None,
    users_error: bool = False,
) -> str:
    online_nodes = sum(1 for node in nodes if node.get("isConnected"))
    node_rows = [
        t(
            "admin.node_online" if node["isConnected"] else "admin.node_offline",
            name=escape(node["name"]),
        )
        for node in nodes
    ] or [t("admin.nodes_empty")]

    service_rows = [
        t("admin.panel_health") + ": "
        + (t("admin.health_ok") if panel_healthy else t("admin.health_fail")),
        f"{t('admin.nodes_online')}: <b>{online_nodes} / {len(nodes)}</b>",
    ]
    blocks = [
        f"<b>{t('admin.status_title')}</b>",
        f"<blockquote>┌ 🛡 <b>{t('admin.service_box')}</b>\n"
        + "\n".join(
            f"{'└' if index == len(service_rows) - 1 else '├'} {row}"
            for index, row in enumerate(service_rows)
        )
        + "</blockquote>",
    ]

    if users_error or users is None or users_total is None:
        user_rows = [t("admin.data_unexpected")]
    else:
        counts = {
            "ACTIVE": 0,
            "DISABLED": 0,
            "EXPIRED": 0,
            "LIMITED": 0,
        }
        for user in users:
            if user.status in counts:
                counts[user.status] += 1
        user_rows = [
            t("admin.users_total_line", count=users_total),
            t("admin.users_active", count=counts["ACTIVE"]),
            t("admin.users_disabled", count=counts["DISABLED"]),
            t("admin.users_expired", count=counts["EXPIRED"]),
            t("admin.users_limited", count=counts["LIMITED"]),
        ]
        if all(user.traffic_used_bytes is not None for user in users):
            traffic = sum(
                user.traffic_used_bytes
                for user in users
                if user.traffic_used_bytes is not None
            )
            user_rows.append(
                t("admin.traffic_used", value=f"<b>{format_bytes(traffic)}</b>")
            )
    blocks.append(
        "<blockquote>┌ 👥 <b>" + t("admin.users_box") + "</b>\n"
        + "\n".join(
            f"{'└' if index == len(user_rows) - 1 else '├'} {row}"
            for index, row in enumerate(user_rows)
        )
        + "</blockquote>"
    )
    blocks.append(
        "<blockquote>┌ 🖥 <b>" + t("admin.nodes_box") + "</b>\n"
        + "\n".join(
            f"{'└' if index == len(node_rows) - 1 else '├'} {row}"
            for index, row in enumerate(node_rows)
        )
        + "</blockquote>"
    )
    blocks.append(t("admin.dashboard_note"))
    return "\n\n".join(blocks)


async def _status(
    message: Message,
    panel: RemnawaveClient,
    prefs: PreferencesStore | None = None,
) -> None:
    try:
        panel_healthy = await panel.health()
        nodes = await panel.get_nodes()
    except PanelError as error:
        await _report_panel_error(message, error)
        return
    users: list[PanelUser] | None = None
    users_total: int | None = None
    users_error = False
    try:
        users, users_total = await _collect_users(panel, prefs)
    except PanelError as error:
        logger.warning("Panel user list failed (%s)", type(error).__name__)
        users_error = True
    await answer_screen(
        message,
        _service_status_text(
            panel_healthy,
            nodes,
            users=users,
            users_total=users_total,
            users_error=users_error,
        ),
        admin_keyboard(),
        banner="admin",
    )


@router.message(Command("admin_status"))
async def admin_status(
    message: Message,
    panel: RemnawaveClient,
    settings: Settings,
    state: FSMContext | None = None,
    prefs: PreferencesStore | None = None,
) -> None:
    if state is not None:
        await state.clear()
    if not _private_admin(message, settings):
        await message.answer(t("admin.command_unavailable"))
        return
    await _status(message, panel, prefs)


def _target_id(message: Message) -> int:
    args = _arguments(message)
    if not args or not args[0].isdigit():
        raise ValueError("Telegram ID is required")
    result = int(args[0])
    if not 0 < result <= 9_223_372_036_854_775_807:
        raise ValueError("Telegram ID must be positive")
    return result


def _display_names(
    prefs: PreferencesStore | None,
) -> dict[int, str]:
    if prefs is None:
        return {}
    try:
        return prefs.display_names()
    except PreferencesError:
        logger.warning("User names could not be loaded")
        return {}


def _display_usernames(
    prefs: PreferencesStore | None,
) -> dict[int, str]:
    if prefs is None:
        return {}
    try:
        return prefs.display_usernames()
    except PreferencesError:
        logger.warning("Usernames could not be loaded")
        return {}


async def _lookup(
    message: Message,
    panel: RemnawaveClient,
    telegram_id: int,
    *,
    page: int | None = None,
    prefs: PreferencesStore | None = None,
    settings: Settings | None = None,
    error_back: str = "admin:home",
) -> bool:
    try:
        user = await panel.get_user_by_telegram_id(telegram_id)
    except PanelError as error:
        logger.warning("Panel user lookup failed (%s)", type(error).__name__)
        await message.answer(
            _panel_error_text(error, t("admin.user_not_found")),
            reply_markup=back_keyboard(error_back),
        )
        return False
    name = _display_names(prefs).get(telegram_id)
    devices_count = None
    try:
        devices_count = len(await panel.get_user_devices(user))
    except (PanelError, ValueError) as error:
        logger.warning("Panel device request failed (%s)", type(error).__name__)
    await answer_screen(
        message,
        admin_user_card_text(
            user,
            devices_count=devices_count,
            name=name,
            telegram_id=telegram_id,
        ),
        admin_user_keyboard(
            telegram_id,
            user.status,
            page=page,
            page_url=_page_url(user, settings),
            username=_display_usernames(prefs).get(telegram_id),
        ),
        banner="admin_users",
    )
    return True


async def _user_page(
    panel: RemnawaveClient,
    page: int,
    prefs: PreferencesStore | None = None,
) -> tuple[list[PanelUser], int, int, int]:
    page = max(page, 0)
    try:
        result = await panel.list_users(
            page * ADMIN_USERS_PER_PAGE, ADMIN_USERS_PER_PAGE
        )
        users = result.users
        total = result.total
    except PanelAccessDenied:
        all_users = await _fallback_users(panel, prefs)
        total = len(all_users)
        start = page * ADMIN_USERS_PER_PAGE
        users = all_users[start : start + ADMIN_USERS_PER_PAGE]
    pages = max(1, (total + ADMIN_USERS_PER_PAGE - 1) // ADMIN_USERS_PER_PAGE)
    if page >= pages:
        page = pages - 1
        users, total, _, _ = await _user_page(panel, page, prefs)
    return users, total, page, pages


async def _users_screen(
    callback: CallbackQuery,
    panel: RemnawaveClient,
    page: int,
    prefs: PreferencesStore | None = None,
) -> None:
    try:
        users, total, page, pages = await _user_page(panel, page, prefs)
    except PanelError as error:
        logger.warning("Panel user list failed (%s)", type(error).__name__)
        await _edit(callback, _panel_error_text(error), back_keyboard("admin:home"))
        return
    names = _display_names(prefs)
    await _edit(
        callback,
        admin_users_text(users, total, page, pages),
        admin_users_keyboard(users, page, pages, names),
    )


async def _user_card(
    callback: CallbackQuery,
    panel: RemnawaveClient,
    telegram_id: int,
    page: int,
    prefs: PreferencesStore | None = None,
    settings: Settings | None = None,
) -> None:
    if telegram_id <= 0:
        await callback.answer(t("admin.list_no_telegram"), show_alert=True)
        return
    try:
        user = await panel.get_user_by_telegram_id(telegram_id)
    except PanelError as error:
        logger.warning("Panel user lookup failed (%s)", type(error).__name__)
        await callback.answer(_panel_error_text(error), show_alert=True)
        return
    name = _display_names(prefs).get(telegram_id)
    devices_count = None
    try:
        devices_count = len(await panel.get_user_devices(user))
    except (PanelError, ValueError) as error:
        logger.warning("Panel device request failed (%s)", type(error).__name__)
    await _edit(
        callback,
        admin_user_card_text(
            user,
            devices_count=devices_count,
            name=name,
            telegram_id=telegram_id,
        ),
        admin_user_keyboard(
            telegram_id,
            user.status,
            page=page,
            page_url=_page_url(user, settings),
            username=_display_usernames(prefs).get(telegram_id),
        ),
    )


@router.message(Command("admin_user"))
async def admin_user(
    message: Message,
    panel: RemnawaveClient,
    settings: Settings,
    state: FSMContext | None = None,
    prefs: PreferencesStore | None = None,
) -> None:
    if state is not None:
        await state.clear()
    if not _private_admin(message, settings):
        await message.answer(t("admin.command_unavailable"))
        return
    try:
        target = _target_id(message)
    except ValueError:
        await message.answer(t("admin.usage_user"))
        return
    await _lookup(message, panel, target, page=0, prefs=prefs, settings=settings)


@router.message(Command("admin_grant"))
async def admin_grant(
    message: Message,
    panel: RemnawaveClient,
    settings: Settings,
    state: FSMContext | None = None,
) -> None:
    if not _private_admin(message, settings):
        await message.answer(t("admin.command_unavailable"))
        return
    if state is None:
        await message.answer(t("admin.flow_unavailable"))
        return
    args = _arguments(message)
    if args:
        if len(args) != 2 or not args[0].isdigit() or not args[1].isdigit():
            await message.answer(t("admin.usage_grant"))
            return
        target, days = int(args[0]), int(args[1])
        if target <= 0 or not 1 <= days <= 365:
            await message.answer(t("admin.invalid_target"))
            return
        await _grant_confirmation(message, panel, settings, state, target, days)
        return
    await state.set_state(AdminFlow.grant_user)
    await message.answer(t("admin.ask_grant_user"))


async def _grant_confirmation(
    message: Message,
    panel: RemnawaveClient,
    settings: Settings,
    state: FSMContext,
    telegram_id: int,
    days: int,
) -> None:
    if settings.panel_internal_squad_uuid is None:
        await state.clear()
        await message.answer(t("admin.squad_missing"))
        return
    try:
        await panel.get_user_by_telegram_id(telegram_id)
    except PanelNotFound:
        pass
    except PanelError as error:
        await state.clear()
        await _report_panel_error(message, error)
        return
    else:
        await state.clear()
        await message.answer(t("admin.user_exists"))
        return
    await state.update_data(telegram_id=telegram_id, days=days)
    await state.set_state(AdminFlow.grant_confirm)
    await message.answer(
        f"<b>{t('admin.grant_confirm_title')}</b>\n\n"
        f"<blockquote>┌ <b>{t('common.parameters')}</b>\n"
        f"├ {t('common.telegram_id')}: <code>{telegram_id}</code>\n"
        f"└ {t('admin.term_row', term=tn('plural.days', days))}\n"
        f"</blockquote>\n\n"
        f"{t('admin.grant_confirm_note')}",
        reply_markup=confirm_grant_keyboard(),
        parse_mode="HTML",
    )


@router.message(Command("admin_extend"))
async def admin_extend(
    message: Message,
    panel: RemnawaveClient,
    settings: Settings,
    state: FSMContext | None = None,
) -> None:
    if not _private_admin(message, settings):
        await message.answer(t("admin.command_unavailable"))
        return
    if state is None:
        await message.answer(t("admin.flow_unavailable"))
        return
    args = _arguments(message)
    if args:
        if len(args) != 2 or not args[0].isdigit() or not args[1].isdigit():
            await message.answer(t("admin.usage_extend"))
            return
        target, days = int(args[0]), int(args[1])
        if target <= 0 or not 1 <= days <= 365:
            await message.answer(t("admin.invalid_target"))
            return
        await _extend_confirmation(message, panel, state, target, days)
        return
    await state.clear()
    await state.set_state(AdminFlow.extend_user)
    await message.answer(t("admin.ask_extend_user"))


async def _extend_confirmation(
    message: Message,
    panel: RemnawaveClient,
    state: FSMContext,
    telegram_id: int,
    days: int,
) -> None:
    try:
        user = await panel.get_user_by_telegram_id(telegram_id)
    except PanelNotFound:
        await state.clear()
        await message.answer(
            t("admin.extend_not_found"),
            reply_markup=back_keyboard("admin:home"),
        )
        return
    except PanelError as error:
        await state.clear()
        await _report_panel_error(message, error)
        return
    if user.status == "LIMITED":
        await state.clear()
        await message.answer(
            t("admin.extend_blocked_limited"),
            reply_markup=back_keyboard("admin:find"),
        )
        return
    note = ""
    if user.status == "DISABLED":
        note = "\n\n" + t("admin.extend_note_disabled")
    elif user.status == "EXPIRED":
        note = "\n\n" + t("admin.extend_note_expired")
    await state.update_data(telegram_id=telegram_id, days=days)
    await state.set_state(AdminFlow.extend_confirm)
    await message.answer(
        f"<b>{t('admin.extend_confirm_title')}</b>\n\n"
        f"<blockquote>┌ <b>{t('common.parameters')}</b>\n"
        f"├ {t('common.telegram_id')}: <code>{telegram_id}</code>\n"
        f"├ {t('admin.current_expiry', expires=_expiry_text(user))}\n"
        f"└ {t('admin.extend_for', days_word=tn('plural.days', days))}"
        f"</blockquote>{note}",
        reply_markup=confirm_extend_keyboard(),
        parse_mode="HTML",
    )


@router.message(Command("admin_disable"))
async def admin_disable(
    message: Message,
    panel: RemnawaveClient,
    settings: Settings,
    state: FSMContext | None = None,
) -> None:
    if state is not None:
        await state.clear()
    await _action_command(message, settings, "disable", state)


@router.message(Command("admin_enable"))
async def admin_enable(
    message: Message,
    panel: RemnawaveClient,
    settings: Settings,
    state: FSMContext | None = None,
) -> None:
    if state is not None:
        await state.clear()
    await _action_command(message, settings, "enable", state)


@router.message(Command("admin_revoke"))
async def admin_revoke(
    message: Message,
    panel: RemnawaveClient,
    settings: Settings,
    state: FSMContext | None = None,
) -> None:
    if state is not None:
        await state.clear()
    await _action_command(message, settings, "revoke", state)


async def _action_command(
    message: Message,
    settings: Settings,
    action: str,
    state: FSMContext | None,
) -> None:
    if not _private_admin(message, settings):
        await message.answer(t("admin.command_unavailable"))
        return
    try:
        telegram_id = _target_id(message)
    except ValueError:
        commands = {
            "disable": "admin_disable",
            "enable": "admin_enable",
            "revoke": "admin_revoke",
        }
        await message.answer(t("admin.usage_action", command=commands[action]))
        return
    if state is not None:
        await state.update_data(action=action, telegram_id=telegram_id)
        await state.set_state(AdminFlow.action_confirm)
    explanations = {
        "disable": t("admin.explain_disable"),
        "enable": t("admin.explain_enable"),
        "revoke": t("admin.explain_revoke"),
    }
    await message.answer(
        f"{t('admin.action_for', telegram_id=telegram_id)}\n\n"
        f"<blockquote>└ {explanations[action]}</blockquote>",
        reply_markup=confirm_keyboard(action, telegram_id),
        parse_mode="HTML",
    )


@router.message(StateFilter(AdminFlow.find_user))
async def admin_find_input(
    message: Message,
    panel: RemnawaveClient,
    settings: Settings,
    state: FSMContext,
    prefs: PreferencesStore | None = None,
) -> None:
    if not _private_admin(message, settings):
        await state.clear()
        await message.answer(t("admin.command_unavailable"))
        return
    try:
        raw_target = (message.text or "").strip()
        if not raw_target.isascii() or not raw_target.isdigit():
            raise ValueError
        target = int(raw_target)
        if not 0 < target <= 9_223_372_036_854_775_807:
            raise ValueError
    except ValueError:
        await message.answer(t("admin.ask_positive_id"))
        return
    data = await state.get_data()
    raw_page = data.get("return_page", 0)
    page = raw_page if isinstance(raw_page, int) and raw_page >= 0 else 0
    found = await _lookup(
        message,
        panel,
        target,
        page=page,
        prefs=prefs,
        settings=settings,
        error_back=f"admin:find:{page}",
    )
    if found:
        await state.clear()


@router.message(StateFilter(AdminFlow.grant_user))
async def grant_user_input(
    message: Message, settings: Settings, state: FSMContext
) -> None:
    if not _private_admin(message, settings):
        await state.clear()
        await message.answer(t("admin.command_unavailable"))
        return
    try:
        target = int((message.text or "").strip())
        if target <= 0:
            raise ValueError
    except ValueError:
        await message.answer(t("admin.ask_positive_id"))
        return
    await state.update_data(telegram_id=target)
    await state.set_state(AdminFlow.grant_days)
    await message.answer(t("admin.ask_grant_days"))


@router.message(StateFilter(AdminFlow.grant_days))
async def grant_days_input(
    message: Message,
    panel: RemnawaveClient,
    settings: Settings,
    state: FSMContext,
) -> None:
    if not _private_admin(message, settings):
        await state.clear()
        await message.answer(t("admin.command_unavailable"))
        return
    try:
        days = int((message.text or "").strip())
        if not 1 <= days <= 365:
            raise ValueError
    except ValueError:
        await message.answer(t("admin.invalid_days"))
        return
    data = await state.get_data()
    await _grant_confirmation(
        message, panel, settings, state, int(data["telegram_id"]), days
    )


@router.message(StateFilter(AdminFlow.extend_user))
async def extend_user_input(
    message: Message, settings: Settings, state: FSMContext
) -> None:
    if not _private_admin(message, settings):
        await state.clear()
        await message.answer(t("admin.command_unavailable"))
        return
    try:
        target = int((message.text or "").strip())
        if target <= 0:
            raise ValueError
    except ValueError:
        await message.answer(t("admin.ask_positive_id"))
        return
    await state.update_data(telegram_id=target)
    await state.set_state(AdminFlow.extend_days)
    await message.answer(t("admin.ask_extend_days"))


@router.message(StateFilter(AdminFlow.extend_days))
async def extend_days_input(
    message: Message,
    panel: RemnawaveClient,
    settings: Settings,
    state: FSMContext,
) -> None:
    if not _private_admin(message, settings):
        await state.clear()
        await message.answer(t("admin.command_unavailable"))
        return
    try:
        days = int((message.text or "").strip())
        if not 1 <= days <= 365:
            raise ValueError
    except ValueError:
        await message.answer(t("admin.invalid_days"))
        return
    data = await state.get_data()
    await _extend_confirmation(
        message, panel, state, int(data["telegram_id"]), days
    )


async def _run_grant(
    callback: CallbackQuery,
    bot: Bot,
    panel: RemnawaveClient,
    settings: Settings,
    state: FSMContext,
    prefs: PreferencesStore | None,
) -> None:
    if callback.message is None:
        await callback.answer(t("admin.message_unavailable"), show_alert=True)
        return
    async with _mutation_lock:
        if await state.get_state() != AdminFlow.grant_confirm.state:
            await callback.answer(
                t("admin.confirm_stale"), show_alert=True
            )
            return
        data = await state.get_data()
        await state.clear()
        await callback.answer()
        if settings.panel_internal_squad_uuid is None:
            await _edit(
                callback,
                t("admin.squad_missing"),
                admin_keyboard(),
            )
            return
        target, days = int(data["telegram_id"]), int(data["days"])
        try:
            await panel.get_user_by_telegram_id(target)
        except PanelNotFound:
            pass
        except PanelError as error:
            logger.warning("Panel grant failed (%s)", type(error).__name__)
            await _edit(callback, _panel_error_text(error), admin_keyboard())
            return
        else:
            await _edit(
                callback,
                t("admin.user_exists_no_dup"),
                admin_keyboard(),
            )
            return
        try:
            created = await panel.create_user(
                target,
                days,
                settings.panel_internal_squad_uuid,
                tag=ADMIN_TAG,
            )
        except PanelError as error:
            logger.warning("Panel grant failed (%s)", type(error).__name__)
            await _edit(
                callback,
                t("admin.grant_failed"),
                admin_keyboard(),
            )
            return
        await _notify_user(
            bot,
            target,
            t(
                "subscription.notice_granted",
                expires=_expiry_text(created, _user_offset(prefs, target)),
            ),
        )
        await _edit(
            callback,
            _result_text(
                t("admin.grant_title"),
                [
                    f"{t('common.telegram_id')}: <code>{target}</code>",
                    f"{t('common.tariff')}: "
                    f"{escape(tariff_text(created) or ADMIN_TAG)}",
                    f"{t('common.expires')}: {_expiry_text(created)}",
                ],
            ),
            admin_keyboard(),
        )


async def _run_extend(
    callback: CallbackQuery,
    bot: Bot,
    panel: RemnawaveClient,
    state: FSMContext,
    prefs: PreferencesStore | None,
) -> None:
    if callback.message is None:
        await callback.answer(t("admin.message_unavailable"), show_alert=True)
        return
    async with _mutation_lock:
        if await state.get_state() != AdminFlow.extend_confirm.state:
            await callback.answer(
                t("admin.confirm_stale"), show_alert=True
            )
            return
        data = await state.get_data()
        await state.clear()
        await callback.answer()
        target, days = int(data["telegram_id"]), int(data["days"])
        try:
            user = await panel.get_user_by_telegram_id(target)
            if user.status == "LIMITED":
                await _edit(
                    callback,
                    t("admin.extend_blocked_limited"),
                    admin_keyboard(),
                )
                return
            updated = await panel.extend_user(user, days)
        except PanelError as error:
            logger.warning("Panel extend failed (%s)", type(error).__name__)
            await _edit(
                callback,
                t("admin.extend_failed"),
                admin_keyboard(),
            )
            return
        expiry = _expiry_text(updated, _user_offset(prefs, target))
        notice_key = (
            "subscription.notice_extended_disabled"
            if updated.status == "DISABLED"
            else "subscription.notice_extended"
        )
        await _notify_user(bot, target, t(notice_key, expires=expiry))
        status_note = ""
        if updated.status == "DISABLED":
            status_note = " " + t("admin.status_remains_disabled")
        await _edit(
            callback,
            _result_text(
                t("admin.extend_title"),
                [
                    f"{t('common.telegram_id')}: <code>{target}</code>",
                    f"{t('common.expires')}: {_expiry_text(updated)}",
                    f"{t('common.status')}: "
                    f"{status_text(updated)}{status_note}",
                ],
            ),
            admin_keyboard(),
        )


async def _run_action(
    callback: CallbackQuery,
    bot: Bot,
    panel: RemnawaveClient,
    state: FSMContext,
    telegram_id: int,
    action: str,
    prefs: PreferencesStore | None,
) -> None:
    if callback.message is None:
        await callback.answer(t("admin.message_unavailable"), show_alert=True)
        return
    async with _mutation_lock:
        if await state.get_state() != AdminFlow.action_confirm.state:
            await callback.answer(
                t("admin.confirm_stale"), show_alert=True
            )
            return
        data = await state.get_data()
        if (
            data.get("action") != action
            or data.get("telegram_id") != telegram_id
        ):
            await callback.answer(
                t("admin.confirm_stale"), show_alert=True
            )
            return
        await state.clear()
        await callback.answer()
        try:
            user = await panel.get_user_by_telegram_id(telegram_id)
            if action == "disable":
                if user.status == "DISABLED":
                    await _edit(
                        callback, t("admin.already_disabled"), admin_keyboard()
                    )
                    return
                await panel.disable_user(user)
                notice = t("subscription.notice_disabled")
                result = _result_text(
                    t("admin.disable_title"),
                    [
                        f"{t('common.telegram_id')}: <code>{telegram_id}</code>",
                        t("admin.disable_done"),
                    ],
                )
            elif action == "enable":
                if user.status == "ACTIVE":
                    await _edit(
                        callback, t("admin.already_enabled"), admin_keyboard()
                    )
                    return
                enabled = await panel.enable_user(user)
                if enabled.status == "ACTIVE":
                    notice = t(
                        "subscription.notice_enabled",
                        expires=_expiry_text(
                            enabled, _user_offset(prefs, telegram_id)
                        ),
                    )
                elif enabled.status == "EXPIRED":
                    notice = t(
                        "subscription.notice_expired",
                        expires=_expiry_text(
                            enabled, _user_offset(prefs, telegram_id)
                        ),
                    )
                else:
                    notice = t("subscription.notice_limited")
                rows = [
                    f"{t('common.telegram_id')}: <code>{telegram_id}</code>",
                    f"{t('common.status')}: {status_text(enabled)}",
                    f"{t('common.expires')}: {_expiry_text(enabled)}",
                    t("admin.enable_done"),
                ]
                if enabled.status == "EXPIRED":
                    rows.append(t("admin.enable_expired"))
                    title = t("admin.enable_expired_title")
                elif enabled.status == "LIMITED":
                    rows.append(t("admin.enable_limited"))
                    title = t("admin.enable_limited_title")
                else:
                    title = t("admin.enable_title")
                result = _result_text(title, rows)
            else:
                await panel.revoke_subscription(user)
                notice = t("subscription.notice_revoked")
                result = _result_text(
                    t("admin.revoke_title"),
                    [
                        f"{t('common.telegram_id')}: <code>{telegram_id}</code>",
                        t("admin.revoke_done"),
                    ],
                )
        except PanelError as error:
            logger.warning("Panel action failed (%s)", type(error).__name__)
            await _edit(callback, _panel_error_text(error), admin_keyboard())
            return
        await _notify_user(bot, telegram_id, notice)
        await _edit(callback, result, admin_keyboard())


async def _logs_screen(
    callback: CallbackQuery,
    prefs: PreferencesStore | None,
) -> None:
    if prefs is None:
        await callback.answer(t("admin.logs_unavailable"), show_alert=True)
        return
    try:
        enabled = prefs.admin_logs_enabled()
        tracked = len(prefs.known_users())
    except PreferencesError:
        logger.warning("Connection log settings read failed")
        await callback.answer(t("admin.logs_unavailable"), show_alert=True)
        return
    await _edit(
        callback,
        admin_logs_text(enabled, POLL_INTERVAL_SECONDS, tracked),
        admin_logs_keyboard(enabled),
    )


async def _admin_devices(
    callback: CallbackQuery,
    panel: RemnawaveClient,
    telegram_id: int,
    page: int,
) -> None:
    try:
        user = await panel.get_user_by_telegram_id(telegram_id)
    except PanelError as error:
        logger.warning("Panel user lookup failed (%s)", type(error).__name__)
        await callback.answer(_panel_error_text(error), show_alert=True)
        return
    devices = None
    error_text = None
    try:
        devices = await panel.get_user_devices(user)
    except PanelError as error:
        logger.warning("Panel device request failed (%s)", type(error).__name__)
        error_text = _panel_error_text(error)
    except ValueError:
        error_text = t("admin.data_unexpected")
    pages = max(
        1,
        (
            (len(devices) if devices is not None else 1)
            + DEVICES_PER_PAGE
            - 1
        )
        // DEVICES_PER_PAGE,
    )
    page = min(max(page, 0), pages - 1)
    await _edit(
        callback,
        admin_devices_text(user, devices, page=page, error=error_text),
        admin_devices_keyboard(telegram_id, page, pages),
    )


@router.callback_query(F.data.startswith("admin:"))
async def admin_navigation(
    callback: CallbackQuery,
    bot: Bot,
    panel: RemnawaveClient,
    settings: Settings,
    state: FSMContext,
    prefs: PreferencesStore | None = None,
) -> None:
    if not _admin_callback(callback, settings):
        await callback.answer(t("admin.command_unavailable"), show_alert=True)
        return
    action = (callback.data or "").removeprefix("admin:")
    if action == "home":
        await state.clear()
        await callback.answer()
        await _edit(callback, _menu_text(), admin_keyboard())
    elif action == "wallet":
        await state.clear()
        await callback.answer()
        await _edit(callback, t("admin.wallet_title"), wallet_admin_keyboard())
    elif action == "wallet:adjust":
        await state.clear()
        await state.set_state(WalletAdminFlow.adjust_input)
        await callback.answer()
        await _edit(
            callback, t("admin.wallet_adjust_prompt"), back_keyboard("admin:home")
        )
    elif action == "wallet:bonus":
        await state.clear()
        await state.set_state(WalletAdminFlow.bonus_input)
        await callback.answer()
        await _edit(
            callback, t("admin.wallet_bonus_prompt"), back_keyboard("admin:wallet")
        )
    elif action == "wallet:find":
        await state.clear()
        await state.set_state(WalletAdminFlow.payment_lookup)
        await callback.answer()
        await _edit(
            callback,
            t("admin.wallet_payment_lookup_prompt"),
            back_keyboard("admin:wallet"),
        )
    elif action == "docs":
        await state.clear()
        if prefs is None:
            await callback.answer(t("settings.storage_temporary"), show_alert=True)
            return
        try:
            privacy = prefs.document_url("privacy")
            agreement = prefs.document_url("agreement")
        except (PreferencesError, ValueError):
            logger.warning("Document link settings could not be loaded")
            await callback.answer(t("settings.storage_temporary"), show_alert=True)
            return
        await callback.answer()
        await _edit(
            callback,
            t(
                "docs.admin_status",
                privacy=escape(privacy or t("docs.admin_unset")),
                agreement=escape(agreement or t("docs.admin_unset")),
            ),
            admin_documents_keyboard(),
        )
    elif action in {"docs:privacy", "docs:agreement"}:
        document = action.partition(":")[2]
        await state.clear()
        await state.update_data(document=document)
        await state.set_state(DocumentsFlow.url)
        await callback.answer()
        await _edit(
            callback,
            t(
                "docs.admin_prompt_privacy"
                if document == "privacy"
                else "docs.admin_prompt_agreement"
            ),
            back_keyboard("admin:docs"),
        )
    elif action == "broadcast":
        await state.clear()
        await state.set_state(BroadcastFlow.message)
        await callback.answer()
        await _edit(callback, t("admin.broadcast_prompt"), back_keyboard("admin:home"))
    elif action == "broadcast:cancel":
        await state.clear()
        await callback.answer()
        await _edit(callback, t("admin.broadcast_cancelled"), admin_keyboard())
    elif action == "broadcast:send":
        if not _admin_callback(callback, settings):
            await callback.answer(t("admin.command_unavailable"), show_alert=True)
            return
        await _send_broadcast(callback, bot, settings, state, prefs)
    elif action == "logs":
        await state.clear()
        await callback.answer()
        await _logs_screen(callback, prefs)
    elif action in {"logs:on", "logs:off"}:
        if prefs is None:
            await callback.answer(t("admin.logs_unavailable"), show_alert=True)
            return
        await callback.answer()
        try:
            if action == "logs:on" and not prefs.admin_logs_enabled():
                await baseline(panel, prefs)
            prefs.set_admin_logs_enabled(action == "logs:on")
        except (PanelError, PreferencesError, ValueError) as error:
            logger.warning(
                "Connection log settings update failed (%s)",
                type(error).__name__,
            )
            await _edit(callback, t("admin.logs_unavailable"), admin_keyboard())
            return
        await _logs_screen(callback, prefs)
    elif action.startswith("devices:"):
        await state.clear()
        parts = action.split(":")
        if len(parts) != 3:
            await callback.answer(t("admin.stale_button"), show_alert=True)
            return
        try:
            telegram_id = int(parts[1])
            page = int(parts[2])
            if telegram_id <= 0 or page < 0:
                raise ValueError
        except ValueError:
            await callback.answer(t("admin.bad_button"), show_alert=True)
            return
        await callback.answer()
        await _admin_devices(callback, panel, telegram_id, page)
    elif action == "status":
        await state.clear()
        await callback.answer()
        if callback.message is None:
            return
        try:
            healthy = await panel.health()
            nodes = await panel.get_nodes()
        except PanelError as error:
            logger.warning("Panel request failed (%s)", type(error).__name__)
            await _edit(callback, _panel_error_text(error), admin_keyboard())
            return
        users: list[PanelUser] | None = None
        users_total: int | None = None
        users_error = False
        try:
            users, users_total = await _collect_users(panel, prefs)
        except PanelError as error:
            logger.warning("Panel user list failed (%s)", type(error).__name__)
            users_error = True
        await _edit(
            callback,
            _service_status_text(
                healthy,
                nodes,
                users=users,
                users_total=users_total,
                users_error=users_error,
            ),
            admin_keyboard(),
        )
    elif action == "find" or action.startswith("find:"):
        raw_page = action.partition(":")[2]
        try:
            page = int(raw_page) if raw_page else 0
            if page < 0:
                raise ValueError
        except ValueError:
            await callback.answer(t("admin.bad_button"), show_alert=True)
            return
        await state.clear()
        await state.update_data(return_page=page)
        await state.set_state(AdminFlow.find_user)
        await callback.answer()
        await _edit(
            callback,
            f"<b>{t('admin.find_title')}</b>\n\n{t('admin.find_note')}",
            back_keyboard(f"admin:list:{page}"),
        )
    elif action.startswith("list:"):
        await state.clear()
        raw_page = action.partition(":")[2]
        try:
            page = int(raw_page)
            if page < 0:
                raise ValueError
        except ValueError:
            await callback.answer(t("admin.bad_button"), show_alert=True)
            return
        await callback.answer()
        await _users_screen(callback, panel, page, prefs)
    elif action.startswith("u:"):
        await state.clear()
        parts = action.split(":")
        if len(parts) != 3:
            await callback.answer(t("admin.stale_button"), show_alert=True)
            return
        try:
            telegram_id = int(parts[1])
            page = int(parts[2])
            if telegram_id <= 0 or page < 0:
                raise ValueError
        except ValueError:
            await callback.answer(t("admin.bad_button"), show_alert=True)
            return
        await callback.answer()
        await _user_card(callback, panel, telegram_id, page, prefs, settings)
    elif action == "grant":
        await state.set_state(AdminFlow.grant_user)
        await callback.answer()
        await _edit(
            callback,
            f"<b>{t('admin.grant_ask_title')}</b>\n\n"
            f"<blockquote>└ {t('admin.grant_ask_note')}</blockquote>",
            back_keyboard("admin:home"),
        )
    elif action.startswith("extend:"):
        parts = action.split(":")
        if len(parts) != 2:
            await callback.answer(t("admin.stale_button"), show_alert=True)
            return
        try:
            telegram_id = int(parts[1])
            if telegram_id <= 0:
                raise ValueError
        except ValueError:
            await callback.answer(t("admin.bad_button"), show_alert=True)
            return
        await state.clear()
        await state.update_data(telegram_id=telegram_id)
        await state.set_state(AdminFlow.extend_days)
        await callback.answer()
        await _edit(
            callback,
            f"<b>{t('admin.extend_ask_title')}</b>\n\n"
            f"<blockquote>┌ <b>{t('common.parameters')}</b>\n"
            f"├ {t('common.telegram_id')}: <code>{telegram_id}</code>\n"
            f"└ {t('admin.extend_ask_note')}</blockquote>",
            back_keyboard("admin:home"),
        )
    elif action.startswith("confirm:"):
        await state.clear()
        parts = action.split(":")
        if len(parts) != 3 or parts[1] not in {"disable", "enable", "revoke"}:
            await callback.answer(t("admin.stale_button"), show_alert=True)
            return
        try:
            telegram_id = int(parts[2])
            if telegram_id <= 0:
                raise ValueError
        except ValueError:
            await callback.answer(t("admin.bad_button"), show_alert=True)
            return
        await state.update_data(action=parts[1], telegram_id=telegram_id)
        await state.set_state(AdminFlow.action_confirm)
        await callback.answer()
        warnings = {
            "disable": t("admin.warn_disable"),
            "enable": t("admin.warn_enable"),
            "revoke": t("admin.warn_revoke"),
        }
        await _edit(
            callback,
            f"<b>{t('admin.confirm_action_title')}</b>\n\n"
            f"<blockquote>└ {warnings[parts[1]]}</blockquote>",
            confirm_keyboard(parts[1], telegram_id),
        )
    elif action == "run:grant":
        await _run_grant(callback, bot, panel, settings, state, prefs)
    elif action == "run:extend":
        await _run_extend(callback, bot, panel, state, prefs)
    elif action.startswith("run:"):
        parts = action.split(":")
        if len(parts) != 3 or parts[1] not in {"disable", "enable", "revoke"}:
            await callback.answer(t("admin.stale_button"), show_alert=True)
            return
        try:
            telegram_id = int(parts[2])
            if telegram_id <= 0:
                raise ValueError
        except ValueError:
            await callback.answer(t("admin.bad_button"), show_alert=True)
            return
        await _run_action(
            callback, bot, panel, state, telegram_id, parts[1], prefs
        )
    else:
        await callback.answer(t("admin.stale_button_menu"), show_alert=True)
