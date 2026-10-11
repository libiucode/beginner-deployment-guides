"""Defensive Stripe checkout subset derived from xgift (MIT; LICENSE.upstream).

Responses and transient card data are never logged or persisted. A timeout is
unknown, never a refusal. No write request is retried, even with an idempotency key.
"""
import re
import httpx
from .routes import client, json_response
from .types import PaymentResult


def guard_page(page, checkout, username, *, before=False):
    q=checkout.quote
    currency=q.currency.lower()
    group=page.get('line_item_group',{})
    base=f'https://x.com/{username}/gift-premium'
    def require(condition):
        if not condition:
            raise ValueError('checkout identity, product, price or payment state not verified')
    require(page.get('session_id')==checkout.session_id and page.get('account_settings',{}).get('account_id')==q.merchant
            and page.get('livemode') is True and page.get('mode')=='payment'
            and page.get('currency')==currency and group.get('currency')==currency
            and page.get('success_url')==base+'/success' and page.get('cancel_url')==base)
    require(all(page.get(field) is None for field in ('setup_future_usage','subscription_data','setup_intent')))
    total=page.get('total_summary',{})
    require(all(container.get(field)==q.amount_minor for container in (total,group) for field in ('total','subtotal')))
    items=group.get('line_items',[])
    require(isinstance(items,list) and len(items)==1)
    item=items[0]; price=item.get('price',{}); product=price.get('product',{})
    name=f'Premium Gift - {q.months} months'
    require(item.get('name')==name and item.get('quantity')==1 and item.get('subtotal')==q.amount_minor and item.get('total')==q.amount_minor
            and price.get('currency')==currency and price.get('type')=='one_time' and price.get('unit_amount')==q.amount_minor
            and price.get('recurring') is None and product.get('id')==q.product_id and product.get('name')==name and product.get('livemode') is True)
    intent=page.get('payment_intent')
    if intent is not None:
        require(isinstance(intent,dict) and intent.get('currency')==currency and intent.get('amount')==q.amount_minor)
    if page.get('payment_status')=='paid':
        require(intent is not None and intent.get('status')=='succeeded' and intent.get('amount_received')==q.amount_minor)
    if before:
        require('payment_intent' in page and intent is None and page.get('status')=='open' and page.get('payment_status')=='unpaid'
                and total.get('due')==q.amount_minor and group.get('due')==q.amount_minor and bool(page.get('init_checksum')))
    return page


class StripeClient:
    def __init__(self, key, *, http=None, endpoint=None, pool=None, client_factory=client):
        if not re.fullmatch(r'pk_live_[A-Za-z0-9]{8,}',key):
            raise ValueError('invalid live publishable key')
        self.key=key
        self.pool=pool
        self.client_factory=client_factory
        self.route=pool.choose() if pool is not None else {'name':'direct','url':endpoint}
        self.http=http or client_factory(self.route['url'])

    def close(self):
        self.http.close()

    def _call(self, method, path, form=None, key=None):
        form={**(form or {}),'key':self.key}
        headers={'Idempotency-Key':key} if key else {}
        url='https://api.stripe.com/v1/'+path
        safe = method == 'GET' or path.endswith('/init')
        for attempt in range(3):
            try:
                response=self.http.get(url,params=form,headers=headers) if method=='GET' else self.http.post(url,data=form,headers=headers)
                break
            except httpx.HTTPError:
                if self.pool is not None:
                    self.pool.failed(self.route['name'])
                if not safe or self.pool is None or attempt == 2:
                    raise
                route=self.pool.choose()
                self.http.close()
                self.route=route
                self.http=self.client_factory(route['url'])
        if response.status_code==402:
            # Only an explicit issuer/card decline proves failure; otherwise unknown.
            try:
                error=response.json().get('error',{})
            except (ValueError,AttributeError):
                raise ValueError('unverifiable payment error') from None
            if error.get('type')=='card_error' and error.get('code')=='card_declined':
                return {'_declined':True,'_no_retry':True}
        return json_response(response)

    def page(self, checkout, username, *, before=False):
        if not re.fullmatch(r'cs_live_[A-Za-z0-9]+',checkout.session_id):
            raise ValueError('invalid live checkout session')
        result=self._call('POST',f'payment_pages/{checkout.session_id}/init',{'browser_locale':'en','redirect_type':'url'})
        return guard_page(result,checkout,username,before=before)

    def inspect(self, checkout, username):
        # Reduced completed-session results cannot prove all bindings. Fail closed.
        try:
            page=self.page(checkout,username)
        except (ValueError,KeyError,TypeError,httpx.HTTPError):
            return PaymentResult('unknown',True)
        if page.get('payment_status')=='paid':
            return PaymentResult('succeeded',True)
        if page.get('status')=='open' and page.get('payment_status')=='unpaid' and 'payment_intent' in page and page['payment_intent'] is None:
            return PaymentResult('unpaid')
        return PaymentResult('unknown',True)

    def tokenize(self, checkout, username, card, key):
        self.page(checkout,username,before=True)
        allowed={'number','exp_month','exp_year','cvc','name','email'}
        if set(card)!=allowed or not re.fullmatch(r'[0-9]{12,19}',card['number']) or not re.fullmatch(r'[0-9]{3,4}',card['cvc']):
            raise ValueError('invalid transient card fields')
        if not card['exp_month'].isdigit() or not 1<=int(card['exp_month'])<=12 or not re.fullmatch(r'[0-9]{4}',card['exp_year']):
            raise ValueError('invalid transient card expiry')
        form={'type':'card', **{'card['+field+']':card[field] for field in ('number','exp_month','exp_year','cvc')},'billing_details[name]':card['name'],'billing_details[email]':card['email']}
        result=self._call('POST','payment_methods',form,key+'-method')
        if result.get('livemode') is not True or result.get('type')!='card' or not re.fullmatch(r'pm_[A-Za-z0-9]+',result.get('id','')):
            raise ValueError('live payment method not verified')
        return result['id']

    def confirm(self, checkout, username, method, key):
        if not re.fullmatch(r'pm_[A-Za-z0-9]+',method):
            raise ValueError('invalid payment method')
        page=self.page(checkout,username,before=True)
        try:
            result=self._call('POST',f'payment_pages/{checkout.session_id}/confirm',{'payment_method':method,'expected_amount':str(checkout.quote.amount_minor),'expected_payment_method_type':'card','init_checksum':page['init_checksum'],'return_url':f'https://x.com/{username}/gift-premium/success'},key)
            if result.get('_declined'):
                return PaymentResult('failed',True)
            guard_page(result,checkout,username)
            if result.get('payment_status')=='paid':
                return PaymentResult('succeeded',True)
        except (httpx.HTTPError,ValueError,KeyError,TypeError):
            pass
        return PaymentResult('unknown',True)
