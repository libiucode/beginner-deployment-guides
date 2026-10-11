"""短事务原子绑定兑换码与客户，不调用远端。"""
import re
import secrets
from django.db import transaction
from gift.models import AuditEvent, Code, Order
from gift.services.catalog import snapshot
from gift.services.codes import digest
from gift.integrations.live import configured_mode


def normalize_username(username):
    username = username.strip().removeprefix('@')
    if not re.fullmatch(r'[A-Za-z0-9_]{1,15}', username):
        raise ValueError('请输入有效的 X 用户名（最多 15 个字母、数字或下划线）')
    return username.lower()


def redeem(code: str, username: str) -> Order:
    username = normalize_username(username)
    with transaction.atomic():
        try:
            row = Code.objects.select_related('plan').get(digest=digest(code.strip()))
        except Code.DoesNotExist:
            raise ValueError('兑换码无效') from None
        existing = Order.objects.filter(code=row).first()
        if existing:
            if existing.username != username:
                raise ValueError('兑换码已绑定其他客户')
            return existing
        if not row.enabled:
            raise ValueError('兑换码已停用')
        token = secrets.token_urlsafe(32)
        order = Order.objects.create(code=row, username=username, token_digest=digest(token), plan_snapshot=snapshot(row.plan), mode=configured_mode())
        AuditEvent.objects.create(order=order, kind='order_created', details={'status': order.status})
        order.access_token = token
        return order


def reconcile(order_id, provider) -> Order:
    """Query an existing submitted payment; never submit it again."""
    from gift.services.queue import reconcile as reconcile_payment
    return reconcile_payment(order_id, provider)
