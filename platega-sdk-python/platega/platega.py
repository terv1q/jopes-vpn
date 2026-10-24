"""
Platega API Client

@see https://docs.platega.io/llms.txt
"""

import hashlib
import hmac
import json
import time
import math
import re
from base64 import b64encode
from typing import Dict, Any, Optional, List, Union
from urllib.parse import urlencode, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

from .exceptions import PlategaAPIError


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class Platega:
    """
    Main class for interacting with Platega API

    Example:
        client = Platega('merchant_id', 'secret')
        result = client.create_payment(1000, 'RUB', Platega.METHOD_SBP_QR)
    """

    API_URL = 'https://app.platega.io'
    VERSION = '1.2.0'

    # Payment methods (PaymentMethodInt)
    METHOD_SBP_QR = 2           # СБП QR + Sberpay
    METHOD_ERIP = 3             # ЕРИП
    METHOD_CARDS_RUB = 10       # Legacy; prefer METHOD_CARD_ACQUIRING
    METHOD_CARD_ACQUIRING = 11  # Карточный эквайринг
    METHOD_INTERNATIONAL = 12   # Международная оплата
    METHOD_CRYPTO = 13          # Криптовалюта
    METHOD_SUBSCRIPTION = 6     # Рекуррентная СБП-подписка

    INTERVAL_DAY = 1
    INTERVAL_WEEK = 2
    INTERVAL_MONTH = 3
    INTERVAL_YEAR = 4

    STATUS_PENDING = 'PENDING'
    STATUS_CONFIRMED = 'CONFIRMED'
    STATUS_CANCELED = 'CANCELED'
    STATUS_CHARGEBACKED = 'CHARGEBACKED'

    def __init__(self, merchant_id: str, secret: str, timeout: int = 30, payout_secret: Optional[str] = None):
        if not isinstance(merchant_id, str) or not merchant_id.strip() or any(c in merchant_id for c in '\r\n'):
            raise ValueError('Invalid merchant ID')
        if not isinstance(secret, str) or not secret.strip() or any(c in secret for c in '\r\n'):
            raise ValueError('Invalid API secret')
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('Timeout must be positive')
        self.payout_secret = payout_secret
        self.merchant_id = merchant_id
        self.secret = secret
        self.timeout = timeout

    def create_payment(
        self,
        amount: float,
        currency: str,
        payment_method: Optional[int] = None,
        description: str = None,
        return_url: str = None,
        failed_url: str = None,
        payload: str = None,
        metadata: Optional[Dict[str, str]] = None,
        order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a new payment.

        With payment_method — POST /transaction/process
        Without payment_method (None) — POST /v2/transaction/process
        """
        amount = self._amount(amount)
        currency = self._currency(currency)
        data: Dict[str, Any] = {
            'paymentDetails': {
                'amount': float(amount),
                'currency': str(currency)
            }
        }

        if description:
            data['description'] = str(description)
        if return_url:
            data['return'] = str(return_url)
        if failed_url:
            data['failedUrl'] = str(failed_url)
        if payload:
            data['payload'] = str(payload)
        if order_id is not None:
            data['orderId'] = str(order_id)
        if metadata:
            data['metadata'] = metadata

        if payment_method is None or payment_method == 0 or payment_method == '0':
            return self._request('POST', '/v2/transaction/process', data)

        if isinstance(payment_method, bool) or not re.fullmatch(r'[0-9]+', str(payment_method)) or int(payment_method) <= 0:
            raise ValueError('Invalid payment method')
        data['paymentMethod'] = 11 if int(payment_method) == 10 else int(payment_method)
        return self._request('POST', '/transaction/process', data)

    def get_payment_status(self, transaction_id: str) -> Dict[str, Any]:
        return self._request('GET', f'/transaction/{quote(str(transaction_id), safe="")}')

    def get_transaction_qr(self, transaction_id: str) -> Dict[str, Any]:
        return self._request('GET', f'/transaction/{quote(str(transaction_id), safe="")}/qr')

    def get_rate(
        self,
        payment_method: int,
        currency_from: str,
        currency_to: str
    ) -> Dict[str, Any]:
        params = urlencode({
            'merchantId': self.merchant_id,
            'paymentMethod': payment_method,
            'currencyFrom': currency_from,
            'currencyTo': currency_to
        })
        return self._request('GET', f'/rates/payment_method_rate?{params}')

    def get_balances(self) -> Any:
        return self._request('GET', '/balance/all')

    def is_cancel_supported(self, transaction_id: str) -> Dict[str, Any]:
        return self._request(
            'GET',
            f'/transaction/{quote(str(transaction_id), safe="")}/cancel-supported',
            extra_headers={'Accept': 'text/plain, application/json'}
        )

    def cancel_transaction(self, transaction_id: str) -> Dict[str, Any]:
        return self._request(
            'POST',
            f'/transaction/{quote(str(transaction_id), safe="")}/cancel',
            extra_headers={'Accept': 'text/plain, application/json'}
        )

    def get_conversions(
        self,
        from_dt: str,
        to_dt: str,
        page: Union[int, str] = 1,
        size: Union[int, str] = 20
    ) -> Dict[str, Any]:
        params = urlencode({
            'from': from_dt,
            'to': to_dt,
            'page': str(page),
            'size': str(size)
        })
        return self._request(
            'GET',
            f'/transaction/balance-unlock-operations?{params}',
            extra_headers={'Accept': 'text/plain, application/json'}
        )

    def create_subscription(
        self,
        amount: float,
        currency: str,
        interval: int,
        description: str,
        interval_count: int = 1
    ) -> Dict[str, Any]:
        amount = self._amount(amount)
        limits = {1: 31, 2: 4, 3: 12, 4: 3}
        if not amount.is_integer() or self._currency(currency) != 'RUB' or interval not in limits or isinstance(interval, bool):
            raise ValueError('Invalid subscription amount, currency or interval')
        if isinstance(interval_count, bool) or not isinstance(interval_count, int) or not 1 <= interval_count <= limits[interval] or not description:
            raise ValueError('Invalid interval_count or description')
        data = {
            'paymentMethod': self.METHOD_SUBSCRIPTION,
            'paymentDetails': {
                'amount': int(amount),
                'currency': self._currency(currency),
                'interval': int(interval),
                'intervalCount': interval_count
            },
            'description': str(description)
        }
        return self._request('POST', '/transaction/process', data)

    def get_subscription(self, subscription_id: str) -> Dict[str, Any]:
        return self._request('GET', f'/subscription/{quote(str(subscription_id), safe="")}')

    def list_subscriptions(self, **filters) -> Dict[str, Any]:
        allowed = ('status', 'from', 'to', 'page', 'size')
        query = {k: str(v) for k, v in filters.items() if k in allowed and v is not None}
        endpoint = '/subscription'
        if query:
            endpoint += '?' + urlencode(query)
        return self._request('GET', endpoint)

    def cancel_subscription(self, subscription_id: str) -> Dict[str, Any]:
        return self._request('POST', f'/subscription/{quote(str(subscription_id), safe="")}/cancel')

    def get_cards(self, only_active: Optional[bool] = None) -> Any:
        path = '/api/v1/cards'
        if only_active is not None:
            path += '?onlyActive=' + ('true' if only_active else 'false')
        return self._request_hmac('GET', path)

    def export_transactions_csv(self, filters: Optional[Dict] = None) -> Dict[str, Any]:
        return self._request('POST', '/transaction/export/csv', filters or {})

    def export_transactions_excel(self, filters: Optional[Dict] = None) -> Dict[str, Any]:
        return self._request('POST', '/transaction/export/excel', filters or {})

    def export_transactions_json(self, filters: Optional[Dict] = None) -> Dict[str, Any]:
        return self._request('POST', '/transaction/export/json', filters or {})

    @staticmethod
    def get_payment_methods() -> List[Dict[str, Any]]:
        return [
            {
                'id': Platega.METHOD_SBP_QR,
                'name': 'СБП QR',
                'description': 'СБП (QR-код) + Sberpay (если подключён)'
            },
            {
                'id': Platega.METHOD_ERIP,
                'name': 'ЕРИП',
                'description': 'Оплата через ЕРИП'
            },
            {
                'id': Platega.METHOD_CARD_ACQUIRING,
                'name': 'Карточный эквайринг',
                'description': 'Карточный эквайринг'
            },
            {
                'id': Platega.METHOD_INTERNATIONAL,
                'name': 'Международная оплата',
                'description': 'Международные карточные платежи'
            },
            {
                'id': Platega.METHOD_CRYPTO,
                'name': 'Криптовалюта',
                'description': 'Криптовалютные платежи'
            }
        ]

    @staticmethod
    def is_success_status(status: str) -> bool:
        return status == Platega.STATUS_CONFIRMED

    @staticmethod
    def is_canceled_status(status: str) -> bool:
        return status == Platega.STATUS_CANCELED

    @staticmethod
    def is_chargeback_status(status: str) -> bool:
        return status == Platega.STATUS_CHARGEBACKED

    def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        extra_headers: Optional[Dict[str, str]] = None
    ) -> Any:
        url = f"{self.API_URL}{endpoint}"

        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-MerchantId': self.merchant_id,
            'X-Secret': self.secret,
            'User-Agent': f'Platega-Python-SDK/{self.VERSION}'
        }
        if extra_headers:
            headers.update(extra_headers)

        return self._execute(method, url, headers, data)

    def _request_hmac(
        self,
        method: str,
        path: str,
        data: Optional[Dict] = None,
        idempotency_key: Optional[str] = None
    ) -> Any:
        if not isinstance(self.payout_secret, str) or not self.payout_secret.strip():
            raise ValueError("Configure the separate Payout API secret")
        timestamp = str(int(time.time()))
        body = json.dumps(data, separators=(',', ':'), allow_nan=False) if data is not None else ''
        body_hash = hashlib.sha256(body.encode('utf-8')).hexdigest()
        string_to_sign = f"{method.upper()}\n{path}\n{timestamp}\n{idempotency_key or ''}\n{body_hash}"
        signature = b64encode(
            hmac.new(
                self.payout_secret.encode('utf-8'),
                string_to_sign.encode('utf-8'),
                hashlib.sha256
            ).digest()
        ).decode('ascii')

        url = f"{self.API_URL}{path}"
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Authorization': f'PG-HMAC kid={self.merchant_id}, ts={timestamp}, sig={signature}',
            'User-Agent': f'Platega-Python-SDK/{self.VERSION}'
        }
        if idempotency_key is not None:
            headers['Idempotency-Key'] = idempotency_key
        return self._execute(method, url, headers, body.encode('utf-8') if data is not None else None)

    def _execute(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        data: Optional[Dict] = None
    ) -> Any:
        body = None
        if data is not None:
            body = data if isinstance(data, bytes) else json.dumps(data, separators=(',', ':'), allow_nan=False).encode('utf-8')
        elif method == 'POST':
            body = b''

        request = Request(url, data=body, headers=headers, method=method)

        try:
            with build_opener(_NoRedirect()).open(request, timeout=self.timeout) as response:
                raw = response.read().decode('utf-8')
                result = {} if response.status == 204 and not raw else json.loads(raw)
                if isinstance(result, dict) and 'url' in result and 'redirect' not in result:
                    result['redirect'] = result['url']
                return result

        except HTTPError as e:
            try:
                error_data = json.loads(e.read().decode('utf-8'))
                message = error_data.get('message', str(e))
            except Exception:
                error_data = {}
                message = str(e)

            raise PlategaAPIError(message, e.code, error_data)

        except URLError as e:
            raise PlategaAPIError(f"Connection error: {e.reason}")

        except json.JSONDecodeError as e:
            raise PlategaAPIError(f"Invalid JSON response: {e}")

    @staticmethod
    def _amount(amount):
        if isinstance(amount, bool):
            raise ValueError('Amount must be a finite positive number')
        value = float(amount)
        if not math.isfinite(value) or value <= 0:
            raise ValueError('Amount must be a finite positive number')
        return value

    @staticmethod
    def _currency(currency):
        if not isinstance(currency, str) or not re.fullmatch(r'[A-Za-z]{3,8}', currency):
            raise ValueError('Invalid currency code')
        return currency.upper()

    def create_card_payout(self, params, idempotency_key):
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or any(c in idempotency_key for c in '\r\n'):
            raise ValueError('A stable Idempotency-Key is required')
        if not isinstance(params, dict) or bool(params.get('cardId')) == bool(params.get('cardNumber')):
            raise ValueError('Supply exactly one of cardId or cardNumber')
        params = dict(params)
        params['amountRub'] = self._amount(params.get('amountRub'))
        if not 1000 <= params['amountRub'] <= 87500 or not params['amountRub'].is_integer():
            raise ValueError('Payout amount must be an integer within 1000..87500 RUB')
        params['amountRub'] = int(params['amountRub'])
        params.setdefault('payoutMethod', 'CARD')
        params.setdefault('currencyRequested', 'RUB')
        if params['payoutMethod'] != 'CARD' or params['currencyRequested'] != 'RUB':
            raise ValueError('Card payouts require CARD and RUB')
        return self._request_hmac('POST', '/api/v1/payouts/card-rub', params, idempotency_key)
