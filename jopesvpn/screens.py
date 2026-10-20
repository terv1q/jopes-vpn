from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import cast

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import FSInputFile, InputMediaPhoto, Message

from jopesvpn.preferences import PreferencesError, PreferencesStore

logger = logging.getLogger(__name__)

CAPTION_LIMIT = 1024

BANNER_DIR = Path(__file__).with_name("assets") / "banners"

BANNER_FILES = {
    "home": "home.jpg",
    "cabinet": "cabinet.jpg",
    "connect": "connect.jpg",
    "subscription": "purchase_success.png",
    "purchase_success": "purchase_success.png",
    "devices": "devices.jpg",
    "stats": "stats.jpg",
    "plans": "plans.jpg",
    "guides": "guides.jpg",
    "settings": "settings.jpg",
    "support": "support.jpg",
    "admin": "admin.jpg",
    "admin_users": "admin_users.jpg",
}

USER_BANNERS = {
    "home": "home",
    "setup": "home",
    "more": "home",
    "about": "support",
    "cabinet": "purchase_success",
    "profile": "purchase_success",
    "wallet": "cabinet",
    "topup": "plans",
    "connect": "connect",
    "subscription": "purchase_success",
    "key": "purchase_success",
    "devices": "devices",
    "stats": "stats",
    "referrals": "stats",
    "settings": "settings",
    "timezone": "settings",
    "languages": "settings",
    "plans": "plans",
    "trial": "plans",
    "guides": "guides",
    "guide": "guides",
    "help": "support",
    "troubleshoot": "support",
}

_HEADING = re.compile(r"^<b>([^<]+)</b>\n\n")

_default_store: PreferencesStore | None = None


def configure(prefs: PreferencesStore | None) -> None:
    """Register the process-wide preference store used for the banner cache."""
    global _default_store
    _default_store = prefs


def banner_path(name: str) -> Path:
    return BANNER_DIR / BANNER_FILES[name]


def banner_for_callback(data: str | None) -> str | None:
    """Map callback data to the banner shown on that screen."""
    if not data:
        return None
    if data == "trial:check":
        return "plans"
    if data.startswith("admin:"):
        action = data.removeprefix("admin:")
        if action.startswith(("list:", "u:")):
            return "admin_users"
        return "admin"
    if data.startswith("ticket:"):
        return "support"
    if data.startswith(("dev:", "device:")):
        return "devices"
    if data.startswith("set:"):
        return "settings"
    if data.startswith(("guide:", "g:", "platform:")):
        return "guides"
    if data.startswith(("checkout:", "term:", "pay:", "topup:")):
        return "plans"
    if data.startswith("withdraw:"):
        return "cabinet"
    if data.startswith("user:"):
        action = data.removeprefix("user:").partition(":")[0]
        return USER_BANNERS.get(action)
    return None


def strip_heading(text: str) -> str:
    """Drop the leading uppercase heading: the banner already shows it."""
    match = _HEADING.match(text)
    if match is None:
        return text
    letters = "".join(char for char in match.group(1) if char.isalpha())
    if not letters or not letters.isupper():
        return text
    return text[match.end():]


def _cached_file_id(
    prefs: PreferencesStore | None, name: str
) -> str | None:
    if prefs is None:
        return None
    try:
        return prefs.banner_file_id(name)
    except PreferencesError:
        logger.warning("Banner cache read failed")
        return None


def _remember_file_id(
    prefs: PreferencesStore | None, name: str, message: Message
) -> None:
    photos = getattr(message, "photo", None)
    if prefs is None or not photos:
        return
    try:
        prefs.set_banner_file_id(name, photos[-1].file_id)
    except (PreferencesError, ValueError):
        logger.warning("Banner cache write failed")


def _photo(name: str, prefs: PreferencesStore | None):
    return _cached_file_id(prefs, name) or FSInputFile(banner_path(name))


def _can_use_banner(
    name: str | None, prefs: PreferencesStore | None, text: str
) -> bool:
    return (
        name is not None
        and name in BANNER_FILES
        and prefs is not None
        and len(strip_heading(text)) <= CAPTION_LIMIT
    )


async def _answer_photo(
    message: Message,
    name: str,
    prefs: PreferencesStore | None,
    caption: str,
    markup,
) -> bool:
    sources = [_photo(name, prefs)]
    if _cached_file_id(prefs, name):
        sources.append(FSInputFile(banner_path(name)))
    for source in sources:
        try:
            sent = await message.answer_photo(
                photo=source,
                caption=caption,
                reply_markup=markup,
                parse_mode="HTML",
            )
            _remember_file_id(prefs, name, sent)
            return True
        except TelegramAPIError:
            logger.warning("Banner photo send failed")
    return False


async def _edit_photo(
    message: Message,
    name: str,
    prefs: PreferencesStore | None,
    caption: str,
    markup,
) -> bool:
    sources = [_photo(name, prefs)]
    if _cached_file_id(prefs, name):
        sources.append(FSInputFile(banner_path(name)))
    for source in sources:
        try:
            sent = await message.edit_media(
                InputMediaPhoto(
                    media=source,
                    caption=caption,
                    parse_mode="HTML",
                ),
                reply_markup=markup,
            )
            if not isinstance(sent, bool):
                _remember_file_id(prefs, name, cast(Message, sent))
            return True
        except TelegramBadRequest as error:
            if "message is not modified" in str(error):
                return True
            logger.warning("Banner media edit failed")
        except TelegramAPIError:
            logger.warning("Banner media edit failed")
    return False


async def _delete_old_screen(message: Message) -> None:
    try:
        await message.delete()
    except TelegramAPIError:
        logger.warning("Old screen message cleanup failed")


async def edit_screen(
    message: Message,
    text: str,
    markup,
    *,
    prefs: PreferencesStore | None = None,
    data: str | None = None,
    banner: str | None = None,
) -> None:
    """Edit the screen message, using a cached banner photo when possible."""
    prefs = prefs if prefs is not None else _default_store
    name = banner or banner_for_callback(data)
    if name is None and data == "user:back":
        target = "home"
        if prefs is not None:
            try:
                target = prefs.current_navigation(message.chat.id)
            except (PreferencesError, ValueError):
                logger.warning("Banner navigation context could not be read")
        name = banner_for_callback(f"user:{target}") or "home"
    if getattr(message, "photo", None) is not None:
        if name is not None and _can_use_banner(name, prefs, text):
            if await _edit_photo(message, name, prefs, strip_heading(text), markup):
                return
            if await _answer_photo(message, name, prefs, strip_heading(text), markup):
                await _delete_old_screen(message)
                return
        await message.answer(text, reply_markup=markup, parse_mode="HTML")
        await _delete_old_screen(message)
        return
    if name is not None and _can_use_banner(name, prefs, text):
        caption = strip_heading(text)
        if await _answer_photo(message, name, prefs, caption, markup):
            await _delete_old_screen(message)
            return
    try:
        await message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except TelegramBadRequest as error:
        if "message is not modified" in str(error):
            return
        await message.answer(text, reply_markup=markup, parse_mode="HTML")


async def answer_screen(
    message: Message,
    text: str,
    markup,
    *,
    prefs: PreferencesStore | None = None,
    banner: str | None = None,
) -> None:
    """Send a new screen message, with a banner photo when possible."""
    prefs = prefs if prefs is not None else _default_store
    name = banner
    if (
        name is not None
        and _can_use_banner(name, prefs, text)
        and await _answer_photo(message, name, prefs, strip_heading(text), markup)
    ):
        return
    await message.answer(text, reply_markup=markup, parse_mode="HTML")
