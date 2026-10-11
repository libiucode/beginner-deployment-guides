import pytest
from gift.models import Code, Order, Plan
from gift.services.queue import process_next
from gift.integrations.simulated import SimulatedProvider

@pytest.fixture
def order(db):
    plan = Plan.objects.create(name='Test', months=1, amount_minor=100, currency='USD', merchant='test', product_id='one')
    code = Code.objects.create(plan=plan, digest='a'*64, ciphertext=b'test')
    return Order.objects.create(code=code, username='alice', token_digest='b'*64, plan_snapshot=dict(plan_id=plan.pk, name=plan.name, months=1, amount_minor=100, currency='USD', merchant='test', product_id='one'))

@pytest.mark.django_db(transaction=True)
def test_complete(order):
    provider = SimulatedProvider()
    assert process_next(provider)
    order.refresh_from_db()
    assert order.status == 'succeeded'
    assert provider.confirm_calls == 1
    assert not process_next(provider)

@pytest.mark.django_db(transaction=True)
def test_timeout(order):
    provider = SimulatedProvider(outcome='timeout')
    process_next(provider)
    order.refresh_from_db()
    assert order.status == 'review'

@pytest.mark.django_db(transaction=True)
def test_network_call_allows_parallel_transaction_and_claim(order):
    from concurrent.futures import ThreadPoolExecutor
    from django.db import close_old_connections, transaction
    from gift.models import AuditEvent
    class Provider(SimulatedProvider):
        def eligibility(self, username):
            def independent_worker():
                close_old_connections()
                try:
                    with transaction.atomic():
                        AuditEvent.objects.create(kind='parallel_write')
                    return process_next(SimulatedProvider())
                finally:
                    close_old_connections()
            with ThreadPoolExecutor(max_workers=1) as pool:
                assert pool.submit(independent_worker).result(timeout=5) is False
            return super().eligibility(username)
    provider = Provider()
    process_next(provider)
    order.refresh_from_db()
    assert order.status == 'succeeded'
    assert AuditEvent.objects.filter(kind='parallel_write').count() == 1

@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('outcome,status', [('failed','failed'), ('unknown','review')])
def test_declined_and_unknown_are_distinct(order, outcome, status):
    provider = SimulatedProvider(outcome)
    process_next(provider)
    order.refresh_from_db()
    assert order.status == status
    assert not process_next(provider)
    assert provider.confirm_calls == 1

@pytest.mark.django_db(transaction=True)
def test_missing_live_method_does_not_fake_success(order):
    provider = SimulatedProvider()
    provider.payment_method = None
    process_next(provider)
    order.refresh_from_db()
    assert order.status == 'awaiting_payment'
    assert provider.confirm_calls == 0

@pytest.mark.django_db(transaction=True)
def test_mismatched_quote_never_submits(order):
    from gift.integrations.types import Quote
    provider = SimulatedProvider()
    provider.quote = lambda *args: Quote('wrong','one',1,100,'USD')
    process_next(provider)
    order.refresh_from_db()
    assert order.status == 'review'
    assert provider.confirm_calls == 0

@pytest.mark.django_db(transaction=True)
def test_worker_waits_for_short_database_write_lock(order):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from django.db import close_old_connections, transaction
    from gift.models import AuditEvent
    started = Event()
    def worker():
        close_old_connections()
        started.set()
        try:
            return process_next(SimulatedProvider())
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            AuditEvent.objects.create(kind='lock_holder')
            future = pool.submit(worker)
            assert started.wait(3)
        assert future.result(timeout=5)
    order.refresh_from_db()
    assert order.status == 'succeeded'

@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('targeted',[False,True])
def test_checkout_acceptance_crash_never_creates_second_link(order,monkeypatch,targeted):
    from datetime import timedelta
    from uuid import uuid4
    from django.db.models.query import QuerySet
    from django.utils import timezone
    from gift.models import AuditEvent,TaskLease
    class Provider(SimulatedProvider):
        create_calls=0
        def create_checkout(self,snapshot):
            self.create_calls+=1
            # This represents remote acceptance and a successfully returned response.
            return super().create_checkout(snapshot)
    first=Provider()
    original=QuerySet.update
    def crash_before_save(self,**kwargs):
        if self.model is Order and 'remote_session_id' in kwargs:
            raise SystemExit('crash after remote acceptance before local save')
        return original(self,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(QuerySet,'update',crash_before_save)
        with pytest.raises(SystemExit):process_next(first)
    assert first.create_calls==1 and first.confirm_calls==0
    order.refresh_from_db()
    assert order.remote_session_id==''
    # A real process exit leaves a lease; explicitly reproduce its later expiration.
    TaskLease.objects.filter(order=order).update(owner=uuid4(),expires_at=timezone.now()-timedelta(seconds=1))
    fresh=Provider()
    if targeted:fresh.order_id=order.pk
    assert process_next(fresh,**({'order_id':order.pk} if targeted else {}))
    order.refresh_from_db()
    assert order.status=='review' and fresh.create_calls==0 and fresh.confirm_calls==0
    assert AuditEvent.objects.filter(order=order,kind='checkout_creation_started').count()==1
