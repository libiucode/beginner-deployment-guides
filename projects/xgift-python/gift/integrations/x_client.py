"""X operation snapshots derived from mizorewww/xgift (MIT; see LICENSE.upstream)."""
import json
import re
from urllib.parse import urlsplit
from .routes import client, json_response
from .types import Checkout, Quote
from gift.services.catalog import validate_live_plan


class XClient:
    def __init__(self, auth, *, region=None, direct=None, regional=None):
        for name in ('auth_token', 'ct0'):
            value = auth.get(name, '')
            if not value or any(c in value for c in '\r\n;'):
                raise ValueError('invalid X cookie')
        if not auth.get('authorization', '').startswith('Bearer ') or not auth.get('user_agent'):
            raise ValueError('invalid X authentication metadata')
        self.headers = {'Authorization':auth['authorization'], 'User-Agent':auth['user_agent'],
                        'Cookie':f"auth_token={auth['auth_token']}; ct0={auth['ct0']}",
                        'X-Csrf-Token':auth['ct0'], 'Origin':'https://x.com',
                        'X-Twitter-Auth-Type':'OAuth2Session', 'X-Twitter-Active-User':'yes',
                        'X-Twitter-Client-Language':'en'}
        self.direct = direct or client()
        self.regional = regional or client(region)
        self.users = {}

    def close(self):
        self.direct.close(); self.regional.close()

    def _call(self, username, name, query_id, variables, *, mutation=False, regional=False):
        http = self.regional if regional or mutation else self.direct
        url = f'https://x.com/i/api/graphql/{query_id}/{name}'
        headers = {**self.headers, 'Referer':f'https://x.com/{username}/gift-premium'}
        if mutation:
            response = http.post(url, headers=headers, json={'variables':variables,'queryId':query_id})
        else:
            params = {'variables':json.dumps(variables,separators=(',',':'))}
            if regional:
                params['features']='{"subscriptions_marketing_page_fetch_promotions":true}'
            response = http.get(url, headers=headers, params=params)
        return json_response(response)['data']

    def eligibility(self, username):
        if not re.fullmatch(r'[A-Za-z0-9_]{1,15}',username):
            raise ValueError('invalid username')
        data = self._call(username,'PremiumGiftingQuery','kn8hCE6bHstQV2MtfYDTKg',{'screenName':username})
        user = data['user']['result']
        if not user.get('rest_id') or user.get('core',{}).get('screen_name','').lower()!=username.lower() or user.get('premium_gifting_eligible') is not True:
            raise ValueError('recipient identity or eligibility not verified')
        self.users[user['rest_id']]=username
        return user['rest_id']

    def quote(self, recipient, plan):
        validate_live_plan(plan)
        data=self._call(self.users.get(recipient,'i'),'useSubscriptionProductDetailsByRestIdQuery','Se1Bp6zcNnuXYXRecV2qLA',{'stripeId':plan.product_id},regional=True)
        product=data['web_subscription_product_details_by_rest_id']
        prices=product.get('prices',[])
        if product.get('rest_id')!=plan.product_id or len(prices)!=1:
            raise ValueError('unexpected product or price list')
        price=prices[0]
        if price.get('price_type')!='OneTime' or price.get('currency_code','').upper()!=plan.currency.upper() or price.get('amount_local_micro')!=plan.amount_minor*10000:
            raise ValueError('quote differs from authorized one-time price')
        return Quote(plan.merchant,plan.product_id,plan.months,plan.amount_minor,plan.currency)

    def create_checkout(self, order):
        # The adapter independently checks before the irreversible link mutation.
        if self.eligibility(order.username)!=order.recipient:
            raise ValueError('recipient changed')
        quote=self.quote(order.recipient,order.plan)
        base=f'https://x.com/{order.username}/gift-premium'
        data=self._call(order.username,'useOneTimePurchaseGiftMutation','GqTVJ4S1526tLkxj69xIZw',{'cancel_url':base,'success_url':base+'/success','external_product_id':order.plan.product_id,'gift_recipient':order.recipient},mutation=True)
        gift=data['onetimepurchase_gift']; sid=gift.get('session_id',''); url=gift.get('session_url','')
        parsed=urlsplit(url)
        if gift.get('session_status')!='Unpaid' or not re.fullmatch(r'cs_live_[A-Za-z0-9]+',sid) or parsed.scheme!='https' or parsed.netloc!='checkout.stripe.com' or parsed.path!=f'/c/pay/{sid}':
            raise ValueError('checkout session or URL not verified')
        return Checkout(sid,url,quote)
