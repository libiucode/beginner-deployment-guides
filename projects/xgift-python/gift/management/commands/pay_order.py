"""Explicit local-administrator authorization; never accept card details as flags."""
from getpass import getpass
import sys
from django.core.management.base import BaseCommand, CommandError
from gift.models import Order, PaymentAttempt
from gift.integrations.live import configured_mode, provider_for_mode
from gift.services.queue import process_next

class Command(BaseCommand):
    help='Authorize one live order with transient card/CVC input (trusted local administrator only).'

    def add_arguments(self, parser):
        parser.add_argument('order_id')

    def handle(self,*args,**options):
        if configured_mode()!='live' or not sys.stdin.isatty():
            raise CommandError('A live interactive administrator console is required.')
        try:
            order=Order.objects.get(pk=options['order_id'],mode='live',status__in=('queued','awaiting_payment','link_created'))
        except (Order.DoesNotExist,ValueError):
            raise CommandError('Order is not eligible for manual authorization.') from None
        if PaymentAttempt.objects.filter(order=order,no_retry=True).exists():
            raise CommandError('Existing payment attempt forbids another submission; reconcile first.')
        p=order.plan_snapshot
        self.stdout.write(f'Order {order.pk}, @{order.username}, {p["amount_minor"]} {p["currency"]} minor units, {p["months"]} months, merchant {p["merchant"]}')
        if input('Type the complete order ID to authorize this exact payment: ').strip()!=str(order.pk):
            raise CommandError('Authorization cancelled.')
        card={}
        provider=None
        try:
            for field in ('number','exp_month','exp_year','cvc','name','email'):
                card[field]=getpass(field+': ')
            provider=provider_for_mode(card=card,order_id=order.pk)
            process_next(provider,order_id=order.pk)
            order.refresh_from_db()
            self.stdout.write('Order state: '+order.status+'; no automatic payment replay is permitted.')
        except Exception:
            raise CommandError('Manual workflow stopped; inspect order status before further action.') from None
        finally:
            card.clear()
            if provider is not None:provider.close()
