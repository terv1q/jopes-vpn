"""
Platega Python SDK

Universal Python library for Platega.io payment system integration.

Example usage:
    from platega import Platega, PlategaCallback

    # Create payment
    client = Platega('merchant_id', 'secret')
    result = client.create_payment(
        amount=1000,
        currency='RUB',
        payment_method=Platega.METHOD_SBP_QR,
        description='Order #123',
        return_url='https://example.com/success',
        failed_url='https://example.com/fail',
        payload='123'
    )
    print(result['redirect'])

    # Handle callback (in Flask/Django view)
    callback = PlategaCallback('merchant_id', 'secret')
    if callback.validate(request):
        if callback.is_success():
            # Payment successful
            order_id = callback.get_order_id()
"""

from .platega import Platega
from .callback import PlategaCallback
from .exceptions import PlategaException, PlategaAPIError, PlategaValidationError

__version__ = '1.2.0'
__all__ = [
    'Platega',
    'PlategaCallback', 
    'PlategaException',
    'PlategaAPIError',
    'PlategaValidationError'
]
