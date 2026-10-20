from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

MIN_TOPUP_MINOR = 25_000
MAX_TOPUP_MINOR = 10_000_000
MIN_WITHDRAWAL_MINOR = 25_000
TOPUP_PENDING = "PENDING"
TOPUP_CREDITED = "CREDITED"
TOPUP_REFUNDED = "REFUNDED"
WITHDRAWAL_PENDING = "PENDING"
WITHDRAWAL_PAID = "PAID"
WITHDRAWAL_REJECTED = "REJECTED"
REFERRAL_TOPUP_PERCENT = 20


class WalletError(Exception):
    pass


class InsufficientFunds(WalletError):
    pass


class WalletConflict(WalletError):
    pass


class TopupNotFound(WalletError):
    pass


class TopupConflict(WalletError):
    pass


class WithdrawalUnavailable(WalletError):
    pass


class WithdrawalPending(WalletError):
    pass


@dataclass(frozen=True)
class WalletAccount:
    telegram_id: int
    balance_minor: int
    referral_earned_minor: int
    referral_withdrawn_minor: int
    balance_debt_minor: int = 0
    referral_debt_minor: int = 0

    @property
    def referral_available_minor(self) -> int:
        return max(
            0,
            self.referral_earned_minor
            - self.referral_withdrawn_minor
            - self.referral_debt_minor,
        )


@dataclass(frozen=True)
class WalletEntry:
    kind: str
    amount_minor: int
    balance_delta_minor: int
    referral_delta_minor: int
    created_at: str


@dataclass(frozen=True)
class Topup:
    id: str
    telegram_id: int
    amount_minor: int
    stars_amount: int
    status: str
    provider_payment_id: str | None
    created_at: str
    paid_at: str | None


@dataclass(frozen=True)
class ReferralReward:
    referrer_id: int
    referred_id: int
    percent: int
    amount_minor: int


@dataclass(frozen=True)
class ReferralSummary:
    invited: int
    activated: int
    paying: int
    purchases: int
    earned_minor: int
    withdrawn_minor: int
    available_minor: int
    pending_withdrawal_minor: int
    pending_withdrawal_id: str | None


@dataclass(frozen=True)
class WithdrawalRequest:
    id: str
    telegram_id: int
    amount_minor: int
    status: str
    created_at: str


@dataclass(frozen=True)
class PaymentTransaction:
    id: str
    telegram_id: int
    amount_paid_minor: int
    currency: str
    rub_amount_minor: int
    method: str
    purpose: str
    status: str
    created_at: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _topup(row: sqlite3.Row) -> Topup:
    return Topup(
        id=row["id"],
        telegram_id=int(row["telegram_id"]),
        amount_minor=int(row["amount_minor"]),
        stars_amount=int(row["stars_amount"]),
        status=str(row["status"]),
        provider_payment_id=row["provider_payment_id"],
        created_at=str(row["created_at"]),
        paid_at=row["paid_at"],
    )


