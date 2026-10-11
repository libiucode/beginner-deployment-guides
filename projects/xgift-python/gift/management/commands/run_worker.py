import time
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from gift.integrations.live import provider_for_mode
from gift.services.queue import process_next
from gift.services.recovery import interrupt_recovery, process_recovery
from gift.services.links import reconcile_public_link

class Command(BaseCommand):
    help = 'Run worker in configured mode; live mode creates links but never collects card details.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--stop-file')

    def handle(self, *args, **options):
        interrupt_recovery()
        while not (options['stop_file'] and Path(options['stop_file']).exists()):
            try:
                provider = provider_for_mode()  # reload and validate each order workflow
            except Exception:
                raise CommandError('Worker configuration invalid; no order processed.') from None
            try:
                if not reconcile_public_link(provider) and not process_recovery(provider):
                    process_next(provider)
            finally:
                close=getattr(provider,'close',None)
                if close: close()
            if options['once']:
                return
            time.sleep(1)
