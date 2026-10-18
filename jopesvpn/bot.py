from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sqlite3
import sys
import time
from datetime import UTC, datetime, timedelta
from html import escape

from aiogram import Bot, Dispatcher, F, Router
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from aiogram.utils.token import TokenValidationError

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
from jopesvpn.plans import (
    Plan,
    PlanTerm,
    format_rubles,
    load_plans,
    term_savings,
)
from jopesvpn.preferences import PreferencesError, PreferencesStore
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
from jopesvpn.tickets import TicketStore, TicketStoreError
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
    payment_methods_keyboard,
    plan_terms_keyboard,
    plans_keyboard,
    plans_text,
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
    trial_keyboard,
    trial_text,
    troubleshooting_text,
)

logger = logging.getLogger(__name__)
router = Router(name="user")


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
    state: FSMContext | None = None,
) -> None:
    await user_section_command(
        message, bot, panel, settings, plans, prefs=prefs, state=state
    )


@router.message(Command("about"))
async def show_about_command(
    message: Message,
    plans: tuple[Plan, ...],
    settings: Settings,
    prefs: PreferencesStore | None = None,
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
REFERRAL_INVITER_REWARD_DAYS = 10
REFERRAL_INVITER_MONTHLY_CAP_DAYS = 60
REFERRAL_INVITEE_BONUS_DAYS = 3
_trial_lock = asyncio.Lock()
_device_lock = asyncio.Lock()
_admin_plan_lock = asyncio.Lock()
_free_access_lock = asyncio.Lock()
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
    elif screen == "cabinet":
        await callback.answer()
        await _render_cabinet(callback, panel, settings, prefs)
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
        if prefs is None:
            await callback.answer(t("referral.unavailable"), show_alert=True)
            return
        try:
            stats = prefs.referral_stats(callback.from_user.id)
            bot_user = await bot.get_me()
        except (PreferencesError, TelegramAPIError):
            logger.warning("Referral statistics could not be loaded")
            await callback.answer(t("referral.unavailable"), show_alert=True)
            return
        if not bot_user.username:
            await callback.answer(t("referral.bot_username_missing"), show_alert=True)
            return
        invite_url = (
            f"https://t.me/{bot_user.username}?start=ref_{callback.from_user.id}"
        )
        await callback.answer()
        await _edit_or_send(
            callback,
            referral_text(stats, invite_url),
            referral_keyboard(referral_share_url(invite_url)),
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
    trial_days = settings.trial_days
    if prefs is not None:
        try:
            if prefs.referral_for(callback.from_user.id) is not None:
                trial_days += REFERRAL_INVITEE_BONUS_DAYS
        except PreferencesError:
            logger.warning("Referral attribution could not be read")
    await _edit_or_send(
        callback,
        trial_text(trial_days, settings.trial_channel_url),
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


@router.message(CommandStart())
async def start(
    message: Message,
    bot: Bot,
    panel: RemnawaveClient,
    settings: Settings,
    state: FSMContext | None = None,
    prefs: PreferencesStore | None = None,
) -> None:
    await _clear_state(state)
    if not _private_chat(message):
        await message.answer(t("alert.private_start"))
        return
    if prefs is None:
        await message.answer(t("docs.accept_failed"))
        return
    try:
        consented = prefs.documents_accepted(
            message.from_user.id, prefs.documents_version()
        )
    except (PreferencesError, ValueError):
        logger.warning("Document consent state could not be loaded")
        await message.answer(t("docs.accept_failed"))
        return
    privacy_url, agreement_url = _document_urls(prefs)
    if (
        not consented
        and privacy_url is not None
        and agreement_url is not None
    ):
        await answer_screen(
            message,
            _documents_consent_text(prefs),
            _documents_consent_keyboard(prefs),
            banner="support",
        )
        return
    referral_registered = False
    referrer_id: int | None = None
    if prefs is not None:
        try:
            prefs.reset_navigation(message.from_user.id)
            command_parts = (message.text or "").split(maxsplit=1)
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
        except (PreferencesError, ValueError):
            logger.warning("Preferences database is unavailable")
    if referral_registered and referrer_id is not None:
        await _notify_referrer(bot, referrer_id, t("referral.new_visit"))
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
    await answer_screen(
        message,
        text,
        home_keyboard(
            _is_admin(message, settings),
            page_url,
            has_access=has_access,
            trial_available=trial_available,
            onboarding=onboarding,
        ),
        banner="home",
    )


@router.callback_query(F.data.startswith("docs:"))
async def documents_navigation(
    callback: CallbackQuery,
    panel: RemnawaveClient,
    settings: Settings,
    prefs: PreferencesStore | None = None,
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
                _is_admin(callback, settings),
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
) -> None:
    assert callback.data is not None
    action = callback.data.removeprefix("user:")
    if action == "key":
        action = "subscription"
    elif action == "profile":
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
        await _user_screen(callback, bot, panel, action, settings, plans, prefs)
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
        await _user_screen(callback, bot, panel, action, settings, plans, prefs)
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
                settings.trial_days
                + (REFERRAL_INVITEE_BONUS_DAYS if referrer_id is not None else 0),
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

    referral_notice = ""
    invitee_bonus_notice = ""
    if prefs is not None:
        try:
            prefs.mark_trial_used(telegram_id)
        except PreferencesError:
            logger.warning("Trial usage state could not be saved")
        if referrer_id is not None:
            invitee_bonus_notice = t("referral.invitee_bonus")
            try:
                prefs.mark_referral_activated(telegram_id)
                reserved = prefs.reserve_referral_reward(
                    telegram_id,
                    reward_month=datetime.now(UTC).strftime("%Y-%m"),
                    reward_days=REFERRAL_INVITER_REWARD_DAYS,
                    monthly_cap=REFERRAL_INVITER_MONTHLY_CAP_DAYS,
                )
                if reserved:
                    try:
                        inviter = await panel.get_user_by_telegram_id(referrer_id)
                    except PanelNotFound:
                        prefs.skip_referral_reward(telegram_id)
                    except PanelError as error:
                        logger.warning(
                            "Referral inviter lookup remains pending (%s)",
                            type(error).__name__,
                        )
                        referral_notice = t("referral.reward_pending_notice")
                    else:
                        if inviter.status not in {"ACTIVE", "EXPIRED"}:
                            prefs.skip_referral_reward(telegram_id)
                        else:
                            try:
                                await panel.extend_user(
                                    inviter, REFERRAL_INVITER_REWARD_DAYS
                                )
                            except PanelError as error:
                                logger.warning(
                                    "Referral reward remains pending (%s)",
                                    type(error).__name__,
                                )
                                referral_notice = t("referral.reward_pending_notice")
                            else:
                                prefs.finish_referral_reward(telegram_id)
                                await _notify_referrer(
                                    bot,
                                    referrer_id,
                                    t(
                                        "referral.inviter_reward",
                                        days=REFERRAL_INVITER_REWARD_DAYS,
                                    ),
                                )
            except (PanelError, PreferencesError) as error:
                logger.warning(
                    "Referral reward could not be processed (%s)",
                    type(error).__name__,
                )
                referral_notice = t("referral.reward_pending_notice")

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
        f"{setup_text}"
        + (f"\n\n{invitee_bonus_notice}" if invitee_bonus_notice else "")
        + (f"\n\n{referral_notice}" if referral_notice else ""),
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
    state: FSMContext | None = None,
) -> None:
    await _clear_state(state)
    if not _private_chat(message):
        await message.answer(t("alert.private_use_start"))
        return
    assert message.from_user is not None
    command = (message.text or "").split(maxsplit=1)[0].split("@", maxsplit=1)[0]
    screen = command.removeprefix("/").lower()
    if screen == "account":
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
        if prefs is None:
            await message.answer(t("referral.unavailable"))
            return
        try:
            stats = prefs.referral_stats(message.from_user.id)
            bot_user = await bot.get_me()
        except (PreferencesError, TelegramAPIError):
            logger.warning("Referral statistics could not be loaded")
            await message.answer(t("referral.unavailable"))
            return
        if not bot_user.username:
            await message.answer(t("referral.bot_username_missing"))
            return
        invite_url = (
            f"https://t.me/{bot_user.username}?start=ref_{message.from_user.id}"
        )
        await answer_screen(
            message,
            referral_text(stats, invite_url),
            referral_keyboard(invite_url),
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


def _order_plan_term(
    plan: Plan, order
) -> tuple[int, PlanTerm | None] | None:
    days = order.term_days or plan.days
    term = plan.term_for(days)
    if plan.sellable and term is None:
        return None
    expected_stars = term.stars_amount if term is not None else plan.price_minor
    if order.amount_minor != expected_stars:
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
        await asyncio.sleep(interval_seconds)


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
        payment_methods_keyboard(plan, term),
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
        await callback.answer(
            t("plans.payment_unavailable"), show_alert=True
        )
        return
    operation_key: str | None = None
    selected_days: int | None = None
    if is_admin:
        if len(parts) != 2 or not re.fullmatch(r"[A-Za-z0-9_-]{16}", parts[1]):
            await callback.answer(t("plans.invalid_invoice"), show_alert=True)
            return
        code, operation_key = parts
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
    try:
        user = await _load_user(callback.from_user.id, panel)
    except PanelError as error:
        logger.warning("Subscription pre-purchase check failed (%s)", type(error).__name__)
        await callback.answer(t("plans.purchase_blocked"), show_alert=True)
        return
    if (
        user is not None and user.status not in {"ACTIVE", "EXPIRED"}
        and not (
            user.status == "LIMITED"
            and (
                settings.traffic_plans_enabled
                or callback.from_user.id in settings.admin_telegram_ids
                or plan.traffic_limit_gb is None
            )
        )
    ) or (user is None and settings.panel_internal_squad_uuid is None):
        await callback.answer(t("plans.purchase_blocked"), show_alert=True)
        return
    if is_admin:
        assert operation_key is not None
        await _grant_admin_plan(
            callback, panel, settings, plan, prefs, operation_key
        )
        return
    if selected_term is None:
        await callback.answer(t("plans.invalid_invoice"), show_alert=True)
        return
    try:
        order = orders.create_order(callback.from_user.id, plan, selected_term)
    except (OrderError, sqlite3.Error):
        logger.warning("Stars order could not be created")
        await callback.answer(t("plans.invalid_invoice"), show_alert=True)
        return
    await callback.answer()
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
) -> None:
    valid = False
    error_message = t("plans.invalid_invoice")
    payload = query.invoice_payload
    if payload.startswith("jvpay_"):
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


@router.message(F.successful_payment)
async def process_stars_payment(
    message: Message,
    bot: Bot,
    panel: RemnawaveClient,
    settings: Settings,
    plans: tuple[Plan, ...],
    orders: OrderStore,
) -> None:
    if message.from_user is None or message.successful_payment is None:
        return
    payment = message.successful_payment
    payload = payment.invoice_payload
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
        )
        if not changed or order.status != PAID:
            return
        if not orders.claim_fulfillment(order.id):
            return
    except (OrderError, sqlite3.Error):
        logger.warning("Confirmed Stars payment could not be recorded")
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
        duration_days, _ = order_term
        user = await _load_user(message.from_user.id, panel)
        if user is None:
            if settings.panel_internal_squad_uuid is None:
                raise PanelAccessDenied("Panel squad is not configured")
            updated = await panel.create_user(
                message.from_user.id,
                duration_days,
                settings.panel_internal_squad_uuid,
                tag=plan.panel_tag,
                traffic_limit_gb=plan.traffic_limit_gb,
                permanent_traffic=plan.permanent_traffic,
                traffic_limit_strategy=plan.traffic_limit_strategy,
            )
        elif user.status in {"ACTIVE", "LIMITED"}:
            updated = await panel.apply_paid_plan(
                user,
                duration_days,
                plan.traffic_limit_gb,
                permanent_traffic=plan.permanent_traffic,
                tag=plan.panel_tag,
                traffic_limit_strategy=plan.traffic_limit_strategy,
            )
        else:
            raise OrderConflict("Subscription is no longer eligible for extension")
        orders.mark_fulfilled(order.id)
    except (PanelError, OrderError, sqlite3.Error):
        logger.warning("Paid Stars order requires manual fulfillment review")
        try:
            orders.mark_fulfillment_review(order.id)
        except (OrderError, sqlite3.Error):
            logger.warning("Paid Stars order review state could not be saved")
        await message.answer(t("plans.paid_review"))
        return
    if plan.permanent_traffic:
        success_text = t(
            "plans.payment_success_traffic",
            gb=plan.traffic_limit_gb,
        )
    elif plan.traffic_limit_gb is not None:
        success_text = t(
            "plans.payment_success_traffic_limited",
            gb=plan.traffic_limit_gb,
            expires=_expiry_text(updated),
        )
    else:
        success_text = t(
            "plans.payment_success",
            days=duration_days,
            expires=_expiry_text(updated),
        )
    await message.answer(success_text, parse_mode="HTML")


@router.message(F.refunded_payment)
async def process_stars_refund_update(
    message: Message,
    bot: Bot,
    settings: Settings,
    orders: OrderStore,
) -> None:
    payment = message.refunded_payment
    if payment is None:
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
        changed = orders.mark_refunded(order.id)
    except (OrderError, sqlite3.Error):
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
        changed = orders.mark_refunded(order.id)
    except (TelegramAPIError, OrderError, sqlite3.Error):
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
        orders = OrderStore(settings.state_db_path or "bot-state.sqlite3")
    except (OSError, sqlite3.Error) as error:
        logger.error("Cannot start without payment order storage: %s", error)
        await panel.close()
        await bot.session.close()
        raise SystemExit(2) from error
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
        payment_review_loop(bot, settings, orders),
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
    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Главное меню"),
                BotCommand(command="connect", description="Подключить VPN"),
                BotCommand(command="status", description="Проверить доступ"),
                BotCommand(command="subscription", description="Моя подписка"),
                BotCommand(command="account", description="Личный кабинет"),
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
            orders=orders,
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
        trial_nudge_task.cancel()
        for task in (
            reminder_task,
            trial_nudge_task,
            connection_task,
            ticket_followup_task,
            ticket_notification_task,
            payment_review_task,
            free_access_task,
            traffic_notification_task,
        ):
            try:
                await task
            except asyncio.CancelledError:
                pass
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