def _withdrawal(row: sqlite3.Row) -> WithdrawalRequest:
    return WithdrawalRequest(
        id=str(row["id"]),
        telegram_id=int(row["telegram_id"]),
        amount_minor=int(row["amount_minor"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
    )


def _valid_amount(amount_minor: int) -> bool:
    return (
        not isinstance(amount_minor, bool)
        and isinstance(amount_minor, int)
        and amount_minor > 0
    )


class WalletStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS wallet_accounts (
                    telegram_id INTEGER PRIMARY KEY,
                    balance_minor INTEGER NOT NULL DEFAULT 0,
                    referral_earned_minor INTEGER NOT NULL DEFAULT 0,
                    referral_withdrawn_minor INTEGER NOT NULL DEFAULT 0,
                    balance_debt_minor INTEGER NOT NULL DEFAULT 0,
                    referral_debt_minor INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            account_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(wallet_accounts)"
                ).fetchall()
            }
            for column in ("balance_debt_minor", "referral_debt_minor"):
                if column not in account_columns:
                    connection.execute(
                        f"ALTER TABLE wallet_accounts ADD COLUMN {column} "
                        "INTEGER NOT NULL DEFAULT 0"
                    )
            connection.execute(
                """
                UPDATE wallet_accounts
                SET balance_debt_minor = balance_debt_minor - balance_minor,
                    balance_minor = 0
                WHERE balance_minor < 0
                """
            )
            connection.execute(
                """
                UPDATE wallet_accounts
                SET referral_debt_minor =
                        referral_debt_minor - referral_earned_minor,
                    referral_earned_minor = 0
                WHERE referral_earned_minor < 0
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS wallet_ledger (
                    id TEXT PRIMARY KEY,
                    telegram_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    amount_minor INTEGER NOT NULL,
                    balance_delta_minor INTEGER NOT NULL,
                    referral_delta_minor INTEGER NOT NULL,
                    reference TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS payment_transactions (
                    payment_id TEXT PRIMARY KEY,
                    telegram_id INTEGER NOT NULL,
                    amount_paid_minor INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    rub_amount_minor INTEGER NOT NULL,
                    method TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reference TEXT NOT NULL UNIQUE,
                    external_payment_id TEXT UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS payment_transactions_user_created
                ON payment_transactions (telegram_id, created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS wallet_bonus_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    mode TEXT NOT NULL,
                    value INTEGER NOT NULL,
                    admin_telegram_id INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS wallet_bonus_events (
                    topup_id TEXT PRIMARY KEY,
                    telegram_id INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    value INTEGER NOT NULL,
                    bonus_minor INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS wallet_admin_operations (
                    operation_id TEXT PRIMARY KEY,
                    admin_telegram_id INTEGER NOT NULL,
                    telegram_id INTEGER NOT NULL,
                    amount_minor INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    operation_type TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS wallet_ledger_user_created
                ON wallet_ledger (telegram_id, created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS wallet_topups (
                    id TEXT PRIMARY KEY,
                    telegram_id INTEGER NOT NULL,
                    amount_minor INTEGER NOT NULL,
                    stars_amount INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    provider_payment_id TEXT UNIQUE,
                    created_at TEXT NOT NULL,
                    paid_at TEXT,
                    UNIQUE (telegram_id, provider_payment_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS referral_purchases (
                    order_id TEXT PRIMARY KEY,
                    referred_id INTEGER NOT NULL,
                    referrer_id INTEGER NOT NULL,
                    amount_minor INTEGER NOT NULL,
                    percent INTEGER NOT NULL,
                    cashback_minor INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            purchase_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(referral_purchases)"
                ).fetchall()
            }
            if "reversed_at" not in purchase_columns:
                connection.execute(
                    "ALTER TABLE referral_purchases ADD COLUMN reversed_at TEXT"
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS referral_purchases_referred
                ON referral_purchases (referred_id, created_at)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS withdrawal_requests (
                    id TEXT PRIMARY KEY,
                    telegram_id INTEGER NOT NULL,
                    amount_minor INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    processed_at TEXT,
                    admin_notified_at TEXT
                )
                """
            )
            withdrawal_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(withdrawal_requests)"
                ).fetchall()
            }
            if "admin_notified_at" not in withdrawal_columns:
                connection.execute(
                    "ALTER TABLE withdrawal_requests ADD COLUMN "
                    "admin_notified_at TEXT"
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS withdrawal_requests_user
                ON withdrawal_requests (telegram_id, status)
                """
            )
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if "orders" in tables:
                connection.execute(
                    """
                INSERT OR IGNORE INTO payment_transactions (
                    payment_id, telegram_id, amount_paid_minor, currency,
                    rub_amount_minor, method, purpose, status, reference,
                    external_payment_id, created_at, updated_at
                )
                SELECT o.id, o.telegram_id,
                    CASE WHEN o.provider = 'balance'
                        THEN o.rub_price_minor ELSE o.amount_minor END,
                    CASE WHEN o.provider = 'balance' THEN 'RUB' ELSE o.currency END,
                    o.rub_price_minor,
                    CASE WHEN o.provider = 'balance'
                        THEN 'wallet' ELSE 'telegram_stars' END,
                    'subscription',
                    CASE
                        WHEN o.status = 'REFUNDED' THEN 'REFUNDED'
                        WHEN o.status = 'FULFILLED' THEN 'SUCCESS'
                        ELSE 'REVIEW'
                    END,
                    'order_payment:' || o.id,
                    CASE WHEN o.provider = 'balance'
                        THEN NULL ELSE o.provider_payment_id END,
                    COALESCE(o.paid_at, o.created_at),
                    COALESCE(o.paid_at, o.created_at)
                FROM orders o
                WHERE o.provider_payment_id IS NOT NULL
                    AND o.rub_price_minor IS NOT NULL
                    AND o.status IN (
                        'PAID', 'FULFILLING', 'FULFILLED',
                        'FULFILLMENT_REVIEW', 'REFUNDED'
                    )
                """
                    )
            connection.execute(
                """
                INSERT OR IGNORE INTO payment_transactions (
                    payment_id, telegram_id, amount_paid_minor, currency,
                    rub_amount_minor, method, purpose, status, reference,
                    external_payment_id, created_at, updated_at
                )
                SELECT id, telegram_id, stars_amount, 'XTR', amount_minor,
                    'telegram_stars', 'topup',
                    CASE WHEN status = 'REFUNDED'
                        THEN 'REFUNDED' ELSE 'SUCCESS' END,
                    'topup_payment:' || id, provider_payment_id,
                    COALESCE(paid_at, created_at), COALESCE(paid_at, created_at)
                FROM wallet_topups
                WHERE provider_payment_id IS NOT NULL
                    AND status IN ('CREDITED', 'REFUNDED')
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO payment_transactions (
                    payment_id, telegram_id, amount_paid_minor, currency,
                    rub_amount_minor, method, purpose, status, reference,
                    external_payment_id, created_at, updated_at
                )
                SELECT id, telegram_id, amount_minor, 'RUB', amount_minor,
                    'manual', 'withdrawal', 'SUCCESS',
                    'withdrawal:' || id, NULL,
                    COALESCE(processed_at, created_at),
                    COALESCE(processed_at, created_at)
                FROM withdrawal_requests
                WHERE status = 'PAID'
                """
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        return connection

    def account(self, telegram_id: int) -> WalletAccount:
        if telegram_id <= 0:
            raise ValueError("Telegram ID must be positive")
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT * FROM wallet_accounts WHERE telegram_id = ?",
                    (telegram_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise WalletError("Cannot read wallet account") from error
        if row is None:
            return WalletAccount(telegram_id, 0, 0, 0)
        return WalletAccount(
            int(row["telegram_id"]),
            int(row["balance_minor"]),
            int(row["referral_earned_minor"]),
            int(row["referral_withdrawn_minor"]),
            int(row["balance_debt_minor"]),
            int(row["referral_debt_minor"]),
        )

    def history(self, telegram_id: int, limit: int = 10) -> list[WalletEntry]:
        if telegram_id <= 0 or not 1 <= limit <= 50:
            raise ValueError("Invalid wallet history request")
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT kind, amount_minor, balance_delta_minor,
                        referral_delta_minor, created_at
                    FROM wallet_ledger
                    WHERE telegram_id = ?
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT ?
                    """,
                    (telegram_id, limit),
                ).fetchall()
        except sqlite3.Error as error:
            raise WalletError("Cannot read wallet history") from error
        return [
            WalletEntry(
                str(row["kind"]),
                int(row["amount_minor"]),
                int(row["balance_delta_minor"]),
                int(row["referral_delta_minor"]),
                str(row["created_at"]),
            )
            for row in rows
        ]

    @staticmethod
    def _record_transaction_locked(
        connection: sqlite3.Connection,
        *,
        payment_id: str,
        telegram_id: int,
        amount_paid_minor: int,
        currency: str,
        rub_amount_minor: int,
        method: str,
        purpose: str,
        status: str,
        reference: str,
        external_payment_id: str | None = None,
    ) -> None:
        now = _now()
        connection.execute(
            """
            INSERT INTO payment_transactions (
                payment_id, telegram_id, amount_paid_minor, currency,
                rub_amount_minor, method, purpose, status, reference,
                external_payment_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payment_id,
                telegram_id,
                amount_paid_minor,
                currency.upper(),
                rub_amount_minor,
                method,
                purpose,
                status,
                reference,
                external_payment_id,
                now,
                now,
            ),
        )

    @staticmethod
    def _payment_transaction(row: sqlite3.Row) -> PaymentTransaction:
        return PaymentTransaction(
            id=str(row["payment_id"]),
            telegram_id=int(row["telegram_id"]),
            amount_paid_minor=int(row["amount_paid_minor"]),
            currency=str(row["currency"]),
            rub_amount_minor=int(row["rub_amount_minor"]),
            method=str(row["method"]),
            purpose=str(row["purpose"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
        )

    def payment_transaction(self, payment_id: str) -> PaymentTransaction | None:
        if not payment_id:
            return None
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    """
                    SELECT * FROM payment_transactions
                    WHERE payment_id = ? OR reference = ?
                        OR external_payment_id = ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (payment_id, payment_id, payment_id),
                ).fetchone()
        except sqlite3.Error as error:
            raise WalletError("Cannot find payment") from error
        return None if row is None else self._payment_transaction(row)

    def payment_history(
        self, telegram_id: int, limit: int = 10
    ) -> list[PaymentTransaction]:
        if telegram_id <= 0 or not 1 <= limit <= 50:
            raise ValueError("Invalid payment history request")
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM payment_transactions
                    WHERE telegram_id = ?
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT ?
                    """,
                    (telegram_id, limit),
                ).fetchall()
        except sqlite3.Error as error:
            raise WalletError("Cannot read payment history") from error
        return [self._payment_transaction(row) for row in rows]

    def complete_order_payment(self, order_id: str, *, status: str) -> None:
        if status not in {"SUCCESS", "REVIEW"}:
            raise ValueError("Invalid payment status")
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    UPDATE payment_transactions
                    SET status = ?, updated_at = ?
                    WHERE reference = ? AND status = 'CONFIRMED'
                    """,
                    (status, _now(), f"order_payment:{order_id}"),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise WalletError("Cannot update payment status") from error

    def record_order_payment(
        self,
        order_id: str,
        *,
        telegram_id: int,
        amount_paid_minor: int,
        currency: str,
        method: str,
        external_payment_id: str,
    ) -> PaymentTransaction:
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                self.record_order_payment_locked(
                    connection,
                    order_id=order_id,
                    telegram_id=telegram_id,
                    amount_paid_minor=amount_paid_minor,
                    currency=currency,
                    method=method,
                    external_payment_id=external_payment_id,
                )
                row = connection.execute(
                    "SELECT * FROM payment_transactions WHERE reference = ?",
                    (f"order_payment:{order_id}",),
                ).fetchone()
                connection.commit()
                if row is None:
                    raise WalletConflict("Payment record was not created")
                return self._payment_transaction(row)
        except sqlite3.Error as error:
            raise WalletError("Cannot record order payment") from error

    def record_order_payment_locked(
        self,
        connection: sqlite3.Connection,
        *,
        order_id: str,
        telegram_id: int,
        amount_paid_minor: int,
        currency: str,
        method: str,
        external_payment_id: str,
    ) -> None:
        order = connection.execute(
            "SELECT * FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        if (
            order is None
            or int(order["telegram_id"]) != telegram_id
            or order["status"] not in {"PAID", "FULFILLING", "FULFILLED"}
            or order["provider_payment_id"] != external_payment_id
            or order["rub_price_minor"] is None
            or int(order["amount_minor"]) != amount_paid_minor
            or str(order["currency"]).upper() != currency.upper()
        ):
            raise WalletConflict("Payment does not match order")
        reference = f"order_payment:{order_id}"
        existing = connection.execute(
            "SELECT * FROM payment_transactions WHERE reference = ?",
            (reference,),
        ).fetchone()
        if existing is not None:
            if (
                existing["external_payment_id"] != external_payment_id
                or int(existing["telegram_id"]) != telegram_id
                or int(existing["amount_paid_minor"]) != amount_paid_minor
            ):
                raise WalletConflict("Order payment record does not match")
            return
        self._record_transaction_locked(
            connection,
            payment_id=order_id,
            telegram_id=telegram_id,
            amount_paid_minor=amount_paid_minor,
            currency=currency,
            rub_amount_minor=int(order["rub_price_minor"]),
            method=method,
            purpose="subscription",
            status="CONFIRMED",
            reference=reference,
            external_payment_id=external_payment_id,
        )

    def bonus_setting(self) -> tuple[str, int] | None:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT mode, value FROM wallet_bonus_settings WHERE id = 1"
                ).fetchone()
        except sqlite3.Error as error:
            raise WalletError("Cannot read bonus setting") from error
        return None if row is None else (str(row["mode"]), int(row["value"]))

    def configure_bonus(
        self, admin_telegram_id: int, mode: str, value: int
    ) -> None:
        if (
            admin_telegram_id <= 0
            or mode not in {"fixed", "percent"}
            or value <= 0
            or (mode == "fixed" and value > MAX_TOPUP_MINOR)
            or (mode == "percent" and value > 100)
        ):
            raise ValueError("Invalid top-up bonus")
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO wallet_bonus_settings (
                        id, mode, value, admin_telegram_id, updated_at
                    ) VALUES (1, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET mode = excluded.mode,
                        value = excluded.value,
                        admin_telegram_id = excluded.admin_telegram_id,
                        updated_at = excluded.updated_at
                    """,
                    (mode, value, admin_telegram_id, _now()),
                )
                connection.execute(
                    """
                    INSERT INTO wallet_admin_operations (
                        operation_id, admin_telegram_id, telegram_id,
                        amount_minor, reason, operation_type, created_at
                    ) VALUES (?, ?, ?, ?, ?, 'bonus_config', ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        admin_telegram_id,
                        admin_telegram_id,
                        value,
                        f"{mode} top-up bonus",
                        _now(),
                    ),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise WalletError("Cannot configure top-up bonus") from error

    def adjust_balance(
        self,
        *,
        operation_id: str,
        admin_telegram_id: int,
        telegram_id: int,
        amount_minor: int,
        reason: str,
    ) -> bool:
        if (
            not operation_id
            or admin_telegram_id <= 0
            or telegram_id <= 0
            or amount_minor == 0
            or abs(amount_minor) > MAX_TOPUP_MINOR
            or not reason.strip()
        ):
            raise ValueError("Invalid administrative balance change")
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                if connection.execute(
                    "SELECT 1 FROM wallet_admin_operations WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone():
                    connection.commit()
                    return False
                if amount_minor > 0:
                    self._credit_locked(
                        connection,
                        telegram_id,
                        amount_minor,
                        kind="ADMIN_CREDIT",
                        reference=f"admin:{operation_id}",
                    )
                else:
                    self._apply_locked(
                        connection,
                        telegram_id,
                        kind="ADMIN_DEBIT",
                        amount_minor=-amount_minor,
                        balance_delta_minor=amount_minor,
                        referral_delta_minor=0,
                        reference=f"admin:{operation_id}",
                    )
                connection.execute(
                    """
                    INSERT INTO wallet_admin_operations (
                        operation_id, admin_telegram_id, telegram_id,
                        amount_minor, reason, operation_type, created_at
                    ) VALUES (?, ?, ?, ?, ?, 'balance_adjustment', ?)
                    """,
                    (
                        operation_id,
                        admin_telegram_id,
                        telegram_id,
                        amount_minor,
                        reason.strip(),
                        _now(),
                    ),
                )
                self._record_transaction_locked(
                    connection,
                    payment_id=operation_id,
                    telegram_id=telegram_id,
                    amount_paid_minor=0,
                    currency="RUB",
                    rub_amount_minor=abs(amount_minor),
                    method="admin",
                    purpose="balance_adjustment",
                    status="SUCCESS",
                    reference=f"admin_adjustment:{operation_id}",
                )
                connection.commit()
                return True
        except InsufficientFunds:
            raise
        except sqlite3.Error as error:
            raise WalletError("Cannot change wallet balance") from error

    @staticmethod
    def _ensure_account_locked(
        connection: sqlite3.Connection, telegram_id: int
    ) -> sqlite3.Row:
        connection.execute(
            """
            INSERT INTO wallet_accounts (telegram_id)
            VALUES (?)
            ON CONFLICT(telegram_id) DO NOTHING
            """,
            (telegram_id,),
        )
        return connection.execute(
            "SELECT * FROM wallet_accounts WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()

    def _apply_locked(
        self,
        connection: sqlite3.Connection,
        telegram_id: int,
        *,
        kind: str,
        amount_minor: int,
        balance_delta_minor: int,
        referral_delta_minor: int,
        reference: str,
    ) -> bool:
        if not reference or len(reference) > 128:
            raise ValueError("Wallet reference is invalid")
        existing = connection.execute(
            "SELECT 1 FROM wallet_ledger WHERE reference = ?",
            (reference,),
        ).fetchone()
        if existing is not None:
            return False
        account = self._ensure_account_locked(connection, telegram_id)
        balance = int(account["balance_minor"]) + balance_delta_minor
        earned = int(account["referral_earned_minor"]) + referral_delta_minor
        if balance < 0 or earned < 0:
            raise InsufficientFunds("Wallet has insufficient funds")
        connection.execute(
            """
            UPDATE wallet_accounts
            SET balance_minor = ?, referral_earned_minor = ?,
                updated_at = datetime('now')
            WHERE telegram_id = ?
            """,
            (balance, earned, telegram_id),
        )
        connection.execute(
            """
            INSERT INTO wallet_ledger (
                id, telegram_id, kind, amount_minor,
                balance_delta_minor, referral_delta_minor,
                reference, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                telegram_id,
                kind,
                amount_minor,
                balance_delta_minor,
                referral_delta_minor,
                reference,
                _now(),
            ),
        )
        return True

    @staticmethod
    def _insert_ledger_locked(
        connection: sqlite3.Connection,
        telegram_id: int,
        *,
        kind: str,
        amount_minor: int,
        balance_delta_minor: int,
        referral_delta_minor: int,
        reference: str,
    ) -> None:
        if not reference or len(reference) > 128:
            raise ValueError("Wallet reference is invalid")
        connection.execute(
            """
            INSERT INTO wallet_ledger (
                id, telegram_id, kind, amount_minor,
                balance_delta_minor, referral_delta_minor,
                reference, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                telegram_id,
                kind,
                amount_minor,
                balance_delta_minor,
                referral_delta_minor,
                reference,
                _now(),
            ),
        )

    def _credit_locked(
        self,
        connection: sqlite3.Connection,
        telegram_id: int,
        amount_minor: int,
        *,
        kind: str,
        reference: str,
    ) -> int:
        account = self._ensure_account_locked(connection, telegram_id)
        debt = int(account["balance_debt_minor"])
        settled = min(debt, amount_minor)
        credited = amount_minor - settled
        connection.execute(
            """
            UPDATE wallet_accounts
            SET balance_minor = balance_minor + ?,
                balance_debt_minor = balance_debt_minor - ?,
                updated_at = datetime('now')
            WHERE telegram_id = ?
            """,
            (credited, settled, telegram_id),
        )
        self._insert_ledger_locked(
            connection,
            telegram_id,
            kind=kind,
            amount_minor=amount_minor,
            balance_delta_minor=credited,
            referral_delta_minor=0,
            reference=reference,
        )
        if settled:
            self._insert_ledger_locked(
                connection,
                telegram_id,
                kind="REFUND_DEBT_SETTLEMENT",
                amount_minor=settled,
                balance_delta_minor=0,
                referral_delta_minor=0,
                reference=f"{reference}:debt",
            )
        return credited

    def credit(
        self,
        telegram_id: int,
        amount_minor: int,
        *,
        kind: str,
        reference: str,
    ) -> bool:
        if telegram_id <= 0 or not _valid_amount(amount_minor):
            raise ValueError("Invalid wallet credit")
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                applied = connection.execute(
                    "SELECT 1 FROM wallet_ledger WHERE reference = ?",
                    (reference,),
                ).fetchone() is None
                if applied:
                    self._credit_locked(
                        connection,
                        telegram_id,
                        amount_minor,
                        kind=kind,
                        reference=reference,
                    )
                connection.commit()
                return applied
        except sqlite3.Error as error:
            raise WalletError("Cannot credit wallet") from error

    def debit(
        self,
        telegram_id: int,
        amount_minor: int,
        *,
        kind: str,
        reference: str,
    ) -> bool:
        if telegram_id <= 0 or not _valid_amount(amount_minor):
            raise ValueError("Invalid wallet debit")
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                applied = self._apply_locked(
                    connection,
                    telegram_id,
                    kind=kind,
                    amount_minor=amount_minor,
                    balance_delta_minor=-amount_minor,
                    referral_delta_minor=0,
                    reference=reference,
                )
                connection.commit()
                return applied
        except sqlite3.Error as error:
            raise WalletError("Cannot debit wallet") from error

    def create_topup(
        self,
        telegram_id: int,
        amount_minor: int,
        stars_amount: int,
        *,
        now: str | None = None,
    ) -> Topup:
        if telegram_id <= 0:
            raise ValueError("Telegram ID must be positive")
        if not MIN_TOPUP_MINOR <= amount_minor <= MAX_TOPUP_MINOR:
            raise ValueError("Top-up amount is out of range")
        if not 1 <= stars_amount <= 10_000:
            raise ValueError("Top-up Stars amount is out of range")
        created_at = now or _now()
        topup = Topup(
            id=uuid.uuid4().hex,
            telegram_id=telegram_id,
            amount_minor=amount_minor,
            stars_amount=stars_amount,
            status=TOPUP_PENDING,
            provider_payment_id=None,
            created_at=created_at,
            paid_at=None,
        )
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE wallet_topups SET status = ?
                WHERE telegram_id = ? AND status = ?
                  AND provider_payment_id IS NULL
                  AND datetime(created_at) <= datetime(?, '-30 minutes')
                """,
                (TOPUP_REFUNDED, telegram_id, TOPUP_PENDING, created_at),
            )
            existing = connection.execute(
                """
                SELECT * FROM wallet_topups
                WHERE telegram_id = ? AND amount_minor = ? AND status = ?
                  AND provider_payment_id IS NULL
                  AND datetime(created_at) > datetime(?, '-30 minutes')
                ORDER BY created_at DESC LIMIT 1
                """,
                (telegram_id, amount_minor, TOPUP_PENDING, created_at),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return _topup(existing)
            connection.execute(
                """
                INSERT INTO wallet_topups (
                    id, telegram_id, amount_minor, stars_amount,
                    status, provider_payment_id, created_at, paid_at
                ) VALUES (?, ?, ?, ?, ?, NULL, ?, NULL)
                """,
                (
                    topup.id,
                    topup.telegram_id,
                    topup.amount_minor,
                    topup.stars_amount,
                    topup.status,
                    topup.created_at,
                ),
            )
            connection.commit()
        return topup

    def topup(self, topup_id: str) -> Topup:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM wallet_topups WHERE id = ?", (topup_id,)
            ).fetchone()
        if row is None:
            raise TopupNotFound(topup_id)
        return _topup(row)

    def topup_by_payment_id(self, payment_id: str) -> Topup | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM wallet_topups WHERE provider_payment_id = ?",
                (payment_id,),
            ).fetchone()
        return None if row is None else _topup(row)

    def topup_bonus_minor(self, topup_id: str) -> int:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT bonus_minor FROM wallet_bonus_events WHERE topup_id = ?",
                    (topup_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise WalletError("Cannot read top-up bonus") from error
        return 0 if row is None else int(row["bonus_minor"])

    def confirm_topup(
        self,
        topup_id: str,
        *,
        provider_payment_id: str,
        amount_minor: int,
        currency: str,
    ) -> tuple[Topup, bool, ReferralReward | None]:
        if not provider_payment_id:
            raise ValueError("Provider payment ID is required")
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM wallet_topups WHERE id = ?", (topup_id,)
                ).fetchone()
                if row is None:
                    raise TopupNotFound(topup_id)
                topup = _topup(row)
                if topup.provider_payment_id is not None:
                    if (
                        topup.provider_payment_id == provider_payment_id
                        and topup.status in (TOPUP_CREDITED, TOPUP_REFUNDED)
                    ):
                        connection.commit()
                        return topup, False, None
                    raise TopupConflict("Top-up already has another payment")
                if topup.status != TOPUP_PENDING:
                    raise TopupConflict("Top-up is no longer pending")
                if currency.upper() != "XTR" or amount_minor != topup.stars_amount:
                    raise TopupConflict("Top-up payment details do not match")
                paid_at = _now()
                connection.execute(
                    """
                    UPDATE wallet_topups
                    SET status = ?, provider_payment_id = ?, paid_at = ?
                    WHERE id = ? AND status = ?
                    """,
                    (
                        TOPUP_CREDITED,
                        provider_payment_id,
                        paid_at,
                        topup.id,
                        TOPUP_PENDING,
                    ),
                )
                self._credit_locked(
                    connection,
                    topup.telegram_id,
                    kind="TOPUP",
                    amount_minor=topup.amount_minor,
                    reference=f"topup:{topup.id}",
                )
                bonus = connection.execute(
                    "SELECT mode, value FROM wallet_bonus_settings WHERE id = 1"
                ).fetchone()
                bonus_minor = 0
                if bonus is not None:
                    mode = str(bonus["mode"])
                    value = int(bonus["value"])
                    bonus_minor = (
                        value
                        if mode == "fixed"
                        else topup.amount_minor * value // 100
                    )
                connection.execute(
                    """
                    INSERT INTO wallet_bonus_events (
                        topup_id, telegram_id, mode, value, bonus_minor, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        topup.id,
                        topup.telegram_id,
                        "none" if bonus is None else mode,
                        0 if bonus is None else value,
                        bonus_minor,
                        paid_at,
                    ),
                )
                if bonus_minor:
                    self._credit_locked(
                        connection,
                        topup.telegram_id,
                        bonus_minor,
                        kind="TOPUP_BONUS",
                        reference=f"topup_bonus:{topup.id}",
                    )
                    self._record_transaction_locked(
                        connection,
                        payment_id=f"bonus_{topup.id}",
                        telegram_id=topup.telegram_id,
                        amount_paid_minor=0,
                        currency="RUB",
                        rub_amount_minor=bonus_minor,
                        method="wallet_bonus",
                        purpose="topup_bonus",
                        status="SUCCESS",
                        reference=f"topup_bonus:{topup.id}",
                    )
                self._record_transaction_locked(
                    connection,
                    payment_id=topup.id,
                    telegram_id=topup.telegram_id,
                    amount_paid_minor=amount_minor,
                    currency=currency,
                    rub_amount_minor=topup.amount_minor,
                    method="telegram_stars",
                    purpose="topup",
                    status="SUCCESS",
                    reference=f"topup_payment:{topup.id}",
                    external_payment_id=provider_payment_id,
                )
                reward = self._reward_referral_locked(
                    connection,
                    referred_id=topup.telegram_id,
                    amount_minor=topup.amount_minor,
                    percent=REFERRAL_TOPUP_PERCENT,
                    reference=f"referral_topup:{topup.id}",
                )
                updated = connection.execute(
                    "SELECT * FROM wallet_topups WHERE id = ?", (topup.id,)
                ).fetchone()
                connection.commit()
        except sqlite3.IntegrityError as error:
            raise TopupConflict("Payment ID was already used") from error
        except sqlite3.Error as error:
            raise WalletError("Cannot confirm top-up") from error
        return _topup(updated), True, reward

    def refund_topup(self, topup_id: str) -> bool:
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM wallet_topups WHERE id = ?", (topup_id,)
                ).fetchone()
                if row is None:
                    raise TopupNotFound(topup_id)
                topup = _topup(row)
                if topup.status == TOPUP_REFUNDED:
                    connection.commit()
                    return False
                if topup.status != TOPUP_CREDITED:
                    connection.commit()
                    return False
                account = self._ensure_account_locked(
                    connection, topup.telegram_id
                )
                bonus = connection.execute(
                    "SELECT bonus_minor FROM wallet_bonus_events WHERE topup_id = ?",
                    (topup.id,),
                ).fetchone()
                refund_amount = topup.amount_minor + (
                    0 if bonus is None else int(bonus["bonus_minor"])
                )
                refundable = min(
                    int(account["balance_minor"]), refund_amount
                )
                debt = refund_amount - refundable
                connection.execute(
                    """
                    UPDATE wallet_accounts
                    SET balance_minor = balance_minor - ?,
                        balance_debt_minor = balance_debt_minor + ?,
                        updated_at = datetime('now')
                    WHERE telegram_id = ?
                    """,
                    (refundable, debt, topup.telegram_id),
                )
                connection.execute(
                    "UPDATE wallet_topups SET status = ? WHERE id = ?",
                    (TOPUP_REFUNDED, topup.id),
                )
                connection.execute(
                    """
                    UPDATE payment_transactions
                    SET status = 'REFUNDED', updated_at = ?
                    WHERE reference IN (?, ?)
                    """,
                    (
                        _now(),
                        f"topup_payment:{topup.id}",
                        f"topup_bonus:{topup.id}",
                    ),
                )
                self._insert_ledger_locked(
                    connection,
                    topup.telegram_id,
                    kind="TOPUP_REFUND",
                    amount_minor=refund_amount,
                    balance_delta_minor=-refundable,
                    referral_delta_minor=0,
                    reference=f"topup_refund:{topup.id}",
                )
                if debt:
                    self._insert_ledger_locked(
                        connection,
                        topup.telegram_id,
                        kind="REFUND_DEBT",
                        amount_minor=debt,
                        balance_delta_minor=0,
                        referral_delta_minor=0,
                        reference=f"topup_refund_debt:{topup.id}",
                    )
                referral = connection.execute(
                    "SELECT referrer_id FROM referrals WHERE referred_id = ?",
                    (topup.telegram_id,),
                ).fetchone()
                if referral is not None:
                    original_reward = connection.execute(
                        """
                        SELECT amount_minor FROM wallet_ledger
                        WHERE reference = ?
                        """,
                        (f"referral_topup:{topup.id}",),
                    ).fetchone()
                    if original_reward is not None:
                        self._reverse_referral_locked(
                            connection,
                            int(referral["referrer_id"]),
                            int(original_reward["amount_minor"]),
                            reference=f"referral_topup_refund:{topup.id}",
                        )
                connection.commit()
                return True
        except sqlite3.Error as error:
            raise WalletError("Cannot refund top-up") from error

    def _reward_referral_locked(
        self,
        connection: sqlite3.Connection,
        *,
        referred_id: int,
        amount_minor: int,
        percent: int,
        reference: str,
    ) -> ReferralReward | None:
        referral = connection.execute(
            "SELECT referrer_id FROM referrals WHERE referred_id = ?",
            (referred_id,),
        ).fetchone()
        if referral is None:
            return None
        referrer_id = int(referral["referrer_id"])
        if referrer_id <= 0 or referrer_id == referred_id:
            return None
        cashback = amount_minor * percent // 100
        if cashback <= 0:
            return None
        if connection.execute(
            "SELECT 1 FROM wallet_ledger WHERE reference = ?", (reference,)
        ).fetchone():
            return None
        account = self._ensure_account_locked(connection, referrer_id)
        debt = int(account["referral_debt_minor"])
        settled = min(debt, cashback)
        credited = cashback - settled
        if settled:
            connection.execute(
                """
                UPDATE wallet_accounts
                SET referral_debt_minor = referral_debt_minor - ?,
                    updated_at = datetime('now')
                WHERE telegram_id = ?
                """,
                (settled, referrer_id),
            )
        applied = self._apply_locked(
            connection,
            referrer_id,
            kind="REFERRAL",
            amount_minor=cashback,
            balance_delta_minor=0,
            referral_delta_minor=credited,
            reference=reference,
        )
        if not applied:
            return None
        if settled:
            self._insert_ledger_locked(
                connection,
                referrer_id,
                kind="REFERRAL_DEBT_SETTLEMENT",
                amount_minor=settled,
                balance_delta_minor=0,
                referral_delta_minor=0,
                reference=f"{reference}:debt",
            )
        return ReferralReward(referrer_id, referred_id, percent, credited)

    def _reverse_referral_locked(
        self,
        connection: sqlite3.Connection,
        referrer_id: int,
        amount_minor: int,
        *,
        reference: str,
    ) -> bool:
        if amount_minor <= 0:
            return False
        account = self._ensure_account_locked(connection, referrer_id)
        available = max(
            0,
            int(account["referral_earned_minor"])
            - int(account["referral_withdrawn_minor"])
            - int(account["referral_debt_minor"]),
        )
        clawback = min(available, amount_minor)
        debt = amount_minor - clawback
        if debt:
            connection.execute(
                """
                UPDATE wallet_accounts
                SET referral_debt_minor = referral_debt_minor + ?,
                    updated_at = datetime('now')
                WHERE telegram_id = ?
                """,
                (debt, referrer_id),
            )
        return self._apply_locked(
            connection,
            referrer_id,
            kind="REFERRAL_REFUND",
            amount_minor=amount_minor,
            balance_delta_minor=0,
            referral_delta_minor=-clawback,
            reference=reference,
        )

    def reward_referral_purchase(
        self, order_id: str, referred_id: int, amount_minor: int
    ) -> ReferralReward | None:
        if not order_id or referred_id <= 0 or amount_minor <= 0:
            raise ValueError("Invalid referral purchase")
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                order = connection.execute(
                    """
                    SELECT telegram_id, rub_price_minor, status
                    FROM orders WHERE id = ?
                    """,
                    (order_id,),
                ).fetchone()
                if order is None or (
                    int(order["telegram_id"]) != referred_id
                    or order["rub_price_minor"] != amount_minor
                    or order["status"] != "FULFILLED"
                ):
                    connection.commit()
                    return None
                referral = connection.execute(
                    "SELECT referrer_id FROM referrals WHERE referred_id = ?",
                    (referred_id,),
                ).fetchone()
                if referral is None:
                    connection.commit()
                    return None
                existing = connection.execute(
                    "SELECT 1 FROM referral_purchases WHERE order_id = ?",
                    (order_id,),
                ).fetchone()
                if existing is not None:
                    connection.commit()
                    return None
                referrer_id = int(referral["referrer_id"])
                if referrer_id <= 0 or referrer_id == referred_id:
                    connection.commit()
                    return None
                count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM referral_purchases
                        WHERE referred_id = ? AND reversed_at IS NULL
                        """,
                        (referred_id,),
                    ).fetchone()[0]
                )
                percent = 70 if count == 0 else 40 if count == 1 else 20
                cashback = amount_minor * percent // 100
                if cashback <= 0:
                    connection.commit()
                    return None
                connection.execute(
                    """
                    INSERT INTO referral_purchases (
                        order_id, referred_id, referrer_id, amount_minor,
                        percent, cashback_minor, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order_id,
                        referred_id,
                        referrer_id,
                        amount_minor,
                        percent,
                        cashback,
                        _now(),
                    ),
                )
                reward = self._reward_referral_locked(
                    connection,
                    referred_id=referred_id,
                    amount_minor=amount_minor,
                    percent=percent,
                    reference=f"referral_order:{order_id}",
                )
                if reward is None:
                    connection.rollback()
                    return None
                connection.execute(
                    """
                    UPDATE referrals SET activated_at = COALESCE(
                        activated_at, datetime('now')
                    ) WHERE referred_id = ?
                    """,
                    (referred_id,),
                )
                connection.commit()
                return reward
        except sqlite3.Error as error:
            raise WalletError("Cannot reward referral purchase") from error

    def refund_order(self, order_id: str) -> bool:
        if not order_id:
            raise ValueError("Invalid order refund")
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                order = connection.execute(
                    "SELECT * FROM orders WHERE id = ?", (order_id,)
                ).fetchone()
                if order is None:
                    raise WalletConflict("Order was not found")
                if order["status"] == "REFUNDED":
                    connection.commit()
                    return False
                if order["status"] not in {
                    "REFUNDING",
                    "FULFILLED",
                    "FULFILLMENT_REVIEW",
                    "PAID",
                }:
                    raise WalletConflict("Order cannot be refunded")
                connection.execute(
                    "UPDATE orders SET status = 'REFUNDED' WHERE id = ?",
                    (order_id,),
                )
                connection.execute(
                    """
                    UPDATE payment_transactions
                    SET status = 'REFUNDED', updated_at = ?
                    WHERE reference = ?
                    """,
                    (_now(), f"order_payment:{order_id}"),
                )
                original_payment = connection.execute(
                    "SELECT * FROM payment_transactions WHERE reference = ?",
                    (f"order_payment:{order_id}",),
                ).fetchone()
                if order["provider"] == "balance":
                    refund_amount = int(order["rub_price_minor"])
                    self._credit_locked(
                        connection,
                        int(order["telegram_id"]),
                        refund_amount,
                        kind="PURCHASE_REFUND",
                        reference=f"order_refund:{order_id}",
                    )
                    self._record_transaction_locked(
                        connection,
                        payment_id=f"refund_{order_id}",
                        telegram_id=int(order["telegram_id"]),
                        amount_paid_minor=refund_amount,
                        currency="RUB",
                        rub_amount_minor=refund_amount,
                        method="wallet",
                        purpose="subscription_refund",
                        status="SUCCESS",
                        reference=f"order_refund:{order_id}",
                    )
                elif original_payment is not None:
                    self._record_transaction_locked(
                        connection,
                        payment_id=f"refund_{order_id}",
                        telegram_id=int(order["telegram_id"]),
                        amount_paid_minor=int(original_payment["amount_paid_minor"]),
                        currency=str(original_payment["currency"]),
                        rub_amount_minor=int(original_payment["rub_amount_minor"]),
                        method=str(original_payment["method"]),
                        purpose="subscription_refund",
                        status="SUCCESS",
                        reference=f"order_refund:{order_id}",
                    )
                purchase = connection.execute(
                    """
                    SELECT referrer_id, cashback_minor, reversed_at
                    FROM referral_purchases
                    WHERE order_id = ?
                    """,
                    (order_id,),
                ).fetchone()
                if purchase is not None and purchase["reversed_at"] is None:
                    reward = connection.execute(
                        "SELECT 1 FROM wallet_ledger WHERE reference = ?",
                        (f"referral_order:{order_id}",),
                    ).fetchone()
                    if reward is None:
                        raise WalletConflict(
                            "Referral reward ledger entry is missing"
                        )
                    connection.execute(
                        """
                        UPDATE referral_purchases SET reversed_at = ?
                        WHERE order_id = ? AND reversed_at IS NULL
                        """,
                        (_now(), order_id),
                    )
                    self._reverse_referral_locked(
                        connection,
                        int(purchase["referrer_id"]),
                        int(purchase["cashback_minor"]),
                        reference=f"referral_order_refund:{order_id}",
                    )
                connection.commit()
                return True
        except sqlite3.Error as error:
            raise WalletError("Cannot refund order") from error

    def pay_order(
        self,
        order_id: str,
        telegram_id: int,
        amount_minor: int,
        *,
        now: str | None = None,
    ) -> bool:
        if not order_id or telegram_id <= 0 or amount_minor <= 0:
            raise ValueError("Invalid balance payment")
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM orders WHERE id = ?", (order_id,)
                ).fetchone()
                if row is None:
                    raise WalletConflict("Order was not found")
                if int(row["telegram_id"]) != telegram_id:
                    raise WalletConflict("Order owner does not match")
                if (
                    row["status"] == "PAID"
                    and row["provider"] == "balance"
                    and row["provider_payment_id"] == f"balance:{order_id}"
                ):
                    connection.commit()
                    return False
                if row["status"] != "PENDING":
                    raise WalletConflict("Order is no longer pending")
                if row["rub_price_minor"] != amount_minor:
                    raise WalletConflict("Order amount does not match")
                applied = self._apply_locked(
                    connection,
                    telegram_id,
                    kind="PURCHASE",
                    amount_minor=amount_minor,
                    balance_delta_minor=-amount_minor,
                    referral_delta_minor=0,
                    reference=f"order:{order_id}",
                )
                if not applied:
                    raise WalletConflict("Order payment was already applied")
                changed = connection.execute(
                    """
                    UPDATE orders
                    SET status = 'PAID', provider = 'balance',
                        provider_payment_id = ?, paid_at = ?
                    WHERE id = ? AND status = 'PENDING'
                    """,
                    (f"balance:{order_id}", now or _now(), order_id),
                ).rowcount
                if changed != 1:
                    connection.rollback()
                    raise WalletConflict("Order state changed")
                self._record_transaction_locked(
                    connection,
                    payment_id=order_id,
                    telegram_id=telegram_id,
                    amount_paid_minor=amount_minor,
                    currency="RUB",
                    rub_amount_minor=amount_minor,
                    method="wallet",
                    purpose="subscription",
                    status="CONFIRMED",
                    reference=f"order_payment:{order_id}",
                )
                connection.commit()
                return True
        except sqlite3.Error as error:
            raise WalletError("Cannot pay order from balance") from error

    def request_withdrawal(
        self, telegram_id: int, amount_minor: int
    ) -> WithdrawalRequest:
        if telegram_id <= 0 or amount_minor < MIN_WITHDRAWAL_MINOR:
            raise WithdrawalUnavailable("Withdrawal amount is below the minimum")
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                pending = connection.execute(
                    """
                    SELECT * FROM withdrawal_requests
                    WHERE telegram_id = ? AND status = ?
                    """,
                    (telegram_id, WITHDRAWAL_PENDING),
                ).fetchone()
                if pending is not None:
                    raise WithdrawalPending("Withdrawal is already pending")
                account = self._ensure_account_locked(connection, telegram_id)
                available = int(account["referral_earned_minor"]) - int(
                    account["referral_withdrawn_minor"]
                )
                if amount_minor > available:
                    raise InsufficientFunds("Withdrawal exceeds available amount")
                request = WithdrawalRequest(
                    id=uuid.uuid4().hex,
                    telegram_id=telegram_id,
                    amount_minor=amount_minor,
                    status=WITHDRAWAL_PENDING,
                    created_at=_now(),
                )
                connection.execute(
                    """
                    INSERT INTO withdrawal_requests (
                        id, telegram_id, amount_minor, status, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        request.id,
                        request.telegram_id,
                        request.amount_minor,
                        request.status,
                        request.created_at,
                    ),
                )
                connection.execute(
                    """
                    UPDATE wallet_accounts
                    SET referral_withdrawn_minor = referral_withdrawn_minor + ?,
                        updated_at = datetime('now')
                    WHERE telegram_id = ?
                    """,
                    (amount_minor, telegram_id),
                )
                self._apply_locked(
                    connection,
                    telegram_id,
                    kind="WITHDRAWAL",
                    amount_minor=amount_minor,
                    balance_delta_minor=0,
                    referral_delta_minor=0,
                    reference=f"withdrawal:{request.id}",
                )
                connection.commit()
                return request
        except sqlite3.Error as error:
            raise WalletError("Cannot request withdrawal") from error

    def pending_withdrawal(self, telegram_id: int) -> WithdrawalRequest | None:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    """
                    SELECT * FROM withdrawal_requests
                    WHERE telegram_id = ? AND status = ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (telegram_id, WITHDRAWAL_PENDING),
                ).fetchone()
        except sqlite3.Error as error:
            raise WalletError("Cannot read withdrawal request") from error
        return None if row is None else _withdrawal(row)

    def pending_withdrawal_notifications(
        self, limit: int = 25
    ) -> list[WithdrawalRequest]:
        if not 1 <= limit <= 100:
            raise ValueError("Invalid withdrawal notification limit")
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM withdrawal_requests
                    WHERE status = ? AND admin_notified_at IS NULL
                    ORDER BY created_at LIMIT ?
                    """,
                    (WITHDRAWAL_PENDING, limit),
                ).fetchall()
        except sqlite3.Error as error:
            raise WalletError("Cannot read withdrawal notification queue") from error
        return [_withdrawal(row) for row in rows]

    def mark_withdrawal_notified(self, request_id: str) -> bool:
        if not request_id:
            raise ValueError("Invalid withdrawal request ID")
        try:
            with closing(self._connect()) as connection:
                changed = connection.execute(
                    """
                    UPDATE withdrawal_requests SET admin_notified_at = ?
                    WHERE id = ? AND status = ? AND admin_notified_at IS NULL
                    """,
                    (_now(), request_id, WITHDRAWAL_PENDING),
                ).rowcount
                connection.commit()
        except sqlite3.Error as error:
            raise WalletError("Cannot mark withdrawal notification") from error
        return changed == 1

    def settle_withdrawal(
        self, request_id: str, *, paid: bool
    ) -> tuple[WithdrawalRequest, bool]:
        if not request_id or not isinstance(paid, bool):
            raise ValueError("Invalid withdrawal settlement")
        status = WITHDRAWAL_PAID if paid else WITHDRAWAL_REJECTED
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM withdrawal_requests WHERE id = ?",
                    (request_id,),
                ).fetchone()
                if row is None:
                    raise WithdrawalUnavailable("Withdrawal request not found")
                request = _withdrawal(row)
                if request.status != WITHDRAWAL_PENDING:
                    connection.commit()
                    return request, False
                changed = connection.execute(
                    """
                    UPDATE withdrawal_requests
                    SET status = ?, processed_at = ?
                    WHERE id = ? AND status = ?
                    """,
                    (status, _now(), request_id, WITHDRAWAL_PENDING),
                ).rowcount
                if changed != 1:
                    connection.rollback()
                    return request, False
                if paid:
                    self._record_transaction_locked(
                        connection,
                        payment_id=request.id,
                        telegram_id=request.telegram_id,
                        amount_paid_minor=request.amount_minor,
                        currency="RUB",
                        rub_amount_minor=request.amount_minor,
                        method="manual",
                        purpose="withdrawal",
                        status="SUCCESS",
                        reference=f"withdrawal:{request.id}",
                    )
                else:
                    released = connection.execute(
                        """
                        UPDATE wallet_accounts
                        SET referral_withdrawn_minor =
                                referral_withdrawn_minor - ?,
                            updated_at = datetime('now')
                        WHERE telegram_id = ?
                          AND referral_withdrawn_minor >= ?
                        """,
                        (
                            request.amount_minor,
                            request.telegram_id,
                            request.amount_minor,
                        ),
                    ).rowcount
                    if released != 1:
                        raise WalletConflict(
                            "Withdrawal reserve could not be released"
                        )
                    self._insert_ledger_locked(
                        connection,
                        request.telegram_id,
                        kind="WITHDRAWAL_REJECTED",
                        amount_minor=request.amount_minor,
                        balance_delta_minor=0,
                        referral_delta_minor=request.amount_minor,
                        reference=f"withdrawal_rejected:{request_id}",
                    )
                connection.commit()
                return (
                    WithdrawalRequest(
                        request.id,
                        request.telegram_id,
                        request.amount_minor,
                        status,
                        request.created_at,
                    ),
                    True,
                )
        except sqlite3.Error as error:
            raise WalletError("Cannot settle withdrawal request") from error

    def referral_summary(self, referrer_id: int) -> ReferralSummary:
        if referrer_id <= 0:
            raise ValueError("Telegram ID must be positive")
        account = self.account(referrer_id)
        try:
            with closing(self._connect()) as connection:
                invited_row = connection.execute(
                    """
                    SELECT COUNT(*) AS invited,
                        SUM(CASE WHEN activated_at IS NOT NULL THEN 1 ELSE 0 END)
                            AS activated
                    FROM referrals WHERE referrer_id = ?
                    """,
                    (referrer_id,),
                ).fetchone()
                purchase_row = connection.execute(
                    """
                    SELECT COUNT(*) AS purchases,
                        COUNT(DISTINCT referred_id) AS paying
                    FROM referral_purchases
                    WHERE referrer_id = ? AND reversed_at IS NULL
                    """,
                    (referrer_id,),
                ).fetchone()
                pending = connection.execute(
                    """
                    SELECT id, amount_minor FROM withdrawal_requests
                    WHERE telegram_id = ? AND status = ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (referrer_id, WITHDRAWAL_PENDING),
                ).fetchone()
        except sqlite3.Error as error:
            raise WalletError("Cannot read referral summary") from error
        return ReferralSummary(
            invited=int(invited_row["invited"] or 0),
            activated=int(invited_row["activated"] or 0),
            paying=int(purchase_row["paying"] or 0),
            purchases=int(purchase_row["purchases"] or 0),
            earned_minor=account.referral_earned_minor,
            withdrawn_minor=account.referral_withdrawn_minor,
            available_minor=account.referral_available_minor,
            pending_withdrawal_minor=(
                int(pending["amount_minor"]) if pending is not None else 0
            ),
            pending_withdrawal_id=str(pending["id"]) if pending is not None else None,
        )


def seconds_since(timestamp: str) -> float:
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - parsed).total_seconds())


def topup_reuse_deadline(topup: Topup) -> datetime:
    try:
        created = datetime.fromisoformat(topup.created_at)
    except ValueError:
        return datetime.now(UTC)
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return created + timedelta(minutes=30)
