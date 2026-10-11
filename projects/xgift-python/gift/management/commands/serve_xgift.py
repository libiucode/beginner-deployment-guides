from django.core.management.base import BaseCommand, CommandError

class Command(BaseCommand):
    help='Serve collected static files and Django on loopback with Waitress'
    def add_arguments(self,parser):
        parser.add_argument('--port',type=int,default=8000)
        parser.add_argument('--stop-file',help='Private control file; create it to stop gracefully')
    def handle(self,*args,**options):
        from pathlib import Path
        import threading
        import time
        from waitress import create_server, wasyncore
        from whitenoise import WhiteNoise
        from django.conf import settings
        from django.core.wsgi import get_wsgi_application
        if not Path(settings.STATIC_ROOT).is_dir():
            raise CommandError('Run collectstatic first')
        stopping=threading.Event()
        app=WhiteNoise(get_wsgi_application(),root=settings.STATIC_ROOT,prefix='static/')
        def application(environ,start_response):
            if stopping.is_set():
                start_response('503 Service Unavailable',[('Content-Type','text/plain')])
                return [b'Server stopping']
            return app(environ,start_response)
        server=create_server(application,host='127.0.0.1',port=options['port'],threads=4)
        def drain():
            if stopping.is_set(): return
            stopping.set()
            server.close()  # stop listening; existing request channels remain open
            dispatcher=server.task_dispatcher
            while True:
                with dispatcher.lock:
                    idle=not dispatcher.queue and dispatcher.active_count==0
                if idle: break
                time.sleep(.1)
            dispatcher.shutdown()
            wasyncore.close_all(server._map)
        if options['stop_file']:
            def monitor():
                while not Path(options['stop_file']).exists(): time.sleep(.25)
                drain()
            threading.Thread(target=monitor,daemon=True).start()
        try:
            wasyncore.loop(timeout=1,map=server._map,use_poll=server.adj.asyncore_use_poll)
        except KeyboardInterrupt:
            drain()
        finally:
            if not stopping.is_set(): drain()
