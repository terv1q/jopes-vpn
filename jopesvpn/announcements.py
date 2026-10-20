from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

ANNOUNCEMENT_CAMPAIGN = "protocol_post_hy2_v1"
ANNOUNCEMENT_CALLBACK_PREFIX = "announcement:hy2"
CAMPAIGN_START_PROTOCOLS = {
    "refc_hy2": "hysteria2",
    "refc_reality": "reality",
    "refc_xhttp": "xhttp",
}
PROTOCOL_LABELS = {
    "hysteria2": "Hysteria 2",
    "reality": "VLESS Reality",
    "xhttp": "XHTTP",
}

ANNOUNCEMENT_TEXT = (
    "Добавили новый вариант подключения: Hysteria 2.\n\n"
    "🎮 Для онлайн-игр попробуйте Hysteria 2. Он работает через UDP. "
    "Это способ передачи данных без подтверждения доставки каждого пакета. "
    "Такой обмен подходит для игр, где важны быстрая реакция и низкая задержка. "
    "Если в вашей сети UDP ограничен или нестабилен, попробуйте другой вариант.\n\n"
    "Для повседневного использования начните с VLESS Reality. Это универсальный "
    "вариант для сайтов, видео и приложений.\n\n"
    "Если Reality подключается нестабильно, попробуйте XHTTP. В некоторых сетях "
    "он может работать лучше.\n\n"
    "Универсально самого быстрого варианта нет. Скорость и задержка зависят "
    "от провайдера и сети. Проверьте варианты и выберите тот, который у вас "
    "работает стабильнее."
)


def connection_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Подключить {PROTOCOL_LABELS[protocol]}",
                    url=f"https://t.me/{bot_username}?start={start_parameter}",
                    style="success",
                )
            ]
            for start_parameter, protocol in CAMPAIGN_START_PROTOCOLS.items()
        ]
    )


def approval_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Опубликовать",
                    callback_data=f"{ANNOUNCEMENT_CALLBACK_PREFIX}:publish",
                    style="success",
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=f"{ANNOUNCEMENT_CALLBACK_PREFIX}:cancel",
                    style="danger",
                ),
            ]
        ]
    )


def preview_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    keyboard = connection_keyboard(bot_username)
    keyboard.inline_keyboard.append(
        [
            InlineKeyboardButton(
                text="Добавить картинку",
                callback_data=f"{ANNOUNCEMENT_CALLBACK_PREFIX}:image",
                style="primary",
            )
        ]
    )
    keyboard.inline_keyboard.extend(approval_keyboard().inline_keyboard)
    return keyboard


def image_prompt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Не добавлять картинку",
                    callback_data=f"{ANNOUNCEMENT_CALLBACK_PREFIX}:image_cancel",
                )
            ]
        ]
    )
