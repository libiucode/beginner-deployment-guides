import copy
import pytest
import httpx
from gift.integrations.stripe_client import StripeClient, guard_page
from gift.integrations.types import Checkout, Quote

CHECKOUT=Checkout('cs_live_Test','https://checkout.stripe.com/c/pay/cs_live_Test',Quote('acct_Test','prod_Test',3,30000,'USD'))

def page():
    return {'session_id':'cs_live_Test','currency':'usd','mode':'payment','livemode':True,'status':'open','payment_status':'unpaid','init_checksum':'test','success_url':'https://x.com/alice/gift-premium/success','cancel_url':'https://x.com/alice/gift-premium','account_settings':{'account_id':'acct_Test'},'total_summary':{'due':30000,'subtotal':30000,'total':30000},'line_item_group':{'currency':'usd','due':30000,'subtotal':30000,'total':30000,'line_items':[{'name':'Premium Gift - 3 months','quantity':1,'subtotal':30000,'total':30000,'price':{'currency':'usd','type':'one_time','unit_amount':30000,'product':{'id':'prod_Test','name':'Premium Gift - 3 months','livemode':True}}}]},'payment_intent':None}

@pytest.mark.parametrize('field,value',[('session_id','cs_live_Other'),('currency','eur'),('success_url','https://x.com/bob/gift-premium/success'),('setup_future_usage','off_session'),('payment_intent',{}),('livemode',False)])
def test_guard_rejects(field,value):
    p=page(); p[field]=value
    with pytest.raises(ValueError): guard_page(p,CHECKOUT,'alice',before=True)

@pytest.mark.parametrize('change',[lambda p:p['account_settings'].update(account_id='acct_Other'),lambda p:p['total_summary'].update(total=1),lambda p:p['line_item_group']['line_items'][0]['price']['product'].update(id='prod_Other')])
def test_guard_nested(change):
    p=page(); change(p)
    with pytest.raises(ValueError): guard_page(p,CHECKOUT,'alice',before=True)

def test_confirm_timeout_never_replays():
    calls=[]
    def transport(r):
        calls.append(r.url.path)
        if r.url.path.endswith('/confirm'):raise httpx.ReadTimeout('synthetic')
        return httpx.Response(200,json=page())
    s=StripeClient('pk_live_abcdefgh',http=httpx.Client(transport=httpx.MockTransport(transport)))
    result=s.confirm(CHECKOUT,'alice','pm_Test','key')
    assert result.status=='unknown' and result.no_retry
    assert sum(p.endswith('/confirm') for p in calls)==1

@pytest.mark.parametrize('change',[lambda p:p.pop('payment_intent'),lambda p:p['line_item_group'].update(due=1),lambda p:p.update(status='complete'),lambda p:p['line_item_group']['line_items'][0]['price'].update(recurring={}),lambda p:p['line_item_group']['line_items'][0]['price']['product'].update(name='Premium Gift - 6 months')])
def test_additional_pre_submit_guards(change):
    p=page();change(p)
    with pytest.raises(ValueError):guard_page(p,CHECKOUT,'alice',before=True)


def test_paid_requires_received_funds():
    p=page();p.update(payment_status='paid',payment_intent={'currency':'usd','amount':30000,'status':'succeeded','amount_received':0})
    with pytest.raises(ValueError):guard_page(p,CHECKOUT,'alice')


def test_guard_prevents_confirm_network_write():
    calls=[]
    p=page();p['account_settings']['account_id']='acct_Evil'
    s=StripeClient('pk_live_abcdefgh',http=httpx.Client(transport=httpx.MockTransport(lambda r:(calls.append(r.url.path) or httpx.Response(200,json=p)))))
    with pytest.raises(ValueError):s.confirm(CHECKOUT,'alice','pm_Test','key')
    assert not any(path.endswith('/confirm') for path in calls)


