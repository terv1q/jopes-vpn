from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from secrets import token_urlsafe
from urllib.parse import quote, urlsplit

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from jopesvpn.i18n import (
    LANGUAGE_FLAGS,
    SUPPORTED_LANGUAGES,
    current_language,
    t,
    tn,
)
from jopesvpn.panel import PanelDevice, PanelUser
from jopesvpn.plans import (
    Plan,
    PlanTerm,
    format_price,
    format_rubles,
    term_savings,
    title_for_panel_tag,
)
from jopesvpn.timezones import (
    ADMIN_UTC_OFFSET,
    SUPPORTED_UTC_OFFSETS,
    current_utc_offset,
    format_datetime,
    utc_label,
)


def channel_title() -> str:
    return t("channel.title")


STATUS_LABELS = {
    "ACTIVE": "status.active",
    "DISABLED": "status.disabled",
    "LIMITED": "status.limited",
    "EXPIRED": "status.expired",
}


def _box(title: str, rows: list[str]) -> str:
    if not rows:
        return f"<blockquote>┌ <b>{title}</b>\n└ —</blockquote>"
    lines = [f"┌ <b>{title}</b>"]
    for index, row in enumerate(rows):
        branch = "└" if index == len(rows) - 1 else "├"
        lines.append(f"{branch} {row}")
    return "<blockquote>" + "\n".join(lines) + "</blockquote>"


def _heading(title: str) -> str:
    return f"<b>{title.upper()}</b>"


def keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
            for row in rows
        ]
    )


def _url_button(text: str, url: str) -> InlineKeyboardButton:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Telegram inline buttons require an HTTPS URL")
    return InlineKeyboardButton(text=text, url=url)


def support_url(contact: str | None) -> str | None:
    if not contact:
        return None
    value = contact.strip()
    if value.startswith("https://t.me/") or value.startswith("http://t.me/"):
        return value.replace("http://", "https://", 1)
    username = value.lstrip("@")
    if username and all(
        character.isascii() and (character.isalnum() or character == "_")
        for character in username
    ):
        return f"https://t.me/{username}"
    return None


def _support_line(contact: str | None) -> str:
    return t("support.ticket_hint")


def _parse_expiry(user: PanelUser) -> datetime | None:
    if not user.expire_at:
        return None
    try:
        expires = datetime.fromisoformat(user.expire_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if expires.tzinfo is None:
        return None
    return expires.astimezone(UTC)


def expiry_text(user: PanelUser) -> str:
    if _has_traffic_package_expiry(user):
        return t("traffic.valid_until_exhausted")
    expires = _parse_expiry(user)
    if expires is None:
        return t("time.unknown")
    return format_datetime(expires, current_utc_offset())


def days_left(user: PanelUser) -> int | None:
    if _has_traffic_package_expiry(user):
        return None
    expires = _parse_expiry(user)
    if expires is None:
        return None
    seconds = (expires - datetime.now(UTC)).total_seconds()
    return int(seconds // 86400)


def remaining_text(user: PanelUser) -> str:
    if _has_traffic_package_expiry(user):
        return t("traffic.valid_until_exhausted")
    expires = _parse_expiry(user)
    if expires is None:
        return t("time.unknown")
    seconds = (expires - datetime.now(UTC)).total_seconds()
    if seconds <= 0:
        return t("time.expired")
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    if days > 0:
        return t(
            "time.days_hours",
            days=days,
            day_word=tn("plural.days", days),
            hours=hours,
        )
    return t("time.hours", hours=hours)


def format_bytes(value: int | None) -> str:
    if value is None:
        return t("common.unknown")
    gigabytes = value / (1024**3)
    if gigabytes >= 100:
        return t("units.gb", value=f"{gigabytes:.0f}")
    if gigabytes >= 1:
        return t("units.gb", value=f"{gigabytes:.1f}")
    megabytes = value / (1024**2)
    if megabytes >= 1:
        return t("units.mb", value=f"{megabytes:.0f}")
    return t("units.bytes", value=value)


def traffic_text(user: PanelUser) -> str:
    if user.traffic_limit_bytes is None:
        return t("traffic.data_unavailable")
    if user.traffic_limit_bytes == 0:
        if user.traffic_used_bytes is None:
            return t("traffic.unlimited")
        return t("traffic.used_unlimited", used=format_bytes(user.traffic_used_bytes))
    if user.traffic_used_bytes is None:
        return t(
            "traffic.remaining_unknown",
            total=format_bytes(user.traffic_limit_bytes),
        )
    remaining = max(user.traffic_limit_bytes - user.traffic_used_bytes, 0)
    return t(
        "traffic.remaining",
        remaining=format_bytes(remaining),
        total=format_bytes(user.traffic_limit_bytes),
        used=format_bytes(user.traffic_used_bytes),
    )


def _has_traffic_package_expiry(user: PanelUser) -> bool:
    return (
        user.traffic_limit_bytes not in (None, 0)
        and user.expire_at is not None
        and user.expire_at.startswith("2099-")
    )


def status_text(user: PanelUser) -> str:
    key = STATUS_LABELS.get(user.status)
    if key is None:
        return t("status.unknown", status=escape(user.status))
    return t(key)


def tariff_text(user: PanelUser) -> str | None:
    if user.tag is None:
        return None
    if user.tag == "TRIAL":
        return t("tariff.trial")
    if user.tag == "ADMIN":
        return t("tariff.admin")
    if user.tag == "FREE":
        return t("tariff.free")
    title = title_for_panel_tag(user.tag)
    return escape(title) if title is not None else t("tariff.unknown")


def device_limit_text(user: PanelUser) -> str:
    if user.hwid_device_limit in (None, 0):
        return t("common.unknown")
    return str(user.hwid_device_limit)


def devices_usage_text(user: PanelUser, devices_count: int | None) -> str:
    count = t("common.unknown") if devices_count is None else str(devices_count)
    return f"{count} / {device_limit_text(user)}"


def home_keyboard(
    is_admin: bool,
    connect_url: str | None = None,
    *,
    has_access: bool = False,
    trial_available: bool = False,
    onboarding: bool = False,
) -> InlineKeyboardMarkup:
    if onboarding:
        primary = InlineKeyboardButton(
            text=t("setup.start_button"), callback_data="user:setup"
        )
    elif has_access:
        primary = (
            _url_button(t("common.connect"), connect_url)
            if connect_url is not None
            else InlineKeyboardButton(
                text=t("common.connect"), callback_data="user:connect"
            )
        )
    elif trial_available:
        primary = InlineKeyboardButton(
            text=t("common.trial"), callback_data="user:trial"
        )
    else:
        primary = InlineKeyboardButton(
            text=t("common.plans"), callback_data="user:plans"
        )
    rows: list[list[InlineKeyboardButton]] = [
        [primary],
        [
            InlineKeyboardButton(
                text=t("common.help"), callback_data="user:help"
            ),
            InlineKeyboardButton(
                text=t("common.more"), callback_data="user:more"
            ),
        ],
        [
            InlineKeyboardButton(
                text=t("common.about"), callback_data="user:about"
            )
        ],
    ]
    if has_access:
        rows.insert(
            1,
            [
                InlineKeyboardButton(
                    text=t("common.subscription"),
                    callback_data="user:subscription",
                )
            ],
        )
    if is_admin:
        rows.append(
            [InlineKeyboardButton(text=t("admin.panel_button"), callback_data="admin:home")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def more_keyboard() -> InlineKeyboardMarkup:
    return keyboard(
        [
            [(t("common.cabinet"), "user:cabinet")],
            [(t("common.subscription"), "user:subscription")],
            [(t("common.devices"), "user:devices")],
            [(t("common.stats"), "user:stats")],
            [(t("common.plans"), "user:plans")],
            [(t("common.guides"), "user:guides")],
            [(t("common.about"), "user:about")],
            [(t("common.cooperation"), "user:referrals")],
            [(t("common.settings"), "user:settings")],
            [(t("common.help"), "user:help")],
            [(t("common.back"), "user:back")],
        ]
    )


def about_text(
    plans: tuple[Plan, ...],
    *,
    traffic_plans_enabled: bool,
    free_access_enabled: bool,
    documents_configured: bool,
) -> str:
    del plans, traffic_plans_enabled
    lines = [_heading(t("about.title")), t("about.intro")]
    lines.append(t("about.tariff_process"))
    lines.append(
        t("about.free_enabled")
        if free_access_enabled
        else t("about.free_unavailable")
    )
    lines.append(
        f"<b>{t('about.payment_title')}</b>\n{t('about.payment_status')}"
    )
    lines.append(t("about.connection"))
    lines.append(t("about.refunds"))
    lines.append(
        t("about.documents_ready")
        if documents_configured
        else t("about.documents_missing")
    )
    return "\n\n".join(lines)


def about_keyboard(
    privacy_url: str | None,
    agreement_url: str | None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for label, url, document in (
        (t("docs.agreement_button"), agreement_url, "agreement"),
        (t("docs.privacy_button"), privacy_url, "privacy"),
    ):
        rows.append(
            [
                _url_button(label, url)
                if url
                else InlineKeyboardButton(
                    text=label, callback_data=f"about:missing:{document}"
                )
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t("common.guides"), callback_data="user:guides"
                ),
                InlineKeyboardButton(
                    text=t("common.plans"), callback_data="user:plans"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("common.support"), callback_data="user:help"
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("common.back"), callback_data="user:back"
                ),
                InlineKeyboardButton(
                    text=t("common.menu"), callback_data="user:home"
                ),
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plan_terms_keyboard(plan: Plan) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for term in plan.terms:
        savings, discount_percent = term_savings(plan, term)
        label = (
            f"💸 {format_rubles(term.price_rub_minor)} | "
            f"{term.days} дней"
        )
        if savings:
            label += f" (−{discount_percent}%)"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label[:64],
                    callback_data=f"term:{plan.code}:{term.days}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=t("common.back"),
                callback_data=(
                    f"user:plans:traffic:family:{plan.traffic_limit_strategy}"
                    if plan.traffic_limit_gb is not None
                    else "user:plans:time"
                ),
            ),
            InlineKeyboardButton(
                text=t("common.menu"), callback_data="user:home"
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_methods_keyboard(plan: Plan, term: PlanTerm) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("plans.pay_stars", amount=term.stars_amount),
                callback_data=f"pay:stars:{plan.code}:{term.days}",
            )
        ]
    ]
    for method in ("sbp", "card", "crypto"):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(
                        f"plans.pay_{method}",
                        amount=format_rubles(term.price_rub_minor),
                    )[:64],
                    callback_data=(
                        f"pay:unavailable:{method}:{plan.code}:{term.days}"
                    ),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=t("common.back"), callback_data=f"checkout:{plan.code}"
            ),
            InlineKeyboardButton(
                text=t("common.menu"), callback_data="user:home"
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def setup_keyboard(
    step: int,
    *,
    trial_available: bool,
    page_url: str | None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if step == 0:
        if trial_available:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=t("setup.get_trial"), callback_data="user:trial"
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("common.plans"), callback_data="user:plans"
                )
            ]
        )
    elif step == 1:
        if page_url is not None:
            rows.append([_url_button(t("setup.open_page"), page_url)])
        rows.append(
            [InlineKeyboardButton(text=t("setup.next"), callback_data="setup:next")]
        )
    elif step == 2:
        if page_url is not None:
            rows.append([_url_button(t("setup.open_page"), page_url)])
        rows.append(
            [InlineKeyboardButton(text=t("setup.next"), callback_data="setup:next")]
        )
    else:
        if page_url is not None:
            rows.append([_url_button(t("setup.open_page"), page_url)])
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("setup.finish"), callback_data="setup:finish"
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text=t("common.menu"), callback_data="user:home")]
    )
    if step > 0:
        rows.insert(
            -1,
            [InlineKeyboardButton(text=t("common.back"), callback_data="setup:back")],
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_keyboard(target: str = "user:back") -> InlineKeyboardMarkup:
    return keyboard([[(t("common.back"), target), (t("common.menu"), "user:home")]])


def documents_keyboard(
    privacy_url: str | None,
    agreement_url: str | None,
    *,
    acceptance_required: bool = False,
    can_accept: bool = True,
    version: str = "1",
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for label, url, document in (
        (t("docs.privacy_button"), privacy_url, "privacy"),
        (t("docs.agreement_button"), agreement_url, "agreement"),
    ):
        rows.append(
            [
                _url_button(label, url)
                if url
                else InlineKeyboardButton(
                    text=label, callback_data=f"docs:missing:{document}"
                )
            ]
        )
    if (
        acceptance_required
        and can_accept
        and privacy_url is not None
        and agreement_url is not None
    ):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("docs.accept_button"),
                    callback_data=f"docs:accept:{version}",
                )
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t("ticket.open_button"), callback_data="ticket:open"
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("common.back"), callback_data="user:back"
                ),
                InlineKeyboardButton(
                    text=t("common.menu"), callback_data="user:home"
                ),
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_documents_keyboard() -> InlineKeyboardMarkup:
    return keyboard(
        [
            [
                (
                    t("docs.admin_set_privacy"),
                    "admin:docs:privacy",
                )
            ],
            [
                (
                    t("docs.admin_set_agreement"),
                    "admin:docs:agreement",
                )
            ],
            [(t("common.back"), "admin:home")],
        ]
    )


