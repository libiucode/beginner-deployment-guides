"""Public links have an internal encrypted code, never a publicly usable code."""
import secrets
from django.db import transaction
from gift.models import Code, Order, LinkTicket, AuditEvent, Plan, TaskLease
from gift.services.codes import digest
from gift.services.vault import seal
from gift.services.catalog import snapshot
from gift.services.orders import normalize_username
from gift.integrations.live import configured_mode

def internal_order(plan, username, *, mode=None, frozen=None):
    value = secrets.token_urlsafe(32)
    code = Code.objects.create(plan=plan, digest=digest(value), ciphertext=seal(value.encode(), 'redeem-code'), enabled=False)
    token = secrets.token_urlsafe(32)
    order = Order.objects.create(code=code, username=normalize_username(username), token_digest=digest(token),
        mode=mode or configured_mode(), plan_snapshot=frozen or snapshot(plan))
    order.access_token = token
    return order

@transaction.atomic
def request_link(plan_id, username):
    try: plan = Plan.objects.get(pk=plan_id, enabled=True)
    except Plan.DoesNotExist: raise ValueError('套餐无效') from None
    order = internal_order(plan, username)
    LinkTicket.objects.create(order=order)
    AuditEvent.objects.create(order=order, kind='public_link_requested')
    return order

def authorized_link(order_id, token):
    order = Order.objects.filter(pk=order_id, link_ticket__isnull=False).first()
    if not order or not token or not secrets.compare_digest(order.token_digest, digest(token)):
        raise PermissionError('无权查询此队列票据')
    return order

@transaction.atomic
def cancel_link(order_id, token):
    order = authorized_link(order_id, token)
    lease = TaskLease.objects.filter(order=order, owner__isnull=False).exists()
    if order.status != 'queued' or lease or order.recovery_items.exists():
        raise ValueError('任务已开始或已由管理员接管，不能取消')
    Order.objects.filter(pk=order.pk, status='queued').update(status='cancelled')
    AuditEvent.objects.create(order=order, kind='public_link_cancelled')
    return order

@transaction.atomic
def request_link_reconciliation(order_id, token):
    order = authorized_link(order_id, token)
    if order.status not in ('awaiting_payment', 'link_created', 'review') or not order.remote_session_id:
        raise ValueError('尚无可核实的原付款会话')
    LinkTicket.objects.filter(order=order).update(reconcile_requested=True)
    AuditEvent.objects.create(order=order, kind='public_link_reconciliation_requested')

def reconcile_public_link(provider):
    from gift.services.queue import _compatible, _checkout, _finish, _claim_inspection
    from gift.integrations.types import PaymentResult
    # Only inspect the retained original checkout. Provider.inspect must establish
    # merchant, recipient, plan, amount, currency and session bindings itself.
    with transaction.atomic():
        ticket = LinkTicket.objects.filter(reconcile_requested=True, order__mode=provider.mode).order_by('pk').first()
        if not ticket: return False
        order = ticket.order
        if order.status in ('succeeded', 'failed', 'cancelled'):
            ticket.reconcile_requested = False; ticket.save(update_fields=['reconcile_requested'])
            return True
        owner = _claim_inspection(order)
        if owner is None: return False  # preserve the request until current work releases its lease
        ticket.reconcile_requested = False; ticket.save(update_fields=['reconcile_requested'])
    try:
        try:
            if not _compatible(order, provider) or not order.remote_session_id: raise ValueError('session mismatch')
            result = provider.inspect(_checkout(order))
            if result.status == 'unpaid': result = PaymentResult('awaiting_payment')
        except Exception: result = PaymentResult('unknown')
        _finish(order, result, owner, allow_review=True)
    finally:
        TaskLease.objects.filter(order=order, owner=owner).update(owner=None, expires_at=None)
    return True
