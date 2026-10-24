import sys
import pathlib
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import hashlib
import hmac
import base64
import json
import unittest
from unittest.mock import patch
from platega import Platega, PlategaCallback
from platega.platega import _NoRedirect

class Capture(Platega):
    def _execute(self,method,url,headers,data=None):
        self.last=(method,url,headers,data)
        return {'transactionId':'test-id','url':'https://pay.platega.io/test'}

class SDKTests(unittest.TestCase):
    def test_contract(self):
        c=Capture('merchant','secret',payout_secret='payout-secret')
        for method in (None,0,'0'):
            c.create_payment(100.25,'rub',method,order_id='123')
            self.assertTrue(c.last[1].endswith('/v2/transaction/process'))
            self.assertNotIn('paymentMethod',c.last[3])
            self.assertEqual(c.last[3]['orderId'],'123')
        c.create_payment(100,'USD',10)
        self.assertEqual(c.last[3]['paymentMethod'],11)
        for amount in (0,-1,float('nan'),float('inf'),True,'100junk'):
            with self.assertRaises((ValueError,TypeError)): c.create_payment(amount,'RUB')
        c.create_subscription(100,'RUB',3,'Subscription',12)
        self.assertEqual(c.last[3]['paymentDetails']['intervalCount'],12)
        with self.assertRaises(ValueError): c.create_subscription(100.1,'RUB',3,'Subscription')
        with self.assertRaises(ValueError): c.create_subscription(100,'RUB',3,'Subscription',13)
        c.get_payment_status('a/b?x=y')
        self.assertTrue(c.last[1].endswith('/transaction/a%2Fb%3Fx%3Dy'))
        c.create_card_payout({'cardId':'card','amountRub':1500},'stable-key')
        auth=c.last[2]['Authorization'];ts=auth.split('ts=')[1].split(',')[0]
        wire=c.last[3]
        signed='POST\n/api/v1/payouts/card-rub\n'+ts+'\nstable-key\n'+hashlib.sha256(wire).hexdigest()
        expected=base64.b64encode(hmac.new(b'payout-secret',signed.encode(),hashlib.sha256).digest()).decode()
        self.assertTrue(auth.endswith('sig='+expected))
        self.assertEqual(c.last[2]['Idempotency-Key'],'stable-key')
        self.assertIsNone(_NoRedirect().redirect_request(None,None,302,'',{},'https://external.example'))

    def test_callbacks(self):
        c=PlategaCallback('merchant','secret')
        headers={'X-MerchantId':'merchant','X-Secret':'secret'}
        body=dict(id='test',amount=100,currency='RUB',status='CONFIRMED',paymentMethod=2,payload='123')
        self.assertTrue(c.validate_raw(headers,json.dumps(body)))
        self.assertTrue(c.is_success())
        self.assertFalse(c.validate_raw(dict(headers,**{'X-Secret':'wrong'}),json.dumps(body)))
        self.assertFalse(c.is_success());self.assertIsNone(c.get_payload())
        for invalid in ('null','[]','true','"scalar"','x'*65537): self.assertFalse(c.validate_raw(headers,invalid))
        for amount in (0,-1,'NaN',True,[10]):
            self.assertFalse(c.validate_raw(headers,json.dumps(dict(body,amount=amount))))
        pascal=dict(Id='sub',Amount=100,Currency='RUB',Status='SUBSCRIPTION_ACTIVATED',PaymentMethod=6,SubscriptionId='sub')
        self.assertTrue(c.validate_raw(headers,json.dumps(pascal)))
        self.assertEqual(c.get_subscription_id(),'sub');self.assertFalse(c.is_success())
        class Unauthorized:
            method='POST'
            headers={'X-MerchantId':'merchant','X-Secret':'wrong'}
            def get_data(self,**kw): raise AssertionError('Unauthenticated body read')
        self.assertFalse(c.validate(Unauthorized()))
        self.assertFalse(c.is_valid());self.assertIsNone(c.get_payload())

if __name__=='__main__': unittest.main(verbosity=2)
