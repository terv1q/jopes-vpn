from __future__ import annotations

import hmac
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx


class PlategaError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class PlategaProtocolError(PlategaError):
    pass


@dataclass(frozen=True)
class PaymentLink:
    transaction_id: str
    status: str
    url: str
    expires_in: str | None

    def expires_at(self) -> str:
        duration = timedelta(minutes=15)
        if self.expires_in and re.fullmatch(r"\d{1,3}:[0-5]\d:[0-5]\d", self.expires_in):
            hours, minutes, seconds = map(int, self.expires_in.split(":"))
            duration = timedelta(hours=hours, minutes=minutes, seconds=seconds)
        return (datetime.now(UTC) + duration).isoformat()


@dataclass(frozen=True)
class TransactionStatus:
    transaction_id: str
    status: str
    amount: Decimal
    currency: str
    payload: str | None = None


def _https_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not any(char.isspace() for char in value)
    )


def _response_object(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise PlategaProtocolError("Platega returned an unexpected response")
    return data


class PlategaClient:
    """Thin API client; it does not initiate requests until explicitly called."""

    BASE_URL = "https://app.platega.io"
    PAYMENT_METHODS = frozenset({2, 3, 11, 12, 13, 14})

    def __init__(
        self,
        merchant_id: str,
        secret: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        try:
            merchant_id = str(UUID(merchant_id))
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("Platega MerchantId must be a UUID") from error
        if (
            not secret or len(secret) > 4096 or not secret.isascii()
            or any(ord(char) < 33 or ord(char) == 127 for char in secret)
        ):
            raise ValueError("Platega API secret is required")
        self._merchant_id = merchant_id
        self._secret = secret
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={
                "X-MerchantId": merchant_id,
                "X-Secret": secret,
                "Accept": "application/json",
            },
            timeout=20.0,
            transport=transport,
        )

    async def create_payment(
        self,
        *,
        amount: Decimal,
        currency: str,
        payment_method: int | None = None,
        description: str,
        return_url: str,
        failed_url: str,
        order_id: str,
        user_id: int | None = None,
        user_name: str = "",
    ) -> PaymentLink:
        try:
            normalized_amount = Decimal(str(amount)).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise ValueError("Payment amount must be a positive decimal") from error
        if not normalized_amount.is_finite() or normalized_amount <= 0:
            raise ValueError("Payment amount must be a positive decimal")
        if not isinstance(currency, str):
            raise ValueError("Payment currency must be a three-letter code")
        normalized_currency = currency.strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", normalized_currency):
            raise ValueError("Payment currency must be a three-letter code")
        if payment_method is not None and (
            isinstance(payment_method, bool) or payment_method not in self.PAYMENT_METHODS
        ):
            raise ValueError("Unsupported Platega payment method")
        if (
            not isinstance(description, str)
            or not description.strip()
            or len(description) > 256
        ):
            raise ValueError("Payment description is required")
        if not _https_url(return_url) or not _https_url(failed_url):
            raise ValueError("Platega return URLs must use HTTPS")
        try:
            UUID(order_id)
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("Order ID must be a UUID") from error

        body: dict[str, Any] = {
            "paymentDetails": {
                "amount": float(normalized_amount),
                "currency": normalized_currency,
            },
            "description": description.strip(),
            "return": return_url,
            "failedUrl": failed_url,
            "payload": order_id,
            "orderId": order_id,
        }
        if payment_method is not None:
            body["paymentMethod"] = payment_method
        if user_id is not None:
            if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
                raise ValueError("Payment user ID must be positive")
            body["metadata"] = {"userId": str(user_id), "userName": user_name[:256]}
        try:
            response = await self._client.post(
                "/v2/transaction/process" if payment_method is None else "/transaction/process",
                json=body,
            )
            response.raise_for_status()
            data = _response_object(response.json())
        except httpx.HTTPError as error:
            raise PlategaError(
                f"Platega payment creation failed ({type(error).__name__})",
                status_code=error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None,
            ) from error
        except ValueError as error:
            raise PlategaProtocolError(
                "Platega returned invalid JSON"
            ) from error
        transaction_id = data.get("transactionId")
        status = data.get("status", "PENDING")
        url = data.get("url") or data.get("redirect")
        expires_in = data.get("expiresIn")
        try:
            UUID(transaction_id)
        except (ValueError, TypeError, AttributeError) as error:
            raise PlategaProtocolError(
                "Platega response has no valid transaction ID"
            ) from error
        if (
            not isinstance(status, str)
            or status not in {"PENDING", "CANCELED", "CONFIRMED", "CHARGEBACKED"}
            or not isinstance(url, str)
            or not _https_url(url)
            or (expires_in is not None and not isinstance(expires_in, str))
        ):
            raise PlategaProtocolError(
                "Platega returned incomplete payment details"
            )
        return PaymentLink(str(UUID(transaction_id)), status, url, expires_in)

    async def get_transaction_status(
        self, transaction_id: str
    ) -> TransactionStatus:
        try:
            normalized_id = str(UUID(transaction_id))
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("Transaction ID must be a UUID") from error
        try:
            response = await self._client.get(f"/transaction/{normalized_id}")
            response.raise_for_status()
            data = _response_object(response.json())
        except httpx.HTTPError as error:
            raise PlategaError(
                f"Platega status request failed ({type(error).__name__})"
            ) from error
        except ValueError as error:
            raise PlategaProtocolError(
                "Platega returned invalid JSON"
            ) from error
        payment = data.get("paymentDetails")
        if not isinstance(payment, dict):
            raise PlategaProtocolError(
                "Platega status has no payment details"
            )
        response_id = data.get("id")
        status = data.get("status")
        amount = payment.get("amount")
        currency = payment.get("currency")
        if (
            response_id != normalized_id
            or not isinstance(status, str)
            or status not in {"PENDING", "CANCELED", "CONFIRMED", "CHARGEBACKED"}
        ):
            raise PlategaProtocolError("Platega status identity is invalid")
        if (
            isinstance(amount, bool)
            or not isinstance(amount, (int, float))
            or not isinstance(currency, str)
            or not re.fullmatch(r"[A-Za-z]{3}", currency)
        ):
            raise PlategaProtocolError(
                "Platega status amount or currency is invalid"
            )
        try:
            parsed_amount = Decimal(str(amount))
        except InvalidOperation as error:
            raise PlategaProtocolError(
                "Platega status amount is invalid"
            ) from error
        if not parsed_amount.is_finite() or parsed_amount <= 0:
            raise PlategaProtocolError(
                "Platega status amount is invalid"
            )
        return TransactionStatus(
            transaction_id=normalized_id,
            status=status,
            amount=parsed_amount,
            currency=currency.upper(),
            payload=data.get("payload") if isinstance(data.get("payload"), str) else None,
        )

    def validate_callback(
        self,
        headers: Any,
        payload: Any,
    ) -> TransactionStatus:
        if not isinstance(headers, Mapping):
            raise PlategaProtocolError("Callback headers are invalid")
        normalized_headers = {str(key).lower(): value for key, value in headers.items()}
        merchant_id = normalized_headers.get("x-merchantid")
        secret = normalized_headers.get("x-secret")
        if (
            not isinstance(merchant_id, str)
            or not merchant_id.isascii()
            or not hmac.compare_digest(merchant_id, self._merchant_id)
            or not isinstance(secret, str)
            or not secret.isascii()
            or not hmac.compare_digest(secret, self._secret)
        ):
            raise PlategaError("Platega callback authentication failed")
        data = _response_object(payload)
        transaction_id = data.get("id")
        status = data.get("status")
        amount = data.get("amount")
        currency = data.get("currency")
        try:
            normalized_id = str(UUID(transaction_id))
        except (ValueError, TypeError, AttributeError) as error:
            raise PlategaProtocolError(
                "Platega callback transaction ID is invalid"
            ) from error
        if not isinstance(status, str) or status not in {
            "CONFIRMED",
            "CANCELED",
            "CHARGEBACKED",
        }:
            raise PlategaProtocolError("Platega callback status is invalid")
        if (
            isinstance(amount, bool)
            or not isinstance(amount, (int, float))
            or not isinstance(currency, str)
            or not re.fullmatch(r"[A-Za-z]{3}", currency)
        ):
            raise PlategaProtocolError(
                "Platega callback amount or currency is invalid"
            )
        try:
            parsed_amount = Decimal(str(amount))
        except InvalidOperation as error:
            raise PlategaProtocolError(
                "Platega callback amount is invalid"
            ) from error
        if not parsed_amount.is_finite() or parsed_amount <= 0:
            raise PlategaProtocolError(
                "Platega callback amount is invalid"
            )
        return TransactionStatus(
            transaction_id=normalized_id,
            status=status,
            amount=parsed_amount,
            currency=currency.upper(),
            payload=data.get("payload") if isinstance(data.get("payload"), str) else None,
        )

    async def request_refund(self, transaction_id: str) -> bool:
        """Request a full refund; only CHARGEBACKED proves that money was returned."""
        normalized_id = str(UUID(transaction_id))
        try:
            response = await self._client.get(
                f"/transaction/{normalized_id}/cancel-supported",
                headers={"Accept": "text/plain, application/json"},
            )
            response.raise_for_status()
            supported = _response_object(response.json())
            if supported.get("supported") is not True:
                return False
            response = await self._client.post(
                f"/transaction/{normalized_id}/cancel",
                headers={"Accept": "text/plain, application/json"},
            )
            response.raise_for_status()
            result = _response_object(response.json())
        except httpx.HTTPError as error:
            raise PlategaError(f"Platega refund request failed ({type(error).__name__})") from error
        except ValueError as error:
            raise PlategaProtocolError("Platega returned invalid refund JSON") from error
        if result.get("transactionId") != normalized_id:
            raise PlategaProtocolError("Platega refund identity is invalid")
        return result.get("accepted") is True and result.get("manualControlRequired") is not True

    async def close(self) -> None:
        await self._client.aclose()
