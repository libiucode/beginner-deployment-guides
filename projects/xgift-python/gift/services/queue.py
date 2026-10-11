"""Short SQLite transactions surround, but never contain, provider calls."""
from datetime import timedelta
from uuid import uuid4
from django.db import transaction
from django.utils import timezone
from gift.models import AuditEvent, Order, PaymentAttempt, PaymentLock, TaskLease
from gift.integrations.types import Checkout, OrderSnapshot, PaymentResult, PlanSnapshot, Quote

LEASE_SECONDS = 300
SUBMIT_INTERVAL = 30
now = timezone.now
TERMINAL = ('succeeded', 'failed', 'cancelled')
CHECKOUT_STARTED = 'checkout_creation_started'

def _snapshot(order):
    return OrderSnapshot(order.id, order.username, order.recipient_id, PlanSnapshot(**order.plan_snapshot))

def _quote(plan):
    return Quote(plan.merchant, plan.product_id, plan.months, plan.amount_minor, plan.currency)

def _checkout(order):
    return Checkout(order.remote_session_id, order.payment_url, _quote(_snapshot(order).plan))

def _status(order, status):
    # All callers hold the short write transaction. Terminal outcomes are final.
    current = Order.objects.get(pk=order.pk)
    if current.status in TERMINAL:
        order.status = current.status
        return
    changed_at = now()
    Order.objects.filter(pk=order.pk).update(status=status, updated_at=changed_at)
    AuditEvent.objects.create(order=order, kind='order_status', details={'status': status})
    order.status = status
    order.updated_at = changed_at

def _claim(provider_mode, order_id=None):
    with transaction.atomic():
        excluded = (*TERMINAL, 'review') if order_id is not None else (*TERMINAL, 'review', 'awaiting_payment')
        orders = Order.objects.filter(mode=provider_mode).exclude(status__in=excluded).order_by('created_at')
        if order_id is not None:
            orders = orders.filter(pk=order_id)
        else:
            # Batch-owned work is never picked up by the unattended queue, even
            # after a stop or interrupted restart.
            orders = orders.filter(recovery_items__isnull=True, replacement_item__isnull=True)
        for order in orders:
            lease, _ = TaskLease.objects.get_or_create(order=order)
            if lease.owner and lease.expires_at > now():
                continue
            lease.owner = uuid4()
            lease.expires_at = now() + timedelta(seconds=LEASE_SECONDS)
            lease.save()
            return order, lease.owner
    return None

def _owned(order, owner, *, allow_review=False):
    excluded = TERMINAL if allow_review else (*TERMINAL, 'review')
    return TaskLease.objects.filter(order=order, owner=owner, expires_at__gt=now(),
        order__status=order.status, order__updated_at=order.updated_at).exclude(order__status__in=excluded).exists()

def _claim_inspection(order):
    """Caller holds a short transaction; read-only reconciliation shares the task lease."""
    lease, _ = TaskLease.objects.get_or_create(order=order)
    if lease.owner and lease.expires_at and lease.expires_at > now():
        return None
    lease.owner = uuid4()
    lease.expires_at = now() + timedelta(seconds=LEASE_SECONDS)
    lease.save()
    return lease.owner

def _finish(order, result, owner=None, *, allow_review=False):
    status = result.status if result.status in ('succeeded', 'failed', 'awaiting_payment') else 'review'
    with transaction.atomic():
        current = Order.objects.get(pk=order.pk)
        if (current.status in TERMINAL or current.status != order.status or current.updated_at != order.updated_at
                or (owner is not None and not _owned(order, owner, allow_review=allow_review))):
            return current
        _status(order, status)
        PaymentAttempt.objects.filter(order=order).update(state=status, no_retry=True)
        if result.no_retry or result.status == 'failed':
            attempt, _ = PaymentAttempt.objects.get_or_create(order=order, defaults={'no_retry': True, 'state': status})
            attempt.retry_forbidden = attempt.retry_forbidden or result.no_retry
            attempt.result_evidence = {'status': result.status, 'unpaid_confirmed': result.unpaid_confirmed,
                'evidence': result.evidence}
            attempt.save(update_fields=['retry_forbidden', 'result_evidence'])
        if status in TERMINAL:
            PaymentLock.objects.filter(pk=1, order=order).update(order=None, next_submit_at=now() + timedelta(seconds=SUBMIT_INTERVAL))
    return Order.objects.get(pk=order.pk)

def reconcile(order_id, provider):
    # Explicit manual read-only inspections may overlap. _finish compares their
    # captured status/timestamp, so a delayed result cannot overwrite newer proof.
    return _reconcile(order_id, provider, allow_review=True)

