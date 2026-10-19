from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jopesvpn.plans import PlanTerm


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

    @property
    def referral_available_minor(self) -> int:
        return max(0, self.referral_earned_minor - self.referral_withdrawn_minor)


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
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
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
                    processed_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS withdrawal_requests_user
                ON withdrawal_requests (telegram_id, status)
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
        allow_negative: bool = False,
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
        if not allow_negative and (balance < 0 or earned < 0):
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
                applied = self._apply_locked(
                    connection,
                    telegram_id,
                    kind=kind,
                    amount_minor=amount_minor,
                    balance_delta_minor=amount_minor,
                    referral_delta_minor=0,
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
                self._apply_locked(
                    connection,
                    topup.telegram_id,
                    kind="TOPUP",
                    amount_minor=topup.amount_minor,
                    balance_delta_minor=topup.amount_minor,
                    referral_delta_minor=0,
                    reference=f"topup:{topup.id}",
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
                connection.execute(
                    "UPDATE wallet_topups SET status = ? WHERE id = ?",
                    (TOPUP_REFUNDED, topup.id),
                )
                self._apply_locked(
                    connection,
                    topup.telegram_id,
                    kind="TOPUP_REFUND",
                    amount_minor=topup.amount_minor,
                    balance_delta_minor=-topup.amount_minor,
                    referral_delta_minor=0,
                    reference=f"topup_refund:{topup.id}",
                    allow_negative=True,
                )
                referral = connection.execute(
                    "SELECT referrer_id FROM referrals WHERE referred_id = ?",
                    (topup.telegram_id,),
                ).fetchone()
                if referral is not None:
                    cashback = topup.amount_minor * REFERRAL_TOPUP_PERCENT // 100
                    self._apply_locked(
                        connection,
                        int(referral["referrer_id"]),
                        kind="REFERRAL_TOPUP_REFUND",
                        amount_minor=cashback,
                        balance_delta_minor=0,
                        referral_delta_minor=-cashback,
                        reference=f"referral_topup_refund:{topup.id}",
                        allow_negative=True,
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
        applied = self._apply_locked(
            connection,
            referrer_id,
            kind="REFERRAL",
            amount_minor=cashback,
            balance_delta_minor=0,
            referral_delta_minor=cashback,
            reference=reference,
        )
        if not applied:
            return None
        return ReferralReward(referrer_id, referred_id, percent, cashback)

    def reward_referral_purchase(
        self, order_id: str, referred_id: int, amount_minor: int
    ) -> ReferralReward | None:
        if not order_id or referred_id <= 0 or amount_minor <= 0:
            raise ValueError("Invalid referral purchase")
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
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
                        WHERE referred_id = ?
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
                applied = self._apply_locked(
                    connection,
                    referrer_id,
                    kind="REFERRAL",
                    amount_minor=cashback,
                    balance_delta_minor=0,
                    referral_delta_minor=cashback,
                    reference=f"referral_order:{order_id}",
                )
                if not applied:
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
                return ReferralReward(
                    referrer_id, referred_id, percent, cashback
                )
        except sqlite3.Error as error:
            raise WalletError("Cannot reward referral purchase") from error

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
                    FROM referral_purchases WHERE referrer_id = ?
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
