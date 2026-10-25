from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from jopesvpn.config import Settings
from jopesvpn.service_ops import ServiceOpsStore
from jopesvpn.ui import back_keyboard

router = Router(name="service-review")


def event_text(event: dict) -> str:
    facts = event["facts"]
    kind = event["kind"]
    if kind == "support_waiting":
        return f"Тикет №{facts['ticket_id']}: ждёт ответа поддержки; Telegram ID {facts['user_id']}."
    if kind == "repeat_trial":
        return f"Повторные попытки trial: Telegram ID {facts['user_id']}, попыток {facts['attempts']}."
    if kind == "shared_device":
        return f"Одно устройство у {facts.get('accounts', len(facts['users']))} аккаунтов: {', '.join(map(str, facts['users']))}."
    if kind == "referral_burst":
        return f"Реферальный всплеск: Telegram ID {facts['user_id']}, приглашений за сутки {facts['count']}."
    if kind == "referral_loop":
        return f"Взаимные приглашения: {', '.join(map(str, facts['users']))}."
    if kind == "privacy_request":
        return f"Удаление данных №{facts['request_id']}: Telegram ID {facts['user_id']}. Команда после проверки: /privacy_process {facts['request_id']} confirm"
    return "Служебное событие требует проверки."


@router.message(Command("delete_data"))
async def request_erasure(message: Message, service_ops: ServiceOpsStore) -> None:
    if message.chat.type != "private" or message.from_user is None:
        return
    request_id = service_ops.request_erasure(message.from_user.id)
    await message.answer(
        f"Запрос на удаление данных №{request_id} передан владельцу Jopes VPN. "
        "Поддержка уточнит, какие данные можно удалить и какие нужны для действующего доступа и учёта платежей. "
        "Подписка и баланс сохраняются до согласования действий.",
        reply_markup=back_keyboard(),
    )


@router.message(Command("service"))
async def service_review(
    message: Message, settings: Settings, service_ops: ServiceOpsStore
) -> None:
    if (
        message.chat.type != "private"
        or message.from_user is None
        or message.from_user.id != settings.service_owner_id
    ):
        return
    events = service_ops.pending()
    text = "Jopes VPN — служебные события\n\n" + (
        "\n".join(event_text(event) for event in events) or "Новых событий нет."
    )
    await message.answer(text[:4000], reply_markup=back_keyboard())


@router.message(Command("privacy_process"))
async def process_erasure(
    message: Message, settings: Settings, service_ops: ServiceOpsStore
) -> None:
    if (
        message.chat.type != "private"
        or message.from_user is None
        or message.from_user.id != settings.service_owner_id
    ):
        return
    args = (message.text or "").split()
    if len(args) != 3 or not args[1].isdigit() or args[2] != "confirm":
        await message.answer(
            "После проверки запроса: /privacy_process <номер> confirm. Удаляются персональные данные бота; платёжный учёт и VPN-доступ сохраняются."
        )
        return
    changed = service_ops.erase_personal_data(int(args[1]))
    await message.answer(
        "Персональные данные бота удалены. Платёжный учёт и данные действующего VPN-доступа сохранены для отдельной проверки запроса."
        if changed
        else "Запрос уже обработан или не найден."
    )
