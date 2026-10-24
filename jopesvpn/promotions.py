from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

PROMO_POST_TEXT = (
    "🎁 АКЦИЯ ОТ JOPES VPN\n\n"
    "Быстрый и безопасный VPN для обхода блокировок и доступа к привычным "
    "сервисам. Дарим безлимитный доступ новым и прежним пользователям.\n\n"
    "Если вы ещё ни разу не активировали подписку, включая пробную, вы "
    "участвуете в акции для новых пользователей. Первые 5 человек, которые "
    "перейдут в бота по кнопке под постом, получат 15 дней безлимита.\n\n"
    "Уже активировали подписку раньше? Первые 3 таких пользователя получат "
    "ещё 7 дней. Если подписка действует, мы продлим её. Если срок закончился, "
    "сразу включим доступ на 7 дней.\n\n"
    "Нажмите «Получить подарок» под постом. Бот проверит ваш аккаунт и "
    "сообщит, удалось ли получить подарок. Одна награда на Telegram-аккаунт."
)
PROMO_BUTTON_TEXT = "Получить подарок"
PROMO_START_PREFIX = "promo_"
PROMO_NEW_LIMIT = 5
PROMO_RETURNING_LIMIT = 3
PROMO_NEW_DAYS = 15
PROMO_RETURNING_DAYS = 7
PROMO_MAX_EXTRA_BUTTONS = 7


class PromotionStoreError(Exception):
    pass


def promo_builder_text_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Использовать согласованный текст",
                    callback_data="promo-builder:text:default",
                    style="success",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Изменить текст",
                    callback_data="promo-builder:text:edit",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data="promo-builder:cancel",
                    style="danger",
                )
            ],
        ]
    )


def promo_builder_photo_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Продолжить без фото",
                    callback_data="promo-builder:photo:skip",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data="promo-builder:cancel",
                    style="danger",
                )
            ],
        ]
    )


def promo_review_photo_keyboard(post_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Не добавлять фото",
                    callback_data=f"channelpost:{post_id}:photo_cancel",
                )
            ]
        ]
    )


def promo_builder_buttons_keyboard(button_count: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="Изменить текст",
                    callback_data="promo-builder:text:edit",
                    style="primary",
                ),
                InlineKeyboardButton(
                    text="Фото",
                    callback_data="promo-builder:photo:replace",
                    style="primary",
                ),
            ]
        ]
    )
    if button_count < PROMO_MAX_EXTRA_BUTTONS:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Добавить ссылку",
                    callback_data="promo-builder:button:add",
                    style="primary",
                )
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="Предпросмотр",
                    callback_data="promo-builder:preview",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отправить разработчику",
                    callback_data="promo-builder:submit",
                    style="success",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data="promo-builder:cancel",
                    style="danger",
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def promo_preview_keyboard(
    post_id: int | None,
    bot_username: str,
    buttons: list[dict[str, str]],
    *,
    approval: bool = False,
) -> InlineKeyboardMarkup:
    start = f"{PROMO_START_PREFIX}{post_id}" if post_id else "promo_preview"
    rows = [
        [
            InlineKeyboardButton(
                text=PROMO_BUTTON_TEXT,
                url=f"https://t.me/{bot_username}?start={start}",
                style="success",
            )
        ]
    ]
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=button["text"],
                    url=(
                        f"https://t.me/{bot_username}?start=cp_{post_id}_{index}"
                        if post_id
                        else button["url"]
                    ),
                    style="success",
                )
            ]
            for index, button in enumerate(buttons)
        ]
    )
    if approval and post_id:
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text="Добавить фото",
                        callback_data=f"channelpost:{post_id}:photo",
                        style="primary",
                    )
                ],
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
                ],
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


class PromotionStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with closing(self._connect()) as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS promo_campaigns (
                        post_id INTEGER PRIMARY KEY REFERENCES channel_posts(post_id),
                        status TEXT NOT NULL CHECK(status IN ('pending', 'active', 'cancelled')),
                        created_at TEXT NOT NULL DEFAULT (datetime('now')),
                        published_at TEXT
                    );
                    CREATE TABLE IF NOT EXISTS promo_claims (
                        telegram_id INTEGER PRIMARY KEY,
                        post_id INTEGER NOT NULL REFERENCES promo_campaigns(post_id),
                        category TEXT NOT NULL CHECK(category IN ('new', 'returning')),
                        status TEXT NOT NULL CHECK(status IN ('reserved', 'applied', 'review')),
                        created_at TEXT NOT NULL DEFAULT (datetime('now')),
                        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                        error_code TEXT
                    );
                    CREATE INDEX IF NOT EXISTS promo_claims_campaign_category
                        ON promo_claims(post_id, category, status);
                    CREATE TABLE IF NOT EXISTS promo_start_requests (
                        telegram_id INTEGER PRIMARY KEY,
                        post_id INTEGER NOT NULL REFERENCES promo_campaigns(post_id),
                        created_at TEXT NOT NULL DEFAULT (datetime('now'))
                    );
                    CREATE TABLE IF NOT EXISTS promo_preparation_log (
                        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        post_id INTEGER NOT NULL REFERENCES promo_campaigns(post_id),
                        actor_id INTEGER NOT NULL,
                        action TEXT NOT NULL,
                        detail TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL DEFAULT (datetime('now'))
                    );
                    CREATE INDEX IF NOT EXISTS promo_preparation_log_post
                        ON promo_preparation_log(post_id, event_id);
                    """
                )
                connection.execute("PRAGMA journal_mode = WAL")
                connection.commit()
        except (OSError, sqlite3.Error) as error:
            raise PromotionStoreError("Cannot initialize promotion storage") from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def create_campaign(self, post_id: int) -> None:
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    INSERT INTO promo_campaigns (post_id, status)
                    VALUES (?, 'pending')
                    """,
                    (post_id,),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PromotionStoreError("Cannot create promotion campaign") from error

    def log_preparation(
        self, post_id: int, actor_id: int, action: str, detail: str = ""
    ) -> None:
        if post_id <= 0 or actor_id <= 0 or not action or len(detail) > 1000:
            raise ValueError("Invalid promotion preparation log")
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    INSERT INTO promo_preparation_log
                        (post_id, actor_id, action, detail)
                    VALUES (?, ?, ?, ?)
                    """,
                    (post_id, actor_id, action[:80], detail),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PromotionStoreError("Cannot save promotion preparation log") from error

    def preparation_log(self, post_id: int, limit: int = 20) -> list[dict[str, Any]]:
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT actor_id, action, detail, created_at
                    FROM promo_preparation_log
                    WHERE post_id = ?
                    ORDER BY event_id
                    LIMIT ?
                    """,
                    (post_id, limit),
                ).fetchall()
        except sqlite3.Error as error:
            raise PromotionStoreError("Cannot read promotion preparation log") from error
        return [dict(row) for row in rows]

    def finish_publication(self, post_id: int, message_id: int) -> bool:
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                post = connection.execute(
                    """
                    UPDATE channel_posts
                    SET status = 'published', channel_message_id = ?,
                        updated_at = datetime('now')
                    WHERE post_id = ? AND status = 'publishing'
                      AND post_type = 'promo'
                    """,
                    (message_id, post_id),
                )
                if post.rowcount != 1:
                    connection.rollback()
                    return False
                campaign = connection.execute(
                    """
                    UPDATE promo_campaigns
                    SET status = 'active', published_at = datetime('now')
                    WHERE post_id = ? AND status = 'pending'
                    """,
                    (post_id,),
                )
                if campaign.rowcount != 1:
                    connection.rollback()
                    raise PromotionStoreError("Promotion campaign is not pending")
                connection.commit()
                return True
        except sqlite3.Error as error:
            raise PromotionStoreError("Cannot activate published campaign") from error

    def cancel_campaign(self, post_id: int) -> None:
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    UPDATE promo_campaigns
                    SET status = 'cancelled'
                    WHERE post_id = ? AND status = 'pending'
                    """,
                    (post_id,),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PromotionStoreError("Cannot cancel promotion campaign") from error

    def store_start_request(self, telegram_id: int, post_id: int) -> bool:
        try:
            with closing(self._connect()) as connection:
                valid = connection.execute(
                    """
                    SELECT 1
                    FROM promo_campaigns c
                    JOIN channel_posts p ON p.post_id = c.post_id
                    WHERE c.post_id = ? AND c.status = 'active'
                      AND p.status = 'published'
                    """,
                    (post_id,),
                ).fetchone()
                if valid is None:
                    return False
                connection.execute(
                    """
                    INSERT INTO promo_start_requests (telegram_id, post_id)
                    VALUES (?, ?)
                    ON CONFLICT(telegram_id) DO UPDATE SET
                        post_id = excluded.post_id,
                        created_at = datetime('now')
                    """,
                    (telegram_id, post_id),
                )
                connection.commit()
                return True
        except sqlite3.Error as error:
            raise PromotionStoreError("Cannot save promotion start request") from error

    def pending_start_request(self, telegram_id: int) -> int | None:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    """
                    SELECT post_id FROM promo_start_requests
                    WHERE telegram_id = ?
                    """,
                    (telegram_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise PromotionStoreError("Cannot read promotion start request") from error
        return None if row is None else int(row["post_id"])

    def clear_start_request(self, telegram_id: int) -> None:
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    "DELETE FROM promo_start_requests WHERE telegram_id = ?",
                    (telegram_id,),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PromotionStoreError("Cannot clear promotion start request") from error

    def reserve_reward(
        self, post_id: int, telegram_id: int, category: str
    ) -> tuple[str, str | None]:
        if category not in {"new", "returning"}:
            raise ValueError("Invalid promotion reward category")
        limit = PROMO_NEW_LIMIT if category == "new" else PROMO_RETURNING_LIMIT
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                campaign = connection.execute(
                    """
                    SELECT 1 FROM promo_campaigns c
                    JOIN channel_posts p ON p.post_id = c.post_id
                    WHERE c.post_id = ? AND c.status = 'active'
                      AND p.status = 'published'
                    """,
                    (post_id,),
                ).fetchone()
                if campaign is None:
                    connection.rollback()
                    return "inactive", None
                prior = connection.execute(
                    """
                    SELECT category, status FROM promo_claims
                    WHERE telegram_id = ?
                    """,
                    (telegram_id,),
                ).fetchone()
                if prior is not None:
                    connection.rollback()
                    return str(prior["status"]), str(prior["category"])
                claimed = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM promo_claims
                        WHERE post_id = ? AND category = ?
                          AND status IN ('reserved', 'applied', 'review')
                        """,
                        (post_id, category),
                    ).fetchone()[0]
                )
                if claimed >= limit:
                    connection.rollback()
                    return "full", None
                connection.execute(
                    """
                    INSERT INTO promo_claims (telegram_id, post_id, category, status)
                    VALUES (?, ?, ?, 'reserved')
                    """,
                    (telegram_id, post_id, category),
                )
                connection.commit()
                return "claimed", category
        except sqlite3.Error as error:
            raise PromotionStoreError("Cannot reserve promotion reward") from error

    def finish_reward(
        self, telegram_id: int, post_id: int, *, applied: bool, error_code: str = ""
    ) -> bool:
        status = "applied" if applied else "review"
        try:
            with closing(self._connect()) as connection:
                changed = connection.execute(
                    """
                    UPDATE promo_claims
                    SET status = ?, error_code = ?, updated_at = datetime('now')
                    WHERE telegram_id = ? AND post_id = ? AND status = 'reserved'
                    """,
                    (status, error_code[:80] or None, telegram_id, post_id),
                ).rowcount
                connection.commit()
                return changed == 1
        except sqlite3.Error as error:
            raise PromotionStoreError("Cannot finish promotion reward") from error

    def claim(self, telegram_id: int) -> dict[str, Any] | None:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT * FROM promo_claims WHERE telegram_id = ?",
                    (telegram_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise PromotionStoreError("Cannot read promotion claim") from error
        return None if row is None else dict(row)
