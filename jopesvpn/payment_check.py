"""Explicit owner's control payments. Refund only top-ups created by this flow."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
)

from jopesvpn.config import Settings
from jopesvpn.payments import PaymentStore
from jopesvpn.plans import Plan, format_rubles, load_plans, stars_for_rub_amount
from jopesvpn.platega import PlategaClient, PlategaError
from jopesvpn.wallet import MIN_TOPUP_MINOR, WalletError, WalletStore

logger = logging.getLogger(__name__)
router = Router(name="owner-payment-check")
_prepare_lock = asyncio.Lock()


class PaymentCheckStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        with closing(self.connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS service_payment_checks (
                topup_id TEXT PRIMARY KEY, provider TEXT NOT NULL,
                owner_id INTEGER NOT NULL, amount_minor INTEGER NOT NULL,
                state TEXT NOT NULL DEFAULT 'prepared',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                credited_at TEXT, refunded_at TEXT, notified_at TEXT
            )""")

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def add(
        self, topup_id: str, provider: str, owner_id: int, amount_minor: int
    ) -> None:
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO service_payment_checks(topup_id,provider,owner_id,amount_minor) VALUES (?,?,?,?)",
                (topup_id, provider, owner_id, amount_minor),
            )

    def rows(self) -> list[dict]:
        with closing(self.connect()) as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM service_payment_checks WHERE state NOT IN ('refunded','expired') OR created_at >= datetime('now','-7 days')"
                )
            ]

    def claim_refund(self, topup_id: str) -> bool:
        with closing(self.connect()) as db, db:
            return (
                db.execute(
                    "UPDATE service_payment_checks SET state='refund_requesting',credited_at=datetime('now') WHERE topup_id=? AND state IN ('prepared','expired')",
                    (topup_id,),
                ).rowcount
                == 1
            )

    def state(self, topup_id: str, state: str) -> None:
        with closing(self.connect()) as db, db:
            db.execute(
                "UPDATE service_payment_checks SET state=?,refunded_at=CASE WHEN ?='refunded' THEN datetime('now') ELSE refunded_at END WHERE topup_id=?",
                (state, state, topup_id),
            )

    def existing_topups(self, owner: int) -> set[str]:
        with closing(self.connect()) as db:
            return {
                row[0]
                for row in db.execute(
                    "SELECT id FROM wallet_topups WHERE telegram_id=?", (owner,)
                )
            }


async def prepare(*args, **kwargs) -> dict:
    async with _prepare_lock:
        return await _prepare(*args, **kwargs)


