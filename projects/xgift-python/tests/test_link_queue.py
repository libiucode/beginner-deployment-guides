import pytest
from gift.integrations.simulated import SimulatedProvider
from gift.services.queue import process_next
from gift.services.links import request_link, cancel_link
from gift.models import Plan

pytestmark = pytest.mark.django_db(transaction=True)

@pytest.fixture
def plan(tmp_path, settings):
    settings.XGIFT_KEY_FILE = tmp_path / 'key'
    settings.XGIFT_KEY_FILE.write_bytes(b'a'*32)
    return Plan.objects.create(name='月度', months=1, amount_minor=100, currency='USD', merchant='test', product_id='one')

def test_cancel_never_pays(plan):
    order = request_link(plan.pk, 'alice')
    cancel_link(order.pk, order.access_token)
    provider = SimulatedProvider()
    assert not process_next(provider)
    assert provider.confirm_calls == 0

def test_customer_authorization(plan, client):
    order = request_link(plan.pk, 'alice')
    assert client.get(f'/api/links/{order.pk}', HTTP_AUTHORIZATION='Bearer wrong').status_code == 403
    assert client.post(f'/api/links/{order.pk}/cancel', HTTP_AUTHORIZATION='Bearer wrong').status_code == 403

def test_links_never_confirm(plan):
    order = request_link(plan.pk, 'alice')
    provider = SimulatedProvider()
    assert process_next(provider)
    order.refresh_from_db()
    assert order.status == 'awaiting_payment'
    assert provider.confirm_calls == 0

def test_cancel_started_refused(plan):
    order = request_link(plan.pk, 'alice')
    process_next(SimulatedProvider())
    with pytest.raises(ValueError): cancel_link(order.pk, order.access_token)

def test_public_browser_payment_readonly_reconciliation(plan):
    from gift.services.links import request_link_reconciliation, reconcile_public_link
    from gift.integrations.types import PaymentResult
    order = request_link(plan.pk, 'alice')
    provider = SimulatedProvider(); process_next(provider)
    provider.results[order.remote_session_id] = PaymentResult('succeeded')
    order.refresh_from_db()
    provider.results[order.remote_session_id] = PaymentResult('succeeded')
    request_link_reconciliation(order.pk, order.access_token)
    assert reconcile_public_link(provider)
    order.refresh_from_db()
    assert order.status == 'succeeded' and provider.confirm_calls == 0

def test_public_internal_code_not_exported(plan, client, django_user_model):
    order = request_link(plan.pk, 'alice')
    client.force_login(django_user_model.objects.create_superuser('boss', password='test'))
    response = client.post('/operator/export')
    assert len(response.content.decode('utf-8-sig').splitlines()) == 1

def test_public_csrf(plan):
    from django.test import Client
    assert Client(enforce_csrf_checks=True).post('/api/links', {'plan': plan.pk, 'username': 'alice'}).status_code == 403

@pytest.mark.parametrize('first', ['worker', 'reconciler'])
def test_public_inspection_serializes_both_completion_orders(plan, first):
    from gift.services.links import request_link_reconciliation, reconcile_public_link
    from gift.integrations.types import PaymentResult
    order = request_link(plan.pk, 'alice')
    process_next(SimulatedProvider()); order.refresh_from_db()
    request_link_reconciliation(order.pk, order.access_token)
    observed = []
    class Unknown(SimulatedProvider):
        def inspect(self, checkout):
            if first == 'reconciler': observed.append(process_next(SimulatedProvider(), order_id=order.pk))
            return PaymentResult('unknown')
    class Unpaid(SimulatedProvider):
        def inspect(self, checkout):
            observed.append(reconcile_public_link(Unknown()))
            return PaymentResult('unpaid')
    if first == 'worker':
        process_next(Unpaid(), order_id=order.pk)
        assert observed == [False]
        assert reconcile_public_link(Unknown())
    else:
        assert reconcile_public_link(Unknown())
        assert observed == [False]
        assert not process_next(SimulatedProvider(), order_id=order.pk)
    order.refresh_from_db(); assert order.status == 'review'

def test_ordinary_delayed_unpaid_does_not_overwrite_review(plan):
    from django.utils import timezone
    from gift.models import Order
    from gift.integrations.types import PaymentResult
    order = request_link(plan.pk, 'alice')
    class Provider(SimulatedProvider):
        def inspect(self, checkout):
            Order.objects.filter(pk=order.pk).update(status='review', updated_at=timezone.now())
            return PaymentResult('unpaid')
    provider = Provider(); process_next(provider)
    order.refresh_from_db(); assert order.status == 'review' and provider.confirm_calls == 0

def test_manual_public_reconcile_review_can_establish_success(plan):
    from gift.models import Order
    from gift.integrations.types import PaymentResult
    from gift.services.links import request_link_reconciliation, reconcile_public_link
    order = request_link(plan.pk, 'alice')
    process_next(SimulatedProvider()); order.refresh_from_db()
    Order.objects.filter(pk=order.pk).update(status='review')
    provider = SimulatedProvider(); provider.results[order.remote_session_id] = PaymentResult('succeeded')
    request_link_reconciliation(order.pk, order.access_token)
    reconcile_public_link(provider)
    order.refresh_from_db(); assert order.status == 'succeeded' and provider.confirm_calls == 0
