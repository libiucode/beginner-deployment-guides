"""Configuration factory and transient, order-scoped real provider."""
import json
from django.conf import settings
from gift.forms_credentials import validate_credential
from gift.models import Secret, Order
from gift.services.vault import unseal
from .routes import RoutePool
from .x_client import XClient
from .stripe_client import StripeClient
from .simulated import SimulatedProvider
from .types import PaymentResult


def configured_mode():
    mode=settings.XGIFT_MODE
    if mode not in ('simulated','live'):
        raise ValueError('XGIFT_MODE must be simulated or live')
    return mode


def load_config():
    config={}
    for name in ('x-auth','stripe-key','proxy'):
        secret=Secret.objects.filter(name=name).first()
        if secret is None:
            if name=='proxy':
                config[name]={}; continue
            raise ValueError('live credentials missing')
        raw=unseal(bytes(secret.ciphertext),'secret:'+name).decode()
        checked=validate_credential(name,raw)
        config[name]=checked if name=='stripe-key' else json.loads(checked)
    return config


class LiveProvider:
    mode='live'
    payment_method=None

    def __init__(self, config, *, card=None, order_id=None, pool=None):
        if card is not None and order_id is None:
            raise ValueError('transient card must authorize exactly one order')
        self.order_id=order_id
        self.card=card
        self.payment_method='transient' if card is not None else None
        self.pool=pool or RoutePool(config['proxy'].get('payment_outbounds',[]))
        self.x=XClient(config['x-auth'],region=config['proxy'].get('x_region'))
        try:
            self.stripe=StripeClient(config['stripe-key'],pool=self.pool)
        except Exception:
            self.x.close()
            raise
        self.used=False

    def close(self):
        if self.card is not None:
            self.card.clear()
        self.x.close(); self.stripe.close()

    def eligibility(self, username):
        return self.x.eligibility(username)

    def quote(self, recipient, plan):
        return self.x.quote(recipient,plan)

    def create_checkout(self, order):
        return self.x.create_checkout(order)

    def _order(self, checkout):
        order=Order.objects.get(remote_session_id=checkout.session_id,mode='live')
        if self.order_id is not None and str(order.pk)!=str(self.order_id):
            raise ValueError('transient authorization does not match order')
        return order

    def inspect(self, checkout):
        return self.stripe.inspect(checkout,self._order(checkout).username)

    def confirm(self, checkout, payment_method, key):
        order=self._order(checkout)
        if self.used or self.card is None or self.order_id is None or payment_method!='transient':
            raise ValueError('fresh order-specific transient authorization required')
        self.used=True
        try:
            method=self.stripe.tokenize(checkout,order.username,self.card,key)
            return self.stripe.confirm(checkout,order.username,method,key)
        except Exception:
            self.pool.failed(self.stripe.route['name'])
            return PaymentResult('unknown',True)
        finally:
            self.card.clear()
            self.payment_method=None


_pools = {}

def provider_for_mode(**kwargs):
    if configured_mode()=='simulated':
        if kwargs.get('card') is not None:
            raise ValueError('real card forbidden in simulation')
        return SimulatedProvider()
    config=load_config()
    signature=json.dumps(config['proxy'].get('payment_outbounds',[]),sort_keys=True)
    pool=_pools.setdefault(signature,RoutePool(config['proxy'].get('payment_outbounds',[])))
    return LiveProvider(config,pool=pool,**kwargs)
