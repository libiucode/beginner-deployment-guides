import pytest
from django.contrib.auth.models import User, Permission
from django.test import Client
from django.core.cache import cache
from gift.models import Plan, Folder, Code, AuditEvent
from gift.services.codes import generate_codes

pytestmark = pytest.mark.django_db

@pytest.fixture
def setup(tmp_path, settings):
    cache.clear()
    settings.XGIFT_KEY_FILE = tmp_path / 'key'
    settings.XGIFT_KEY_FILE.write_bytes(b'a' * 32)
    plan = Plan.objects.create(name='年度', months=12, amount_minor=100, currency='USD', merchant='m', product_id='p')
    generate_codes(plan.pk, 2)
    return plan

def test_operator_denies_anonymous_nonstaff_and_unprivileged_staff(client, setup):
    assert client.post('/operator/export').status_code == 302
    user = User.objects.create_user('customer', password='password')
    client.force_login(user)
    assert client.get('/operator/').status_code == 403
    user.is_staff = True; user.save()
    assert client.post('/operator/export').status_code == 403
    assert client.get('/admin/gift/secret/').status_code == 403

def test_operator_export_move_revoke_and_audit(client, setup):
    user = User.objects.create_superuser('admin', 'a@example.com', 'password')
    client.force_login(user)
    folder = Folder.objects.create(name='交付')
    ids = list(Code.objects.values_list('id', flat=True))
    assert client.post('/operator/', {'action':'move', 'codes':ids, 'folder':folder.pk}).status_code == 302
    assert Code.objects.filter(folder=folder).count() == 2
    response = client.post('/operator/export', {'folder':folder.pk})
    assert response.status_code == 200 and response['Content-Type'].startswith('text/csv')
    assert AuditEvent.objects.filter(kind='codes_exported').exists()
    assert client.post('/operator/', {'action':'revoke', 'codes':ids}).status_code == 302
    assert Code.objects.filter(enabled=True).count() == 0

def test_operator_csrf(setup):
    user = User.objects.create_superuser('admin', 'a@example.com', 'password')
    client = Client(enforce_csrf_checks=True); client.force_login(user)
    assert client.post('/operator/', {'action':'folder', 'name':'new'}).status_code == 403

def test_operator_generation_and_lookup(client, setup):
    user = User.objects.create_superuser('admin', 'a@example.com', 'password'); client.force_login(user)
    assert client.post('/operator/', {'action':'generate', 'plan': setup.pk, 'count':3}).status_code == 302
    assert Code.objects.count() == 5
    assert '运营工作台' in client.get('/operator/', {'q':'alice'}).content.decode()


def test_export_requires_csrf(setup):
    user = User.objects.create_superuser('admin', 'a@example.com', 'password')
    client = Client(enforce_csrf_checks=True); client.force_login(user)
    assert client.post('/operator/export').status_code == 403

def test_admin_login_rate_limit(client, setup):
    for _ in range(10):
        client.post('/admin/login/', {'username':'unknown', 'password':'incorrect'})
    assert client.post('/admin/login/', {'username':'unknown', 'password':'incorrect'}).status_code == 429

def test_code_detail_requires_reveal_permission_and_audits(client, setup):
    user = User.objects.create_user('staff', password='password', is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename='view_code'))
    client.force_login(user)
    code = Code.objects.first()
    from gift.services.vault import unseal
    value = unseal(bytes(code.ciphertext), 'redeem-code').decode()
    url = '/admin/gift/code/' + str(code.pk) + '/change/'
    assert value not in client.get(url).content.decode()
    user.user_permissions.add(Permission.objects.get(codename='reveal_code'))
    response = client.get(url)
    assert value in response.content.decode()
    assert 'no-store' in response['Cache-Control']
    assert AuditEvent.objects.filter(kind='code_revealed').exists()

def test_secret_admin_encrypted_write_only(client, setup):
    from gift.models import Secret
    user = User.objects.create_superuser('admin', 'a@example.com', 'password'); client.force_login(user)
    plaintext = 'pk_live_credentialnotforlogs'
    response = client.post('/admin/gift/secret/add/', {'name':'stripe-key', 'value':plaintext, '_save':'Save'})
    assert response.status_code == 302
    secret = Secret.objects.get()
    assert plaintext.encode() not in bytes(secret.ciphertext)
    assert plaintext not in client.get('/admin/gift/secret/'+str(secret.pk)+'/change/').content.decode()
    assert plaintext not in str(list(AuditEvent.objects.values()))

