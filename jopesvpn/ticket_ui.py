from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from jopesvpn.i18n import t, tn
from jopesvpn.timezones import format_datetime


CATEGORIES = {
    "connection": "ticket.category_connection",
    "account": "ticket.category_account",
    "other": "ticket.category_other",
}
TICKET_DETAIL_LIMIT = 3500
TICKET_MESSAGES_PER_PAGE = 4
USER_TICKETS_PER_PAGE = 10
ADMIN_TICKETS_PER_PAGE = 20


def category_text(category: str) -> str:
    return t(CATEGORIES.get(category, "ticket.category_other"))


def _relative_age(value: str) -> str:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return t("time.unknown")
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    seconds = max(0, int((datetime.now(UTC) - timestamp.astimezone(UTC)).total_seconds()))
    if seconds < 60:
        return t("ticket.age_now")
    if seconds < 3600:
        return t("ticket.age_value", value=tn("plural.minutes", seconds // 60))
    if seconds < 86400:
        return t("ticket.age_value", value=tn("plural.hours", seconds // 3600))
    return t("ticket.age_value", value=tn("plural.days", seconds // 86400))


def category_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("ticket.category_connection"),
                    callback_data="ticket:category:connection",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("ticket.category_account"),
                    callback_data="ticket:category:account",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("ticket.category_other"),
                    callback_data="ticket:category:other",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("common.cancel"), callback_data="ticket:cancel"
                )
            ],
        ]
    )


def ticket_list_text(tickets: list[dict[str, Any]]) -> str:
    if not tickets:
        return f"<b>{t('ticket.my_title')}</b>\n\n{t('ticket.empty')}"
    lines = [f"<b>{t('ticket.my_title')}</b>", ""]
    for item in tickets:
        status = "🔓" if item["status"] == "open" else "🔒"
        category = {
            "connection": "Подключение",
            "account": "Доступ и тариф",
            "other": "Другой вопрос",
        }.get(str(item["category"]), "Другой вопрос")
        lines.append(
            f"[{item['ticket_id']}] {status} {category} · "
            f"{_relative_age(str(item['updated_at']))}"
        )
    return "\n".join(lines)


def ticket_list_keyboard(
    tickets: list[dict[str, Any]],
    *,
    admin: bool = False,
    page: int = 0,
    total: int = 0,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if not admin:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("ticket.open_button"), callback_data="ticket:open"
                )
            ]
        )
    for item in tickets:
        ticket_id = int(item["ticket_id"])
        if admin:
            label = str(ticket_id)
            callback = f"ticket:admin:view:{ticket_id}"
        else:
            label = str(ticket_id)
            callback = f"ticket:view:{ticket_id}"
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=callback)]
        )
    if admin:
        pages = max(
            1,
            (total + ADMIN_TICKETS_PER_PAGE - 1) // ADMIN_TICKETS_PER_PAGE,
        )
        page = min(max(page, 0), pages - 1)
        navigation: list[InlineKeyboardButton] = []
        if page > 0:
            navigation.append(
                InlineKeyboardButton(
                    text=t("common.previous"),
                    callback_data=f"ticket:admin:list:{page - 1}",
                )
            )
        if page < pages - 1:
            navigation.append(
                InlineKeyboardButton(
                    text=t("common.next"),
                    callback_data=f"ticket:admin:list:{page + 1}",
                )
            )
        if navigation:
            rows.append(navigation)
    elif total > USER_TICKETS_PER_PAGE:
        pages = (total + USER_TICKETS_PER_PAGE - 1) // USER_TICKETS_PER_PAGE
        page = min(max(page, 0), pages - 1)
        navigation = []
        if page > 0:
            navigation.append(
                InlineKeyboardButton(
                    text=t("common.previous"),
                    callback_data=f"ticket:mine:{page - 1}",
                )
            )
        if page < pages - 1:
            navigation.append(
                InlineKeyboardButton(
                    text=t("common.next"),
                    callback_data=f"ticket:mine:{page + 1}",
                )
            )
        if navigation:
            rows.append(navigation)
    rows.append(
        [
            InlineKeyboardButton(
                text=t("common.refresh"),
                callback_data="ticket:admin:list" if admin else "ticket:mine",
            ),
            InlineKeyboardButton(
                text=t("common.back"),
                callback_data="admin:home" if admin else "user:help",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ticket_detail_text(
    ticket: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    utc_offset: int,
    admin: bool,
) -> str:
    status = t(
        "ticket.status_open"
        if ticket["status"] == "open"
        else "ticket.status_closed"
    )
    rows = [
        f"<b>{t('ticket.title', ticket_id=ticket['ticket_id'])}</b>",
        t("ticket.category", category=category_text(str(ticket["category"]))),
        t("ticket.status", status=status),
        t("ticket.updated", value=format_datetime(ticket["updated_at"], utc_offset)),
    ]
    if admin:
        rows.append(
            t(
                "ticket.user",
                name=escape(str(ticket["user_name"] or t("common.unknown"))),
                user_id=ticket["user_id"],
            )
        )
    if ticket.get("rating") is not None:
        rows.append(t("ticket.rating_saved", rating=ticket["rating"]))
    rows.extend(("", f"<b>{t('ticket.messages')}</b>"))
    history: list[str] = []
    omitted = False
    rendered_length = sum(map(len, rows))
    for message in reversed(messages):
        role = t(
            "ticket.sender_admin"
            if message["sender_role"] == "admin"
            else "ticket.sender_user"
        )
        raw_body = str(message["body"] or "")
        body = escape(raw_body[:500])
        if len(raw_body) > 500:
            body += "…"
        if message.get("photo_file_id"):
            body = (body + "\n" if body else "") + t("ticket.photo_attached")
        item = (
            f"<blockquote><b>{role}</b> · "
            f"{format_datetime(message['created_at'], utc_offset)}\n{body}</blockquote>"
        )
        if rendered_length + len(item) + 1 > TICKET_DETAIL_LIMIT:
            omitted = True
            break
        history.append(item)
        rendered_length += len(item) + 1
    rows.extend(reversed(history))
    if omitted:
        rows.append(t("ticket.history_older"))
    return "\n".join(rows)


def ticket_detail_keyboard(
    ticket: dict[str, Any],
    *,
    admin: bool,
    has_admin_reply: bool = False,
    portal_enabled: bool = False,
    history_page: int = 0,
    has_older_messages: bool = False,
) -> InlineKeyboardMarkup:
    ticket_id = int(ticket["ticket_id"])
    is_open = ticket["status"] == "open"
    if admin:
        rows = []
        pager: list[InlineKeyboardButton] = []
        if history_page > 0:
            pager.append(
                InlineKeyboardButton(
                    text=t("common.newer"),
                    callback_data=f"ticket:admin:view:{ticket_id}:{history_page - 1}",
                )
            )
        if has_older_messages:
            pager.append(
                InlineKeyboardButton(
                    text=t("common.older"),
                    callback_data=f"ticket:admin:view:{ticket_id}:{history_page + 1}",
                )
            )
        if pager:
            rows.append(pager)
        if portal_enabled:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=t("ticket.portal_button"),
                        callback_data=f"ticket:portal:{ticket_id}",
                    )
                ]
            )
        if is_open:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=t("ticket.reply_button"),
                        callback_data=f"ticket:admin:reply:{ticket_id}",
                    )
                ]
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        text=t("ticket.close_button"),
                        callback_data=f"ticket:admin:close:{ticket_id}",
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("common.back"), callback_data="ticket:admin:list"
                )
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    rows = []
    pager = []
    if history_page > 0:
        pager.append(
            InlineKeyboardButton(
                text=t("common.newer"),
                callback_data=f"ticket:view:{ticket_id}:{history_page - 1}",
            )
        )
    if has_older_messages:
        pager.append(
            InlineKeyboardButton(
                text=t("common.older"),
                callback_data=f"ticket:view:{ticket_id}:{history_page + 1}",
            )
        )
    if pager:
        rows.append(pager)
    if is_open:
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text=t("ticket.reply_button"),
                        callback_data=f"ticket:reply:{ticket_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t("ticket.close_button"),
                        callback_data=f"ticket:close:{ticket_id}",
                    )
                ],
            ]
        )
    else:
        if ticket.get("rating") is None and has_admin_reply:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{score} ★",
                        callback_data=f"ticket:rate:{ticket_id}:{score}",
                    )
                    for score in range(1, 6)
                ]
            )
    rows.append(
        [
            InlineKeyboardButton(text=t("common.back"), callback_data="ticket:mine")
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ticket_close_prompt_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("ticket.close_button"),
                    callback_data=f"ticket:close:{ticket_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("ticket.view_button"),
                    callback_data=f"ticket:view:{ticket_id}",
                )
            ],
        ]
    )


def rating_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{score} ★", callback_data=f"ticket:rate:{ticket_id}:{score}"
                )
                for score in range(1, 6)
            ],
            [
                InlineKeyboardButton(
                    text=t("common.back"),
                    callback_data=f"ticket:view:{ticket_id}",
                )
            ],
        ]
    )


def ticket_admin_summary_text(summary: dict[str, int | float | None]) -> str:
    average = (
        "—" if summary["average"] is None else f"{float(summary['average']):.1f}/5"
    )
    return (
        f"<b>{t('ticket.admin_title')}</b>\n\n"
        f"{t('ticket.admin_open_count', count=summary['open'])}\n"
        f"{t('ticket.admin_rating', average=average, count=summary['rated'])}"
    )
