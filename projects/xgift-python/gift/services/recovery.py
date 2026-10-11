"""Explicit, durable recovery scheduling. Network work stays outside transactions."""
import hashlib
import json
from datetime import timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from gift.models import Order, PaymentAttempt, PaymentLock, Secret, Plan, TaskLease, AuditEvent, RecoveryPreview, RecoveryBatch, RecoveryItem
from gift.integrations.live import configured_mode
from gift.services.queue import process_next, _checkout, _compatible
from gift.services.links import internal_order

def config_fingerprint():
    data = {'mode': configured_mode(), 'secrets': [(s.name, bytes(s.ciphertext).hex()) for s in Secret.objects.order_by('name')],
        'plans': list(Plan.objects.order_by('pk').values()), 'interval': getattr(settings, 'XGIFT_SUBMIT_INTERVAL', 30)}
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()

def fingerprint(order):
    attempt = PaymentAttempt.objects.filter(order=order).first()
    return {'id': str(order.pk), 'status': order.status, 'session': order.remote_session_id, 'mode': order.mode,
        'status_label': order.get_status_display(), 'mode_label': order.get_mode_display(),
        'plan': order.plan_snapshot, 'updated': order.updated_at.isoformat(),
        'attempt': None if not attempt else {'key': str(attempt.key), 'state': attempt.state,
            'no_retry': attempt.no_retry, 'forbidden': attempt.retry_forbidden, 'evidence': attempt.result_evidence}}

def validate(order, mode, unpaid_confirmed):
    if order.mode != configured_mode(): raise ValueError('订单与当前运行模式不符')
    attempt = PaymentAttempt.objects.filter(order=order).first()
    if TaskLease.objects.filter(order=order, owner__isnull=False, expires_at__gt=timezone.now()).exists():
        raise ValueError('订单正在处理')
    if order.status in ('review', 'payment_submitted', 'succeeded', 'cancelled'):
        raise ValueError('结果未知、已提交或终态订单不能补单')
    if PaymentLock.objects.filter(pk=1, order=order).exists(): raise ValueError('付款锁尚未释放')
    if attempt and attempt.retry_forbidden: raise ValueError('商户禁止重试')
    if mode == 'pay' and order.mode == 'live':
        raise ValueError('真实付款需使用 pay_order 单独授权；批量补单只支持生成链接')
    if mode == 'pay' and hasattr(order, 'link_ticket'): raise ValueError('公开链接申请不能后台付款')
    if order.status == 'failed':
        evidence = attempt.result_evidence if attempt else {}
        if not unpaid_confirmed or not evidence.get('unpaid_confirmed') or not evidence.get('evidence') or not order.remote_session_id:
            raise ValueError('替换需要明确失败证据及人工未扣款确认；现有证据不足')
        if RecoveryItem.objects.filter(order=order, replacement__isnull=False).count() >= 3:
            raise ValueError('原会话人工替换最多三次')
    elif attempt and attempt.no_retry:
        raise ValueError('已越过提交边界；只能核实原订单，不能重新提交')
    if order.recovery_items.filter(batch__status__in=['scheduled', 'running']).exists():
        raise ValueError('订单已有待执行补单')
    if order.status not in ('queued', 'link_created', 'awaiting_payment', 'failed'):
        raise ValueError('订单当前不可补单')

@transaction.atomic
def preview_recovery(order_ids, mode, *, unpaid_confirmed=False):
    if mode not in ('links', 'pay') or not 1 <= len(order_ids) <= 100 or len(set(map(str, order_ids))) != len(order_ids):
        raise ValueError('请选择 1 到 100 个不同订单及有效模式')
    rows = []
    for order_id in order_ids:
        try: order = Order.objects.get(pk=order_id)
        except Order.DoesNotExist: raise ValueError('订单不存在') from None
        validate(order, mode, unpaid_confirmed)
        rows.append(fingerprint(order))
    return RecoveryPreview.objects.create(mode=mode, snapshot=rows, unpaid_confirmed=unpaid_confirmed,
        config_digest=config_fingerprint(), expires_at=timezone.now()+timedelta(minutes=10))

@transaction.atomic
def start_recovery(preview_id, actor_id):
    actor = get_user_model().objects.get(pk=actor_id)
    if not actor.is_active or not actor.is_staff or not actor.has_perm('gift.change_order'):
        raise PermissionError('没有补单权限')
    preview = RecoveryPreview.objects.get(pk=preview_id)
    if preview.expires_at <= timezone.now() or preview.config_digest != config_fingerprint():
        raise ValueError('预览已过期或配置改变，请重新预览')
    if RecoveryBatch.objects.filter(preview=preview).exists(): raise ValueError('预览已使用')
    orders = []
    for row in preview.snapshot:
        order = Order.objects.get(pk=row['id'])
        if fingerprint(order) != row: raise ValueError('订单已改变，请重新预览')
        validate(order, preview.mode, preview.unpaid_confirmed)
        orders.append(order)
    batch = RecoveryBatch.objects.create(preview=preview, actor_id=actor_id)
    RecoveryItem.objects.bulk_create([RecoveryItem(batch=batch, order=o, position=i) for i, o in enumerate(orders)])
    AuditEvent.objects.create(kind='recovery_scheduled', details={'batch_id': str(batch.pk), 'actor_id': actor_id, 'mode': preview.mode})
    return batch