def cabinet_keyboard(page_url: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[tuple[str, str]]] = [
        [(t("common.subscription"), "user:subscription")],
        [(t("common.more"), "user:more")],
        [(t("common.help"), "user:help")],
        [(t("common.refresh"), "user:cabinet")],
        [(t("common.back"), "user:back"), (t("common.menu"), "user:home")],
    ]
    inline_keyboard = []
    if page_url is not None:
        inline_keyboard.append(
            [_url_button(t("common.subscription_page"), page_url)]
        )
    inline_keyboard.extend(
        [
            [
                InlineKeyboardButton(text=label, callback_data=data)
                for label, data in row
            ]
            for row in rows
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


def subscription_keyboard(
    page_url: str | None = None,
    *,
    has_access: bool = True,
    support: str | None = None,
    trial_available: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if has_access:
        if page_url is not None:
            rows.append([_url_button(t("connect.button_page"), page_url)])
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("plans.extend_button"), callback_data="user:plans"
                ),
                InlineKeyboardButton(
                    text=t("connect.button_problems"),
                    callback_data="user:troubleshoot",
                ),
            ]
        )
    else:
        if trial_available:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=t("common.trial"), callback_data="user:trial"
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("common.plans"), callback_data="user:plans"
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("common.guides"), callback_data="user:guides"
                ),
                InlineKeyboardButton(
                    text=t("common.support"), callback_data="user:help"
                ),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text=t("common.back"), callback_data="user:back"),
            InlineKeyboardButton(text=t("common.menu"), callback_data="user:home"),
        ]
    )
    rows.insert(
        -1,
        [
            InlineKeyboardButton(
                text=t("ticket.open_button"), callback_data="user:help"
            )
        ],
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def connect_keyboard(
    page_url: str | None = None,
    *,
    has_access: bool = True,
    support: str | None = None,
    trial_available: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if has_access and page_url is not None:
        rows.append([_url_button(t("connect.button_page"), page_url)])
    if has_access:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("common.guides"), callback_data="user:guides"
                ),
                InlineKeyboardButton(
                    text=t("connect.button_problems"),
                    callback_data="user:troubleshoot",
                ),
            ]
        )
    else:
        if trial_available:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=t("common.trial"), callback_data="user:trial"
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("common.plans"), callback_data="user:plans"
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("common.guides"), callback_data="user:guides"
                ),
                InlineKeyboardButton(
                    text=t("common.support"), callback_data="user:help"
                ),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text=t("common.back"), callback_data="user:back"),
            InlineKeyboardButton(text=t("common.menu"), callback_data="user:home"),
        ]
    )
    rows.insert(
        -1,
        [
            InlineKeyboardButton(
                text=t("ticket.open_button"), callback_data="user:help"
            )
        ],
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plans_keyboard(
    plans: tuple[Plan, ...],
    *,
    trial_available: bool = False,
    traffic_plans_enabled: bool = False,
    category: str | None = None,
    traffic_family: str | None = None,
    free_for_admin: bool = False,
    free_available: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if category is None:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("plans.category_time"),
                    callback_data="user:plans:time",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("plans.category_traffic"),
                    callback_data="user:plans:traffic",
                )
            ]
        )
        if free_available:
            rows.insert(
                0,
                [
                    InlineKeyboardButton(
                        text=t("plans.free_button"),
                        callback_data="user:plans:free",
                    )
                ],
            )
        if trial_available:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=t("plans.button_trial"), callback_data="user:trial"
                    )
                ]
            )
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text=t("plans.button_how"), callback_data="user:guides"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t("ticket.open_button"),
                        callback_data="ticket:category:account",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t("common.back"), callback_data="user:back"
                    ),
                    InlineKeyboardButton(
                        text=t("common.menu"), callback_data="user:home"
                    ),
                ],
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    if category == "free":
        if free_available:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=t("plans.free_confirm"),
                        callback_data="user:plans:free:confirm",
                    )
                ]
            )
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text=t("plans.back_to_categories"),
                        callback_data="user:plans",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t("common.menu"), callback_data="user:home"
                    )
                ],
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    if category == "traffic" and traffic_family is None:
        families: dict[str, list[Plan]] = {}
        for plan in plans:
            if (
                plan.sellable
                and plan.traffic_limit_gb is not None
                and (traffic_plans_enabled or free_for_admin)
                and (free_for_admin or (
                    plan.price_minor is not None and plan.currency == "XTR"
                ))
            ):
                families.setdefault(plan.traffic_limit_strategy, []).append(plan)
        family_order = ("NO_RESET", "DAY", "WEEK", "MONTH", "MONTH_ROLLING")
        for family in family_order:
            variants = families.get(family)
            if not variants:
                continue
            rows.append(
                [
                    InlineKeyboardButton(
                        text=t(
                            f"plans.family_{family.lower()}",
                            count=len(variants),
                        )[:64],
                        callback_data=(
                            f"user:plans:traffic:family:{family}"
                        ),
                    )
                ]
            )
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text=t("plans.back_to_categories"),
                        callback_data="user:plans",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t("common.menu"), callback_data="user:home"
                    )
                ],
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    if trial_available:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("plans.button_trial"), callback_data="user:trial"
                )
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=(
                        f"{plan.title} · "
                        f"{t('plans.admin_free') if free_for_admin else format_price(plan)}"
                    )[:64],
                    callback_data=(
                        f"pay:{plan.code}:{token_urlsafe(12)}"
                        if free_for_admin
                        else f"checkout:{plan.code}"
                    ),
                )
            ]
            for plan in plans
            if (
                plan.sellable
                and (
                    free_for_admin
                    or (
                        plan.price_minor is not None
                        and plan.currency == "XTR"
                    )
                )
                and (
                    (category == "time" and plan.traffic_limit_gb is None)
                    or (
                        category == "traffic"
                        and (
                            traffic_family is None
                            or plan.traffic_limit_strategy == traffic_family
                        )
                        and (traffic_plans_enabled or free_for_admin)
                        and plan.traffic_limit_gb is not None
                    )
                )
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t("plans.back_to_categories"),
                callback_data="user:plans",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t("plans.button_how"), callback_data="user:guides"
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t("ticket.open_button"),
                callback_data="ticket:category:account",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text=t("common.back"), callback_data="user:back"),
            InlineKeyboardButton(text=t("common.menu"), callback_data="user:home"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def trial_keyboard(channel_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_url_button(t("trial.button_channel"), channel_url)],
            [
                _url_button(
                    t("trial.button_community"),
                    "https://t.me/JopesVpnChat",
                ),
                _url_button(
                    t("trial.button_reviews"),
                    "https://t.me/JopesVpnReviews",
                ),
            ],
            [
                _url_button(
                    t("trial.button_resources"),
                    "https://t.me/addlist/cEJd4DRCx25kMmEy",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("trial.button_check"), callback_data="trial:check"
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("common.back"), callback_data="user:back"
                ),
                InlineKeyboardButton(
                    text=t("common.menu"), callback_data="user:home"
                ),
            ],
        ]
    )


