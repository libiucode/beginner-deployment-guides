from datetime import timedelta
import pytest
from django.utils import timezone
from gift.models import Order, PaymentAttempt, Secret
from gift.integrations.simulated import SimulatedProvider
from gift.services.recovery import preview_recovery, start_recovery, stop_recovery, process_recovery, interrupt_recovery
from tests.test_link_queue import plan
from gift.services.links import request_link
from gift.services.queue import process_next
from gift.integrations.types import PaymentResult

def normal_order(plan, username='alice'):
    from gift.services.codes import generate_codes
    from gift.services.orders import redeem
    return redeem(generate_codes(plan.pk, 1)[0], username)

pytestmark = pytest.mark.django_db(transaction=True)

@pytest.fixture
def actor(django_user_model):
    return django_user_model.objects.create_superuser('boss', password='test')

def test_expired_and_changed(plan, actor):
    order = request_link(plan.pk, 'alice')
    preview = preview_recovery([order.pk], 'links')
    type(preview).objects.filter(pk=preview.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
    with pytest.raises(ValueError): start_recovery(preview.pk, actor.pk)
    preview = preview_recovery([order.pk], 'links')
    Secret.objects.create(name='config-test', ciphertext=b'changed')
    with pytest.raises(ValueError): start_recovery(preview.pk, actor.pk)

@pytest.mark.parametrize('status,no_retry', [('review', False), ('queued', True)])
def test_unknown_and_submit_boundary_blocked(plan, status, no_retry):
    order = normal_order(plan)
    Order.objects.filter(pk=order.pk).update(status=status)
    PaymentAttempt.objects.create(order=order, no_retry=no_retry)
    with pytest.raises(ValueError): preview_recovery([order.pk], 'pay')

def test_stop_after_current(plan, actor):
    first = request_link(plan.pk, 'alice'); second = request_link(plan.pk, 'bob')
    batch = start_recovery(preview_recovery([first.pk, second.pk], 'links').pk, actor.pk)
    class Provider(SimulatedProvider):
        def eligibility(self, username):
            stop_recovery(batch.pk)
            return super().eligibility(username)
    provider = Provider()
    assert process_recovery(provider)
    first.refresh_from_db(); second.refresh_from_db(); batch.refresh_from_db()
    assert first.status == 'awaiting_payment' and second.status == 'queued'
    assert batch.status == 'stopped' and provider.confirm_calls == 0
    assert not process_recovery(provider)
    assert not __import__('gift.services.queue', fromlist=['process_next']).process_next(provider)

def test_restart_interrupts(plan, actor):
    order = request_link(plan.pk, 'alice')
    batch = start_recovery(preview_recovery([order.pk], 'links').pk, actor.pk)
    type(batch).objects.filter(pk=batch.pk).update(status='running')
    interrupt_recovery()
    batch.refresh_from_db()
    assert batch.status == 'interrupted'
    assert not process_recovery(SimulatedProvider())

def test_startup_does_not_interrupt_other_live_worker(plan, actor):
    order = request_link(plan.pk, 'alice')
    batch = start_recovery(preview_recovery([order.pk], 'links').pk, actor.pk)
    type(batch).objects.filter(pk=batch.pk).update(status='running', lease_expires_at=timezone.now()+timedelta(seconds=300))
    interrupt_recovery()
    batch.refresh_from_db(); assert batch.status == 'running'

def test_permissions_and_csrf(plan, actor, client, django_user_model):
    order = normal_order(plan)
    preview = preview_recovery([order.pk], 'pay')
    user = django_user_model.objects.create_user('reader', is_staff=True)
    with pytest.raises(PermissionError): start_recovery(preview.pk, user.pk)
    client.force_login(user)
    assert client.get('/operator/recovery/').status_code == 403
    from django.test import Client
    secure = Client(enforce_csrf_checks=True); secure.force_login(actor)
    assert secure.post('/operator/recovery/', {'action': 'start', 'preview': str(preview.pk)}).status_code == 403

def test_order_changed_preview_rejected(plan, actor):
    order = normal_order(plan)
    preview = preview_recovery([order.pk], 'pay')
    Order.objects.filter(pk=order.pk).update(status='checking')
    with pytest.raises(ValueError): start_recovery(preview.pk, actor.pk)

def test_simulated_pay_batch_and_submit_spacing(plan, actor):
    first = normal_order(plan); second = normal_order(plan, 'bob')
    batch = start_recovery(preview_recovery([first.pk, second.pk], 'pay').pk, actor.pk)
    provider = SimulatedProvider()
    assert process_recovery(provider)
    assert process_recovery(provider)  # second checkout exists but submit interval defers payment
    second.refresh_from_db(); assert second.status == 'link_created'
    from gift.models import PaymentLock
    PaymentLock.objects.update(next_submit_at=timezone.now()-timedelta(seconds=1))
    assert process_recovery(provider)
    batch.refresh_from_db(); second.refresh_from_db()
    assert batch.status == 'completed' and second.status == 'succeeded' and provider.confirm_calls == 2

def test_worker_crash_interrupts_not_replayed(plan, actor):
    order = normal_order(plan)
    batch = start_recovery(preview_recovery([order.pk], 'pay').pk, actor.pk)
    with pytest.raises(SystemExit): process_recovery(SimulatedProvider('crash'))
    batch.refresh_from_db()
    assert batch.status == 'interrupted'
    fresh = SimulatedProvider()
    assert not process_recovery(fresh) and not process_next(fresh)
    assert fresh.confirm_calls == 0

def test_replacement_evidence_archive_and_limit(plan, actor):
    order = normal_order(plan)
    class AllowedFailure(SimulatedProvider):
        def confirm(self, checkout, payment_method, key):
            result = PaymentResult('failed', False, True, 'verified-decline-without-charge')
            self.results[checkout.session_id] = result
            return result
    provider = AllowedFailure(); process_next(provider)
    order.refresh_from_db()
    assert PaymentAttempt.objects.get(order=order).no_retry
    assert not PaymentAttempt.objects.get(order=order).retry_forbidden
    with pytest.raises(ValueError): preview_recovery([order.pk], 'links')
    original_session = order.remote_session_id
    for _ in range(3):
        preview = preview_recovery([order.pk], 'links', unpaid_confirmed=True)
        batch = start_recovery(preview.pk, actor.pk)
        process_recovery(provider)
        item = batch.items.get()
        assert item.replacement and item.replacement.status == 'awaiting_payment'
    order.refresh_from_db(); assert order.status == 'failed' and order.remote_session_id == original_session
    from gift.models import AuditEvent
    assert AuditEvent.objects.filter(order=order, kind='checkout_archived_for_replacement').count() == 3
    with pytest.raises(ValueError): preview_recovery([order.pk], 'links', unpaid_confirmed=True)

def test_fresh_evidence_insufficient_refuses_replacement(plan, actor):
    order = normal_order(plan)
    Order.objects.filter(pk=order.pk).update(status='failed', remote_session_id='simulated:'+str(order.pk))
    PaymentAttempt.objects.create(order=order, no_retry=True, state='failed', result_evidence={'unpaid_confirmed': True, 'evidence': 'old-proof'})
    batch = start_recovery(preview_recovery([order.pk], 'links', unpaid_confirmed=True).pk, actor.pk)
    process_recovery(SimulatedProvider())
    assert batch.items.get().status == 'blocked' and batch.items.get().replacement is None

def test_permanent_forbidden_refuses_links_too(plan):
    order = normal_order(plan)
    PaymentAttempt.objects.create(order=order, retry_forbidden=True)
    with pytest.raises(ValueError): preview_recovery([order.pk], 'links')
    provider = SimulatedProvider()
    process_next(provider, order_id=order.pk)
    order.refresh_from_db()
    assert order.status == 'review' and provider.confirm_calls == 0

def test_live_pay_batch_refused(plan, settings):
    settings.XGIFT_MODE = 'live'
    order = request_link(plan.pk, 'alice')
    with pytest.raises(ValueError, match='pay_order'): preview_recovery([order.pk], 'pay')

def test_delayed_recovery_exception_preserves_interrupted_item(plan, actor, monkeypatch):
    from gift.models import AuditEvent
    order = request_link(plan.pk, 'alice')
    batch = start_recovery(preview_recovery([order.pk], 'links').pk, actor.pk)
    def delayed_error(*args, **kwargs):
        type(batch).objects.filter(pk=batch.pk).update(lease_expires_at=timezone.now()-timedelta(seconds=1))
        interrupt_recovery()
        raise ValueError('private provider details must not enter audit')
    monkeypatch.setattr('gift.services.recovery.process_next', delayed_error)
    process_recovery(SimulatedProvider())
    assert batch.items.get().status == 'interrupted'
    assert not AuditEvent.objects.filter(order=order, kind='recovery_blocked').exists()