@transaction.atomic
def stop_recovery(batch_id):
    batch = RecoveryBatch.objects.get(pk=batch_id)
    if batch.status in ('scheduled', 'running'):
        batch.stop_requested = True
        if batch.status == 'scheduled': batch.status = 'stopped'
        batch.save()
        AuditEvent.objects.create(kind='recovery_stop_requested', details={'batch_id': str(batch.pk)})

@transaction.atomic
def interrupt_recovery():
    """Only abandoned leases are interrupted; another worker's live lease is protected."""
    for batch in RecoveryBatch.objects.filter(status='running').filter(Q(lease_expires_at__lte=timezone.now()) | Q(lease_expires_at__isnull=True)):
        batch.status = 'interrupted'; batch.save(update_fields=['status'])
        batch.items.filter(status='running').update(status='interrupted')
        AuditEvent.objects.create(kind='recovery_interrupted', details={'batch_id': str(batch.pk)})

def process_recovery(provider):
    interrupt_recovery()
    with transaction.atomic():
        batch = RecoveryBatch.objects.filter(status='scheduled', stop_requested=False).order_by('created_at').first()
        if not batch: return False
        if batch.preview.config_digest != config_fingerprint():
            batch.status = 'interrupted'; batch.save(update_fields=['status']); return True
        item = batch.items.filter(status='pending').order_by('position').first()
        if not item:
            batch.status = 'completed'; batch.save(update_fields=['status']); return True
        if provider.mode != item.order.mode: raise ValueError('provider mode mismatch')
        batch.status = 'running'
        batch.lease_expires_at = timezone.now() + timedelta(seconds=300)
        batch.save(update_fields=['status', 'lease_expires_at'])
        item.status = 'running'; item.save(update_fields=['status'])
    try:
        order = Order.objects.get(pk=item.replacement_id or item.order_id)
        original = next(row for row in batch.preview.snapshot if row['id'] == str(item.order_id))
        if fingerprint(order) != (item.work_snapshot or original): raise ValueError('订单已改变')
        if order.status == 'failed':
            if not _compatible(order, provider): raise ValueError('原会话模式不符')
            result = provider.inspect(_checkout(order))
            if result.status != 'failed' or result.no_retry or not result.unpaid_confirmed or not result.evidence:
                raise ValueError('商户未提供原会话明确失败和未扣款证据')
            with transaction.atomic():
                if not RecoveryBatch.objects.filter(pk=batch.pk, status='running', lease_expires_at__gt=timezone.now()).exists():
                    return True
                if fingerprint(Order.objects.get(pk=order.pk)) != original: raise ValueError('原订单已改变')
                if RecoveryItem.objects.filter(order=order, replacement__isnull=False).count() >= 3: raise ValueError('替换次数超限')
                replacement = internal_order(order.code.plan, order.username, mode=order.mode, frozen=order.plan_snapshot)
                item.replacement = replacement; item.save(update_fields=['replacement'])
                AuditEvent.objects.create(order=order, kind='checkout_archived_for_replacement', details={
                    'session_id': order.remote_session_id, 'payment_url': order.payment_url, 'replacement_id': str(replacement.pk),
                    'actor_id': batch.actor_id, 'evidence': result.evidence, 'unpaid_confirmed': True})
                order = replacement
        if not RecoveryBatch.objects.filter(pk=batch.pk, status='running', lease_expires_at__gt=timezone.now()).exists():
            return True
        process_next(provider, order_id=order.pk, links_only=batch.preview.mode == 'links')
        order.refresh_from_db()
        complete = order.status in ('awaiting_payment', 'succeeded', 'failed', 'review', 'cancelled')
        with transaction.atomic():
            if not RecoveryBatch.objects.filter(pk=batch.pk, status='running', lease_expires_at__gt=timezone.now()).exists():
                return True
            item.status = 'completed' if complete else 'pending'
            item.work_snapshot = fingerprint(order)
            item.save(update_fields=['status', 'work_snapshot'])
    except Exception:
        with transaction.atomic():
            if not RecoveryBatch.objects.filter(pk=batch.pk, status='running', lease_expires_at__gt=timezone.now()).exists():
                return True
            item.status = 'blocked'; item.save(update_fields=['status'])
            AuditEvent.objects.create(order=item.order, kind='recovery_blocked',
                details={'batch_id': str(batch.pk), 'reason': 'recovery_validation_or_provider_failure'})
    except BaseException:
        RecoveryBatch.objects.filter(pk=batch.pk).update(status='interrupted')
        RecoveryItem.objects.filter(pk=item.pk).update(status='interrupted')
        raise
    finally:
        with transaction.atomic():
            batch.refresh_from_db()
            if batch.status == 'running' and batch.lease_expires_at <= timezone.now():
                batch.status = 'interrupted'
                batch.save(update_fields=['status'])
                batch.items.filter(status='running').update(status='interrupted')
            if batch.status != 'interrupted':
                batch.status = 'stopped' if batch.stop_requested else ('completed' if not batch.items.filter(status='pending').exists() else 'scheduled')
                batch.lease_expires_at = None
                batch.save(update_fields=['status', 'lease_expires_at'])
    return True
