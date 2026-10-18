from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
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
    created_at: str
    paid_at: str | None
    fulfilled_at: str | None
    term_days: int | None = None
    rub_price_minor: int | None = None
    discount_rub_minor: int | None = None


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
        created_at=row["created_at"],
        paid_at=row["paid_at"],
        fulfilled_at=row["fulfilled_at"],
        term_days=row["term_days"],
        rub_price_minor=row["rub_price_minor"],
        discount_rub_minor=row["discount_rub_minor"],
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
            for column, declaration in (
                ("term_days", "INTEGER"),
                ("rub_price_minor", "INTEGER"),
                ("discount_rub_minor", "INTEGER"),
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
    ) -> Order:
        if telegram_id <= 0:
            raise ValueError("Telegram ID must be positive")
        if term is not None and plan.term_for(term.days) != term:
            raise ValueError("Selected term is not part of the plan")
        stars_amount = plan.price_minor if term is None else term.stars_amount
        if stars_amount is None:
            raise PlanPriceMissing(f"Plan {plan.code} has no price")
        term_days = plan.days if term is None else term.days
        order = Order(
            id=uuid.uuid4().hex,
            telegram_id=telegram_id,
            plan_code=plan.code,
            amount_minor=stars_amount,
            currency=plan.currency,
            status=PENDING,
            provider=None,
            provider_payment_id=None,
            created_at=now or _now(),
            paid_at=None,
            fulfilled_at=None,
            term_days=term_days,
            rub_price_minor=None if term is None else term.price_rub_minor,
            discount_rub_minor=(
                None if term is None else term_savings(plan, term)[0]
            ),
        )
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE orders SET status = ?
                WHERE telegram_id = ? AND status = ?
                  AND datetime(created_at) <= datetime(?, '-30 minutes')
                """,
                (CANCELLED, telegram_id, PENDING, order.created_at),
            )
            existing = connection.execute(
                """
                SELECT * FROM orders
                WHERE telegram_id = ? AND plan_code = ? AND status = ?
                  AND term_days IS ?
                  AND datetime(created_at) > datetime(?, '-30 minutes')
                ORDER BY created_at DESC LIMIT 1
                """,
                (telegram_id, plan.code, PENDING, term_days, order.created_at),
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
                    discount_rub_minor
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                raise OrderConflict("Order already has another payment")
            if order.status != PENDING:
                raise OrderStateError(f"Order is {order.status}")
            if amount_minor != order.amount_minor or currency.upper() != order.currency:
                raise OrderConflict("Payment amount or currency does not match the order")
            paid_at = now or _now()
            try:
                connection.execute(
                    """
                    UPDATE orders
                    SET status = ?, provider = ?, provider_payment_id = ?, paid_at = ?
                    WHERE id = ? AND status = ?
                    """,
                    (
                        PAID,
                        provider,
                        provider_payment_id,
                        paid_at,
                        order.id,
                        PENDING,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise OrderConflict("Payment ID was already used") from error
            updated = connection.execute(
                "SELECT * FROM orders WHERE id = ?", (order.id,)
            ).fetchone()
            connection.commit()
        return _order(updated), True

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

    def mark_fulfilled(self, order_id: str, *, now: str | None = None) -> bool:
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
            connection.execute(
                """
                UPDATE orders SET status = ?, fulfilled_at = ?
                WHERE id = ? AND status = ?
                """,
                (FULFILLED, now or _now(), order.id, FULFILLING),
            )
            connection.commit()
        return True

    def mark_fulfillment_review(self, order_id: str) -> None:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """
                UPDATE orders SET status = ?
                WHERE id = ? AND status = ?
                """,
                (FULFILLMENT_REVIEW, order_id, FULFILLING),
            ).rowcount
            connection.commit()
        if changed != 1:
            raise OrderStateError("Order is not awaiting fulfillment")

    def get_by_payment_id(self, payment_id: str) -> Order | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM orders
                WHERE provider = 'telegram_stars' AND provider_payment_id = ?
                """,
                (payment_id,),
            ).fetchone()
        return None if row is None else _order(row)

    def claim_refund(self, order_id: str) -> bool:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """
                UPDATE orders SET status = ?
                WHERE id = ? AND status = ?
                """,
                (REFUNDING, order_id, FULFILLED),
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