async def _prepare(
    bot: Bot,
    settings: Settings,
    wallet: WalletStore,
    payments: PaymentStore,
    client: PlategaClient | None,
    plans: tuple[Plan, ...],
) -> dict:
    owner = settings.service_owner_id
    if owner is None:
        raise ValueError("A unique owner must be configured")
    store = PaymentCheckStore(wallet.database_path)
    await check_once(bot, settings, wallet, client, store)
    existing = [row for row in store.rows() if row["owner_id"] == owner]
    if {row["provider"] for row in existing if row["state"] != "expired"} >= {
        "telegram_stars",
        "platega",
    }:
        return {
            "prepared": False,
            "reason": "existing_check",
            "states": [row["state"] for row in existing],
        }
    stars = stars_for_rub_amount(MIN_TOPUP_MINOR, plans)
    await bot.send_message(
        owner,
        "Jopes VPN — контроль оплаты\n1. Оплатите счёт Stars ниже.\n2. Затем оплатите минимальное пополнение через СБП по следующей кнопке.\nНачисление проверится обычным механизмом кошелька; возврат этих двух контрольных платежей запрашивается автоматически. Не расходуйте контрольные пополнения до окончания проверки.\nСтатусы: /payment_check_status",
    )
    if not any(
        row["provider"] == "telegram_stars" and row["state"] != "expired"
        for row in existing
    ):
        previous_ids = store.existing_topups(owner)
        stars_topup = wallet.create_topup(owner, MIN_TOPUP_MINOR, stars)
        if stars_topup.id in previous_ids:
            raise ValueError("An unrelated pending owner top-up already exists")
        store.add(stars_topup.id, "telegram_stars", owner, MIN_TOPUP_MINOR)
        await bot.send_invoice(
            owner,
            title="Jopes VPN — проверка Stars",
            description=f"Контрольное пополнение {format_rubles(MIN_TOPUP_MINOR)} с последующей проверкой возврата.",
            payload="jvtop_" + stars_topup.id,
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label="Контрольное пополнение", amount=stars)],
        )
    if client is None:
        return {"prepared": True, "stars": stars, "platega": "disabled"}
    if any(
        row["provider"] == "platega" and row["state"] != "expired" for row in existing
    ):
        return {"prepared": True, "stars": stars, "platega": "existing_check"}
    previous_ids = store.existing_topups(owner)
    topup = wallet.create_topup(
        owner, MIN_TOPUP_MINOR, stars, provider="platega", payment_method=2
    )
    if topup.id in previous_ids:
        raise ValueError("An unrelated pending owner top-up already exists")
    username = (await bot.get_me()).username
    if not username:
        raise ValueError("Bot username is required")
    url = "https://t.me/" + username
    store.add(topup.id, "platega", owner, MIN_TOPUP_MINOR)
    link = await client.create_payment(
        amount=Decimal(MIN_TOPUP_MINOR) / 100,
        currency="RUB",
        payment_method=2,
        description="Jopes VPN owner payment check",
        return_url=url,
        failed_url=url,
        order_id=topup.id,
        user_id=owner,
    )
    wallet.save_topup_payment_link(
        topup.id, link.transaction_id, link.url, expires_at=link.expires_at()
    )
    wallet.record_pending_payment(
        payment_id=topup.id,
        telegram_id=owner,
        amount_minor=MIN_TOPUP_MINOR,
        reference=f"topup_payment:{topup.id}",
        external_payment_id=link.transaction_id,
        purpose="topup",
    )
    payments.track(link.transaction_id)
    await bot.send_message(
        owner,
        "Шаг 2 — контрольное пополнение через СБП: "
        + format_rubles(MIN_TOPUP_MINOR)
        + ". Итог платёжного сервиса проверьте перед оплатой.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Оплатить контрольный счёт СБП", url=link.url
                    )
                ]
            ]
        ),
    )
    return {
        "prepared": True,
        "stars": stars,
        "rub_amount_minor": MIN_TOPUP_MINOR,
        "platega": "ready",
    }


