import pytest
import httpx
from gift.integrations.x_client import XClient
from gift.integrations.types import PlanSnapshot

AUTH = dict(auth_token='abc',ct0='def',authorization='Bearer abc',user_agent='agent')
PLAN = PlanSnapshot(1,'gift',3,30000,'USD','acct_Test','prod_Test')

def test_eligibility_rejects_ineligible():
    direct = httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'data':{'user':{'result':{'rest_id':'123','premium_gifting_eligible':False,'core':{'screen_name':'alice'}}}}})))
    with pytest.raises(ValueError):
        XClient(AUTH,direct=direct).eligibility('alice')

def test_quote_wrong_price():
    regional=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'data':{'web_subscription_product_details_by_rest_id':{'rest_id':'prod_Test','prices':[{'amount_local_micro':1,'currency_code':'USD','price_type':'OneTime'}]}}})))
    with pytest.raises(ValueError):
        XClient(AUTH,regional=regional).quote('123',PLAN)

def test_x_routing_quote_and_mutation_share_regional_client():
    from gift.integrations.types import OrderSnapshot
    from uuid import uuid4
    calls=[]
    def account(r):
        calls.append(('direct',r.method,r.url.path))
        return httpx.Response(200,json={'data':{'user':{'result':{'rest_id':'123','premium_gifting_eligible':True,'core':{'screen_name':'alice'}}}}})
    def region(r):
        calls.append(('regional',r.method,r.url.path))
        if r.method=='GET':
            return httpx.Response(200,json={'data':{'web_subscription_product_details_by_rest_id':{'rest_id':'prod_Test','prices':[{'amount_local_micro':300000000,'currency_code':'USD','price_type':'OneTime'}]}}})
        return httpx.Response(200,json={'data':{'onetimepurchase_gift':{'session_id':'cs_live_Test','session_url':'https://checkout.stripe.com/c/pay/cs_live_Test','session_status':'Unpaid'}}})
    x=XClient(AUTH,direct=httpx.Client(transport=httpx.MockTransport(account)),regional=httpx.Client(transport=httpx.MockTransport(region)))
    x.create_checkout(OrderSnapshot(uuid4(),'alice','123',PLAN))
    assert [route for route,method,path in calls]==['direct','regional','regional']
    assert calls[-1][1]=='POST'


def test_x_redirect_rejected_without_following():
    direct=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(302,headers={'location':'https://example.com'})))
    with pytest.raises(ValueError):XClient(AUTH,direct=direct).eligibility('alice')


def test_x_mutation_timeout_not_replayed():
    from gift.integrations.types import OrderSnapshot
    from uuid import uuid4
    x=XClient(AUTH)
    x.eligibility=lambda username:'123'
    x.quote=lambda recipient,plan:None
    calls=[]
    def transport(r):
        calls.append(r)
        raise httpx.ReadTimeout('synthetic')
    x.regional.close()
    x.regional=httpx.Client(transport=httpx.MockTransport(transport))
    with pytest.raises(httpx.ReadTimeout):x.create_checkout(OrderSnapshot(uuid4(),'alice','123',PLAN))
    assert len(calls)==1
    x.close()

def test_live_catalog_rejected_before_network():
    from dataclasses import replace
    def transport(r):raise RuntimeError('network operation forbidden by fixture')
    x=XClient(AUTH,regional=httpx.Client(transport=httpx.MockTransport(transport)))
    try:
        with pytest.raises(ValueError):x.quote('123',replace(PLAN,merchant='demo'))
    finally:x.close()
