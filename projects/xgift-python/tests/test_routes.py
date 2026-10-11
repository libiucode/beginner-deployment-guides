import pytest
from gift.integrations.routes import RoutePool, client
from gift.integrations.x_client import XClient
from gift.integrations.stripe_client import guard_page

def test_pool_cooldown():
    clock = [0]
    pool = RoutePool([{'name':'a','url':'http://localhost:8080'}, {'name':'direct','url':None}], clock=lambda: clock[0])
    assert pool.choose()['name'] == 'a'
    pool.failed('a')
    assert pool.choose()['name'] == 'direct'
    clock[0] = 61
    assert pool.choose()['name'] == 'a'

def test_invalid_cookie():
    with pytest.raises(ValueError):
        XClient({'auth_token':'a;evil','ct0':'b','authorization':'Bearer abc','user_agent':'agent'})

def test_environment_proxy_disabled(monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY','http://unexpected.invalid:1234')
    http=client()
    assert http._mounts == {}
    assert http.follow_redirects is False
    http.close()

@pytest.mark.parametrize('endpoint',['http://127.0.0.1:8080','socks5://127.0.0.1:1080','socks5h://127.0.0.1:1080'])
def test_http_and_socks_clients(endpoint):
    http=client(endpoint)
    assert http.follow_redirects is False
    http.close()
