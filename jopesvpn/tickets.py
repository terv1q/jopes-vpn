from __future__ import annotations

import hashlib
import ipaddress
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


class TicketStoreError(Exception):
    pass


class TicketStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS support_tickets (
                        ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        user_name TEXT NOT NULL,
                        category TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'open'
                            CHECK(status IN ('open', 'closed')),
                        created_at TEXT NOT NULL DEFAULT (datetime('now')),
                        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                        closed_at TEXT,
                        closed_by TEXT,
                        rating INTEGER CHECK(rating BETWEEN 1 AND 5),
                        rated_at TEXT,
                        close_prompted_message_id INTEGER,
                        close_prompt_attempted_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS support_tickets_status_updated
                        ON support_tickets(status, updated_at DESC);
                    CREATE INDEX IF NOT EXISTS support_tickets_user_updated
                        ON support_tickets(user_id, updated_at DESC);
                    CREATE TABLE IF NOT EXISTS support_ticket_messages (
                        message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ticket_id INTEGER NOT NULL REFERENCES
                            support_tickets(ticket_id) ON DELETE CASCADE,
                        sender_id INTEGER NOT NULL,
                        sender_role TEXT NOT NULL CHECK(sender_role IN ('user', 'admin')),
                        body TEXT NOT NULL DEFAULT '',
                        photo_file_id TEXT,
                        created_at TEXT NOT NULL DEFAULT (datetime('now')),
                        source_ip TEXT,
                        CHECK(length(body) > 0 OR photo_file_id IS NOT NULL)
                    );
                    CREATE INDEX IF NOT EXISTS support_ticket_messages_ticket
                        ON support_ticket_messages(ticket_id, message_id);
                    CREATE TABLE IF NOT EXISTS support_ticket_portal_tokens (
                        ticket_id INTEGER PRIMARY KEY REFERENCES
                            support_tickets(ticket_id) ON DELETE CASCADE,
                        token_hash TEXT NOT NULL UNIQUE
                    );
                    CREATE TABLE IF NOT EXISTS support_ticket_notifications (
                        notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ticket_id INTEGER NOT NULL REFERENCES
                            support_tickets(ticket_id) ON DELETE CASCADE,
                        recipient_id INTEGER NOT NULL,
                        created_at TEXT NOT NULL DEFAULT (datetime('now')),
                        attempts INTEGER NOT NULL DEFAULT 0,
                        attempted_at TEXT,
                        delivered_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS support_ticket_notifications_pending
                        ON support_ticket_notifications(delivered_at, attempted_at);
                    """
                )
                columns = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info(support_tickets)"
                    ).fetchall()
                }
                if "close_prompted_message_id" not in columns:
                    connection.execute(
                        "ALTER TABLE support_tickets "
                        "ADD COLUMN close_prompted_message_id INTEGER"
                    )
                if "close_prompt_attempted_at" not in columns:
                    connection.execute(
                        "ALTER TABLE support_tickets "
                        "ADD COLUMN close_prompt_attempted_at TEXT"
                    )
                message_columns = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info(support_ticket_messages)"
                    ).fetchall()
                }
                if "source_ip" not in message_columns:
                    connection.execute(
                        "ALTER TABLE support_ticket_messages ADD COLUMN source_ip TEXT"
                    )
                connection.execute("PRAGMA journal_mode = WAL")
                connection.commit()
        except (OSError, sqlite3.Error) as error:
            raise TicketStoreError("Cannot initialize support ticket database") from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @staticmethod
    def _validate_message(body: str, photo_file_id: str | None) -> str:
        message = body.strip()
        if len(message) > 4000:
            raise ValueError("Ticket message is too long")
        if not message and not photo_file_id:
            raise ValueError("Ticket message cannot be empty")
        return message

    def create(
        self,
        user_id: int,
        user_name: str,
        category: str,
        body: str,
        photo_file_id: str | None = None,
    ) -> int:
        if user_id <= 0 or category not in {"connection", "account", "other"}:
            raise ValueError("Invalid ticket")
        message = self._validate_message(body, photo_file_id)
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    INSERT INTO support_tickets (user_id, user_name, category)
                    VALUES (?, ?, ?)
                    """,
                    (user_id, user_name[:120], category),
                )
                ticket_id = int(cursor.lastrowid)
                connection.execute(
                    """
                    INSERT INTO support_ticket_messages
                        (ticket_id, sender_id, sender_role, body, photo_file_id)
                    VALUES (?, ?, 'user', ?, ?)
                    """,
                    (ticket_id, user_id, message, photo_file_id),
                )
                connection.commit()
                return ticket_id
        except sqlite3.Error as error:
            raise TicketStoreError("Cannot create support ticket") from error

    def add_message(
        self,
        ticket_id: int,
        sender_id: int,
        sender_role: str,
        body: str,
        photo_file_id: str | None = None,
        *,
        source_ip: str | None = None,
        notification_recipients: tuple[int, ...] = (),
    ) -> bool:
        if ticket_id <= 0 or sender_id <= 0 or sender_role not in {"user", "admin"}:
            raise ValueError("Invalid ticket message")
        message = self._validate_message(body, photo_file_id)
        if source_ip is not None:
            try:
                source_ip = str(ipaddress.ip_address(source_ip))
            except ValueError:
                raise ValueError("Invalid source IP") from None
        if any(recipient_id <= 0 for recipient_id in notification_recipients):
            raise ValueError("Invalid notification recipient")
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                ticket = connection.execute(
                    "SELECT status FROM support_tickets WHERE ticket_id = ?",
                    (ticket_id,),
                ).fetchone()
                if ticket is None or ticket["status"] != "open":
                    connection.rollback()
                    return False
                connection.execute(
                    """
                    INSERT INTO support_ticket_messages
                        (ticket_id, sender_id, sender_role, body, photo_file_id,
                         source_ip)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ticket_id,
                        sender_id,
                        sender_role,
                        message,
                        photo_file_id,
                        source_ip,
                    ),
                )
                connection.execute(
                    """
                    UPDATE support_tickets
                    SET updated_at = datetime('now') WHERE ticket_id = ?
                    """,
                    (ticket_id,),
                )
                connection.executemany(
                    """
                    INSERT INTO support_ticket_notifications
                        (ticket_id, recipient_id)
                    VALUES (?, ?)
                    """,
                    (
                        (ticket_id, recipient_id)
                        for recipient_id in dict.fromkeys(notification_recipients)
                    ),
                )
                connection.commit()
                return True
        except sqlite3.Error as error:
            raise TicketStoreError("Cannot add support ticket message") from error

    def create_portal_token(self, ticket_id: int, user_id: int) -> str:
        if ticket_id <= 0 or user_id <= 0:
            raise ValueError("Invalid ticket portal request")
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        try:
            with closing(self._connect()) as connection:
                changed = connection.execute(
                    """
                    INSERT INTO support_ticket_portal_tokens (ticket_id, token_hash)
                    SELECT ticket_id, ? FROM support_tickets
                    WHERE ticket_id = ? AND user_id = ?
                    ON CONFLICT(ticket_id) DO UPDATE SET token_hash = excluded.token_hash
                    """,
                    (token_hash, ticket_id, user_id),
                ).rowcount
                connection.commit()
        except sqlite3.Error as error:
            raise TicketStoreError("Cannot create ticket portal access") from error
        if changed != 1:
            raise ValueError("Ticket does not belong to user")
        return token

    def portal_ticket(self, token: str) -> dict[str, Any] | None:
        if not token or len(token) > 128:
            return None
        token_hash = hashlib.sha256(token.encode("ascii", errors="ignore")).hexdigest()
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    """
                    SELECT t.* FROM support_tickets t
                    JOIN support_ticket_portal_tokens p ON p.ticket_id = t.ticket_id
                    WHERE p.token_hash = ?
                    """,
                    (token_hash,),
                ).fetchone()
        except sqlite3.Error as error:
            raise TicketStoreError("Cannot read ticket portal") from error
        return None if row is None else dict(row)

    def admin_tickets(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        if limit < 1 or offset < 0:
            raise ValueError("Invalid ticket list query")
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT t.*,
                        (SELECT body FROM support_ticket_messages m
                         WHERE m.ticket_id = t.ticket_id
                         ORDER BY m.message_id DESC LIMIT 1) AS last_message
                    FROM support_tickets t
                    ORDER BY CASE t.status WHEN 'open' THEN 0 ELSE 1 END,
                        t.updated_at DESC, t.ticket_id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                ).fetchall()
        except sqlite3.Error as error:
            raise TicketStoreError("Cannot read support ticket queue") from error
        return [dict(row) for row in rows]

    def queue_notification(
        self, ticket_id: int, recipient_id: int
    ) -> None:
        if ticket_id <= 0 or recipient_id <= 0:
            raise ValueError("Invalid ticket notification")
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    INSERT INTO support_ticket_notifications (ticket_id, recipient_id)
                    VALUES (?, ?)
                    """,
                    (ticket_id, recipient_id),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise TicketStoreError("Cannot queue ticket notification") from error

    def pending_notifications(self, limit: int = 50) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("Invalid notification query")
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT n.notification_id, n.ticket_id, n.recipient_id,
                        t.user_name, t.category
                    FROM support_ticket_notifications n
                    JOIN support_tickets t ON t.ticket_id = n.ticket_id
                    WHERE n.delivered_at IS NULL
                      AND (n.attempted_at IS NULL OR
                           n.attempted_at <= datetime('now', '-1 minute'))
                      AND n.attempts < 10
                    ORDER BY n.notification_id
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        except sqlite3.Error as error:
            raise TicketStoreError("Cannot read ticket notifications") from error
        return [dict(row) for row in rows]

    def mark_notification_attempt(
        self, notification_id: int, *, delivered: bool
    ) -> None:
        if notification_id <= 0:
            raise ValueError("Invalid notification")
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    UPDATE support_ticket_notifications
                    SET attempts = attempts + 1,
                        attempted_at = datetime('now'),
                        delivered_at = CASE WHEN ? THEN datetime('now')
                                            ELSE delivered_at END
                    WHERE notification_id = ? AND delivered_at IS NULL
                    """,
                    (int(delivered), notification_id),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise TicketStoreError("Cannot update ticket notification") from error

    def purge_old_source_ips(self, retention_days: int = 90) -> int:
        if not 1 <= retention_days <= 3650:
            raise ValueError("Invalid IP retention period")
        try:
            with closing(self._connect()) as connection:
                changed = connection.execute(
                    """
                    UPDATE support_ticket_messages
                    SET source_ip = NULL
                    WHERE source_ip IS NOT NULL
                      AND created_at < datetime('now', ?)
                    """,
                    (f"-{retention_days} days",),
                ).rowcount
                connection.commit()
                return changed
        except sqlite3.Error as error:
            raise TicketStoreError("Cannot purge old ticket IP addresses") from error

    def close(self, ticket_id: int, actor_role: str) -> bool:
        if ticket_id <= 0 or actor_role not in {"user", "admin"}:
            raise ValueError("Invalid ticket close request")
        try:
            with closing(self._connect()) as connection:
                changed = connection.execute(
                    """
                    UPDATE support_tickets
                    SET status = 'closed', closed_at = datetime('now'),
                        closed_by = ?, updated_at = datetime('now')
                    WHERE ticket_id = ? AND status = 'open'
                    """,
                    (actor_role, ticket_id),
                ).rowcount
                connection.commit()
                return changed == 1
        except sqlite3.Error as error:
            raise TicketStoreError("Cannot close support ticket") from error

    def rate(self, ticket_id: int, user_id: int, rating: int) -> bool:
        if ticket_id <= 0 or user_id <= 0 or not 1 <= rating <= 5:
            raise ValueError("Invalid ticket rating")
        try:
            with closing(self._connect()) as connection:
                changed = connection.execute(
                    """
                    UPDATE support_tickets
                    SET rating = ?, rated_at = datetime('now')
                    WHERE ticket_id = ? AND user_id = ? AND status = 'closed'
                        AND rating IS NULL
                        AND EXISTS (
                            SELECT 1 FROM support_ticket_messages
                            WHERE support_ticket_messages.ticket_id =
                                support_tickets.ticket_id
                                AND sender_role = 'admin'
                        )
                    """,
                    (rating, ticket_id, user_id),
                ).rowcount
                connection.commit()
                return changed == 1
        except sqlite3.Error as error:
            raise TicketStoreError("Cannot save ticket rating") from error

    def get(self, ticket_id: int) -> dict[str, Any] | None:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT * FROM support_tickets WHERE ticket_id = ?",
                    (ticket_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise TicketStoreError("Cannot read support ticket") from error
        return None if row is None else dict(row)

    def messages(
        self, ticket_id: int, limit: int = 25, offset: int = 0
    ) -> list[dict[str, Any]]:
        if ticket_id <= 0 or limit < 1 or offset < 0:
            raise ValueError("Invalid ticket message query")
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM (
                        SELECT * FROM support_ticket_messages
                        WHERE ticket_id = ?
                        ORDER BY message_id DESC LIMIT ? OFFSET ?
                    ) ORDER BY message_id
                    """,
                    (ticket_id, limit, offset),
                ).fetchall()
        except sqlite3.Error as error:
            raise TicketStoreError("Cannot read support ticket messages") from error
        return [dict(row) for row in rows]

    def message_count(self, ticket_id: int) -> int:
        if ticket_id <= 0:
            raise ValueError("Invalid ticket query")
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT COUNT(*) AS total FROM support_ticket_messages "
                    "WHERE ticket_id = ?",
                    (ticket_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise TicketStoreError("Cannot count support ticket messages") from error
        return int(row["total"])

    def user_tickets(
        self, user_id: int, limit: int = 10, offset: int = 0
    ) -> list[dict[str, Any]]:
        if user_id <= 0 or limit < 1 or offset < 0:
            raise ValueError("Invalid ticket list query")
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM support_tickets WHERE user_id = ?
                    ORDER BY updated_at DESC, ticket_id DESC LIMIT ? OFFSET ?
                    """,
                    (user_id, limit, offset),
                ).fetchall()
        except sqlite3.Error as error:
            raise TicketStoreError("Cannot read user support tickets") from error
        return [dict(row) for row in rows]

    def user_ticket_count(self, user_id: int) -> int:
        if user_id <= 0:
            raise ValueError("Invalid ticket list query")
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT COUNT(*) AS total FROM support_tickets "
                    "WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise TicketStoreError("Cannot count user support tickets") from error
        return int(row["total"])

    def open_tickets(
        self, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        if limit < 1 or offset < 0:
            raise ValueError("Invalid ticket list query")
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT t.*,
                        (SELECT body FROM support_ticket_messages m
                         WHERE m.ticket_id = t.ticket_id
                         ORDER BY m.message_id DESC LIMIT 1) AS last_message
                    FROM support_tickets t
                    WHERE t.status = 'open'
                    ORDER BY t.updated_at ASC, t.ticket_id ASC LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                ).fetchall()
        except sqlite3.Error as error:
            raise TicketStoreError("Cannot read open support tickets") from error
        return [dict(row) for row in rows]

    def has_admin_reply(self, ticket_id: int) -> bool:
        if ticket_id <= 0:
            raise ValueError("Invalid ticket query")
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    """
                    SELECT 1 FROM support_ticket_messages
                    WHERE ticket_id = ? AND sender_role = 'admin'
                    LIMIT 1
                    """,
                    (ticket_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise TicketStoreError("Cannot read ticket reply status") from error
        return row is not None

    def pending_close_prompts(self, limit: int = 50) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("Limit must be positive")
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT t.ticket_id, t.user_id, m.message_id
                    FROM support_tickets t
                    JOIN support_ticket_messages m
                      ON m.message_id = (
                          SELECT MAX(latest.message_id)
                          FROM support_ticket_messages latest
                          WHERE latest.ticket_id = t.ticket_id
                      )
                    WHERE t.status = 'open'
                      AND m.sender_role = 'admin'
                      AND m.created_at <= datetime('now', '-12 hours')
                      AND (
                          t.close_prompted_message_id IS NULL
                          OR t.close_prompted_message_id != m.message_id
                      )
                      AND (
                          t.close_prompt_attempted_at IS NULL
                          OR t.close_prompt_attempted_at <= datetime('now', '-1 hour')
                      )
                    ORDER BY m.created_at
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        except sqlite3.Error as error:
            raise TicketStoreError("Cannot read pending ticket follow-ups") from error
        return [dict(row) for row in rows]

    def mark_close_prompt_attempted(
        self, ticket_id: int, message_id: int
    ) -> bool:
        if ticket_id <= 0 or message_id <= 0:
            raise ValueError("Invalid ticket follow-up")
        try:
            with closing(self._connect()) as connection:
                changed = connection.execute(
                    """
                    UPDATE support_tickets
                    SET close_prompt_attempted_at = datetime('now')
                    WHERE ticket_id = ? AND status = 'open'
                      AND EXISTS (
                          SELECT 1 FROM support_ticket_messages m
                          WHERE m.ticket_id = support_tickets.ticket_id
                            AND m.message_id = ?
                            AND m.sender_role = 'admin'
                            AND m.message_id = (
                                SELECT MAX(latest.message_id)
                                FROM support_ticket_messages latest
                                WHERE latest.ticket_id =
                                    support_tickets.ticket_id
                            )
                      )
                    """,
                    (ticket_id, message_id),
                ).rowcount
                connection.commit()
        except sqlite3.Error as error:
            raise TicketStoreError("Cannot reserve ticket follow-up") from error
        return changed == 1

    def mark_close_prompt_sent(self, ticket_id: int, message_id: int) -> bool:
        if ticket_id <= 0 or message_id <= 0:
            raise ValueError("Invalid ticket follow-up")
        try:
            with closing(self._connect()) as connection:
                changed = connection.execute(
                    """
                    UPDATE support_tickets
                    SET close_prompted_message_id = ?,
                        close_prompt_attempted_at = NULL
                    WHERE ticket_id = ? AND status = 'open'
                      AND EXISTS (
                          SELECT 1 FROM support_ticket_messages m
                          WHERE m.ticket_id = support_tickets.ticket_id
                            AND m.message_id = ?
                            AND m.sender_role = 'admin'
                            AND m.message_id = (
                                SELECT MAX(latest.message_id)
                                FROM support_ticket_messages latest
                                WHERE latest.ticket_id =
                                    support_tickets.ticket_id
                            )
                      )
                    """,
                    (message_id, ticket_id, message_id),
                ).rowcount
                connection.commit()
        except sqlite3.Error as error:
            raise TicketStoreError("Cannot mark ticket follow-up sent") from error
        return changed == 1

    def summary(self) -> dict[str, int | float | None]:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    """
                    SELECT
                        SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_count,
                        AVG(rating) AS average_rating,
                        SUM(CASE WHEN rating IS NOT NULL THEN 1 ELSE 0 END) AS rated_count
                    FROM support_tickets
                    """
                ).fetchone()
        except sqlite3.Error as error:
            raise TicketStoreError("Cannot read support ticket summary") from error
        return {
            "open": int(row["open_count"] or 0),
            "average": (
                None if row["average_rating"] is None else float(row["average_rating"])
            ),
            "rated": int(row["rated_count"] or 0),
        }
