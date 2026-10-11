import pytest
from tests.test_worker import order
from gift.services.queue import process_next, reconcile
from gift.integrations.simulated import SimulatedProvider

@pytest.mark.django_db(transaction=True)
def test_crash_after_submit_never_replays(order):
    provider = SimulatedProvider(outcome='crash')
    with pytest.raises(SystemExit):
        process_next(provider)
    assert reconcile(order.id, provider).status == 'succeeded'
    assert provider.confirm_calls == 1

@pytest.mark.django_db(transaction=True)
def test_spacing_persists_across_workers(order, monkeypatch):
    from datetime import timedelta
    from gift.services import queue
    from gift.models import Code, Order
    clock = [queue.now()]
    monkeypatch.setattr(queue, 'now', lambda: clock[0])
    first = SimulatedProvider()
    process_next(first)
    code = Code.objects.create(plan=order.code.plan, digest='c'*64, ciphertext=b'test')
    second_order = Order.objects.create(code=code, username='bob', token_digest='d'*64, plan_snapshot=order.plan_snapshot)
    second = SimulatedProvider()
    process_next(second)
    assert second.confirm_calls == 0
    clock[0] += timedelta(seconds=29)
    process_next(second)
    assert second.confirm_calls == 0
    clock[0] += timedelta(seconds=1)
    process_next(second)
    assert second.confirm_calls == 1
    second_order.refresh_from_db()
    assert second_order.status == 'succeeded'

@pytest.mark.django_db(transaction=True)
def test_crash_before_actual_call_remains_unknown(order):
    from gift.models import PaymentAttempt, PaymentLock
    order.remote_session_id = 'simulated:' + str(order.id)
    order.payment_url = 'https://example.invalid'
    order.status = 'payment_submitted'
    order.save()
    PaymentAttempt.objects.create(order=order, no_retry=True, state='submitted')
    PaymentLock.objects.create(order=order)
    provider = SimulatedProvider()
    process_next(provider)
    order.refresh_from_db()
    assert order.status == 'review'
    assert provider.confirm_calls == 0
    assert PaymentLock.objects.get(pk=1).order_id == order.id

@pytest.mark.django_db(transaction=True)
def test_expired_lease_recovers_without_confirm_replay(order):
    from datetime import timedelta
    from uuid import uuid4
    from gift.models import TaskLease
    from gift.services.queue import now
    TaskLease.objects.create(order=order, owner=uuid4(), expires_at=now()-timedelta(seconds=1))
    provider = SimulatedProvider()
    assert process_next(provider)
    assert provider.confirm_calls == 1

@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('delayed', ['unknown', 'error'])
def test_delayed_reconcile_cannot_overwrite_terminal_success(order, delayed):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from django.db import close_old_connections
    from gift.integrations.types import PaymentResult
    from gift.models import PaymentAttempt, PaymentLock
    order.remote_session_id = 'simulated:' + str(order.id)
    order.payment_url = 'https://example.invalid'
    order.status = 'review'
    order.save()
    PaymentAttempt.objects.create(order=order, no_retry=True, state='submitted', simulated_result='succeeded')
    PaymentLock.objects.create(order=order)
    entered, release = Event(), Event()
    class DelayedProvider(SimulatedProvider):
        def inspect(self, checkout):
            entered.set()
            assert release.wait(5)
            if delayed == 'error':
                raise TimeoutError('delayed response')
            return PaymentResult('unknown')
    def delayed_reconcile():
        close_old_connections()
        try:
            return reconcile(order.id, DelayedProvider()).status
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(delayed_reconcile)
        try:
            assert entered.wait(5)
            assert reconcile(order.id, SimulatedProvider()).status == 'succeeded'
        finally:
            release.set()
        assert future.result(timeout=5) == 'succeeded'
    order.refresh_from_db()
    assert order.status == 'succeeded'
    assert PaymentAttempt.objects.get(order=order).state == 'succeeded'
    assert PaymentLock.objects.get(pk=1).order_id is None

@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('outcome', ['succeeded', 'error'])
@pytest.mark.parametrize('lease_state', ['expired', 'replaced'])
def test_expired_worker_cannot_write_result(order, outcome, lease_state):
    from datetime import timedelta
    from uuid import uuid4
    from gift.models import TaskLease, PaymentAttempt, PaymentLock
    from gift.integrations.types import PaymentResult
    from gift.services.queue import now
    replacement_owner = uuid4()
    class StaleProvider(SimulatedProvider):
        def confirm(self, checkout, payment_method, key):
            if lease_state == 'replaced':
                TaskLease.objects.filter(order=order).update(owner=replacement_owner, expires_at=now()+timedelta(seconds=300))
            else:
                TaskLease.objects.filter(order=order).update(expires_at=now()-timedelta(seconds=1))
            if outcome == 'error':
                raise TimeoutError('stale result')
            return PaymentResult('succeeded')
    process_next(StaleProvider())
    order.refresh_from_db()
    assert order.status == 'payment_submitted'
    assert PaymentAttempt.objects.get(order=order).state == 'submitted'
    assert PaymentLock.objects.get(pk=1).order_id == order.id
    if lease_state == 'replaced':
        assert TaskLease.objects.get(order=order).owner == replacement_owner

@pytest.mark.django_db(transaction=True)
def test_restart_with_expired_submitted_lease_inspects_fresh_provider(order):
    from datetime import timedelta
    from uuid import uuid4
    from gift.models import TaskLease, PaymentAttempt
    from gift.services.queue import now
    old_provider = SimulatedProvider(outcome='crash')
    with pytest.raises(SystemExit):
        process_next(old_provider)
    TaskLease.objects.filter(order=order).update(owner=uuid4(), expires_at=now()-timedelta(seconds=1))
    fresh_provider = SimulatedProvider()
    assert process_next(fresh_provider)
    order.refresh_from_db()
    assert order.status == 'succeeded'
    assert PaymentAttempt.objects.get(order=order).state == 'succeeded'
    assert old_provider.confirm_calls == 1
    assert fresh_provider.confirm_calls == 0
