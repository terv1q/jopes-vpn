from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from jopesvpn.config import PromoPolicy
from jopesvpn.wallet import WalletError, WalletStore


class PromoCodeError(Exception):
    pass


@dataclass(frozen=True)
class PromoResult:
    status: str
    reward_minor: int = 0
    balance_minor: int = 0
    settled_minor: int = 0
    retry_after: int = 0


class PromoCodeStore:
    """Balance rewards committed together with their redemption and wallet entry."""

    def __init__(self, wallet: WalletStore, policy: PromoPolicy | None = None) -> None:
        self.wallet = wallet
        self.policy = policy if policy is not None else PromoPolicy()
        self._path = wallet.database_path
        try:
            with closing(self._connect()) as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS voucher_codes (
                        id INTEGER PRIMARY KEY,
                        code_digest TEXT NOT NULL UNIQUE,
                        reward_minor INTEGER NOT NULL CHECK(reward_minor > 0),
                        expires_at INTEGER NOT NULL,
                        max_uses INTEGER NOT NULL CHECK(max_uses > 0),
                        uses INTEGER NOT NULL DEFAULT 0 CHECK(uses >= 0 AND uses <= max_uses),
                        enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                        created_by INTEGER NOT NULL,
                        created_at INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS voucher_redemptions (
                        code_id INTEGER NOT NULL REFERENCES voucher_codes(id),
                        telegram_id INTEGER NOT NULL,
                        reward_minor INTEGER NOT NULL,
                        redeemed_at INTEGER NOT NULL,
                        PRIMARY KEY (code_id, telegram_id)
                    );
                    CREATE TABLE IF NOT EXISTS voucher_attempt_windows (
                        scope_id INTEGER PRIMARY KEY,
                        started_at INTEGER NOT NULL,
                        attempts INTEGER NOT NULL CHECK(attempts >= 0)
                    );
                    """
                )
        except sqlite3.Error as error:
            raise PromoCodeError("Cannot initialize voucher storage") from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def normalize(self, raw: str) -> str:
        # Check raw size before stripping; accept only the documented ASCII alphabet.
        if not isinstance(raw, str) or len(raw) > self.policy.max_code_length + 2:
            raise ValueError("Invalid voucher format")
        code = raw.strip()
        if (
            not self.policy.min_code_length <= len(code) <= self.policy.max_code_length
            or re.fullmatch(r"[A-Za-z0-9_-]+", code, flags=re.ASCII) is None
        ):
            raise ValueError("Invalid voucher format")
        return code.upper()

    def _digest(self, raw: str) -> str:
        return sha256(self.normalize(raw).encode("ascii")).hexdigest()

    @staticmethod
    def _now(now: datetime | None) -> int:
        moment = now if now is not None else datetime.now(UTC)
        if moment.tzinfo is None:
            raise ValueError("A timezone-aware date is required")
        return int(moment.timestamp())

    def create(
        self,
        code: str,
        *,
        reward_minor: int,
        valid_days: int,
        max_uses: int,
        admin_id: int,
        now: datetime | None = None,
    ) -> bool:
        digest = self._digest(code)
        if (
            any(
                type(value) is not int
                for value in (reward_minor, valid_days, max_uses, admin_id)
            )
            or not 1 <= reward_minor <= self.policy.max_reward_minor
            or not 1 <= valid_days <= self.policy.max_valid_days
            or not 1 <= max_uses <= self.policy.max_uses
            or admin_id <= 0
        ):
            raise ValueError("Invalid voucher conditions")
        timestamp = self._now(now)
        try:
            with closing(self._connect()) as connection, connection:
                changed = connection.execute(
                    """
                    INSERT INTO voucher_codes (
                        code_digest, reward_minor, expires_at, max_uses, created_by, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(code_digest) DO NOTHING
                    """,
                    (
                        digest,
                        reward_minor,
                        timestamp + valid_days * 86_400,
                        max_uses,
                        admin_id,
                        timestamp,
                    ),
                ).rowcount
                return changed == 1
        except sqlite3.Error as error:
            raise PromoCodeError("Cannot create voucher") from error

    def disable(self, code: str) -> bool:
        digest = self._digest(code)
        try:
            with closing(self._connect()) as connection, connection:
                return (
                    connection.execute(
                        "UPDATE voucher_codes SET enabled = 0 WHERE code_digest = ? AND enabled = 1",
                        (digest,),
                    ).rowcount
                    == 1
                )
        except sqlite3.Error as error:
            raise PromoCodeError("Cannot disable voucher") from error

    def _attempt(
        self, connection: sqlite3.Connection, telegram_id: int, now: int
    ) -> int:
        waits = []
        for scope_id, limit in (
            (0, self.policy.global_max_attempts),
            (telegram_id, self.policy.max_attempts),
        ):
            row = connection.execute(
                "SELECT started_at, attempts FROM voucher_attempt_windows WHERE scope_id = ?",
                (scope_id,),
            ).fetchone()
            if row is not None:
                remaining = row["started_at"] + self.policy.attempt_window_seconds - now
                if remaining > 0 and row["attempts"] >= limit:
                    waits.append(remaining)
        if waits:
            return max(waits)
        for scope_id in (0, telegram_id):
            connection.execute(
                """
                INSERT INTO voucher_attempt_windows (scope_id, started_at, attempts)
                VALUES (?, ?, 1)
                ON CONFLICT(scope_id) DO UPDATE SET
                    attempts = CASE WHEN started_at <= ? THEN 1 ELSE attempts + 1 END,
                    started_at = CASE WHEN started_at <= ? THEN excluded.started_at ELSE started_at END
                """,
                (
                    scope_id,
                    now,
                    now - self.policy.attempt_window_seconds,
                    now - self.policy.attempt_window_seconds,
                ),
            )
        return 0

    def redeem(
        self, telegram_id: int, raw: str, *, now: datetime | None = None
    ) -> PromoResult:
        if type(telegram_id) is not int or telegram_id <= 0:
            raise ValueError("Telegram ID must be positive")
        timestamp = self._now(now)
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                wait = self._attempt(connection, telegram_id, timestamp)
                if wait:
                    return PromoResult("limited", retry_after=wait)
                try:
                    digest = self._digest(raw)
                except ValueError:
                    return PromoResult("format")
                code = connection.execute(
                    "SELECT * FROM voucher_codes WHERE code_digest = ?", (digest,)
                ).fetchone()
                if code is None:
                    return PromoResult("unavailable")
                previous = connection.execute(
                    "SELECT 1 FROM voucher_redemptions WHERE code_id = ? AND telegram_id = ?",
                    (code["id"], telegram_id),
                ).fetchone()
                if previous is not None:
                    return PromoResult("used")
                if (
                    not code["enabled"]
                    or code["expires_at"] <= timestamp
                    or code["uses"] >= code["max_uses"]
                ):
                    return PromoResult("unavailable")
                amount = int(code["reward_minor"])
                reference = f"voucher:{code['id']}:{telegram_id}"
                # Use the wallet's debt accounting and existing ledger in this same transaction.
                credited = self.wallet._credit_locked(
                    connection,
                    telegram_id,
                    amount,
                    kind="PROMO_REWARD",
                    reference=reference,
                )
                self.wallet._record_transaction_locked(
                    connection,
                    payment_id=reference,
                    telegram_id=telegram_id,
                    amount_paid_minor=0,
                    currency="RUB",
                    rub_amount_minor=amount,
                    method="promo_code",
                    purpose="promo_reward",
                    status="SUCCESS",
                    reference=reference,
                )
                connection.execute(
                    "INSERT INTO voucher_redemptions VALUES (?, ?, ?, ?)",
                    (code["id"], telegram_id, amount, timestamp),
                )
                connection.execute(
                    "UPDATE voucher_codes SET uses = uses + 1 WHERE id = ?",
                    (code["id"],),
                )
                balance = connection.execute(
                    "SELECT balance_minor FROM wallet_accounts WHERE telegram_id = ?",
                    (telegram_id,),
                ).fetchone()[0]
                return PromoResult("applied", amount, balance, amount - credited)
        except (sqlite3.Error, WalletError) as error:
            raise PromoCodeError("Cannot redeem voucher") from error
