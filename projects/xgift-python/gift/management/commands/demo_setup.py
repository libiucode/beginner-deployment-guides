"""首次体验用：创建模拟演示套餐与兑换码（仅模拟模式，不连接真实服务）。"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from gift.integrations.live import configured_mode
from gift.models import Code, Folder, Plan
from gift.services.catalog import snapshot
from gift.services.codes import generate_codes


class Command(BaseCommand):
    help = '创建模拟演示套餐与兑换码；仅 simulated 模式可用，不会连接 X 或支付服务。'

    def add_arguments(self, parser):
        parser.add_argument('--count', type=int, default=5, help='生成数量（1-1000，默认 5）')
        parser.add_argument('--plan-name', default='模拟演示套餐 · 1 个月')
        parser.add_argument('--months', type=int, default=1)
        parser.add_argument('--amount-minor', type=int, default=999, help='金额（最小货币单位，仅用于模拟）')
        parser.add_argument('--currency', default='USD')
        parser.add_argument('--folder', default='示例批次', help='放入的批次名称；留空表示未分组')
        parser.add_argument('--only-if-empty', action='store_true', help='已有兑换码时直接跳过，便于重复运行')

    def handle(self, *args, **options):
        if configured_mode() != 'simulated':
            raise CommandError('演示数据仅用于模拟模式；当前为 live，已拒绝执行。')

        if options['only_if_empty'] and Code.objects.exists():
            self.stdout.write('已存在兑换码，跳过演示数据创建。')
            return

        count = options['count']
        if not 1 <= count <= 1000:
            raise CommandError('生成数量必须为 1 到 1000 的整数。')
        folder_name = options['folder'].strip()

        with transaction.atomic():
            plan, _ = Plan.objects.get_or_create(
                name=options['plan_name'],
                defaults={
                    'months': options['months'],
                    'amount_minor': options['amount_minor'],
                    'currency': options['currency'].upper(),
                    'merchant': 'simulated-merchant',
                    'product_id': 'simulated-premium',
                    'enabled': True,
                },
            )
            if not plan.enabled:
                plan.enabled = True
                plan.save(update_fields=['enabled'])
            snapshot(plan)  # 复用与真实业务相同的配置校验
            folder = Folder.objects.get_or_create(name=folder_name)[0] if folder_name else None

        values = generate_codes(plan.pk, count, folder.pk if folder else None)

        self.stdout.write(self.style.SUCCESS(
            f'已准备模拟套餐「{plan.name}」，并生成 {len(values)} 个兑换码'
            f'（批次：{folder_name or "未分组"}）。'))
        self.stdout.write('以下兑换码仅用于本机模拟演示，请勿用于真实业务：')
        for value in values:
            self.stdout.write('  ' + value)
        self.stdout.write('')
        self.stdout.write('体验方法：打开 http://127.0.0.1:8000/ ，粘贴任一兑换码，'
                          'X 用户名随便填（例如 qa_customer），点击「确认兑换」。')
