from django.core.management.base import BaseCommand, CommandError
from gift.integrations.live import provider_for_mode
from gift.services.queue import reconcile

class Command(BaseCommand):
    help='Read and reconcile one original order; never collect cards or submit payment.'

    def add_arguments(self,parser):
        parser.add_argument('order_id')

    def handle(self,*args,**options):
        provider=None
        try:
            provider=provider_for_mode()
            order=reconcile(options['order_id'],provider)
            self.stdout.write('Order state: '+order.status)
        except Exception:
            raise CommandError('Reconciliation stopped; inspect configuration and order mode.') from None
        finally:
            close=getattr(provider,'close',None)
            if close:close()