@pytest.mark.parametrize('name,value', [('cvc','123'), ('x-auth','{"auth_token":"a","ct0":"b","authorization":"Bearer x","user_agent":"ua","card":{"cvc":"123"}}'), ('proxy','{"endpoint":"http://localhost:8080","card":{"cvv":"123"}}'), ('stripe-key','{"cvc":"123"}')])
def test_secret_rejects_raw_card_payloads(client, setup, name, value):
    from gift.models import Secret
    user = User.objects.create_superuser('admin', 'a@example.com', 'password'); client.force_login(user)
    response = client.post('/admin/gift/secret/add/', {'name':name, 'value':value, '_save':'Save'})
    assert response.status_code == 200
    assert not Secret.objects.exists()

def test_secret_code_lookup_post_only(client, setup):
    user = User.objects.create_superuser('admin', 'a@example.com', 'password'); client.force_login(user)
    from gift.services.vault import unseal
    code = Code.objects.first(); value=unseal(bytes(code.ciphertext), 'redeem-code').decode()
    response=client.get('/operator/', {'q':value})
    assert value not in response.content.decode()
    response=client.post('/operator/', {'action':'lookup', 'code':value})
    assert response.status_code == 200
    assert list(response.context['codes']) == [code]
    assert value not in response.content.decode()

def test_invalid_code_edit_reveal_audited(client, setup):
    user=User.objects.create_superuser('admin', 'a@example.com', 'password'); client.force_login(user)
    code=Code.objects.first()
    response=client.post('/admin/gift/code/'+str(code.pk)+'/change/', {'plan':'invalid'})
    assert response.status_code == 200
    assert AuditEvent.objects.filter(kind='code_revealed', details__actor_id=user.pk).exists()

def test_generation_audit_actor(client, setup):
    user=User.objects.create_superuser('admin', 'a@example.com', 'password'); client.force_login(user)
    client.post('/operator/', {'action':'generate','plan':setup.pk,'count':1})
    assert AuditEvent.objects.filter(kind='codes_generated', details__actor_id=user.pk).exists()

@pytest.mark.parametrize('name,value', [
    ('x-auth', '{"auth_token":"token","ct0":"csrf","authorization":"Bearer key","user_agent":"Mozilla/5.0"}'),
    ('stripe-key', 'pk_live_abcdefghijklmnop'),
    ('proxy', '{"x_region":"socks5://localhost:1080","payment_outbounds":[{"name":"direct","url":null},{"name":"backup","url":"http://localhost:8080"}]}'),
    ('proxy', '{}'),
])
def test_approved_credentials(name, value):
    from gift.forms_credentials import validate_credential
    assert validate_credential(name, value)

@pytest.mark.parametrize('value', [
    '{"x_region":"http://localhost"}',
    '{"x_region":"ftp://localhost:21"}',
    '{"payment_outbounds":[{"name":"direct","url":"http://localhost:8080"}]}',
    '{"payment_outbounds":[{"name":"same","url":"http://localhost:8080"},{"name":"same","url":"http://localhost:8081"}]}',
    '{"payment_outbounds":[{"name":"node","url":"http://localhost:8080","cvv":"123"}]}',
    '{"x_region":"http://localhost:8080\\r\\nHeader: injected"}',
])
def test_proxy_schema_rejects_unsafe_fields(value):
    from django.core.exceptions import ValidationError
    from gift.forms_credentials import validate_credential
    with pytest.raises(ValidationError):
        validate_credential('proxy', value)

def test_code_lookup_requires_csrf(setup):
    user = User.objects.create_superuser('admin', 'a@example.com', 'password')
    client = Client(enforce_csrf_checks=True); client.force_login(user)
    assert client.post('/operator/', {'action':'lookup','code':'private'}).status_code == 403

def test_operator_distribution_uses_current_mode(client,setup,settings):
    from gift.models import Order
    from gift.services.catalog import snapshot
    codes=list(Code.objects.all())
    Order.objects.create(code=codes[0],username='alice',token_digest='a'*64,plan_snapshot=snapshot(setup),mode='simulated',status='succeeded')
    Order.objects.create(code=codes[1],username='bob',token_digest='b'*64,plan_snapshot=snapshot(setup),mode='live',status='queued')
    settings.XGIFT_MODE='live'
    client.force_login(User.objects.create_superuser('admin','a@example.com','password'))
    response=client.get('/operator/')
    assert response.context['successes']==0
    assert response.context['stats']==[{'status':'排队','count':1}]
