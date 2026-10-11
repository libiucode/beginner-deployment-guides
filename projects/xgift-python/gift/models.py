from django.db import models

class Secret(models.Model):
    name = models.CharField("名称", max_length=120, unique=True)
    ciphertext = models.BinaryField("密文")

    class Meta:
        verbose_name = "加密配置"
        verbose_name_plural = "加密配置"

import uuid


class Plan(models.Model):
    name = models.CharField('套餐名称', max_length=120)
    months = models.PositiveIntegerField('月数')
    amount_minor = models.PositiveBigIntegerField('金额（最小货币单位）')
    currency = models.CharField('币种', max_length=3)
    merchant = models.CharField('商户', max_length=200)
    product_id = models.CharField('远端套餐', max_length=200)
    enabled = models.BooleanField('启用', default=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(months__gt=0), name='plan_positive_months'), models.CheckConstraint(condition=models.Q(amount_minor__gt=0), name='plan_positive_amount')]


class Folder(models.Model):
    name = models.CharField('批次名称', max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)


class Code(models.Model):
    class Meta:
        permissions = [('export_code', '导出完整兑换码'), ('reveal_code', '查看完整兑换码')]

    digest = models.CharField(max_length=64, unique=True)
    ciphertext = models.BinaryField('加密兑换码')
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT)
    folder = models.ForeignKey(Folder, null=True, blank=True, on_delete=models.PROTECT)
    enabled = models.BooleanField('启用', default=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Order(models.Model):
    class Status(models.TextChoices):
        QUEUED = 'queued', '排队'
        CHECKING = 'checking', '核验中'
        LINK_CREATED = 'link_created', '链接已创建'
        AWAITING_PAYMENT = 'awaiting_payment', '等待付款'
        PAYMENT_READY = 'payment_ready', '付款准备'
        PAYMENT_SUBMITTED = 'payment_submitted', '付款已提交'
        SUCCEEDED = 'succeeded', '成功'
        FAILED = 'failed', '明确失败'
        REVIEW = 'review', '待核实'
        CANCELLED = 'cancelled', '取消'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.OneToOneField(Code, on_delete=models.PROTECT)
    mode = models.CharField('订单模式', max_length=12, default='simulated', choices=[('simulated', '模拟'), ('live', '真实')])
    username = models.CharField('客户用户名', max_length=15)
    token_digest = models.CharField(max_length=64, unique=True)
    plan_snapshot = models.JSONField('冻结套餐')
    status = models.CharField(max_length=32, choices=Status, default=Status.QUEUED)
    recipient_id = models.CharField(max_length=200, blank=True)
    remote_session_id = models.CharField(max_length=200, blank=True)
    payment_url = models.URLField(max_length=2000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    access_token = None  # 仅新订单返回对象持有一次性明文；不属于数据库字段。

    def save(self, *args, **kwargs):
        if not self._state.adding:
            old = type(self).objects.get(pk=self.pk)
            if old.plan_snapshot != self.plan_snapshot or old.code_id != self.code_id or old.username != self.username or old.mode != self.mode:
                raise ValueError('订单的套餐、客户和兑换码不可修改')
        super().save(*args, **kwargs)


class AuditEvent(models.Model):
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.PROTECT)
    kind = models.CharField('操作类型', max_length=80)
    details = models.JSONField('非敏感操作详情', default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class TaskLease(models.Model):
    order = models.OneToOneField(Order, on_delete=models.CASCADE)
    owner = models.UUIDField(null=True)
    expires_at = models.DateTimeField(null=True)


class PaymentAttempt(models.Model):
    order = models.OneToOneField(Order, on_delete=models.PROTECT)
    key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    state = models.CharField(max_length=32, default='prepared')
    submitted_at = models.DateTimeField(null=True)
    no_retry = models.BooleanField(default=False)
    retry_forbidden = models.BooleanField(default=False)
    result_evidence = models.JSONField(default=dict)
    simulated_result = models.CharField(max_length=32, blank=True)


class PaymentLock(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    order = models.ForeignKey(Order, null=True, on_delete=models.PROTECT)
    next_submit_at = models.DateTimeField(null=True)


class LinkTicket(models.Model):
    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name='link_ticket')
    reconcile_requested = models.BooleanField(default=False)


class RecoveryPreview(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mode = models.CharField(max_length=8)
    snapshot = models.JSONField()
    config_digest = models.CharField(max_length=64)
    unpaid_confirmed = models.BooleanField(default=False)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def mode_label(self):
        return '仅生成链接' if self.mode == 'links' else '模拟付款'


class RecoveryBatch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    preview = models.OneToOneField(RecoveryPreview, on_delete=models.PROTECT)
    actor_id = models.PositiveBigIntegerField()
    status = models.CharField(max_length=20, default='scheduled')
    stop_requested = models.BooleanField(default=False)
    lease_expires_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def status_label(self):
        return {'scheduled': '已排队', 'running': '正在处理', 'stopped': '已停止',
            'interrupted': '已中断，须重新预览', 'completed': '处理结束'}.get(self.status, self.status)


class RecoveryItem(models.Model):
    batch = models.ForeignKey(RecoveryBatch, on_delete=models.PROTECT, related_name='items')
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='recovery_items')
    replacement = models.OneToOneField(Order, null=True, on_delete=models.PROTECT, related_name='replacement_item')
    position = models.PositiveIntegerField()
    status = models.CharField(max_length=20, default='pending')
    work_snapshot = models.JSONField(default=dict)

    @property
    def status_label(self):
        return {'pending': '待处理', 'running': '正在处理', 'completed': '处理结束',
            'blocked': '核验未通过', 'interrupted': '已中断'}.get(self.status, self.status)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['batch', 'order'], name='unique_batch_order')]