def test_non_card_402_is_unknown():
    def transport(r):
        return httpx.Response(402,json={'error':{'type':'api_error'}}) if r.url.path.endswith('/confirm') else httpx.Response(200,json=page())
    s=StripeClient('pk_live_abcdefgh',http=httpx.Client(transport=httpx.MockTransport(transport)))
    assert s.confirm(CHECKOUT,'alice','pm_Test','key').status=='unknown'

def test_safe_read_route_failover_cools_node():
    from gift.integrations.routes import RoutePool
    calls=[]
    pool=RoutePool([{'name':'a','url':None},{'name':'b','url':None}])
    def factory(endpoint):
        name=pool.choose()['name']
        def transport(r):
            calls.append((name,r.url.path))
            if name=='a':raise httpx.ConnectTimeout('synthetic')
            return httpx.Response(200,json=page())
        return httpx.Client(transport=httpx.MockTransport(transport))
    s=StripeClient('pk_live_abcdefgh',pool=pool,client_factory=factory)
    assert s.inspect(CHECKOUT,'alice').status=='unpaid'
    assert [name for name,path in calls]==['a','b']
    assert pool.choose()['name']=='b'
    s.close()


def test_write_timeout_does_not_rotate():
    from gift.integrations.routes import RoutePool
    calls=[]
    pool=RoutePool([{'name':'a','url':None},{'name':'b','url':None}])
    def factory(endpoint):
        def transport(r):
            calls.append(r.url.path)
            if r.url.path.endswith('/confirm'):raise httpx.ReadTimeout('synthetic')
            return httpx.Response(200,json=page())
        return httpx.Client(transport=httpx.MockTransport(transport))
    s=StripeClient('pk_live_abcdefgh',pool=pool,client_factory=factory)
    assert s.confirm(CHECKOUT,'alice','pm_Test','key').status=='unknown'
    assert pool.choose()['name']=='b'
    assert sum(path.endswith('/confirm') for path in calls)==1
    s.close()

def test_tokenization_uses_fresh_cvc_and_stable_key():
    from urllib.parse import parse_qs
    calls=[]
    def transport(r):
        calls.append(r)
        if r.url.path.endswith('/payment_methods'):
            return httpx.Response(200,json={'id':'pm_Test','type':'card','livemode':True})
        return httpx.Response(200,json=page())
    s=StripeClient('pk_live_abcdefgh',http=httpx.Client(transport=httpx.MockTransport(transport)))
    card=dict(number='4242424242424242',exp_month='12',exp_year='2030',cvc='123',name='Test',email='a@example.invalid')
    assert s.tokenize(CHECKOUT,'alice',card,'stable')=='pm_Test'
    assert parse_qs(calls[-1].content.decode())['card[cvc]']==['123']
    assert calls[-1].headers['Idempotency-Key']=='stable-method'


def test_tokenization_timeout_not_replayed():
    calls=[]
    def transport(r):
        calls.append(r.url.path)
        if r.url.path.endswith('/payment_methods'):raise httpx.ReadTimeout('synthetic')
        return httpx.Response(200,json=page())
    s=StripeClient('pk_live_abcdefgh',http=httpx.Client(transport=httpx.MockTransport(transport)))
    card=dict(number='4242424242424242',exp_month='12',exp_year='2030',cvc='123',name='Test',email='a@example.invalid')
    with pytest.raises(httpx.ReadTimeout):s.tokenize(CHECKOUT,'alice',card,'stable')
    assert sum(path.endswith('/payment_methods') for path in calls)==1


def test_card_decline_blocks_retry():
    def transport(r):
        return httpx.Response(402,json={'error':{'type':'card_error','code':'card_declined','advice_code':'do_not_try_again'}}) if r.url.path.endswith('/confirm') else httpx.Response(200,json=page())
    s=StripeClient('pk_live_abcdefgh',http=httpx.Client(transport=httpx.MockTransport(transport)))
    result=s.confirm(CHECKOUT,'alice','pm_Test','key')
    assert result.status=='failed' and result.no_retry
