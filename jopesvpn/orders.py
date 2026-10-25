from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jopesvpn.plans import Plan, PlanTerm, term_savings

PENDING = "PENDING"
PAID = "PAID"
FULFILLED = "FULFILLED"
CANCELLED = "CANCELLED"
FULFILLING = "FULFILLING"
FULFILLMENT_REVIEW = "FULFILLMENT_REVIEW"
REFUNDING = "REFUNDING"
REFUNDED = "REFUNDED"

STALE_PENDING_TTL_MINUTES = 24 * 60


class OrderError(Exception):
    pass


class OrderNotFound(OrderError):
    pass


class OrderConflict(OrderError):
    pass


class OrderStateError(OrderError):
    pass


class PlanPriceMissing(OrderError):
    pass


@dataclass(frozen=True)
class Order:
    id: str
    telegram_id: int
    plan_code: str
    amount_minor: int
    currency: str
    status: str
    provider: str | None
    provider_payment_id: str | None
    provider_payment_url: str | None
    created_at: str
    paid_at: str | None
    fulfilled_at: str | None
    term_days: int | None = None
    rub_price_minor: int | None = None
    discount_rub_minor: int | None = None
    provider_payment_method: int | None = None
    provider_expires_at: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _order(row: sqlite3.Row) -> Order:
    return Order(
        id=row["id"],
        telegram_id=row["telegram_id"],
        plan_code=row["plan_code"],
        amount_minor=row["amount_minor"],
        currency=row["currency"],
        status=row["status"],
        provider=row["provider"],
        provider_payment_id=row["provider_payment_id"],
        provider_payment_url=row["provider_payment_url"],
        created_at=row["created_at"],
        paid_at=row["paid_at"],
        fulfilled_at=row["fulfilled_at"],
        term_days=row["term_days"],
        rub_price_minor=row["rub_price_minor"],
        discount_rub_minor=row["discount_rub_minor"],
        provider_payment_method=row["provider_payment_method"],
        provider_expires_at=row["provider_expires_at"],
    )