def profile_keyboard() -> InlineKeyboardMarkup:
    return cabinet_keyboard()


def help_keyboard(support: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=t("ticket.open_button"), callback_data="ticket:open"
            )
        ],
        [
            InlineKeyboardButton(
                text=t("ticket.my_button"), callback_data="ticket:mine"
            )
        ],
        [
            InlineKeyboardButton(
                text=t("common.how_connect"), callback_data="user:guides"
            )
        ],
        [
            InlineKeyboardButton(
                text=t("common.problems"), callback_data="user:troubleshoot"
            )
        ],
    ]
    rows.append(
        [
            InlineKeyboardButton(text=t("common.back"), callback_data="user:back"),
            InlineKeyboardButton(text=t("common.menu"), callback_data="user:home"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def stats_keyboard() -> InlineKeyboardMarkup:
    return keyboard(
        [
            [
                (t("common.devices"), "user:devices"),
                (t("common.subscription"), "user:subscription"),
            ],
            [(t("common.refresh"), "user:stats")],
            [(t("common.back"), "user:back"), (t("common.menu"), "user:home")],
        ]
    )


def referral_share_url(invite_url: str) -> str:
    text = "Подключайтесь к Jopes VPN по моей ссылке"
    return (
        "https://t.me/share/url?url="
        + quote(invite_url, safe="")
        + "&text="
        + quote(text, safe="")
    )


def referral_keyboard(share_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_url_button(t("referral.share"), share_url)],
            [
                InlineKeyboardButton(
                    text=t("common.refresh"), callback_data="user:referrals"
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("common.back"), callback_data="user:back"
                ),
                InlineKeyboardButton(
                    text=t("common.menu"), callback_data="user:home"
                ),
            ],
        ]
    )


def referral_text(stats: dict[str, int], invite_url: str) -> str:
    return "\n\n".join(
        [
            _heading(t("referral.title")),
            _box(
                t("referral.stats"),
                [
                    t("referral.invited", count=stats["invited"]),
                    t("referral.activated", count=stats["activated"]),
                    t(
                        "referral.waiting",
                        count=max(0, stats["invited"] - stats["activated"]),
                    ),
                    t("referral.rewarded", days=stats["reward_days"]),
                    t("referral.pending", count=stats["pending"]),
                ],
            ),
            t("referral.rules"),
            t("referral.link", link=escape(invite_url)),
        ]
    )


DEVICES_PER_PAGE = 8


def devices_keyboard(
    devices: list[PanelDevice], page: int = 0
) -> InlineKeyboardMarkup:
    pages = max(1, (len(devices) + DEVICES_PER_PAGE - 1) // DEVICES_PER_PAGE)
    page = min(max(page, 0), pages - 1)
    start = page * DEVICES_PER_PAGE
    rows: list[list[InlineKeyboardButton]] = []
    for offset, device in enumerate(devices[start : start + DEVICES_PER_PAGE]):
        label = device.device_model or device.platform or t("common.devices")
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("devices.delete_button", label=label)[:60],
                    callback_data=f"user:dev:del:{start + offset}",
                )
            ]
        )
    pagination: list[InlineKeyboardButton] = []
    if page > 0:
        pagination.append(
            InlineKeyboardButton(
                text=t("devices.page_prev"), callback_data=f"user:devices:{page - 1}"
            )
        )
    if page < pages - 1:
        pagination.append(
            InlineKeyboardButton(
                text=t("devices.page_next"), callback_data=f"user:devices:{page + 1}"
            )
        )
    if pagination:
        rows.append(pagination)
    rows.append(
        [
            InlineKeyboardButton(
                text=t("common.refresh"), callback_data=f"user:devices:{page}"
            ),
            InlineKeyboardButton(
                text=t("devices.button_how"), callback_data="user:guides"
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text=t("common.back"), callback_data="user:back"),
            InlineKeyboardButton(text=t("common.menu"), callback_data="user:home"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def device_confirm_keyboard(index: int) -> InlineKeyboardMarkup:
    return keyboard(
        [
            [(t("devices.confirm_yes"), f"user:dev:rm:{index}")],
            [(t("common.cancel"), "user:devices")],
        ]
    )


def settings_keyboard(reminders_enabled: bool) -> InlineKeyboardMarkup:
    toggle = (
        (t("settings.disable_reminders"), "user:set:rem:0")
        if reminders_enabled
        else (t("settings.enable_reminders"), "user:set:rem:1")
    )
    rows: list[list[tuple[str, str]]] = [[toggle]]
    rows.append([(t("settings.timezone_button"), "user:timezone")])
    rows.append([(t("settings.language_button"), "user:languages")])
    rows.extend(
        [
            [
                (t("common.support"), "user:help"),
                (t("common.guides"), "user:guides"),
            ],
            [(t("common.back"), "user:back"), (t("common.menu"), "user:home")],
        ]
    )
    return keyboard(rows)


LANGUAGES_PER_PAGE = 8


def language_page_count(current: str) -> int:
    current = current if current in SUPPORTED_LANGUAGES else "ru"
    available = sum(code != current for code in SUPPORTED_LANGUAGES)
    return max(1, (available + LANGUAGES_PER_PAGE - 1) // LANGUAGES_PER_PAGE)


def language_text(current: str, page: int, pages: int) -> str:
    code = current if current in SUPPORTED_LANGUAGES else "ru"
    name = SUPPORTED_LANGUAGES.get(code, code)
    flag = LANGUAGE_FLAGS.get(code, "🌐")
    return "\n\n".join(
        [
            _heading(t("settings.language_title")),
            t("settings.language_current", flag=flag, name=name),
            t("settings.language_page", page=page + 1, pages=pages),
        ]
    )


def language_keyboard(current: str, page: int = 0) -> InlineKeyboardMarkup:
    current = current if current in SUPPORTED_LANGUAGES else "ru"
    choices = [
        (code, name)
        for code, name in SUPPORTED_LANGUAGES.items()
        if code != current
    ]
    pages = language_page_count(current)
    page = min(max(page, 0), pages - 1)
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=(
                    f"{LANGUAGE_FLAGS.get(current, '🌐')} "
                    f"{SUPPORTED_LANGUAGES.get(current, current)} · "
                    f"{t('settings.language_selected')}"
                ),
                callback_data=f"user:set:lang:{current}",
            )
        ]
    ]
    page_choices = choices[
        page * LANGUAGES_PER_PAGE : (page + 1) * LANGUAGES_PER_PAGE
    ]
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=f"{LANGUAGE_FLAGS.get(code, '🌐')} {name}",
                    callback_data=f"user:set:lang:{code}",
                )
            ]
            for code, name in page_choices
        ]
    )
    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("common.previous"),
                callback_data=f"user:languages:{page - 1}",
            )
        )
    if page < pages - 1:
        navigation.append(
            InlineKeyboardButton(
                text=t("common.next"),
                callback_data=f"user:languages:{page + 1}",
            )
        )
    if navigation:
        rows.append(navigation)
    rows.append(
        [
            InlineKeyboardButton(
                text=t("common.back"), callback_data="user:back"
            ),
            InlineKeyboardButton(text=t("common.menu"), callback_data="user:home"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def timezone_keyboard(current: int) -> InlineKeyboardMarkup:
    choices = [
        InlineKeyboardButton(
            text=f"{'✓ ' if offset == current else ''}{utc_label(offset)}",
            callback_data=f"user:set:tz:{offset}",
        )
        for offset in SUPPORTED_UTC_OFFSETS
    ]
    rows = [choices[index : index + 4] for index in range(0, len(choices), 4)]
    rows.append(
        [
            InlineKeyboardButton(
                text=t("common.back"), callback_data="user:back"
            ),
            InlineKeyboardButton(text=t("common.menu"), callback_data="user:home"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def timezone_text(offset: int) -> str:
    return "\n\n".join(
        [
            _heading(t("settings.timezone_title")),
            t("settings.timezone_note", offset=utc_label(offset)),
        ]
    )


def guide_platform_keyboard(page_url: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if page_url is not None:
        rows.append([_url_button(t("common.subscription_page"), page_url)])
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t("guides.platform_android"),
                    callback_data="user:guide:android",
                ),
                InlineKeyboardButton(
                    text=t("guides.platform_ios"), callback_data="user:guide:ios"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("guides.platform_windows"),
                    callback_data="user:guide:windows",
                ),
                InlineKeyboardButton(
                    text=t("guides.platform_macos"),
                    callback_data="user:guide:macos",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("guides.platform_linux"),
                    callback_data="user:guide:linux",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("common.what_to_choose"),
                    callback_data="user:guide:android",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("common.back"), callback_data="user:back"
                ),
                InlineKeyboardButton(
                    text=t("common.menu"), callback_data="user:home"
                ),
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


PLATFORM_NAMES = {
    "android": "guides.name_android",
    "ios": "guides.name_ios",
    "windows": "guides.name_windows",
    "macos": "guides.name_macos",
    "linux": "guides.name_linux",
}

APP_NAMES = {
    "happ": "Happ",
    "v2rayng": "v2rayNG",
    "v2raytun": "v2rayTun",
    "streisand": "Streisand",
}

APP_DOWNLOADS = {
    "happ": {
        "android": "https://play.google.com/store/apps/details?id=com.happproxy",
        "ios": "https://apps.apple.com/us/app/happ-proxy-utility/id6504287215",
        "windows": "https://github.com/Happ-proxy/happ-desktop/releases/latest",
        "macos": "https://github.com/Happ-proxy/happ-desktop/releases/latest",
        "linux": "https://github.com/Happ-proxy/happ-desktop/releases/latest",
    },
    "v2rayng": {
        "android": "https://github.com/2dust/v2rayNG/releases/latest",
    },
    "v2raytun": {
        "android": "https://v2raytun.com/",
        "ios": "https://v2raytun.com/",
        "windows": "https://v2raytun.com/",
    },
    "streisand": {
        "ios": "https://apps.apple.com/us/app/streisand/id6450534064",
    },
}

PLATFORM_APPS = {
    "android": ("happ", "v2rayng", "v2raytun"),
    "ios": ("happ", "streisand", "v2raytun"),
    "windows": ("happ", "v2raytun"),
    "macos": ("happ",),
    "linux": ("happ",),
}

APP_IMPORT_STEPS = {
    "happ": ("guides.import.happ.first", "guides.import.happ.second"),
    "v2rayng": ("guides.import.v2rayng.first", "guides.import.v2rayng.second"),
    "v2raytun": ("guides.import.v2raytun.first", "guides.import.v2raytun.second"),
    "streisand": ("guides.import.streisand.first", "guides.import.streisand.second"),
}


def _guide_steps(platform: str, app: str) -> tuple[str, ...]:
    name = APP_NAMES[app]
    platform_name = t(PLATFORM_NAMES[platform])
    import_first_key, import_second_key = APP_IMPORT_STEPS[app]
    ios_note = t("guides.ios_note") if platform == "ios" else ""
    return (
        t("guides.step1", name=name, platform=platform_name),
        t(
            "guides.step2",
            name=name,
            import_first=t(import_first_key),
            import_second=t(import_second_key),
        ),
        t("guides.step3"),
        t("guides.step4", ios_note=ios_note),
        t("guides.step5"),
    )


def guide_step_text(
    platform: str,
    app: str,
    step: int,
    *,
    page_url: str | None = None,
) -> str:
    steps = _guide_steps(platform, app)
    step = max(0, min(step, len(steps) - 1))
    total = len(steps)
    name = APP_NAMES[app]
    lines = [
        f"<b>{name}</b> · {t(PLATFORM_NAMES[platform])}",
        t("guides.step_of", step=step + 1, total=total),
        "",
        steps[step],
        "",
        t("guides.secret_note"),
    ]
    if page_url is None and step == 1:
        lines.append("\n" + t("subscription.page_missing"))
    return "\n".join(lines)


def guide_step_keyboard(
    platform: str,
    app: str,
    step: int,
    *,
    page_url: str | None = None,
) -> InlineKeyboardMarkup:
    total = len(_guide_steps(platform, app))
    rows: list[list[InlineKeyboardButton]] = []
    download = APP_DOWNLOADS.get(app, {}).get(platform)
    if step == 0 and download is not None:
        rows.append([_url_button(t("guides.download"), download)])
    if step == 1:
        if page_url is not None:
            rows.append([_url_button(t("common.subscription_page"), page_url)])
    navigation: list[InlineKeyboardButton] = []
    if step > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("common.previous"),
                callback_data=f"user:g:{platform}:{app}:{step - 1}",
            )
        )
    if step < total - 1:
        navigation.append(
            InlineKeyboardButton(
                text=t("common.next"),
                callback_data=f"user:g:{platform}:{app}:{step + 1}",
            )
        )
    if navigation:
        rows.append(navigation)
    rows.append(
        [
            InlineKeyboardButton(
                text=t("guides.not_working"), callback_data="user:troubleshoot"
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t("common.other_app"),
                callback_data=f"user:guide:{platform}",
            ),
            InlineKeyboardButton(text=t("common.menu"), callback_data="user:home"),
        ]
    )
    rows.insert(
        -1,
        [
            InlineKeyboardButton(
                text=t("common.back"),
                callback_data=f"user:guide:{platform}",
            )
        ],
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def guide_apps_keyboard(platform: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=APP_NAMES[app], callback_data=f"user:g:{platform}:{app}:0"
            )
        ]
        for app in PLATFORM_APPS[platform]
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text=t("common.back"), callback_data="user:back"
            ),
            InlineKeyboardButton(text=t("common.menu"), callback_data="user:home"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_keyboard() -> InlineKeyboardMarkup:
    return keyboard(
        [
            [
                (t("admin.grant"), "admin:grant"),
                (t("admin.find"), "admin:find"),
            ],
            [(t("admin.users"), "admin:list:0")],
            [(t("ticket.admin_button"), "ticket:admin:list")],
            [(t("admin.logs"), "admin:logs")],
            [(t("admin.status"), "admin:status")],
            [(t("docs.admin_menu"), "admin:docs")],
            [(t("admin.broadcast"), "admin:broadcast")],
            [(t("common.menu"), "user:home")],
        ]
    )


def broadcast_keyboard() -> InlineKeyboardMarkup:
    return keyboard(
        [
            [(t("admin.broadcast_send"), "admin:broadcast:send")],
            [(t("common.cancel"), "admin:broadcast:cancel")],
        ]
    )


ADMIN_USERS_PER_PAGE = 8


def tariff_emoji(user: PanelUser) -> str:
    if user.tag == "TRIAL":
        return "🎁"
    if user.tag == "ADMIN":
        return "👑"
    if user.tag:
        return "💎"
    return "👤"


def admin_user_label(user: PanelUser, name: str | None = None) -> str:
    display = (name or "").strip() or (user.username or "").strip()
    if not display:
        display = t("admin.card_username_none")
    telegram_id = user.telegram_id or 0
    return f"{tariff_emoji(user)} {display} ({telegram_id})"[:60]


def admin_users_keyboard(
    users: list[PanelUser],
    page: int,
    pages: int,
    names: dict[int, str] | None = None,
) -> InlineKeyboardMarkup:
    names = names or {}
    rows: list[list[InlineKeyboardButton]] = []
    for user in users:
        if user.telegram_id is None:
            continue
        telegram_id = user.telegram_id
        rows.append(
            [
                InlineKeyboardButton(
                    text=admin_user_label(user, names.get(telegram_id)),
                    callback_data=f"admin:u:{telegram_id}:{page}",
                )
            ]
        )
    pagination: list[InlineKeyboardButton] = []
    if page > 0:
        pagination.append(
            InlineKeyboardButton(
                text=t("devices.page_prev"), callback_data=f"admin:list:{page - 1}"
            )
        )
    if page < pages - 1:
        pagination.append(
            InlineKeyboardButton(
                text=t("devices.page_next"), callback_data=f"admin:list:{page + 1}"
            )
        )
    if pagination:
        rows.append(pagination)
    rows.append(
        [
            InlineKeyboardButton(
                text=t("admin.find"),
                callback_data=f"admin:find:{page}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text=t("admin.grant"), callback_data="admin:grant"),
            InlineKeyboardButton(
                text=t("common.refresh"), callback_data=f"admin:list:{page}"
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text=t("common.back"), callback_data="admin:home"),
            InlineKeyboardButton(text=t("common.menu"), callback_data="user:home"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_user_keyboard(
    telegram_id: int,
    status: str | None = None,
    *,
    page: int | None = None,
    page_url: str | None = None,
    username: str | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    normalized_username = (username or "").strip().lstrip("@")
    if (
        5 <= len(normalized_username) <= 32
        and normalized_username[0].isalpha()
        and normalized_username.isascii()
        and all(char.isalnum() or char == "_" for char in normalized_username)
    ):
        chat_url = f"https://t.me/{normalized_username}"
    else:
        chat_url = f"tg://user?id={telegram_id}"
    rows.append(
        [
            InlineKeyboardButton(
                text=t("admin.open_chat"),
                url=chat_url,
            )
        ]
    )
    if page_url is not None:
        rows.append([_url_button(t("common.subscription_page"), page_url)])
    if status != "LIMITED":
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin.extend"),
                    callback_data=f"admin:extend:{telegram_id}",
                )
            ]
        )
    if status == "DISABLED":
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin.enable"),
                    callback_data=f"admin:confirm:enable:{telegram_id}",
                )
            ]
        )
    elif status == "ACTIVE":
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin.disable"),
                    callback_data=f"admin:confirm:disable:{telegram_id}",
                )
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t("admin.revoke"),
                    callback_data=f"admin:confirm:revoke:{telegram_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin.find_other"), callback_data="admin:find"
                ),
                InlineKeyboardButton(
                    text=t("admin.admin_home"), callback_data="admin:home"
                ),
            ],
        ]
    )
    if page is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin.user_back"), callback_data=f"admin:list:{page}"
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text=t("common.menu"), callback_data="user:home")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_logs_text(
    enabled: bool,
    interval_seconds: int,
    tracked: int,
) -> str:
    rows = [
        t("admin.logs_on" if enabled else "admin.logs_off"),
        t("admin.logs_interval", seconds=interval_seconds),
        t("admin.logs_tracked", count=tracked),
    ]
    note = t("admin.logs_note") if enabled else t("admin.logs_off_hint")
    return "\n\n".join(
        [
            _heading(t("admin.logs_title")),
            _box(t("admin.logs_box"), rows),
            note,
        ]
    )


