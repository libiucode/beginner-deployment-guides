"""Explicit HTTP/SOCKS exits. Never consult environment proxy settings."""
import time
import httpx
from gift.forms_credentials import _endpoint


def client(endpoint=None, *, transport=None):
    if endpoint is not None:
        _endpoint(endpoint)
    return httpx.Client(proxy=endpoint, transport=transport, trust_env=False,
                        follow_redirects=False, timeout=httpx.Timeout(35, connect=15))


class RoutePool:
    """Deterministic first-available selection; writes never fail over or replay."""
    def __init__(self, routes=(), *, clock=time.monotonic, cooldown=60):
        self.routes = list(routes) or [{'name':'direct','url':None}]
        self.clock, self.cooldown, self.until = clock, cooldown, {}

    def choose(self):
        for route in self.routes:
            if self.until.get(route['name'], 0) <= self.clock():
                return route.copy()
        raise ValueError('all configured payment routes are cooling down')

    def failed(self, name):
        self.until[name] = self.clock() + self.cooldown


def json_response(response):
    if response.status_code != 200 or len(response.content) > 2 << 20:
        raise ValueError('remote response rejected')
    try:
        body = response.json()
    except ValueError:
        raise ValueError('remote response is not JSON') from None
    if not isinstance(body, dict) or body.get('errors') or body.get('error'):
        raise ValueError('remote operation rejected')
    return body
