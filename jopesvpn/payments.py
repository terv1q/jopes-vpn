from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from jopesvpn.platega import TransactionStatus


@dataclass(frozen=True)
class PaymentCheck:
    transaction_id: str
    created_at: float
    attempts: int
    generation: int
    callback: TransactionStatus | None


@dataclass(frozen=True)
class PaymentNotice:
    id: str
    telegram_id: int
    text: str
    message_id: int | None
    attempts: int


class PaymentStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.changed = asyncio.Event()
        self.notices_changed = asyncio.Event()
        self._locks: dict[str, asyncio.Lock] = {}
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS platega_checks (
                    transaction_id TEXT PRIMARY KEY,
                    callback TEXT,
                    created_at REAL NOT NULL,
                    next_check_at REAL NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    generation INTEGER NOT NULL DEFAULT 0,
                    completed_at REAL,
                    result TEXT,
                    screen_chat_id INTEGER,
                    screen_message_id INTEGER
                );
                CREATE INDEX IF NOT EXISTS platega_checks_due
                ON platega_checks (completed_at, next_check_at);
                CREATE TABLE IF NOT EXISTS payment_outbox (
                    id TEXT PRIMARY KEY,
                    telegram_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    message_id INTEGER,
                    next_attempt_at REAL NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    delivered_at REAL
                );
                CREATE INDEX IF NOT EXISTS payment_outbox_due
                ON payment_outbox (delivered_at, next_attempt_at);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        return connection

    def processing_lock(self, transaction_id: str) -> asyncio.Lock:
        return self._locks.setdefault(transaction_id, asyncio.Lock())

    def track(self, transaction_id: str) -> None:
        now = time.time()
        with closing(self._connect()) as connection:
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO platega_checks (
                    transaction_id, created_at, next_check_at
                ) VALUES (?, ?, ?)
                """,
                (transaction_id, now, now),
            ).rowcount
            connection.commit()
        if inserted:
            self.changed.set()

    def request_check(self, payment: TransactionStatus) -> None:
        now = time.time()
        callback = json.dumps(
            {
                "transaction_id": payment.transaction_id,
                "status": payment.status,
                "amount": str(payment.amount),
                "currency": payment.currency,
                "payload": payment.payload,
            }
        )
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO platega_checks (
                    transaction_id, callback, created_at, next_check_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(transaction_id) DO UPDATE SET
                    callback = excluded.callback, next_check_at = excluded.next_check_at,
                    completed_at = NULL, attempts = 0, generation = generation + 1
                """,
                (payment.transaction_id, callback, now, now),
            )
            connection.commit()
        self.changed.set()

    def wake_check(self, transaction_id: str) -> None:
        self.track(transaction_id)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE platega_checks SET next_check_at = ?, completed_at = NULL,
                    generation = generation + 1
                WHERE transaction_id = ?
                """,
                (time.time(), transaction_id),
            )
            connection.commit()
        self.changed.set()

    def synchronize(self) -> None:
        now = time.time()
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO platega_checks (
                    transaction_id, created_at, next_check_at
                )
                SELECT provider_payment_id, COALESCE(CAST(strftime('%s', created_at) AS REAL), ?), ? FROM wallet_topups
                WHERE provider = 'platega' AND provider_payment_id IS NOT NULL
                  AND status IN ('PENDING', 'REFUNDING')
                UNION ALL
                SELECT provider_payment_id, COALESCE(CAST(strftime('%s', created_at) AS REAL), ?), ? FROM orders
                WHERE provider = 'platega' AND provider_payment_id IS NOT NULL
                  AND status IN ('PENDING', 'PAID', 'REFUNDING')
                """,
                (now, now, now, now),
            )
            connection.execute(
                """
                UPDATE platega_checks SET completed_at = NULL, next_check_at = ?,
                    generation = generation + 1
                WHERE completed_at IS NOT NULL AND transaction_id IN (
                    SELECT provider_payment_id FROM wallet_topups
                    WHERE provider = 'platega' AND status = 'REFUNDING'
                    UNION ALL
                    SELECT provider_payment_id FROM orders
                    WHERE provider = 'platega' AND status = 'REFUNDING'
                )
                """,
                (now,),
            )
            connection.commit()

    def pending_checks(
        self, *, now: float | None = None, limit: int = 25
    ) -> list[PaymentCheck]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM platega_checks
                WHERE completed_at IS NULL AND next_check_at <= ?
                ORDER BY next_check_at, created_at LIMIT ?
                """,
                (time.time() if now is None else now, limit),
            ).fetchall()
        checks = []
        for row in rows:
            callback = None
            if row["callback"] is not None:
                data = json.loads(row["callback"])
                callback = TransactionStatus(
                    transaction_id=data["transaction_id"],
                    status=data["status"],
                    amount=Decimal(data["amount"]),
                    currency=data["currency"],
                    payload=data["payload"],
                )
            checks.append(
                PaymentCheck(
                    row["transaction_id"],
                    row["created_at"],
                    row["attempts"],
                    row["generation"],
                    callback,
                )
            )
        return checks

    def awaiting_refund(self, transaction_id: str) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT 1 FROM wallet_topups
                WHERE provider_payment_id = ? AND status = 'REFUNDING'
                UNION ALL
                SELECT 1 FROM orders
                WHERE provider_payment_id = ? AND status = 'REFUNDING'
                LIMIT 1
                """,
                (transaction_id, transaction_id),
            ).fetchone()
        return row is not None

    def finish_check(
        self, check: PaymentCheck, result: str, *, retry: bool = False
    ) -> None:
        now = time.time()
        age = now - check.created_at
        delay = 10 if age < 900 else 60 if age < 3600 else 300 if age < 86400 else 3600
        completed_at = None if result == "pending" or retry else now
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE platega_checks SET completed_at = ?, result = ?,
                    next_check_at = ?, attempts = 0
                WHERE transaction_id = ? AND generation = ?
                """,
                (
                    completed_at,
                    result,
                    now + delay,
                    check.transaction_id,
                    check.generation,
                ),
            )
            connection.commit()

    def retry_check(self, check: PaymentCheck) -> None:
        delay = min(300, 10 * 2 ** min(check.attempts, 5))
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE platega_checks SET next_check_at = ?, attempts = attempts + 1
                WHERE transaction_id = ? AND generation = ?
                """,
                (time.time() + delay, check.transaction_id, check.generation),
            )
            connection.commit()

    def record_screen(
        self, transaction_id: str, telegram_id: int, message_id: int
    ) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE platega_checks SET screen_chat_id = ?, screen_message_id = ?,
                    completed_at = NULL, next_check_at = ?, generation = generation + 1
                WHERE transaction_id = ?
                """,
                (telegram_id, message_id, time.time(), transaction_id),
            )
            connection.commit()
        self.changed.set()

    def screen(self, transaction_id: str) -> tuple[int, int] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT screen_chat_id, screen_message_id FROM platega_checks WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()
        if (
            row is None
            or row["screen_chat_id"] is None
            or row["screen_message_id"] is None
        ):
            return None
        return row["screen_chat_id"], row["screen_message_id"]

    def queue_screen(self, transaction_id: str, result: str, text: str) -> None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT screen_chat_id, screen_message_id FROM platega_checks WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()
        if (
            row is not None
            and row["screen_chat_id"] is not None
            and row["screen_message_id"] is not None
        ):
            self.queue_notice(
                f"screen:{transaction_id}:{row['screen_message_id']}:{result}",
                row["screen_chat_id"],
                text,
                message_id=row["screen_message_id"],
            )

    def queue_notice(
        self,
        notice_id: str,
        telegram_id: int,
        text: str,
        *,
        message_id: int | None = None,
    ) -> None:
        if not notice_id or telegram_id <= 0 or not text:
            raise ValueError("Invalid payment notification")
        with closing(self._connect()) as connection:
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO payment_outbox (
                    id, telegram_id, text, message_id, next_attempt_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (notice_id, telegram_id, text, message_id, time.time()),
            ).rowcount
            connection.commit()
        if inserted:
            self.notices_changed.set()

    def pending_notices(
        self, *, now: float | None = None, limit: int = 25
    ) -> list[PaymentNotice]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM payment_outbox
                WHERE delivered_at IS NULL AND next_attempt_at <= ?
                ORDER BY next_attempt_at, id LIMIT ?
                """,
                (time.time() if now is None else now, limit),
            ).fetchall()
        return [
            PaymentNotice(
                row["id"],
                row["telegram_id"],
                row["text"],
                row["message_id"],
                row["attempts"],
            )
            for row in rows
        ]

    def mark_notice_delivered(self, notice_id: str) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                "UPDATE payment_outbox SET delivered_at = ? WHERE id = ?",
                (time.time(), notice_id),
            )
            connection.commit()

    def retry_notice(
        self, notice: PaymentNotice, *, delay: float | None = None
    ) -> None:
        delay = min(300, 10 * 2 ** min(notice.attempts, 5)) if delay is None else delay
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE payment_outbox SET next_attempt_at = ?, attempts = attempts + 1
                WHERE id = ? AND delivered_at IS NULL
                """,
                (time.time() + delay, notice.id),
            )
            connection.commit()