def admin_logs_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    toggle = (
        (t("admin.logs_disable"), "admin:logs:off")
        if enabled
        else (t("admin.logs_enable"), "admin:logs:on")
    )
    return keyboard(
        [
            [toggle],
            [
                (t("common.back"), "admin:home"),
                (t("common.menu"), "user:home"),
            ],
        ]
    )


def _device_details(device: PanelDevice) -> str:
    details = " · ".join(
        part
        for part in (device.platform, device.os_version, device.device_model)
        if part
    )
    return escape(details or t("devices.unnamed"))


def connection_log_text(
    user: PanelUser,
    device: PanelDevice,
    *,
    name: str | None = None,
    username: str | None = None,
    same_ip_count: int = 0,
) -> str:
    unknown = t("admin.log_unknown")
    account_rows = []
    if name:
        account_rows.append(t("admin.log_tg_name", name=escape(name)))
    if username:
        account_rows.append(
            t("admin.log_tg_username", username=escape(username))
        )
    if user.telegram_id is not None:
        account_rows.append(
            t("admin.log_tg_id", telegram_id=user.telegram_id)
        )
    account_rows.append(t("admin.log_panel_id", panel_id=user.id))
    tariff = tariff_text(user)
    if tariff is not None:
        account_rows.append(t("admin.log_tariff", tariff=tariff))
    account_rows.append(t("admin.log_status", status=status_text(user)))
    account_rows.append(
        t(
            "admin.log_expires",
            expires=format_datetime(user.expire_at, ADMIN_UTC_OFFSET),
        )
    )
    device_rows = [
        t(
            "admin.log_platform",
            platform=_device_details(device),
        )
    ]
    if device.user_agent:
        device_rows.append(
            t(
                "admin.log_client",
                client=escape(device.user_agent[:120]),
            )
        )
    device_rows.append(
        t("admin.log_hwid", hwid=escape(device.hwid or unknown))
    )
    device_rows.append(
        t("admin.log_ip", ip=escape(device.request_ip or unknown))
    )
    if user.last_node_uuid:
        device_rows.append(
            t("admin.log_node", node=escape(user.last_node_uuid))
        )
    seen = format_datetime(device.updated_at or datetime.now(UTC), ADMIN_UTC_OFFSET)
    device_rows.append(t("admin.log_seen", seen=escape(seen)))
    blocks = [
        _heading(t("admin.log_event_title")),
        _box(t("admin.log_account"), account_rows),
        _box(t("admin.log_device"), device_rows),
    ]
    if same_ip_count >= 2:
        blocks.append(t("admin.log_same_ip", count=same_ip_count))
    return "\n\n".join(blocks)


