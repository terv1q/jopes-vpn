"""Owner review signals and retention. Detection never changes VPN access."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from jopesvpn.config import ServicePolicy, Settings
from jopesvpn.panel import PanelError, RemnawaveClient
from jopesvpn.preferences import PreferencesError, PreferencesStore

logger = logging.getLogger(__name__)


class ServiceOpsStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS service_events (
                    event_key TEXT PRIMARY KEY, kind TEXT NOT NULL,
                    facts_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    notified_at TEXT
                );
                CREATE TABLE IF NOT EXISTS trial_attempt_events (
                    telegram_id INTEGER NOT NULL, minute TEXT NOT NULL,
                    PRIMARY KEY (telegram_id, minute)
                );
                CREATE TABLE IF NOT EXISTS service_device_observations (
                    device_digest TEXT NOT NULL, telegram_id INTEGER NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY (device_digest, telegram_id)
                );
                CREATE TABLE IF NOT EXISTS privacy_requests (
                    request_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    processed_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS privacy_pending_user
                    ON privacy_requests(telegram_id) WHERE status = 'pending';
            """)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def record(
        self, key: str, kind: str, facts: dict, *, now: str | None = None
    ) -> bool:
        payload = json.dumps(facts)
        if len(key) > 256 or len(payload) > 4096:
            raise ValueError("Service event too large")
        with closing(self.connect()) as db, db:
            return (
                db.execute(
                    "INSERT OR IGNORE INTO service_events VALUES (?, ?, ?, ?, NULL)",
                    (key, kind, payload, now or datetime.now(UTC).isoformat()),
                ).rowcount
                == 1
            )

    def repeated_trial(
        self, telegram_id: int, threshold: int, *, now: datetime | None = None
    ) -> None:
        current = now or datetime.now(UTC)
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO trial_attempt_events VALUES (?, ?)",
                (telegram_id, current.strftime("%Y-%m-%dT%H:%M:00+00:00")),
            )
            count = db.execute(
                "SELECT count(*) FROM trial_attempt_events WHERE telegram_id = ? AND julianday(minute) >= julianday(?, '-1 day')",
                (telegram_id, current.isoformat()),
            ).fetchone()[0]
        if count >= threshold:
            self.record(
                f"trial:{telegram_id}:{current.date()}",
                "repeat_trial",
                {"user_id": telegram_id, "attempts": count},
                now=current.isoformat(),
            )

    def observe_device(self, telegram_id: int, hwid: str, secret: str) -> None:
        digest = hmac.digest(
            secret.encode(), ("service-device:" + hwid.strip()).encode(), "sha256"
        ).hex()
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT INTO service_device_observations VALUES (?, ?, ?) ON CONFLICT(device_digest, telegram_id) DO UPDATE SET last_seen_at = excluded.last_seen_at",
                (digest, telegram_id, datetime.now(UTC).isoformat()),
            )

    def collect(self, policy: ServicePolicy) -> dict[str, int]:
        day = datetime.now(UTC).date().isoformat()
        with closing(self.connect()) as db:
            waiting = db.execute(
                """
                SELECT t.ticket_id, t.user_id, m.message_id
                FROM support_tickets t JOIN support_ticket_messages m ON m.ticket_id=t.ticket_id
                WHERE t.status='open'
                  AND m.message_id=(SELECT max(message_id) FROM support_ticket_messages WHERE ticket_id=t.ticket_id)
                  AND m.sender_role='user' AND julianday(m.created_at)<=julianday('now', ?)
            """,
                (f"-{policy.support_reminder_hours} hours",),
            ).fetchall()
            devices = db.execute(
                """
                SELECT device_digest,group_concat(telegram_id) AS users,count(*) AS count
                FROM service_device_observations
                WHERE julianday(last_seen_at)>=julianday('now', ?)
                GROUP BY device_digest HAVING count(*)>=2
            """,
                (f"-{policy.event_retention_days} days",),
            ).fetchall()
            referrals = db.execute(
                """
                SELECT referrer_id,count(*) AS count FROM referrals
                WHERE julianday(created_at)>=julianday('now','-1 day')
                GROUP BY referrer_id HAVING count(*)>=?
            """,
                (policy.referral_daily_threshold,),
            ).fetchall()
            loops = db.execute(
                "SELECT a.referred_id,a.referrer_id FROM referrals a JOIN referrals b ON a.referred_id=b.referrer_id AND a.referrer_id=b.referred_id"
            ).fetchall()
        for row in waiting:
            self.record(
                f"support:{row['ticket_id']}:{row['message_id']}",
                "support_waiting",
                {"ticket_id": row["ticket_id"], "user_id": row["user_id"]},
            )
        waiting_keys = {
            f"support:{row['ticket_id']}:{row['message_id']}" for row in waiting
        }
        obsolete = [
            event["key"]
            for event in self.pending(1000)
            if event["kind"] == "support_waiting" and event["key"] not in waiting_keys
        ]
        self.mark_notified(obsolete)
        for row in devices:
            users = sorted(map(int, row["users"].split(",")))
            self.record(
                f"device:{row['device_digest']}:{len(users)}",
                "shared_device",
                {"users": users[:20], "accounts": len(users)},
            )
        for row in referrals:
            self.record(
                f"referrals:{row['referrer_id']}:{day}",
                "referral_burst",
                {"user_id": row["referrer_id"], "count": row["count"]},
            )
        for row in loops:
            users = sorted([row["referred_id"], row["referrer_id"]])
            self.record(f"referral-loop:{users}", "referral_loop", {"users": users})
        return {
            "waiting_support": len(waiting),
            "shared_devices": len(devices),
            "referral_bursts": len(referrals),
        }

    def pending(self, limit: int = 30) -> list[dict]:
        with closing(self.connect()) as db:
            return [
                {
                    "key": row["event_key"],
                    "kind": row["kind"],
                    "facts": json.loads(row["facts_json"]),
                }
                for row in db.execute(
                    "SELECT * FROM service_events WHERE notified_at IS NULL ORDER BY created_at LIMIT ?",
                    (limit,),
                )
            ]

    def mark_notified(self, keys: list[str]) -> None:
        with closing(self.connect()) as db, db:
            db.executemany(
                "UPDATE service_events SET notified_at=datetime('now') WHERE event_key=? AND notified_at IS NULL",
                ((key,) for key in keys),
            )

    def request_erasure(self, telegram_id: int) -> int:
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO privacy_requests(telegram_id) VALUES (?)",
                (telegram_id,),
            )
            request_id = db.execute(
                "SELECT request_id FROM privacy_requests WHERE telegram_id=? AND status='pending'",
                (telegram_id,),
            ).fetchone()[0]
        self.record(
            f"privacy:{request_id}",
            "privacy_request",
            {"request_id": request_id, "user_id": telegram_id},
        )
        return request_id

    def erase_personal_data(self, request_id: int) -> bool:
        """Explicit owner action: remove bot PII, retain billing and entitlement records."""
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT telegram_id FROM privacy_requests WHERE request_id=? AND status='pending'",
                (request_id,),
            ).fetchone()
            if row is None:
                return False
            user_id = row["telegram_id"]
            db.execute("DELETE FROM support_tickets WHERE user_id=?", (user_id,))
            db.execute(
                "UPDATE user_preferences SET first_name=NULL,last_name=NULL,username=NULL WHERE telegram_id=?",
                (user_id,),
            )
            db.execute(
                "DELETE FROM connection_log_endpoints WHERE telegram_id=?", (user_id,)
            )
            db.execute(
                "DELETE FROM connection_log_state WHERE telegram_id=?", (user_id,)
            )
            db.execute(
                "DELETE FROM service_device_observations WHERE telegram_id=?",
                (user_id,),
            )
            db.execute(
                "DELETE FROM trial_attempt_events WHERE telegram_id=?", (user_id,)
            )
            db.execute("DELETE FROM user_navigation WHERE telegram_id=?", (user_id,))
            db.execute(
                "DELETE FROM support_ticket_rate_limits WHERE user_id=?", (user_id,)
            )
            db.execute(
                "UPDATE campaign_button_clicks SET first_name='Удалено',last_name=NULL,username=NULL WHERE telegram_id=?",
                (user_id,),
            )
            db.execute(
                "UPDATE channel_post_clicks SET user_name='Удалено' WHERE user_id=?",
                (user_id,),
            )
            for event in db.execute(
                "SELECT event_key,facts_json FROM service_events"
            ).fetchall():
                facts = json.loads(event["facts_json"])
                if facts.get("user_id") == user_id or user_id in facts.get("users", []):
                    db.execute(
                        "DELETE FROM service_events WHERE event_key=?",
                        (event["event_key"],),
                    )
            db.execute(
                "UPDATE privacy_requests SET status='bot_data_erased',processed_at=datetime('now') WHERE request_id=?",
                (request_id,),
            )
        return True

    def retain(self, policy: ServicePolicy) -> dict[str, int]:
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            deleted = {
                "closed_tickets": db.execute(
                    "DELETE FROM support_tickets WHERE status='closed' AND julianday(closed_at)<julianday('now', ?)",
                    (f"-{policy.ticket_retention_days} days",),
                ).rowcount
            }
            for table, field in (
                ("connection_log_endpoints", "last_seen_at"),
                ("connection_log_state", "COALESCE(last_logged_at, online_at)"),
                ("service_device_observations", "last_seen_at"),
                ("trial_attempt_events", "minute"),
                ("service_events", "created_at"),
            ):
                deleted[table] = db.execute(
                    f"DELETE FROM {table} WHERE julianday({field})<julianday('now', ?)",
                    (f"-{policy.event_retention_days} days",),
                ).rowcount
            db.execute(
                "UPDATE support_ticket_messages SET source_ip=NULL WHERE source_ip IS NOT NULL AND julianday(created_at)<julianday('now', ?)",
                (f"-{policy.event_retention_days} days",),
            )
        return deleted


async def service_loop(
    settings: Settings,
    store: ServiceOpsStore,
    panel: RemnawaveClient,
    prefs: PreferencesStore,
) -> None:
    last_scan = -float("inf")
    last_retention = None
    while True:
        try:
            store.collect(settings.service_policy)
            day = datetime.now(UTC).date()
            if last_retention != day:
                store.retain(settings.service_policy)
                last_retention = day
            if (
                time.monotonic() - last_scan
                >= settings.service_policy.device_scan_seconds
            ):
                for user_id in prefs.known_users():
                    try:
                        user = await panel.get_user_by_telegram_id(user_id)
                        devices = await panel.get_user_devices(user)
                    except PanelError:
                        continue
                    for device in devices:
                        if device.hwid:
                            store.observe_device(
                                user_id, device.hwid, settings.bot_token
                            )
                last_scan = time.monotonic()
                store.collect(settings.service_policy)
        except (sqlite3.Error, ValueError, PreferencesError, OSError) as error:
            logger.warning("Service review scan failed (%s)", type(error).__name__)
        await asyncio.sleep(settings.service_policy.poll_seconds)
