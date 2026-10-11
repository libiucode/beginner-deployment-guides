import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from gift.integrations.live import provider_for_mode
from gift.integrations.simulated import SimulatedProvider
from gift.services.queue import process_next, reconcile
from gift.models import Order,Code,Plan
from gift.services.codes import digest

@pytest.fixture
def order(db,request):
    plan=Plan.objects.create(name='Gift',months=3,amount_minor=30000,currency='USD',merchant='acct_Test',product_id='prod_Test')
    code=Code.objects.create(plan=plan,digest='a'*64,ciphertext=b'test')
    return Order.objects.create(code=code,username='alice',token_digest=digest('token'),mode=getattr(request,'param','simulated'),plan_snapshot={'plan_id':plan.pk,'name':plan.name,'months':3,'amount_minor':30000,'currency':'USD','merchant':plan.merchant,'product_id':plan.product_id})


def test_live_factory_missing_config_fails_closed(db,settings):
    settings.XGIFT_MODE='live'
    with pytest.raises(ValueError):provider_for_mode()
    with pytest.raises(CommandError):call_command('run_worker',once=True)


def test_unknown_mode_fails_closed(settings):
    settings.XGIFT_MODE='typo'
    with pytest.raises(ValueError):provider_for_mode()


def test_public_simulation_success_stays_simulation_after_mode_switch(order,settings,client):
    Order.objects.filter(pk=order.pk).update(status='succeeded',remote_session_id='simulated:'+str(order.pk))
    settings.XGIFT_MODE='live'
    data=client.get(f'/api/orders/{order.pk}',HTTP_AUTHORIZATION='Bearer token').json()
    assert data['mode']=='simulated' and data['status']=='succeeded'


@pytest.mark.parametrize('order',['live'],indirect=True)
def test_worker_never_mixes_modes(order):
    provider=SimulatedProvider()
    assert process_next(provider) is False
    assert provider.confirm_calls==0
    with pytest.raises(ValueError):reconcile(order.pk,provider)


def test_live_worker_skips_simulation_order(order):
    provider=SimulatedProvider();provider.mode='live'
    assert process_next(provider) is False


def test_session_prefix_mismatch_cannot_fake_success(order):
    Order.objects.filter(pk=order.pk).update(remote_session_id='cs_live_Test',status='link_created')
    provider=SimulatedProvider()
    assert process_next(provider)
    order.refresh_from_db()
    assert order.status=='review' and provider.confirm_calls==0


def test_order_mode_immutable(order):
    order.mode='live'
    with pytest.raises(ValueError):order.save()


def test_manual_provider_cannot_be_used_as_routine_worker():
    provider=SimulatedProvider();provider.order_id='specific'
    with pytest.raises(ValueError):process_next(provider)

def test_load_secret_uses_admin_purpose(db,tmp_path,settings):
    import json
    from gift.models import Secret
    from gift.services.vault import seal
    from gift.integrations.live import load_config
    settings.XGIFT_KEY_FILE=tmp_path/'key';settings.XGIFT_KEY_FILE.write_bytes(b'a'*32)
    auth=dict(auth_token='abc',ct0='def',authorization='Bearer abc',user_agent='agent')
    for name,value in [('x-auth',json.dumps(auth)),('stripe-key','pk_live_abcdefgh')]:
        Secret.objects.create(name=name,ciphertext=seal(value.encode(),'secret:'+name))
    config=load_config()
    assert config['x-auth']==auth and config['proxy']=={}


@pytest.mark.parametrize('order',['live'],indirect=True)
@pytest.mark.parametrize('outcome',['success','confirm_timeout','token_timeout'])
def test_transient_payment_targets_one_order_and_clears_card(order,outcome):
    import httpx
    from gift.integrations.live import LiveProvider
    from gift.integrations.stripe_client import StripeClient
    from gift.integrations.routes import RoutePool
    from gift.integrations.types import Quote
    from gift.models import PaymentAttempt,AuditEvent
    from test_stripe_client import page
    calls=[]
    def transport(r):
        calls.append(r)
        if r.url.path.endswith('/payment_methods'):
            if outcome=='token_timeout':raise httpx.ReadTimeout('synthetic')
            return httpx.Response(200,json={'id':'pm_Test','type':'card','livemode':True})
        p=page()
        if r.url.path.endswith('/confirm'):
            if outcome=='confirm_timeout':raise httpx.ReadTimeout('synthetic')
            p.update(payment_status='paid',status='complete',payment_intent={'currency':'usd','amount':30000,'amount_received':30000,'status':'succeeded'})
        return httpx.Response(200,json=p)
    Order.objects.filter(pk=order.pk).update(status='awaiting_payment',recipient_id='123',remote_session_id='cs_live_Test',payment_url='https://checkout.stripe.com/c/pay/cs_live_Test')
    provider=LiveProvider.__new__(LiveProvider)
    provider.order_id=order.pk;provider.card=dict(number='4242424242424242',exp_month='12',exp_year='2030',cvc='123',name='Test',email='a@example.invalid')
    provider.used=False;provider.payment_method='transient';provider.pool=RoutePool()
    provider.stripe=StripeClient('pk_live_abcdefgh',http=httpx.Client(transport=httpx.MockTransport(transport)))
    class X:
        def quote(self,*args):return Quote('acct_Test','prod_Test',3,30000,'USD')
    provider.x=X()
    with pytest.raises(ValueError):process_next(provider)
    assert not calls
    assert process_next(provider,order_id=order.pk)
    order.refresh_from_db()
    assert order.status==('succeeded' if outcome=='success' else 'review') and provider.card=={} and provider.payment_method is None
    attempt=PaymentAttempt.objects.get(order=order)
    assert attempt.no_retry and attempt.simulated_result==''
    assert calls[-1].headers['Idempotency-Key']==str(attempt.key)+('-method' if outcome=='token_timeout' else '')
    assert '4242424242424242' not in str(list(AuditEvent.objects.values('details')))
    assert not process_next(provider,order_id=order.pk)
    assert len([r for r in calls if r.url.path.endswith('/confirm')])==(0 if outcome=='token_timeout' else 1)


def test_database_forbids_mode_change(order):
    from django.db import IntegrityError,transaction
    with pytest.raises(IntegrityError),transaction.atomic():Order.objects.filter(pk=order.pk).update(mode='live')