def _reconcile(order_id, provider, owner=None, *, allow_review=False):
    order = Order.objects.get(pk=order_id)
    if not _compatible(order, provider):
        raise ValueError('provider mode does not match order')
    if order.status in TERMINAL or not order.remote_session_id:
        return order
    attempt = PaymentAttempt.objects.filter(order=order).first()
    if not attempt or not attempt.no_retry:
        return order
    try:
        result = provider.inspect(_checkout(order))
    except Exception:
        result = PaymentResult('unknown')
    return _finish(order, result, owner, allow_review=allow_review)

def _compatible(order, provider):
    mode = getattr(provider, 'mode', None)
    session = order.remote_session_id
    return mode == order.mode and (not session or (session.startswith('simulated:') if mode == 'simulated' else session.startswith('cs_live_')))

def process_next(provider, *, order_id=None, links_only=False) -> bool:
    if getattr(provider, 'mode', None) not in ('simulated', 'live'):
        raise ValueError('provider must explicitly declare mode')
    authorized = getattr(provider, 'order_id', None)
    if authorized is not None and str(authorized) != str(order_id):
        raise ValueError('manual provider requires its specific order')
    claimed = _claim(provider.mode, order_id)
    if not claimed:
        return False
    order, owner = claimed
    try:
        if not _compatible(order, provider):
            raise ValueError('provider session mode mismatch')
        attempt = PaymentAttempt.objects.filter(order=order).first()
        if attempt and attempt.retry_forbidden and not attempt.no_retry:
            _finish(order, PaymentResult('unknown', True), owner)
            return True
        if attempt and attempt.no_retry:
            _reconcile(order.id, provider, owner)
            return True
        if not order.remote_session_id:
            # Remote creation may have succeeded before a crash prevented local save.
            # No provider or manual target may silently create another checkout.
            if AuditEvent.objects.filter(order=order, kind=CHECKOUT_STARTED).exists():
                raise ValueError('original checkout creation outcome requires inspection')
            with transaction.atomic():
                if not _owned(order, owner):
                    return True
                _status(order, 'checking')
            recipient = provider.eligibility(order.username)
            if not recipient:
                raise ValueError('recipient is not eligible')
            plan = _snapshot(order).plan
            if provider.quote(recipient, plan) != _quote(plan):
                raise ValueError('remote quote does not match frozen plan')
            with transaction.atomic():
                if not _owned(order, owner):
                    return True
                if AuditEvent.objects.filter(order=order, kind=CHECKOUT_STARTED).exists():
                    raise ValueError('checkout creation already started')
                # Irreversible marker commits BEFORE sending the remote mutation.
                AuditEvent.objects.create(order=order, kind=CHECKOUT_STARTED,
                                          details={'operation_id': str(uuid4())})
            checkout = provider.create_checkout(OrderSnapshot(order.id, order.username, recipient, plan))
            if checkout.quote != _quote(plan) or not checkout.session_id:
                raise ValueError('checkout does not match frozen plan')
            with transaction.atomic():
                if not _owned(order, owner):
                    return True
                Order.objects.filter(pk=order.pk).update(recipient_id=recipient, remote_session_id=checkout.session_id, payment_url=checkout.payment_url)
                order.recipient_id, order.remote_session_id, order.payment_url = recipient, checkout.session_id, checkout.payment_url
                _status(order, 'link_created')
        else:
            checkout = _checkout(order)
        if provider.quote(order.recipient_id, _snapshot(order).plan) != checkout.quote:
            raise ValueError('payment quote changed')
        result = provider.inspect(checkout)
        if result.status != 'unpaid' or result.no_retry:
            _finish(order, result, owner)
            return True
        payment_method = None if links_only or hasattr(order, 'link_ticket') else getattr(provider, 'payment_method', None)
        if not payment_method:
            _finish(order, PaymentResult('awaiting_payment'), owner)
            return True
        with transaction.atomic():
            if not _owned(order, owner):
                return True
            lock, _ = PaymentLock.objects.get_or_create(pk=1)
            if lock.order_id or (lock.next_submit_at and lock.next_submit_at > now()):
                return True
            attempt, _ = PaymentAttempt.objects.get_or_create(order=order)
            if attempt.no_retry:
                return True
            attempt.no_retry = True  # irreversible submit boundary, persisted BEFORE request
            attempt.state = 'submitted'
            attempt.submitted_at = now()
            attempt.save()
            lock.order = order
            lock.next_submit_at = now() + timedelta(seconds=SUBMIT_INTERVAL)
            lock.save()
            _status(order, 'payment_submitted')
        try:
            result = provider.confirm(checkout, payment_method, str(attempt.key))
        except Exception:
            _finish(order, PaymentResult('unknown'), owner)
            return True
        _finish(order, result, owner)
        return True
    except Exception:
        # Pre-submit network errors also remain conservative: never infer refusal.
        _finish(order, PaymentResult('unknown'), owner)
        return True
    finally:
        TaskLease.objects.filter(order=order, owner=owner).update(owner=None, expires_at=None)
