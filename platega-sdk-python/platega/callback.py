"""
Platega Callback Handler
"""

import json
import hmac
import math
import re
from typing import Dict, Any, Optional

from .exceptions import PlategaValidationError


class PlategaCallback:
    """
    Handler for incoming Platega payment callbacks
    
    Example (Flask):
        @app.route('/callback', methods=['POST'])
        def callback():
            callback = PlategaCallback('merchant_id', 'secret')
            if callback.validate(request):
                if callback.is_success():
                    order_id = callback.get_order_id()
                    # Process payment
                return 'OK', 200
            return 'Invalid', 401
    
    Example (Django):
        def callback_view(request):
            callback = PlategaCallback('merchant_id', 'secret')
            if callback.validate_django(request):
                if callback.is_success():
                    order_id = callback.get_order_id()
                    # Process payment
                return HttpResponse('OK')
            return HttpResponse('Invalid', status=401)
    """
    
    # Status constants
    STATUS_CONFIRMED = 'CONFIRMED'
    STATUS_CANCELED = 'CANCELED'
    STATUS_CHARGEBACKED = 'CHARGEBACKED'
    
    def __init__(self, merchant_id: str, secret: str):
        """
        Initialize callback handler
        
        Args:
            merchant_id: Your Merchant ID for validation
            secret: Your API Secret for validation
        """
        if not isinstance(merchant_id, str) or not merchant_id.strip() or not isinstance(secret, str) or not secret.strip():
            raise ValueError('Callback credentials must not be empty')
        self.merchant_id = merchant_id
        self.secret = secret
        self.payload: Optional[Dict[str, Any]] = None
        self.validation_error: Optional[str] = None
        self._validated = False
        self._is_valid = False
    
    def validate(self, request) -> bool:
        """
        Validate callback from Flask request object
        
        Args:
            request: Flask request object
        
        Returns:
            bool: True if callback is valid
        """
        self._reset()
        try:
            # Check method
            if request.method != 'POST':
                self.validation_error = 'Invalid request method'
                return False
            
            # Get headers
            received_merchant_id = request.headers.get('X-MerchantId', '')
            received_secret = request.headers.get('X-Secret', '')

            if not self._credentials_match(received_merchant_id, received_secret):
                return False
            if hasattr(request, 'stream'):
                body = request.stream.read(65537)
            else:
                body = request.get_data(as_text=True)
            
            return self._validate_common(
                received_merchant_id,
                received_secret,
                body
            )
            
        except Exception as e:
            self.validation_error = str(e)
            return False
    
    def validate_django(self, request) -> bool:
        """
        Validate callback from Django request object
        
        Args:
            request: Django HttpRequest object
        
        Returns:
            bool: True if callback is valid
        """
        self._reset()
        try:
            # Check method
            if request.method != 'POST':
                self.validation_error = 'Invalid request method'
                return False
            
            # Get headers (Django uses META with HTTP_ prefix)
            received_merchant_id = request.META.get('HTTP_X_MERCHANTID', '')
            received_secret = request.META.get('HTTP_X_SECRET', '')

            if not self._credentials_match(received_merchant_id, received_secret):
                return False
            
            return self._validate_common(
                received_merchant_id,
                received_secret,
                request.read(65537) if hasattr(request, 'read') else request.body
            )
            
        except Exception as e:
            self.validation_error = str(e)
            return False
    
    def validate_raw(
        self,
        headers: Dict[str, str],
        body: str
    ) -> bool:
        """
        Validate callback from raw headers and body
        
        Args:
            headers: Request headers dict
            body: Raw request body string
        
        Returns:
            bool: True if callback is valid
        """
        self._reset()
        if not isinstance(headers, dict):
            self.validation_error = 'Invalid headers'
            return False
        # Normalize header names (case-insensitive)
        normalized_headers = {k.lower(): v for k, v in headers.items()}
        
        received_merchant_id = (
            normalized_headers.get('x-merchantid', '') or
            normalized_headers.get('x-merchant-id', '')
        )
        received_secret = normalized_headers.get('x-secret', '')
        
        return self._validate_common(received_merchant_id, received_secret, body)
    
    def _validate_common(
        self,
        received_merchant_id: str,
        received_secret: str,
        body: str
    ) -> bool:
        """Common validation logic"""
        self._reset()
        self._validated = True
        if not isinstance(body, (str, bytes)) or len(body) > 65536:
            self.validation_error = 'Callback body is missing or too large'
            return False
        if not isinstance(received_merchant_id, str) or not hmac.compare_digest(received_merchant_id.encode(), self.merchant_id.encode()):
            self.validation_error = 'Invalid or missing X-MerchantId header'
            return False
        if not isinstance(received_secret, str) or not hmac.compare_digest(received_secret.encode(), self.secret.encode()):
            self.validation_error = 'Invalid or missing X-Secret header'
            return False
        try:
            pairs = json.loads(body, object_pairs_hook=lambda pairs: pairs)
            if not isinstance(pairs, list) or not isinstance(body, (str, bytes)) or not body.lstrip().startswith('{' if isinstance(body, str) else b'{'):
                raise ValueError('Callback must be an object')
            payload = {}
            for key, value in pairs:
                key = key[:1].lower() + key[1:]
                if key in payload:
                    raise ValueError('Conflicting callback fields')
                payload[key] = value
            for field in ('id', 'amount', 'currency', 'status', 'paymentMethod'):
                if field not in payload:
                    raise ValueError('Missing field: ' + field)
            if not isinstance(payload['id'], str) or not payload['id'].strip() or not isinstance(payload['status'], str):
                raise ValueError('Invalid callback ID or status')
            if not isinstance(payload['currency'], str) or not re.fullmatch(r'[A-Za-z]{3,8}',payload['currency']):
                raise ValueError('Invalid callback currency')
            amount = payload['amount']
            method = payload['paymentMethod']
            if isinstance(amount,bool) or not isinstance(amount,(int,float,str)) or not math.isfinite(float(amount)) or float(amount) < 0:
                raise ValueError('Invalid callback amount')
            if isinstance(method,bool) or not re.fullmatch(r'[0-9]+',str(method)) or int(method) <= 0:
                raise ValueError('Invalid callback method')
            if payload['status'] == 'CONFIRMED' and float(amount) <= 0:
                raise ValueError('Confirmed amount must be positive')
            if 'payload' in payload and payload['payload'] is not None and not isinstance(payload['payload'],str):
                raise ValueError('Invalid custom payload')
        except (ValueError,TypeError,OverflowError):
            self.validation_error = 'Invalid callback JSON, fields or types'
            return False
        self.payload = payload
        self._is_valid = True
        return True

    def _reset(self):
        self._validated = False
        self._is_valid = False
        self.payload = None
        self.validation_error = None

    def _credentials_match(self, merchant, secret):
        if not isinstance(merchant, str) or not isinstance(secret, str) or not hmac.compare_digest(merchant.encode(), self.merchant_id.encode()) or not hmac.compare_digest(secret.encode(), self.secret.encode()):
            self.validation_error = 'Invalid callback credentials'
            return False
        return True

    def is_valid(self) -> bool:
        """Check if callback was validated and is valid"""
        return self._is_valid
    
    def get_validation_error(self) -> Optional[str]:
        """Get validation error message"""
        return self.validation_error
    
    def get_payload(self) -> Optional[Dict[str, Any]]:
        """Get parsed callback payload"""
        return self.payload
    
    def get_transaction_id(self) -> Optional[str]:
        """Get transaction ID from callback"""
        if self.payload:
            return self.payload.get('id')
        return None
    
    def get_status(self) -> Optional[str]:
        """Get payment status from callback"""
        if self.payload:
            return self.payload.get('status')
        return None
    
    def get_amount(self) -> Optional[float]:
        """Get payment amount from callback"""
        if self.payload:
            return float(self.payload.get('amount', 0))
        return None
    
    def get_currency(self) -> Optional[str]:
        """Get payment currency from callback"""
        if self.payload:
            return self.payload.get('currency')
        return None
    
    def get_payment_method(self) -> Optional[int]:
        """Get payment method ID from callback"""
        if self.payload:
            return int(self.payload.get('paymentMethod', 0))
        return None
    
    def get_custom_payload(self) -> Optional[str]:
        """Get custom payload data (order ID, etc.)"""
        if self.payload:
            return self.payload.get('payload')
        return None
    
    def get_order_id(self) -> Optional[str]:
        """Alias for get_custom_payload() - get order ID from payload"""
        return self.get_custom_payload()
    
    def is_success(self) -> bool:
        """Check if payment was successful (CONFIRMED)"""
        return self.is_valid() and self.get_status() == self.STATUS_CONFIRMED
    
    def is_canceled(self) -> bool:
        """Check if payment was canceled"""
        return self.is_valid() and self.get_status() == self.STATUS_CANCELED
    
    def is_chargeback(self) -> bool:
        """Check if payment has chargeback"""
        return self.is_valid() and self.get_status() == self.STATUS_CHARGEBACKED

    def get_subscription_id(self) -> Optional[str]:
        """Get subscription ID from recurring callbacks"""
        if not self.payload:
            return None
        return self.payload.get('SubscriptionId') or self.payload.get('subscriptionId')

    def get_next_charge_at(self) -> Optional[str]:
        """Get next charge datetime from subscription charge callback"""
        if not self.payload:
            return None
        return self.payload.get('NextChargeAt') or self.payload.get('nextChargeAt')
