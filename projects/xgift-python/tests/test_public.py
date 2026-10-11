import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.core.cache import cache
from gift.models import Plan, Code
from gift.services.codes import generate_codes

pytestmark = pytest.mark.django_db

@pytest.fixture
def issued(tmp_path, settings):
    cache.clear()
    settings.XGIFT_KEY_FILE = tmp_path / 'key'
    settings.XGIFT_KEY_FILE.write_bytes(b'a' * 32)
    plan = Plan.objects.create(name='年度', months=12, amount_minor=100, currency='USD', merchant='m', product_id='p')
    return generate_codes(plan.pk, 2)

def test_invalid_fields(client, issued):
    assert 'username' in client.post('/api/redeem', {'code': issued[0], 'username': 'a/b'}).json()['errors']
    assert 'code' in client.post('/api/redeem', {'code': 'invalid', 'username': 'alice'}).json()['errors']

def test_token_scope_rotation_and_filtered_status(client, issued):
    first = client.post('/api/redeem', {'code': issued[0], 'username': 'alice'}).json()
    second = client.post('/api/redeem', {'code': issued[1], 'username': 'bob'}).json()
    url = '/api/orders/' + first['id']
    assert client.get(url, HTTP_AUTHORIZATION='Bearer ' + second['access_token']).status_code == 403
    response = client.get(url, HTTP_AUTHORIZATION='Bearer ' + first['access_token'])
    assert response.status_code == 200
    assert set(response.json()) == {'id', 'username', 'status', 'status_label', 'plan', 'updated_at', 'mode'}
    replay = client.post('/api/redeem', {'code': issued[0], 'username': 'alice'}).json()
    assert replay['access_token'] != first['access_token']
    assert client.get(url, HTTP_AUTHORIZATION='Bearer ' + first['access_token']).status_code == 403

def test_rate_limit(client, issued):
    for _ in range(10):
        client.post('/api/redeem', {'code': 'bad', 'username': 'alice'})
    assert client.post('/api/redeem', {'code': 'bad', 'username': 'alice'}).status_code == 429

def test_public_csrf(issued):
    assert Client(enforce_csrf_checks=True).post('/api/redeem', {'code': issued[0], 'username': 'alice'}).status_code == 403

def test_wrong_binding_does_not_rotate(client, issued):
    first = client.post('/api/redeem', {'code':issued[0], 'username':'alice'}).json()
    assert client.post('/api/redeem', {'code':issued[0], 'username':'bob'}).status_code == 400
    assert client.get('/api/orders/'+first['id'], HTTP_AUTHORIZATION='Bearer '+first['access_token']).status_code == 200