class OrderStore:
    """Payment-order state independent of a payment provider.

    A provider adapter must call confirm_payment() exactly once per confirmed
    payment. Repeated calls with the same provider payment ID return the same
    order with changed=False, so access cannot be granted twice.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY,
                    telegram_id INTEGER NOT NULL,
                    plan_code TEXT NOT NULL,
                    amount_minor INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    status TEXT NOT NULL,
                    provider TEXT,
                    provider_payment_id TEXT,
                    provider_payment_url TEXT,
                    created_at TEXT NOT NULL,
                    paid_at TEXT,
                    fulfilled_at TEXT,
                    admin_notified_at TEXT,
                    term_days INTEGER,
                    rub_price_minor INTEGER,
                    discount_rub_minor INTEGER,
                    UNIQUE (provider, provider_payment_id)
                )
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(orders)").fetchall()
            }
            if "admin_notified_at" not in columns:
                connection.execute("ALTER TABLE orders ADD COLUMN admin_notified_at TEXT")
            if "provider_payment_url" not in columns:
                connection.execute(
                    "ALTER TABLE orders ADD COLUMN provider_payment_url TEXT"
                )
            for column, declaration in (
                ("term_days", "INTEGER"),
                ("rub_price_minor", "INTEGER"),
                ("discount_rub_minor", "INTEGER"),
                ("recovery_retry_used", "INTEGER NOT NULL DEFAULT 0"),
                ("provider_payment_method", "INTEGER"),
                ("provider_expires_at", "TEXT"),
            ):
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE orders ADD COLUMN {column} {declaration}"
                    )
            connection.execute(
                """
                UPDATE orders SET status = ?
                WHERE status = ?
                """,
                (FULFILLMENT_REVIEW, FULFILLING),
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        return connection

    def create_order(
        self,
        telegram_id: int,
        plan: Plan,
        term: PlanTerm | None = None,
        *,
        now: str | None = None,
        provider: str | None = None,
        payment_method: int | None = None,
    ) -> Order:
        if telegram_id <= 0:
            raise ValueError("Telegram ID must be positive")
        if provider not in {None, "platega"}:
            raise ValueError("External payment requires a priced plan term")
        if provider == "platega" and term is None:
            raise ValueError("External payment requires a priced plan term")
        if term is not None and plan.term_for(term.days) != term:
            raise ValueError("Selected term is not part of the plan")
        stars_amount = plan.price_minor if term is None else term.stars_amount
        if stars_amount is None:
            raise PlanPriceMissing(f"Plan {plan.code} has no price")
        term_days = plan.days if term is None else term.days
        amount_minor = (
            term.price_rub_minor
            if provider == "platega" and term is not None
            else stars_amount
        )
        order = Order(
            id=uuid.uuid4().hex,
            telegram_id=telegram_id,
            plan_code=plan.code,
            amount_minor=amount_minor,
            currency="RUB" if provider == "platega" else plan.currency,
            status=PENDING,
            provider=provider,
            provider_payment_id=None,
            provider_payment_url=None,
            created_at=now or _now(),
            paid_at=None,
            fulfilled_at=None,
            term_days=term_days,
            rub_price_minor=None if term is None else term.price_rub_minor,
            discount_rub_minor=(
                None if term is None else term_savings(plan, term)[0]
            ),
            provider_payment_method=payment_method,
        )
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE orders SET status = ?
                WHERE telegram_id = ? AND status = ?
                  AND (provider IS NULL OR provider != 'platega')
                  AND datetime(created_at) <= datetime(?, '-30 minutes')
                """,
                (CANCELLED, telegram_id, PENDING, order.created_at),
            )
            existing = connection.execute(
                """
                SELECT * FROM orders
                WHERE telegram_id = ? AND plan_code = ? AND status = ?
                  AND term_days IS ?
                  AND provider IS ? AND currency = ?
                  AND provider_payment_method IS ?
                  AND (provider_expires_at IS NULL OR datetime(provider_expires_at) > datetime(?))
                  AND (provider != 'platega' OR provider_payment_id IS NULL OR provider_payment_url IS NOT NULL)
                  AND datetime(created_at) > datetime(?, '-30 minutes')
                ORDER BY created_at DESC LIMIT 1
                """,
                (
                    telegram_id, plan.code, PENDING, term_days, provider,
                    order.currency, payment_method, order.created_at, order.created_at,
                ),
            ).fetchone()
            if existing is not None:
                existing_order = _order(existing)
                if (
                    existing_order.amount_minor == order.amount_minor
                    and existing_order.currency == order.currency
                    and existing_order.rub_price_minor == order.rub_price_minor
                    and existing_order.discount_rub_minor
                    == order.discount_rub_minor
                ):
                    connection.commit()
                    return existing_order
                connection.execute(
                    "UPDATE orders SET status = ? WHERE id = ? AND status = ?",
                    (CANCELLED, existing_order.id, PENDING),
                )
            connection.execute(
                """
                INSERT INTO orders (
                    id, telegram_id, plan_code, amount_minor, currency,
                    status, provider, provider_payment_id, created_at,
                    paid_at, fulfilled_at, term_days, rub_price_minor,
                    discount_rub_minor, provider_payment_method
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order.id,
                    order.telegram_id,
                    order.plan_code,
                    order.amount_minor,
                    order.currency,
                    order.status,
                    order.provider,
                    order.provider_payment_id,
                    order.created_at,
                    order.paid_at,
                    order.fulfilled_at,
                    order.term_days,
                    order.rub_price_minor,
                    order.discount_rub_minor,
                    payment_method,
                ),
            )
            connection.commit()
        return order

    def get(self, order_id: str) -> Order:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM orders WHERE id = ?", (order_id,)
            ).fetchone()
        if row is None:
            raise OrderNotFound(order_id)
        return _order(row)

    def save_payment_link(
        self, order_id: str, payment_id: str, payment_url: str | None = None,
        *, expires_at: str | None = None,
    ) -> Order:
        if not payment_id or (payment_url is not None and not payment_url):
            raise ValueError("Payment ID and URL are required")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """
                UPDATE orders SET provider_payment_id = ?, provider_payment_url = ?, provider_expires_at = ?
                WHERE id = ? AND provider = 'platega' AND status = ?
                  AND provider_payment_id IS NULL
                """,
                (payment_id, payment_url, expires_at, order_id, PENDING),
            ).rowcount
            if changed != 1:
                row = connection.execute(
                    "SELECT * FROM orders WHERE id = ?", (order_id,)
                ).fetchone()
                if (
                    row is None
                    or row["provider"] != "platega"
                    or row["status"] != PENDING
                    or row["provider_payment_id"] != payment_id
                    or row["provider_payment_url"] != payment_url
                ):
                    raise OrderConflict("Payment link cannot be saved")
            row = connection.execute(
                "SELECT * FROM orders WHERE id = ?", (order_id,)
            ).fetchone()
            connection.commit()
        if row is None:
            raise OrderNotFound(order_id)
        return _order(row)

    def latest_for_user(self, telegram_id: int) -> Order | None:
        if telegram_id <= 0:
            raise ValueError("Telegram ID must be positive")
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM orders WHERE telegram_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (telegram_id,),
            ).fetchone()
        return None if row is None else _order(row)

    def user_has_subscription_purchase(self, telegram_id: int) -> bool:
        if telegram_id <= 0:
            raise ValueError("Telegram ID must be positive")
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT 1 FROM orders
                WHERE telegram_id = ?
                  AND status IN (
                      'FULFILLED', 'REFUNDED', 'REFUNDING',
                      'FULFILLMENT_REVIEW'
                  )
                LIMIT 1
                """,
                (telegram_id,),
            ).fetchone()
        return row is not None

    def pending_review_notifications(self, limit: int = 25) -> list[Order]:
        if limit < 1:
            raise ValueError("Limit must be positive")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM orders
                WHERE status = ? AND admin_notified_at IS NULL
                ORDER BY paid_at LIMIT ?
                """,
                (FULFILLMENT_REVIEW, limit),
            ).fetchall()
        return [_order(row) for row in rows]

    def mark_review_notified(self, order_id: str) -> bool:
        with closing(self._connect()) as connection:
            changed = connection.execute(
                """
                UPDATE orders SET admin_notified_at = ?
                WHERE id = ? AND status = ? AND admin_notified_at IS NULL
                """,
                (_now(), order_id, FULFILLMENT_REVIEW),
            ).rowcount
            connection.commit()
        return changed == 1

    def confirm_payment(
        self,
        order_id: str,
        *,
        provider: str,
        provider_payment_id: str,
        amount_minor: int,
        currency: str,
        now: str | None = None,
        payment_recorder: Callable[[sqlite3.Connection, Order], None] | None = None,
    ) -> tuple[Order, bool]:
        if not provider or not provider_payment_id:
            raise ValueError("Provider and provider payment ID are required")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM orders WHERE id = ?", (order_id,)
            ).fetchone()
            if row is None:
                raise OrderNotFound(order_id)
            order = _order(row)
            cancelled_payment = order.status == CANCELLED and provider in {
                "platega", "telegram_stars"
            }
            if order.provider_payment_id is not None:
                if (
                    order.provider == provider
                    and order.provider_payment_id == provider_payment_id
                    and order.status
                    in (
                        PAID,
                        FULFILLING,
                        FULFILLED,
                        FULFILLMENT_REVIEW,
                        REFUNDING,
                        REFUNDED,
                    )
                ):
                    return order, False
                if (
                    order.provider != provider
                    or order.provider_payment_id != provider_payment_id
                    or (order.status != PENDING and not cancelled_payment)
                ):
                    raise OrderConflict("Order already has another payment")
            if order.status != PENDING and not cancelled_payment:
                raise OrderStateError(f"Order is {order.status}")
            if order.provider is not None and order.provider != provider:
                raise OrderConflict("Payment provider does not match the order")
            if amount_minor != order.amount_minor or currency.upper() != order.currency:
                raise OrderConflict("Payment amount or currency does not match the order")
            paid_at = now or _now()
            status = (
                FULFILLMENT_REVIEW
                if cancelled_payment and provider == "telegram_stars"
                else PAID
            )
            try:
                connection.execute(
                    """
                    UPDATE orders
                    SET status = ?, provider = ?, provider_payment_id = ?, paid_at = ?
                    WHERE id = ? AND status = ?
                    """,
                    (
                        status,
                        provider,
                        provider_payment_id,
                        paid_at,
                        order.id,
                        order.status,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise OrderConflict("Payment ID was already used") from error
            updated = connection.execute(
                "SELECT * FROM orders WHERE id = ?", (order.id,)
            ).fetchone()
            confirmed_order = _order(updated)
            if payment_recorder is not None:
                payment_recorder(connection, confirmed_order)
            connection.commit()
        return confirmed_order, True

    def claim_fulfillment(self, order_id: str) -> bool:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM orders WHERE id = ?", (order_id,)
            ).fetchone()
            if row is None:
                raise OrderNotFound(order_id)
            order = _order(row)
            if order.status != PAID:
                return False
            connection.execute(
                "UPDATE orders SET status = ? WHERE id = ? AND status = ?",
                (FULFILLING, order.id, PAID),
            )
            connection.commit()
        return True

    def mark_fulfilled(
        self,
        order_id: str,
        *,
        now: str | None = None,
        payment_completer: Callable[[sqlite3.Connection, str], None] | None = None,
    ) -> bool:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM orders WHERE id = ?", (order_id,)
            ).fetchone()
            if row is None:
                raise OrderNotFound(order_id)
            order = _order(row)
            if order.status == FULFILLED:
                return False
            if order.status != FULFILLING:
                raise OrderStateError(f"Order is {order.status}")
            changed = connection.execute(
                """
                UPDATE orders SET status = ?, fulfilled_at = ?
                WHERE id = ? AND status = ?
                """,
                (FULFILLED, now or _now(), order.id, FULFILLING),
            ).rowcount
            if changed != 1:
                connection.rollback()
                return False
            if payment_completer is not None:
                payment_completer(connection, order.id)
            connection.commit()
        return True

    def mark_fulfillment_review(self, order_id: str) -> None:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """
                UPDATE orders SET status = ?
                WHERE id = ? AND status IN (?, ?)
                """,
                (FULFILLMENT_REVIEW, order_id, FULFILLING, PAID),
            ).rowcount
            connection.commit()
        if changed != 1:
            raise OrderStateError("Order is not awaiting fulfillment")

    def claim_recovery_retry(self, order_id: str) -> bool:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """
                UPDATE orders
                SET status = ?, recovery_retry_used = 1
                WHERE id = ? AND status = ? AND recovery_retry_used = 0
                """,
                (FULFILLING, order_id, FULFILLMENT_REVIEW),
            ).rowcount
            connection.commit()
        return changed == 1

    def get_by_payment_id(
        self, payment_id: str, *, provider: str = "telegram_stars"
    ) -> Order | None:
        if provider not in {"telegram_stars", "platega"}:
            raise ValueError("Unsupported payment provider")
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM orders WHERE provider = ? AND provider_payment_id = ?",
                (provider, payment_id),
            ).fetchone()
        return None if row is None else _order(row)

    def claim_refund(self, order_id: str) -> bool:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """
                UPDATE orders SET status = ?
                WHERE id = ? AND status IN (?, ?, ?)
                """,
                (REFUNDING, order_id, FULFILLED, FULFILLMENT_REVIEW, PAID),
            ).rowcount
            connection.commit()
        return changed == 1

    def release_refund(self, order_id: str, previous_status: str) -> bool:
        if previous_status not in {FULFILLED, FULFILLMENT_REVIEW, PAID}:
            raise ValueError("Invalid previous refund state")
        with closing(self._connect()) as connection:
            changed = connection.execute(
                """
                UPDATE orders SET status = ?
                WHERE id = ? AND status = ?
                """,
                (previous_status, order_id, REFUNDING),
            ).rowcount
            connection.commit()
        return changed == 1

    def mark_refunded(self, order_id: str) -> bool:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """
                UPDATE orders SET status = ?
                WHERE id = ? AND status IN (?, ?, ?, ?)
                """,
                (
                    REFUNDED,
                    order_id,
                    REFUNDING,
                    FULFILLED,
                    FULFILLMENT_REVIEW,
                    PAID,
                ),
            ).rowcount
            connection.commit()
        if not changed:
            order = self.get(order_id)
            if order.status == REFUNDED:
                return False
            raise OrderStateError("Order cannot be marked as refunded")
        return changed == 1

    def cancel(self, order_id: str) -> Order:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM orders WHERE id = ?", (order_id,)
            ).fetchone()
            if row is None:
                raise OrderNotFound(order_id)
            order = _order(row)
            if order.status == CANCELLED:
                return order
            if order.status != PENDING:
                raise OrderStateError(f"Order is {order.status}")
            connection.execute(
                "UPDATE orders SET status = ? WHERE id = ?",
                (CANCELLED, order.id),
            )
            connection.commit()
        return self.get(order_id)

    def cancel_stale_pending(
        self, *, ttl_minutes: int = STALE_PENDING_TTL_MINUTES, now: str | None = None
    ) -> int:
        if not 1 <= ttl_minutes <= 60 * 24 * 365:
            raise ValueError("Stale order TTL is out of range")
        current = datetime.fromisoformat(now or _now())
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        cutoff = current.astimezone(UTC) - timedelta(minutes=ttl_minutes)
        try:
            with closing(self._connect()) as connection:
                cursor = connection.execute(
                    """
                    UPDATE orders SET status = ?
                    WHERE status = ?
                      AND provider_payment_id IS NULL
                      AND julianday(created_at) <= julianday(?)
                    """,
                    (CANCELLED, PENDING, cutoff.isoformat()),
                )
                changed = cursor.rowcount
                connection.commit()
        except sqlite3.Error as error:
            raise OrderError("Stale orders could not be cancelled") from error
        return changed
