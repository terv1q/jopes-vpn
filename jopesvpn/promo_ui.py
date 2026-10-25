from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation
from html import escape
from secrets import token_urlsafe

from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from jopesvpn.config import Settings
from jopesvpn.i18n import t
from jopesvpn.plans import format_rubles
from jopesvpn.preferences import PreferencesError, PreferencesStore
from jopesvpn.promo_codes import PromoCodeError, PromoCodeStore
from jopesvpn.screens import answer_screen, edit_screen
from jopesvpn.ui import keyboard

logger = logging.getLogger(__name__)


class PromoCodeFlow(StatesGroup):
    awaiting_code = State()


class PromoInputExitMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        state = data.get("state")
        if (
            isinstance(event, CallbackQuery)
            and event.message is not None
            and event.message.chat.type == "private"
            and isinstance(state, FSMContext)
            and await state.get_state() == PromoCodeFlow.awaiting_code.state
        ):
            await state.clear()
        return await handler(event, data)


def promo_keyboard(*, result: bool = False):
    rows = []
    if result:
        rows.append(
            [(t("common.plans"), "user:plans"), (t("common.cabinet"), "user:cabinet")]
        )
        rows.append([(t("promo.another"), "user:promo")])
    rows.append([(t("common.back"), "user:back"), (t("common.menu"), "user:home")])
    return keyboard(rows)


def promo_prompt(settings: Settings) -> str:
    policy = settings.promo_policy
    return t(
        "promo.prompt",
        brand=escape(settings.brand_name),
        minimum=policy.min_code_length,
        maximum=policy.max_code_length,
    )


async def show_promo(
    callback: CallbackQuery, settings: Settings, state: FSMContext
) -> None:
    await state.set_state(PromoCodeFlow.awaiting_code)
    await callback.answer()
    await edit_screen(
        callback.message, promo_prompt(settings), promo_keyboard(), data="user:promo"
    )


async def promo_command(
    message: Message,
    settings: Settings,
    state: FSMContext,
    prefs: PreferencesStore | None = None,
) -> None:
    if message.chat.type != "private" or message.from_user is None:
        await message.answer(t("alert.private_chat"))
        return
    await state.clear()
    if prefs is not None:
        try:
            prefs.push_navigation(message.from_user.id, "promo")
        except PreferencesError:
            logger.warning("Voucher navigation could not be saved")
    await state.set_state(PromoCodeFlow.awaiting_code)
    await answer_screen(
        message, promo_prompt(settings), promo_keyboard(), banner="plans"
    )


async def promo_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await answer_screen(
        message, t("promo.cancelled"), promo_keyboard(result=True), banner="plans"
    )


async def promo_code_input(
    message: Message,
    settings: Settings,
    state: FSMContext,
    promo_codes: PromoCodeStore | None = None,
) -> None:
    if message.chat.type != "private" or message.from_user is None:
        return
    if promo_codes is None:
        text = t("promo.error")
    else:
        try:
            result = promo_codes.redeem(message.from_user.id, message.text or "")
        except PromoCodeError:
            logger.warning("Voucher redemption could not be committed")
            text = t("promo.error")
        else:
            if result.status == "applied":
                text = t(
                    "promo.applied",
                    amount=format_rubles(result.reward_minor),
                    balance=format_rubles(result.balance_minor),
                )
                if result.settled_minor:
                    text += "\n\n" + t(
                        "promo.debt", amount=format_rubles(result.settled_minor)
                    )
            elif result.status == "limited":
                text = t(
                    "promo.limited", minutes=max(1, (result.retry_after + 59) // 60)
                )
            elif result.status == "format":
                text = t(
                    "promo.format",
                    minimum=settings.promo_policy.min_code_length,
                    maximum=settings.promo_policy.max_code_length,
                )
            else:
                text = t(
                    "promo.used" if result.status == "used" else "promo.unavailable"
                )
    # Atomic storage handles duplicates even if two updates entered the state together.
    await state.clear()
    await answer_screen(message, text, promo_keyboard(result=True), banner="plans")


def promo_admin_text(settings: Settings) -> str:
    policy = settings.promo_policy
    return t(
        "promo.admin_help",
        maximum=format_rubles(policy.max_reward_minor),
        days=policy.max_valid_days,
        uses=policy.max_uses,
        minimum=policy.min_code_length,
        length=policy.max_code_length,
    )


def promo_admin_keyboard():
    return keyboard(
        [[(t("common.back"), "admin:home"), (t("common.menu"), "user:home")]]
    )


async def promo_admin_help(
    callback: CallbackQuery, settings: Settings, state: FSMContext
) -> None:
    if callback.from_user.id not in settings.admin_telegram_ids:
        await callback.answer(t("ticket.admin_required"), show_alert=True)
        return
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer(t("alert.private_chat"), show_alert=True)
        return
    await state.clear()
    await callback.answer()
    await edit_screen(
        callback.message,
        promo_admin_text(settings),
        promo_admin_keyboard(),
        data="admin:home",
    )


async def promo_admin_command(
    message: Message,
    settings: Settings,
    state: FSMContext,
    promo_codes: PromoCodeStore | None = None,
) -> None:
    if (
        message.chat.type != "private"
        or message.from_user is None
        or message.from_user.id not in settings.admin_telegram_ids
    ):
        await message.answer(t("ticket.admin_required"))
        return
    await state.clear()
    parts = (message.text or "").split()
    command = parts[0].split("@")[0] if parts else ""
    if promo_codes is None:
        text = t("promo.error")
    else:
        try:
            if command == "/promo_create" and len(parts) == 5:
                code = (
                    token_urlsafe(settings.promo_policy.max_code_length)[
                        : settings.promo_policy.max_code_length
                    ]
                    if parts[1].lower() == "auto"
                    else parts[1]
                )
                # Decimal avoids silently rounding a gift to a different amount.
                if re.fullmatch(r"[0-9]{1,9}(?:[.,][0-9]{1,2})?", parts[2]) is None:
                    raise ValueError("Invalid amount")
                amount = Decimal(parts[2].replace(",", ".")) * 100
                if (
                    not amount.is_finite()
                    or not 0 < amount <= settings.promo_policy.max_reward_minor
                    or amount != amount.to_integral_value()
                ):
                    raise ValueError("Invalid amount")
                created = promo_codes.create(
                    code,
                    reward_minor=int(amount),
                    valid_days=int(parts[3]),
                    max_uses=int(parts[4]),
                    admin_id=message.from_user.id,
                )
                text = (
                    t(
                        "promo.created",
                        code=escape(promo_codes.normalize(code)),
                        amount=format_rubles(int(amount)),
                        days=int(parts[3]),
                        uses=int(parts[4]),
                    )
                    if created
                    else t("promo.exists")
                )
            elif command == "/promo_disable" and len(parts) == 2:
                text = t(
                    "promo.disabled"
                    if promo_codes.disable(parts[1])
                    else "promo.not_active"
                )
            else:
                text = promo_admin_text(settings)
        except (ValueError, InvalidOperation):
            text = t("promo.admin_invalid") + "\n\n" + promo_admin_text(settings)
        except PromoCodeError:
            logger.warning("Voucher administration failed")
            text = t("promo.error")
    await answer_screen(message, text, promo_admin_keyboard(), banner="admin")
