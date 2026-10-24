from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from jopesvpn.panel import (
    PanelDevice,
    PanelError,
    PanelNotFound,
    PanelUser,
    RemnawaveClient,
)
from jopesvpn.preferences import PreferencesError, PreferencesStore

DRAFT = "DRAFT"
SENDING = "SENDING"
SENT = "SENT"
FAILED = "FAILED"

DELIVERY_SENT = "SENT"
DELIVERY_FAILED = "FAILED"

ALL = "all"
SUBSCRIBED = "subscribed"
ACTIVE = "active"
NEVER_CONNECTED = "never"
INACTIVE_24H = "inactive_24h"
ACTIVE_INACTIVE_24H = "active_inactive_24h"

AUDIENCES = (
    ALL,
    SUBSCRIBED,
    ACTIVE,
    NEVER_CONNECTED,
    INACTIVE_24H,
    ACTIVE_INACTIVE_24H,
)

MAX_BUTTONS = 8
INACTIVE_HOURS = 24
HTML_TAG_PATTERN = re.compile(
    r"</?(?:b|strong|i|em|u|ins|s|strike|del|span|tg-spoiler|a|code|pre|"
    r"blockquote)\b[^>]*>",
    re.IGNORECASE,
)


class BroadcastError(Exception):
    pass


@dataclass(frozen=True)
class BroadcastButton:
    text: str
    url: str


@dataclass(frozen=True)
class BroadcastDraft:
    id: int
    admin_id: int
    text: str
    audience: str
    buttons: tuple[BroadcastButton, ...]
    status: str
    delivered: int
    failed: int
    created_at: str
    updated_at: str
    sent_at: str | None


@dataclass(frozen=True)
class AudienceResult:
    recipients: list[int]
    matched: int
    errors: int


@dataclass(frozen=True)
class DeliveryResult:
    delivered: int
    failed: int
    skipped: int


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_timestamp(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def activity_state(
    user: PanelUser,
    devices: Iterable[PanelDevice],
    endpoint_last_seen: str | None,
) -> tuple[bool, datetime | None]:
    device_list = list(devices)
    signals = [
        _parse_timestamp(user.online_at),
        _parse_timestamp(user.last_connected_at),
        _parse_timestamp(endpoint_last_seen),
    ]
    signals.extend(_parse_timestamp(device.updated_at) for device in device_list)
    connected = (
        (user.traffic_used_bytes or 0) > 0
        or (user.traffic_lifetime_used_bytes or 0) > 0
        or _parse_timestamp(user.last_connected_at) is not None
        or _parse_timestamp(user.online_at) is not None
    )
    known = [signal for signal in signals if signal is not None]
    return connected, max(known) if known else None


def validate_button(label: str, url: str) -> BroadcastButton:
    clean_label = label.strip()
    clean_url = url.strip()
    if not clean_label or len(clean_label) > 64:
        raise BroadcastError("Broadcast button label is invalid")
    if len(clean_url) > 512:
        raise BroadcastError("Broadcast button URL is invalid")
    try:
        parsed = urlsplit(clean_url)
        port = parsed.port
    except ValueError as error:
        raise BroadcastError("Broadcast button URL is invalid") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port == 0
        or any(character.isspace() for character in clean_url)
    ):
        raise BroadcastError("Broadcast button URL is invalid")
    return BroadcastButton(clean_label, clean_url)


def parse_mode_for(text: str) -> str | None:
    return "HTML" if HTML_TAG_PATTERN.search(text) else None


def broadcast_user_keyboard(
    buttons: Iterable[BroadcastButton],
) -> InlineKeyboardMarkup | None:
    rows = [[InlineKeyboardButton(text=button.text, url=button.url)] for button in buttons]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


class BroadcastStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS broadcasts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        admin_id INTEGER NOT NULL,
                        text TEXT NOT NULL,
                        audience TEXT NOT NULL DEFAULT 'all',
                        buttons_json TEXT NOT NULL DEFAULT '[]',
                        status TEXT NOT NULL DEFAULT 'DRAFT',
                        delivered INTEGER NOT NULL DEFAULT 0,
                        failed INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        sent_at TEXT
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS broadcast_deliveries (
                        broadcast_id INTEGER NOT NULL,
                        telegram_id INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        error TEXT,
                        sent_at TEXT NOT NULL,
                        PRIMARY KEY (broadcast_id, telegram_id)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS broadcasts_created
                    ON broadcasts (created_at DESC)
                    """
                )
                connection.commit()
        except (OSError, sqlite3.Error) as error:
            raise BroadcastError("Cannot open broadcast storage") from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _row(self, row: sqlite3.Row) -> BroadcastDraft:
        buttons: list[BroadcastButton] = []
        try:
            raw = json.loads(row["buttons_json"] or "[]")
        except (TypeError, ValueError):
            raw = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                try:
                    buttons.append(
                        validate_button(
                            str(item.get("text", "")), str(item.get("url", ""))
                        )
                    )
                except BroadcastError:
                    continue
        audience = str(row["audience"])
        return BroadcastDraft(
            id=int(row["id"]),
            admin_id=int(row["admin_id"]),
            text=str(row["text"]),
            audience=audience if audience in AUDIENCES else ALL,
            buttons=tuple(buttons),
            status=str(row["status"]),
            delivered=int(row["delivered"]),
            failed=int(row["failed"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            sent_at=row["sent_at"],
        )

    def create(self, admin_id: int, text: str) -> BroadcastDraft:
        clean = text.strip()
        if admin_id <= 0 or not clean or len(clean) > 4096:
            raise BroadcastError("Broadcast text is invalid")
        moment = _now_iso()
        try:
            with closing(self._connect()) as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO broadcasts
                        (admin_id, text, audience, buttons_json, status,
                         created_at, updated_at)
                    VALUES (?, ?, ?, '[]', ?, ?, ?)
                    """,
                    (admin_id, clean, ALL, DRAFT, moment, moment),
                )
                connection.commit()
                draft_id = int(cursor.lastrowid)
        except sqlite3.Error as error:
            raise BroadcastError("Cannot create broadcast") from error
        draft = self.get(draft_id)
        if draft is None:
            raise BroadcastError("Broadcast was not stored")
        return draft

    def get(self, draft_id: int) -> BroadcastDraft | None:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT * FROM broadcasts WHERE id = ?", (draft_id,)
                ).fetchone()
        except sqlite3.Error as error:
            raise BroadcastError("Cannot read broadcast") from error
        return self._row(row) if row is not None else None

    def list_recent(self, limit: int = 10) -> list[BroadcastDraft]:
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT * FROM broadcasts ORDER BY id DESC LIMIT ?",
                    (max(1, min(limit, 50)),),
                ).fetchall()
        except sqlite3.Error as error:
            raise BroadcastError("Cannot list broadcasts") from error
        return [self._row(row) for row in rows]

    def update_text(self, draft_id: int, text: str) -> None:
        clean = text.strip()
        if not clean or len(clean) > 4096:
            raise BroadcastError("Broadcast text is invalid")
        self._update(draft_id, "text = ?", (clean,))

    def set_audience(self, draft_id: int, audience: str) -> None:
        if audience not in AUDIENCES:
            raise BroadcastError("Broadcast audience is invalid")
        self._update(draft_id, "audience = ?", (audience,))

    def set_buttons(self, draft_id: int, buttons: Iterable[BroadcastButton]) -> None:
        items = list(buttons)
        if len(items) > MAX_BUTTONS:
            raise BroadcastError("Too many broadcast buttons")
        payload = json.dumps(
            [{"text": button.text, "url": button.url} for button in items],
            ensure_ascii=False,
        )
        self._update(draft_id, "buttons_json = ?", (payload,))

    def _update(self, draft_id: int, assignment: str, values: tuple) -> None:
        try:
            with closing(self._connect()) as connection:
                cursor = connection.execute(
                    f"UPDATE broadcasts SET {assignment}, updated_at = ? WHERE id = ?",
                    (*values, _now_iso(), draft_id),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise BroadcastError("Cannot update broadcast") from error
        if cursor.rowcount == 0:
            raise BroadcastError("Broadcast was not found")

    def set_status(
        self,
        draft_id: int,
        status: str,
        *,
        delivered: int | None = None,
        failed: int | None = None,
    ) -> None:
        if status not in {DRAFT, SENDING, SENT, FAILED}:
            raise BroadcastError("Broadcast status is invalid")
        moment = _now_iso()
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    UPDATE broadcasts
                    SET status = ?,
                        delivered = COALESCE(?, delivered),
                        failed = COALESCE(?, failed),
                        sent_at = CASE WHEN ? = 'SENT' THEN ? ELSE sent_at END,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (status, delivered, failed, status, moment, moment, draft_id),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise BroadcastError("Cannot update broadcast status") from error

    def delete(self, draft_id: int) -> None:
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    "DELETE FROM broadcast_deliveries WHERE broadcast_id = ?",
                    (draft_id,),
                )
                connection.execute("DELETE FROM broadcasts WHERE id = ?", (draft_id,))
                connection.commit()
        except sqlite3.Error as error:
            raise BroadcastError("Cannot delete broadcast") from error

    def record_delivery(
        self,
        draft_id: int,
        telegram_id: int,
        status: str,
        *,
        error: str | None = None,
    ) -> None:
        if status not in {DELIVERY_SENT, DELIVERY_FAILED}:
            raise BroadcastError("Broadcast delivery status is invalid")
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    INSERT INTO broadcast_deliveries
                        (broadcast_id, telegram_id, status, error, sent_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(broadcast_id, telegram_id) DO UPDATE SET
                        status = excluded.status,
                        error = excluded.error,
                        sent_at = excluded.sent_at
                    """,
                    (
                        draft_id,
                        telegram_id,
                        status,
                        (error or "")[:200] or None,
                        _now_iso(),
                    ),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise BroadcastError("Cannot store broadcast delivery") from error

    def delivered_ids(self, draft_id: int) -> set[int]:
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT telegram_id FROM broadcast_deliveries
                    WHERE broadcast_id = ? AND status = ?
                    """,
                    (draft_id, DELIVERY_SENT),
                ).fetchall()
        except sqlite3.Error as error:
            raise BroadcastError("Cannot read broadcast deliveries") from error
        return {int(row["telegram_id"]) for row in rows}

    def delivery_counts(self, draft_id: int) -> tuple[int, int]:
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT status, COUNT(*) AS total
                    FROM broadcast_deliveries
                    WHERE broadcast_id = ?
                    GROUP BY status
                    """,
                    (draft_id,),
                ).fetchall()
        except sqlite3.Error as error:
            raise BroadcastError("Cannot read broadcast deliveries") from error
        counts = {str(row["status"]): int(row["total"]) for row in rows}
        return counts.get(DELIVERY_SENT, 0), counts.get(DELIVERY_FAILED, 0)


async def resolve_audience(
    panel: RemnawaveClient,
    prefs: PreferencesStore,
    audience: str,
    *,
    exclude: Iterable[int] = (),
    now: datetime | None = None,
) -> AudienceResult:
    if audience not in AUDIENCES:
        raise BroadcastError("Broadcast audience is invalid")
    excluded = set(exclude)
    try:
        known = prefs.known_users()
    except PreferencesError as error:
        raise BroadcastError("Cannot read broadcast recipients") from error
    candidates = [telegram_id for telegram_id in known if telegram_id not in excluded]
    if audience == ALL:
        return AudienceResult(candidates, len(candidates), 0)
    moment = now or datetime.now(UTC)
    threshold = moment - timedelta(hours=INACTIVE_HOURS)
    recipients: list[int] = []
    matched = 0
    errors = 0
    for telegram_id in candidates:
        try:
            user = await panel.get_user_by_telegram_id(telegram_id)
        except PanelNotFound:
            continue
        except PanelError:
            errors += 1
            continue
        except ValueError:
            errors += 1
            continue
        matched += 1
        if audience == SUBSCRIBED:
            recipients.append(telegram_id)
            continue
        if audience == ACTIVE:
            if user.status == "ACTIVE":
                recipients.append(telegram_id)
            continue
        try:
            devices = await panel.get_user_devices(user)
        except (PanelError, ValueError):
            errors += 1
            continue
        try:
            endpoint_last = prefs.connection_last_seen(telegram_id)
        except PreferencesError:
            errors += 1
            continue
        connected, last_activity = activity_state(
            user, devices, endpoint_last
        )
        inactive = not connected or last_activity is None or last_activity <= threshold
        if audience == NEVER_CONNECTED:
            if not connected:
                recipients.append(telegram_id)
        elif audience == INACTIVE_24H:
            if inactive:
                recipients.append(telegram_id)
        elif audience == ACTIVE_INACTIVE_24H:
            if user.status == "ACTIVE" and inactive:
                recipients.append(telegram_id)
    return AudienceResult(recipients, matched, errors)


async def send_broadcast(
    bot: Bot,
    store: BroadcastStore,
    draft: BroadcastDraft,
    recipients: Iterable[int],
) -> DeliveryResult:
    markup = broadcast_user_keyboard(draft.buttons)
    parse_mode = parse_mode_for(draft.text)
    done = store.delivered_ids(draft.id)
    delivered = 0
    failed = 0
    skipped = 0
    for telegram_id in recipients:
        if telegram_id in done:
            skipped += 1
            continue
        try:
            await bot.send_message(
                telegram_id,
                draft.text,
                parse_mode=parse_mode,
                reply_markup=markup,
            )
        except TelegramRetryAfter as error:
            await asyncio.sleep(error.retry_after)
            try:
                await bot.send_message(
                    telegram_id,
                    draft.text,
                    parse_mode=parse_mode,
                    reply_markup=markup,
                )
            except TelegramAPIError as retry_error:
                failed += 1
                store.record_delivery(
                    draft.id,
                    telegram_id,
                    DELIVERY_FAILED,
                    error=type(retry_error).__name__,
                )
                continue
        except TelegramAPIError as error:
            failed += 1
            store.record_delivery(
                draft.id,
                telegram_id,
                DELIVERY_FAILED,
                error=type(error).__name__,
            )
            continue
        else:
            delivered += 1
            store.record_delivery(draft.id, telegram_id, DELIVERY_SENT)
        await asyncio.sleep(0.05)
    return DeliveryResult(delivered, failed, skipped)