async def check_once(
    bot: Bot,
    settings: Settings,
    wallet: WalletStore,
    client: PlategaClient | None,
    store: PaymentCheckStore,
) -> None:
    for row in store.rows():
        if row["owner_id"] != settings.service_owner_id or row["state"] == "refunded":
            continue
        topup = wallet.topup(row["topup_id"])
        if (
            topup.telegram_id != row["owner_id"]
            or topup.provider != row["provider"]
            or topup.amount_minor != row["amount_minor"]
        ):
            raise ValueError("Control payment identity mismatch")
        if topup.status == "REFUNDED":
            # The wallet also retires unpaid stale invoices with this status.
            store.state(topup.id, "refunded" if topup.paid_at else "expired")
            continue
        if topup.status != "CREDITED" or not topup.provider_payment_id:
            created = datetime.fromisoformat(topup.created_at).replace(tzinfo=UTC)
            expires = getattr(topup, "provider_expires_at", None)
            if (
                topup.status == "CANCELED"
                or (
                    isinstance(expires, str)
                    and datetime.fromisoformat(expires).replace(tzinfo=UTC)
                    <= datetime.now(UTC)
                )
                or datetime.now(UTC) - created > timedelta(hours=24)
            ):
                store.state(topup.id, "expired")
            continue
        claimed = store.claim_refund(topup.id)
        if topup.provider == "telegram_stars":
            if not claimed:
                continue
            try:
                refunded = await bot.refund_star_payment(
                    user_id=topup.telegram_id,
                    telegram_payment_charge_id=topup.provider_payment_id,
                )
            except TelegramAPIError:
                store.state(topup.id, "refund_review")
                await bot.send_message(
                    row["owner_id"],
                    "Проверка Stars: начисление подтверждено, ответ о возврате не получен. Повторный возврат автоматически не отправляется; нужна сверка в Telegram.",
                )
                continue
            if refunded:
                wallet.refund_topup(topup.id)
                store.state(topup.id, "refunded")
                await bot.send_message(
                    row["owner_id"],
                    "Проверка Stars: платёж принят, начисление записано, API подтвердил возврат, кошелёк скорректирован.",
                )
            else:
                store.state(topup.id, "refund_review")
        elif client is not None:
            if claimed:
                try:
                    accepted = await client.request_refund(topup.provider_payment_id)
                except PlategaError:
                    store.state(topup.id, "refund_review")
                    continue
                if not accepted:
                    store.state(topup.id, "manual_refund_required")
                    await bot.send_message(
                        row["owner_id"],
                        "Проверка СБП: платёж и начисление подтверждены, но автоматический возврат не принят провайдером. Требуется возврат через кабинет Platega; факт возврата подтвердится только статусом CHARGEBACKED.",
                    )
                    continue
            actual = await client.get_transaction_status(topup.provider_payment_id)
            if actual.status == "CHARGEBACKED":
                wallet.refund_topup(topup.id)
                store.state(topup.id, "refunded")
                await bot.send_message(
                    row["owner_id"],
                    "Проверка СБП: платёж принят, начисление записано, провайдер подтвердил возврат CHARGEBACKED, кошелёк скорректирован.",
                )


async def loop(
    bot: Bot,
    settings: Settings,
    wallet: WalletStore,
    client: PlategaClient | None,
    store: PaymentCheckStore,
) -> None:
    while True:
        try:
            await check_once(bot, settings, wallet, client, store)
        except (
            TelegramAPIError,
            PlategaError,
            WalletError,
            sqlite3.Error,
            ValueError,
        ) as error:
            logger.warning(
                "Control payment verification failed (%s)", type(error).__name__
            )
        await asyncio.sleep(30)


@router.message(Command("payment_check_status"))
async def status(message: Message, settings: Settings, wallet: WalletStore) -> None:
    if (
        message.chat.type != "private"
        or message.from_user is None
        or message.from_user.id != settings.service_owner_id
    ):
        return
    rows = PaymentCheckStore(wallet.database_path).rows()
    await message.answer(
        "Контрольные платежи:\n"
        + (
            "\n".join(row["provider"] + ": " + row["state"] for row in rows)
            or "Проверка ещё не подготовлена."
        )
    )


@router.message(Command("payment_check"))
async def prepare_command(
    message: Message,
    settings: Settings,
    wallet: WalletStore,
    payments: PaymentStore,
    platega: PlategaClient | None,
    plans: tuple[Plan, ...],
) -> None:
    if (
        message.chat.type != "private"
        or message.from_user is None
        or message.from_user.id != settings.service_owner_id
    ):
        return
    result = await prepare(message.bot, settings, wallet, payments, platega, plans)
    if not result["prepared"]:
        await message.answer(
            "Контрольные счета уже подготовлены. Откройте сообщения выше; статусы: /payment_check_status."
        )


async def main() -> None:
    settings = Settings.from_env()
    bot = Bot(settings.bot_token)
    client = (
        PlategaClient(settings.platega_merchant_id, settings.platega_secret)
        if settings.platega_enabled
        else None
    )
    try:
        result = await prepare(
            bot,
            settings,
            WalletStore(settings.state_db_path),
            PaymentStore(settings.state_db_path),
            client,
            load_plans(settings.plans_file),
        )
        print(json.dumps(result))
    finally:
        await bot.session.close()
        if client is not None:
            await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true", required=True)
    parser.parse_args()
    try:
        asyncio.run(main())
    except Exception as error:  # noqa: BLE001 -- suppress payment identifiers and URLs
        print(f"Payment check preparation failed ({type(error).__name__})")
        raise SystemExit(1) from None