def admin_log_keyboard(
    telegram_id: int,
    status: str | None,
    page_url: str | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=t("admin.log_profile"),
                callback_data=f"admin:u:{telegram_id}:0",
            )
        ]
    ]
    if page_url is not None:
        rows.append([_url_button(t("admin.log_page"), page_url)])
    rows.append(
        [
            InlineKeyboardButton(
                text=t("admin.log_devices"),
                callback_data=f"admin:devices:{telegram_id}:0",
            )
        ]
    )
    if status == "DISABLED":
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin.enable"),
                    callback_data=f"admin:confirm:enable:{telegram_id}",
                )
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin.disable"),
                    callback_data=f"admin:confirm:disable:{telegram_id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=t("admin.log_mute"), callback_data="admin:logs:off"
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t("admin.admin_home"), callback_data="admin:home"
            ),
            InlineKeyboardButton(
                text=t("common.menu"), callback_data="user:home"
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_devices_text(
    user: PanelUser,
    devices: list[PanelDevice] | None,
    *,
    page: int = 0,
    error: str | None = None,
) -> str:
    total = len(devices) if devices is not None else None
    rows = [
        t("cabinet.telegram_id", telegram_id=user.telegram_id or 0),
        t("cabinet.jopes_id", panel_id=user.id),
        t(
            "admin.card_devices",
            devices=devices_usage_text(user, total),
        ),
    ]
    blocks = [
        _heading(t("devices.title")),
        _box(t("common.summary"), rows),
    ]
    if error is not None:
        blocks.append(_box(t("common.error"), [error]))
        return "\n\n".join(blocks)
    devices = devices or []
    if not devices:
        blocks.append(
            _box(t("devices.empty_title"), [t("devices.empty_hwid")])
        )
        return "\n\n".join(blocks)
    pages = max(1, (len(devices) + DEVICES_PER_PAGE - 1) // DEVICES_PER_PAGE)
    page = min(max(page, 0), pages - 1)
    start = page * DEVICES_PER_PAGE
    view_rows: list[str] = [
        t("common.page_of", page=page + 1, pages=pages)
    ]
    for offset, device in enumerate(
        devices[start : start + DEVICES_PER_PAGE], start=start + 1
    ):
        view_rows.append(f"{offset}. {_device_details(device)}")
        if device.updated_at:
            view_rows.append(
                f"   {t('common.updated')}: "
                f"{escape(format_datetime(device.updated_at, ADMIN_UTC_OFFSET))}"
            )
        if device.request_ip:
            view_rows.append(
                f"   {t('admin.log_ip', ip=escape(device.request_ip))}"
            )
    blocks.append(_box(t("common.list"), view_rows))
    blocks.append(t("admin.devices_note"))
    return "\n\n".join(blocks)


def admin_devices_keyboard(
    telegram_id: int,
    page: int,
    pages: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    pagination: list[InlineKeyboardButton] = []
    if page > 0:
        pagination.append(
            InlineKeyboardButton(
                text=t("devices.page_prev"),
                callback_data=f"admin:devices:{telegram_id}:{page - 1}",
            )
        )
    if page < pages - 1:
        pagination.append(
            InlineKeyboardButton(
                text=t("devices.page_next"),
                callback_data=f"admin:devices:{telegram_id}:{page + 1}",
            )
        )
    if pagination:
        rows.append(pagination)
    rows.append(
        [
            InlineKeyboardButton(
                text=t("common.refresh"),
                callback_data=f"admin:devices:{telegram_id}:{page}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t("admin.log_profile"),
                callback_data=f"admin:u:{telegram_id}:0",
            ),
            InlineKeyboardButton(
                text=t("admin.admin_home"), callback_data="admin:home"
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_users_text(
    users: list[PanelUser],
    total: int,
    page: int,
    pages: int,
) -> str:
    rows = [
        t("admin.list_summary") + f": <b>{total}</b>",
        t("admin.list_page", page=page + 1, pages=pages),
    ]
    if not users:
        rows.append(t("admin.list_empty"))
    return "\n\n".join(
        [
            _heading(t("admin.list_title")),
            _box(t("common.summary"), rows),
            t("admin.list_hint"),
        ]
    )


def admin_user_card_text(
    user: PanelUser,
    *,
    devices_count: int | None = None,
    name: str | None = None,
    telegram_id: int | None = None,
) -> str:
    rows = [
        t("admin.card_id", id=user.id),
    ]
    if name:
        rows.append(t("admin.card_name", name=escape(name)))
    rows.extend(
        [
            t("admin.card_username", username=escape(user.username)),
            f"{t('common.telegram_id')}: <code>{telegram_id or user.telegram_id or t('common.unknown')}</code>",
            t("cabinet.status_row", status=status_text(user)),
            t("admin.card_tag", tag=escape(user.tag or t("admin.card_tag_none"))),
            t(
                "cabinet.expires_row",
                expires=format_datetime(user.expire_at, ADMIN_UTC_OFFSET),
            ),
            t("cabinet.traffic_row", traffic=traffic_text(user)),
            t("admin.card_devices", devices=devices_usage_text(user, devices_count)),
        ]
    )
    if user.created_at:
        rows.append(
            t(
                "admin.card_created",
                value=format_datetime(user.created_at, ADMIN_UTC_OFFSET),
            )
        )
    if user.traffic_lifetime_used_bytes is not None:
        rows.append(
            t(
                "admin.card_lifetime",
                value=format_bytes(user.traffic_lifetime_used_bytes),
            )
        )
    if user.online_at:
        rows.append(
            t(
                "admin.card_online",
                value=escape(format_datetime(user.online_at, ADMIN_UTC_OFFSET)),
            )
        )
    return "\n\n".join(
        [
            _heading(t("admin.card_title")),
            _box(t("admin.card_box"), rows),
            t(
                "cabinet.note_active"
                if user.status == "ACTIVE"
                else "subscription.inactive_note"
            ),
        ]
    )


def confirm_keyboard(action: str, telegram_id: int) -> InlineKeyboardMarkup:
    labels = {
        "disable": t("admin.confirm_disable"),
        "enable": t("admin.confirm_enable"),
        "revoke": t("admin.confirm_revoke"),
    }
    return keyboard(
        [
            [
                (
                    labels.get(action, t("admin.confirm")),
                    f"admin:run:{action}:{telegram_id}",
                )
            ],
            [(t("common.cancel"), "admin:home")],
        ]
    )


def confirm_grant_keyboard() -> InlineKeyboardMarkup:
    return keyboard(
        [
            [(t("admin.confirm_grant"), "admin:run:grant")],
            [(t("common.cancel"), "admin:home")],
        ]
    )


def confirm_extend_keyboard() -> InlineKeyboardMarkup:
    return keyboard(
        [
            [(t("admin.confirm_extend"), "admin:run:extend")],
            [(t("common.cancel"), "admin:home")],
        ]
    )


def home_text(
    first_name: str,
    access_summary: str,
    *,
    has_access: bool = False,
    trial_available: bool = False,
) -> str:
    hint = (
        t("home.hint_active")
        if has_access
        else t("home.hint_trial")
        if trial_available
        else t("home.hint_no_trial")
    )
    return (
        f"{t('home.greeting', name=escape(first_name))}\n\n"
        f"{access_summary}\n\n"
        f"{hint}"
    )


def more_text() -> str:
    return _heading(t("more.title"))


def cabinet_text(
    user: PanelUser | None,
    *,
    telegram_id: int,
    devices_count: int | None = None,
    support: str | None = None,
    panel_error: bool = False,
    trial_available: bool = False,
) -> str:
    if panel_error and user is None:
        account_rows = [
            t("cabinet.status_row", status=t("status.no_connection")),
        ]
        return "\n\n".join(
            [
                f"<b>{t('cabinet.title')}</b>",
                _box(t("cabinet.account"), account_rows),
                _box(
                    t("cabinet.check"),
                    [
                        t("cabinet.check_failed"),
                        t("cabinet.check_safe"),
                        t("cabinet.check_retry"),
                    ],
                ),
                _support_line(support),
            ]
        )
    if user is None:
        access_rows = [t("cabinet.no_subscription_note")]
        if trial_available:
            access_rows.append(t("cabinet.trial_available"))
        access_rows.append(t("cabinet.plans_hint"))
        account_rows = [t("cabinet.status_row", status=t("status.none"))]
        return "\n\n".join(
            [
                f"<b>{t('cabinet.title')}</b>",
                _box(t("cabinet.account"), account_rows),
                _box(
                    t("cabinet.access"),
                    access_rows,
                ),
                t("cabinet.about"),
                _support_line(support),
            ]
        )
    account_rows = [t("cabinet.status_row", status=status_text(user))]
    subscription_rows = []
    tariff = tariff_text(user)
    if tariff is not None:
        subscription_rows.append(t("cabinet.tariff_row", tariff=tariff))
    subscription_rows.append(
        t("cabinet.expires_row", expires=expiry_text(user))
    )
    if user.status == "ACTIVE":
        subscription_rows.append(
            t("cabinet.remaining_row", remaining=remaining_text(user))
        )
    subscription_rows.append(t("cabinet.traffic_row", traffic=traffic_text(user)))
    subscription_rows.append(
        t(
            "cabinet.devices_row",
            devices=devices_usage_text(user, devices_count),
        )
    )
    if user.status == "EXPIRED":
        note = t("cabinet.note_expired")
    elif user.status == "DISABLED":
        note = t("cabinet.note_disabled")
    elif user.status == "LIMITED":
        note = t("cabinet.note_limited")
    elif user.status == "ACTIVE":
        note = t("cabinet.note_active")
    else:
        note = t("cabinet.note_unknown")
    return "\n\n".join(
        [
            f"<b>{t('cabinet.title')}</b>",
            _box(t("cabinet.account"), account_rows),
            t("cabinet.about"),
            _box(t("cabinet.subscription_box"), subscription_rows),
            note,
            _support_line(support),
        ]
    )


def profile_text(user: PanelUser) -> str:
    return cabinet_text(user, telegram_id=user.telegram_id or 0)


def subscription_text(
    user: PanelUser | None,
    subscription_url: str | None = None,
    page_url: str | None = None,
    *,
    support: str | None = None,
    devices_count: int | None = None,
) -> str:
    if user is None:
        return no_subscription_text()
    rows = [t("subscription.status_row", status=status_text(user))]
    tariff = tariff_text(user)
    if tariff is not None:
        rows.append(t("subscription.tariff_row", tariff=tariff))
    rows.append(t("subscription.expires_row", expires=expiry_text(user)))
    if user.status == "ACTIVE":
        rows.append(t("subscription.remaining_row", remaining=remaining_text(user)))
    rows.append(t("subscription.traffic_row", traffic=traffic_text(user)))
    rows.append(
        t(
            "subscription.devices_row",
            devices=devices_usage_text(user, devices_count),
        )
    )
    if user.status == "ACTIVE":
        note = t("subscription.active_note")
        if page_url is None:
            note += "\n\n" + t("subscription.page_missing")
        else:
            note += "\n\n" + t("subscription.secret_hidden")
    elif user.status == "EXPIRED":
        note = t("subscription.expired_note")
    elif user.status == "DISABLED":
        note = t("subscription.disabled_note")
    elif user.status == "LIMITED":
        note = t("subscription.limited_note")
    else:
        note = t("subscription.inactive_note")
    return "\n\n".join(
        [
            _heading(t("subscription.title")),
            _box(t("common.parameters"), rows),
            note,
            _support_line(support),
        ]
    )


def no_subscription_text(*, trial_available: bool = False) -> str:
    details = [t("no_subscription.none")]
    if trial_available:
        details.append(t("no_subscription.trial"))
    return "\n\n".join(
        [
            _heading(t("no_subscription.title")),
            _box(
                t("no_subscription.status_box"),
                [
                    t("no_subscription.none"),
                    *details,
                ],
            ),
            t("no_subscription.hint"),
        ]
    )


def trial_text(trial_days: int, channel_url: str) -> str:
    hours = trial_days * 24
    return "\n\n".join(
        [
            _heading(t("trial.title")),
            _box(
                t("common.conditions"),
                [
                    t(
                        "trial.duration",
                        hours_word=tn("plural.hours", hours),
                    ),
                    t("trial.once"),
                    t("trial.ends"),
                ],
            ),
            t("trial.how")
            + "\n"
            + t("trial.step_channel", channel=escape(channel_title()))
            + "\n"
            + t("trial.step_check"),
            t(
                "trial.resources",
                channel=f'<a href="{escape(channel_url, quote=True)}">{t("trial.resource_channel")}</a>',
                community='<a href="https://t.me/JopesVpnChat">сообщество</a>',
                reviews='<a href="https://t.me/JopesVpnReviews">отзывы</a>',
            ),
        ]
    )


def trial_unavailable_text() -> str:
    return "\n\n".join(
        [
            _heading(t("trial.title")),
            _box(
                t("trial.unavailable_title"),
                [
                    t("trial.already_created"),
                    t("trial.once_only"),
                ],
            ),
            t("trial.check_subscription"),
        ]
    )


def plans_text(
    plans: tuple[Plan, ...],
    trial_days: int = 3,
    *,
    trial_available: bool = False,
    traffic_plans_enabled: bool = False,
    category: str | None = None,
    traffic_family: str | None = None,
    free_for_admin: bool = False,
    free_available: bool = False,
) -> str:
    blocks = [_heading(t("plans.title"))]
    if category is None:
        blocks.append(t("plans.choose_category"))
        if trial_available:
            blocks.append(
                f"<b>{t('plans.trial_title')}</b>\n"
                + t(
                    "plans.trial_note",
                    hours_word=tn("plural.hours", trial_days * 24),
                )
            )
        if free_available:
            blocks.append(t("plans.free_available"))
        return "\n\n".join(blocks)

    if category == "free":
        blocks.append(
            f"<b>{t('plans.free_title')}</b>\n"
            + (
                t("plans.free_conditions")
                if free_available
                else t("plans.free_unavailable")
            )
        )
        return "\n\n".join(blocks)

    selected = [
        plan
        for plan in plans
        if (category == "time" and plan.traffic_limit_gb is None)
        or (category == "traffic" and plan.traffic_limit_gb is not None)
    ]
    blocks.append(
        f"<b>{t('plans.time_section' if category == 'time' else 'plans.traffic_section')}</b>"
    )
    if category == "traffic" and not traffic_plans_enabled and not free_for_admin:
        blocks.append(t("plans.traffic_unavailable"))
        return "\n\n".join(blocks)

    if category == "traffic" and traffic_family is None:
        family_order = ("NO_RESET", "DAY", "WEEK", "MONTH", "MONTH_ROLLING")
        for family in family_order:
            variants = [
                plan for plan in selected
                if plan.sellable
                and plan.traffic_limit_strategy == family
                and (free_for_admin or plan.currency == "XTR")
            ]
            if not variants:
                continue
            blocks.append(
                _box(
                    t(f"plans.family_{family.lower()}", count=len(variants)),
                    [t(f"plans.family_note_{family.lower()}")],
                )
            )
        return "\n\n".join(blocks)

    if category == "traffic":
        selected = [
            plan
            for plan in selected
            if plan.traffic_limit_strategy == traffic_family
        ]

    for plan in selected:
        if not plan.sellable:
            continue
        if plan.currency != "XTR" and not free_for_admin:
            continue
        rows = [t("plans.minimum_duration", days=plan.days)]
        if plan.traffic_limit_gb is not None:
            rows.append(t("plans.traffic_volume", gb=plan.traffic_limit_gb))
        if plan.description:
            rows.append(escape(plan.description))
        if free_for_admin:
            rows.append(t("plans.admin_free"))
        elif plan.price_minor is None:
            rows.append(t("plans.price_unknown"))
        else:
            rows.append(
                t("plans.minimum_price", price=format_price(plan))
            )
        blocks.append(_box(escape(plan.title), rows))
    blocks.append(t("plans.switch_policy"))
    blocks.append(t("plans.disclaimer"))
    return "\n\n".join(blocks)


def connect_text(
    user: PanelUser | None,
    page_url: str | None,
    *,
    support: str | None = None,
    panel_error: bool = False,
    trial_available: bool = False,
) -> str:
    blocks = [_heading(t("connect.title"))]
    if panel_error:
        blocks.extend(
            [
                _box(
                    t("common.no_connection"),
                    [
                        t("connect.check_failed"),
                        t("connect.check_retry"),
                        t("connect.still_active"),
                    ],
                ),
                _support_line(support),
            ]
        )
        return "\n\n".join(blocks)
    if user is None:
        blocks.extend(
            [
                _box(
                    t("common.no_subscription"),
                    [
                        t("connect.no_subscription_note"),
                        t("connect.tariff_note"),
                    ],
                ),
                t("connect.page_after"),
            ]
        )
        if trial_available:
            blocks[1] = _box(
                t("common.no_subscription"),
                [
                    t("connect.no_subscription_note"),
                    t("connect.trial_note"),
                    t("connect.tariff_note"),
                ],
            )
        return "\n\n".join(blocks)
    if user.status != "ACTIVE":
        blocks.extend(
            [
                _box(
                    t("common.not_active"),
                    [
                        t("subscription.status_row", status=status_text(user)),
                        t("subscription.expires_row", expires=expiry_text(user)),
                    ],
                ),
                t("connect.inactive_note"),
                _support_line(support),
            ]
        )
        return "\n\n".join(blocks)
    blocks.append(t("connect.method_one"))
    blocks.append(t("connect.method_one_note"))
    if page_url is None:
        blocks.append(t("connect.page_missing"))
    blocks.extend(
        [
            t("connect.expires", expires=expiry_text(user))
            + "\n"
            + t("connect.remaining", remaining=remaining_text(user)),
            t("connect.not_confirmed"),
        ]
    )
    return "\n\n".join(blocks)


def guide_text(
    platform: str | None = None, *, page_url: str | None = None
) -> str:
    if platform is None and page_url is not None:
        return "\n\n".join(
            [
                _heading(t("guides.title")),
                t("guides.subscription_page_first"),
                t("guides.choose_device"),
                t("guides.happ_hint"),
            ]
        )
    if platform in PLATFORM_APPS:
        apps = ", ".join(APP_NAMES[app] for app in PLATFORM_APPS[platform])
        return "\n\n".join(
            [
                _heading(
                    t(
                        "guides.platform_title",
                        platform=t(PLATFORM_NAMES[platform]),
                    )
                ),
                _box(t("guides.apps_box"), [apps]),
                t("guides.choose_app"),
                t("guides.happ_platform_hint"),
            ]
        )
    return "\n\n".join(
        [
            _heading(t("guides.title")),
            t("guides.choose_device"),
            t("guides.happ_hint"),
        ]
    )


def devices_text(
    user: PanelUser | None,
    devices: list[PanelDevice] | None,
    *,
    error: str | None = None,
    page: int = 0,
) -> str:
    if user is None:
        return "\n\n".join(
            [
                _heading(t("devices.title")),
                _box(
                    t("common.no_access"),
                    [
                        t("devices.no_access_note"),
                        t("devices.no_access_after"),
                    ],
                ),
            ]
        )
    total = len(devices) if devices is not None else None
    summary = [
        t("cabinet.devices_row", devices=devices_usage_text(user, total))
    ]
    blocks = [_heading(t("devices.title")), _box(t("common.summary"), summary)]
    if error is not None:
        blocks.append(
            _box(
                t("common.error"),
                [
                    t("devices.error_list"),
                    t("common.changed_nothing"),
                    t("common.refresh_hint"),
                ],
            )
        )
        return "\n\n".join(blocks)
    devices = devices or []
    if not devices:
        blocks.append(
            _box(
                t("devices.empty_title"),
                [
                    t("devices.empty_hwid"),
                    t("devices.empty_clients"),
                    t("devices.empty_refresh"),
                    t("devices.empty_old"),
                ],
            )
        )
        return "\n\n".join(blocks)
    pages = max(1, (len(devices) + DEVICES_PER_PAGE - 1) // DEVICES_PER_PAGE)
    page = min(max(page, 0), pages - 1)
    start = page * DEVICES_PER_PAGE
    page_devices = devices[start : start + DEVICES_PER_PAGE]
    summary.append(t("common.page_of", page=page + 1, pages=pages))
    summary.append(t("common.total_devices", total=len(devices)))
    blocks[1] = _box(t("common.summary"), summary)
    rows: list[str] = []
    for offset, device in enumerate(page_devices, start=1):
        details = " · ".join(
            part
            for part in (device.platform, device.os_version, device.device_model)
            if part
        )
        rows.append(
            f"{start + offset}. {escape(details or t('devices.unnamed'))}"
        )
        if device.updated_at:
            rows.append(
                f"   {t('common.updated')}: "
                f"{escape(format_datetime(device.updated_at, current_utc_offset()))}"
            )
    blocks.append(_box(t("common.list"), rows))
    limit = user.hwid_device_limit
    if limit not in (None, 0) and len(devices) >= limit:
        blocks.append(t("devices.limit_reached"))
    blocks.append(t("devices.limit_meaning"))
    blocks.append(t("devices.ip_note"))
    return "\n\n".join(blocks)


def device_confirm_text(device: PanelDevice) -> str:
    details = " · ".join(
        part
        for part in (device.platform, device.os_version, device.device_model)
        if part
    )
    return "\n\n".join(
        [
            _heading(t("devices.confirm_title")),
            _box(
                t("devices.confirm_box"),
                [escape(details or t("devices.unnamed"))],
            ),
            t("devices.confirm_note"),
            t("devices.confirm_irreversible"),
        ]
    )


def stats_text(
    user: PanelUser | None,
    *,
    panel_error: bool = False,
    devices_count: int | None = None,
) -> str:
    if panel_error:
        return "\n\n".join(
            [
                _heading(t("stats.title")),
                _box(
                    t("common.no_connection"),
                    [t("stats.no_data"), t("common.try_again")],
                ),
            ]
        )
    if user is None:
        return "\n\n".join(
            [
                _heading(t("stats.title")),
                _box(t("common.no_access"), [t("stats.after_access")]),
            ]
        )
    rows = [
        t("stats.status_row", status=status_text(user)),
        t("stats.traffic_row", traffic=traffic_text(user)),
    ]
    if user.traffic_limit_bytes not in (None, 0):
        if user.traffic_used_bytes is not None:
            remaining = max(user.traffic_limit_bytes - user.traffic_used_bytes, 0)
            rows.append(
                t("stats.remaining_row", remaining=format_bytes(remaining))
            )
    if user.traffic_lifetime_used_bytes is not None:
        rows.append(
            t(
                "stats.lifetime_row",
                lifetime=format_bytes(user.traffic_lifetime_used_bytes),
            )
        )
    rows.append(
        t("stats.devices_row", devices=devices_usage_text(user, devices_count))
    )
    if user.online_at:
        rows.append(
            t(
                "stats.last_activity",
                value=escape(format_datetime(user.online_at, current_utc_offset())),
            )
        )
    if user.last_connected_at:
        rows.append(
            t(
                "stats.first_connection",
                value=escape(
                    format_datetime(user.last_connected_at, current_utc_offset())
                ),
            )
        )
    return "\n\n".join(
        [
            _heading(t("stats.title")),
            _box(t("stats.usage"), rows),
            t("stats.note"),
        ]
    )


def settings_text(
    *,
    telegram_id: int,
    reminders_enabled: bool,
    user: PanelUser | None,
    support: str | None,
    timezone_offset: int = 3,
) -> str:
    account = [t("cabinet.telegram_id", telegram_id=telegram_id)]
    if user is not None:
        account.extend(
            [
                t("cabinet.jopes_id", panel_id=escape(str(user.id))),
                t("settings.access", status=status_text(user)),
            ]
        )
        tariff = tariff_text(user)
        if tariff is not None:
            account.append(t("cabinet.tariff_row", tariff=tariff))
        account.append(t("cabinet.expires_row", expires=expiry_text(user)))
    else:
        account.append(t("settings.access", status=t("status.none")))
    language_code = current_language()
    language = (
        f"{LANGUAGE_FLAGS.get(language_code, '🌐')} "
        f"{SUPPORTED_LANGUAGES.get(language_code, language_code)}"
    )
    account.append(t("settings.language", name=language))
    account.append(
        t("settings.timezone", offset=utc_label(timezone_offset))
    )
    return "\n\n".join(
        [
            _heading(t("settings.title")),
            _box(t("settings.account"), account),
            _box(
                t("settings.reminders"),
                [
                    t("settings.reminders_schedule"),
                    t("settings.reminders_once"),
                    t(
                        "settings.reminders_state",
                        state=t(
                            "settings.reminders_on"
                            if reminders_enabled
                            else "settings.reminders_off"
                        ),
                    ),
                ],
            ),
            t("settings.language_note"),
            _support_line(support),
        ]
    )


def help_text(support_contact: str | None = None) -> str:
    return "\n\n".join(
        [
            _heading(t("help.title")),
            _box(
                t("help.sections"),
                [
                    t("help.cabinet"),
                    t("help.connect"),
                    t("help.devices"),
                    t("help.guides"),
                    t("help.problems"),
                ],
            ),
            t("help.ticket_note"),
            t("help.note"),
            t("help.commands"),
        ]
    )


def troubleshooting_text() -> str:
    return "\n\n".join(
        [
            _heading(t("troubleshooting.title")),
            _box(
                t("troubleshooting.add_title"),
                [
                    t("troubleshooting.add_1"),
                    t("troubleshooting.add_2"),
                    t("troubleshooting.add_3"),
                ],
            ),
            _box(
                t("troubleshooting.servers_title"),
                [
                    t("troubleshooting.servers_1"),
                    t("troubleshooting.servers_2"),
                    t("troubleshooting.servers_3"),
                ],
            ),
            _box(
                t("troubleshooting.connect_title"),
                [
                    t("troubleshooting.connect_1"),
                    t("troubleshooting.connect_2"),
                    t("troubleshooting.connect_3"),
                    t("troubleshooting.connect_4"),
                ],
            ),
            _box(
                t("troubleshooting.limit_title"),
                [
                    t("troubleshooting.limit_1"),
                    t("troubleshooting.limit_2"),
                    t("troubleshooting.limit_3"),
                ],
            ),
            _box(
                t("troubleshooting.status_title"),
                [
                    t("troubleshooting.status_1"),
                    t("troubleshooting.status_2"),
                ],
            ),
            _box(
                t("troubleshooting.service_title"),
                [
                    t("troubleshooting.service_1"),
                    t("troubleshooting.service_2"),
                    t("troubleshooting.service_3"),
                ],
            ),
            t("troubleshooting.secret"),
        ]
    )
