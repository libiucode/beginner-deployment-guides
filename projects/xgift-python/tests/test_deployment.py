import os
import sqlite3
import pytest
from io import StringIO
from django.core.management import call_command
from django.core.management.base import CommandError


def test_environment_spaces_and_typed(tmp_path):
    from deploy.environment import load_environment, boolean
    path = tmp_path / 'folder with spaces'
    path.mkdir()
    env = path / 'server.env'
    env.write_text('XGIFT_KEY_FILE="C:/data with spaces/master.key"\nDJANGO_DEBUG=false\n', encoding='utf-8')
    target = {}
    load_environment(env, target)
    assert target['XGIFT_KEY_FILE'] == 'C:/data with spaces/master.key'
    assert boolean(target['DJANGO_DEBUG']) is False
    with pytest.raises(ValueError): boolean('maybe')

@pytest.mark.django_db
def test_lost_key_with_codes(tmp_path, settings):
    from gift.models import Plan, Code
    settings.XGIFT_KEY_FILE = tmp_path / 'lost.key'
    plan = Plan.objects.create(name='test',months=1,amount_minor=1,currency='USD',merchant='test',product_id='test')
    Code.objects.create(plan=plan,digest='a'*64,ciphertext=b'existing')
    with pytest.raises(CommandError, match='主密钥缺失'):
        call_command('setup_xgift',password='Initial-password-123!',stdout=StringIO())
    assert not settings.XGIFT_KEY_FILE.exists()


def test_backup_snapshot_key_and_review(tmp_path, capsys):
    from deploy.backup import backup
    root = tmp_path / 'data with spaces'; root.mkdir()
    db = root / 'source.sqlite3'; key = root / 'master.key'; key.write_bytes(os.urandom(32))
    with sqlite3.connect(db) as connection:
        connection.execute('CREATE TABLE orders (status TEXT, secret TEXT)')
        connection.execute('INSERT INTO orders VALUES (?,?)',('review','private-credential'))
    destination = root / 'backup'
    backup(db,key,destination)
    with sqlite3.connect(destination / 'db.sqlite3') as restored:
        assert restored.execute('SELECT status FROM orders').fetchone()[0] == 'review'
        assert restored.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert (destination / 'master.key').read_bytes() == key.read_bytes()
    assert 'private-credential' not in capsys.readouterr().out
    with pytest.raises(FileExistsError): backup(db,key,destination)

def test_proxy_does_not_trust_spoofed_forwarding():
    from gift.middleware import TrustedProxyMiddleware
    from django.test import RequestFactory
    from django.http import HttpResponse
    middleware=TrustedProxyMiddleware(lambda request: HttpResponse(request.META['REMOTE_ADDR']))
    request=RequestFactory().get('/',REMOTE_ADDR='198.51.100.4',HTTP_X_XGIFT_CLIENT_IP='1.2.3.4')
    assert middleware(request).status_code==400
    request=RequestFactory().get('/',REMOTE_ADDR='127.0.0.1',HTTP_X_XGIFT_CLIENT_IP='198.51.100.4',HTTP_X_FORWARDED_FOR='1.2.3.4')
    assert middleware(request).content==b'198.51.100.4'

@pytest.mark.django_db
def test_live_configuration_missing_fails_closed(settings):
    from gift.integrations.live import provider_for_mode
    settings.XGIFT_MODE='live'
    with pytest.raises(ValueError): provider_for_mode()

@pytest.mark.django_db(transaction=True)
def test_real_order_timeout_backup_restore_retains_review(tmp_path, settings):
    from deploy.backup import backup
    from django.db import connection
    from gift.models import Plan,Code,Order,PaymentAttempt
    from gift.services.queue import process_next
    from gift.integrations.simulated import SimulatedProvider
    settings.XGIFT_KEY_FILE=tmp_path/'master.key';settings.XGIFT_KEY_FILE.write_bytes(os.urandom(32))
    plan=Plan.objects.create(name='test',months=1,amount_minor=100,currency='USD',merchant='test',product_id='one')
    code=Code.objects.create(plan=plan,digest='c'*64,ciphertext=b'encrypted')
    order=Order.objects.create(code=code,username='alice',token_digest='d'*64,plan_snapshot=dict(plan_id=plan.pk,name=plan.name,months=1,amount_minor=100,currency='USD',merchant='test',product_id='one'))
    first=SimulatedProvider(outcome='timeout');process_next(first)
    order.refresh_from_db();assert order.status=='review'
    destination=tmp_path/'restored with spaces'
    backup(connection.settings_dict['NAME'],settings.XGIFT_KEY_FILE,destination)
    with sqlite3.connect(destination/'db.sqlite3') as restored:
        assert restored.execute('SELECT status FROM gift_order WHERE id=?',(order.id.hex,)).fetchone()[0]=='review'
        assert restored.execute('SELECT state FROM gift_paymentattempt').fetchone()[0]=='review'
    fresh=SimulatedProvider();assert not process_next(fresh)
    assert fresh.confirm_calls==0

def test_waitress_drains_inflight_request_and_serves_static(tmp_path):
    import socket,subprocess,sys,time,threading,urllib.request
    static=tmp_path/'static with spaces';static.mkdir();(static/'test.css').write_text('body{color:blue}')
    stop=tmp_path/'stop';started=tmp_path/'started'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    source="""
import os,time
from pathlib import Path
os.environ['DJANGO_SETTINGS_MODULE']='config.settings'
import django;django.setup()
from django.conf import settings
settings.STATIC_ROOT=os.environ['QA_STATIC']
from django.core import wsgi
def app(environ,start_response):
    Path(os.environ['QA_STARTED']).touch()
    time.sleep(.5)
    start_response('200 OK',[('Content-Type','text/plain')])
    return [b'finished']
wsgi.get_wsgi_application=lambda: app
from django.core.management import call_command
call_command('serve_xgift',port=int(os.environ['QA_PORT']),stop_file=os.environ['QA_STOP'])
"""
    env=os.environ.copy();env.update(QA_STATIC=str(static),QA_STARTED=str(started),QA_PORT=str(port),QA_STOP=str(stop))
    process=subprocess.Popen([sys.executable,'-c',source],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    result=[]
    try:
        for _ in range(100):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/static/test.css',timeout=1) as response:
                    assert response.read()==b'body{color:blue}'
                break
            except OSError: time.sleep(.05)
        else: pytest.fail('Waitress did not start')
        def request():
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/slow',timeout=5) as response: result.append(response.read())
        thread=threading.Thread(target=request);thread.start()
        for _ in range(100):
            if started.exists(): break
            time.sleep(.01)
        assert started.exists();stop.touch();thread.join(timeout=5)
        assert result==[b'finished']
        _,errors=process.communicate(timeout=10)
        assert process.returncode==0,errors.decode(errors='replace')
    finally:
        if process.poll() is None:
            stop.touch()
            process.wait(timeout=10)
