"""随机兑换码生成；明文仅返回调用者。"""
import hashlib
import secrets
from django.db import transaction
from gift.models import AuditEvent, Code, Folder, Plan
from gift.services.catalog import snapshot
from gift.services.vault import seal


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def generate_codes(plan_id: int, count: int, folder_id: int | None = None, *, actor_id: int | None = None) -> list[str]:
    if type(count) is not int or not 1 <= count <= 1000:
        raise ValueError('生成数量必须为 1 到 1000 的整数')
    with transaction.atomic():
        try:
            plan = Plan.objects.get(pk=plan_id)
            folder = Folder.objects.get(pk=folder_id) if folder_id is not None else None
        except (Plan.DoesNotExist, Folder.DoesNotExist):
            raise ValueError('套餐或批次不存在') from None
        snapshot(plan)
        values = [secrets.token_urlsafe(32) for _ in range(count)]
        Code.objects.bulk_create([Code(plan=plan, folder=folder, digest=digest(value), ciphertext=seal(value.encode(), 'redeem-code')) for value in values])
        AuditEvent.objects.create(kind='codes_generated', details={'plan_id': plan.pk, 'folder_id': folder_id, 'count': count, 'actor_id': actor_id})
    return values
