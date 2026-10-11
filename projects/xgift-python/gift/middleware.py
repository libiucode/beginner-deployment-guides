from django.http import HttpResponse
from gift.views import limited

class LoginRateLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
    def __call__(self, request):
        if request.path == '/admin/login/' and request.method == 'POST' and limited(request, 'login', 10):
            response = HttpResponse('登录尝试过于频繁，请一分钟后再试。', status=429)
            response['Retry-After'] = '60'
            return response
        return self.get_response(request)


class TrustedProxyMiddleware:
    """Trust only a single Caddy-overwritten header from loopback."""
    def __init__(self, get_response): self.get_response = get_response
    def __call__(self, request):
        import ipaddress
        if request.META.get('REMOTE_ADDR') not in ('127.0.0.1', '::1'):
            return HttpResponse('Untrusted proxy',status=400)
        try: address = str(ipaddress.ip_address(request.META.get('HTTP_X_XGIFT_CLIENT_IP','')))
        except ValueError: return HttpResponse('Invalid proxy client',status=400)
        request.META['REMOTE_ADDR']=address
        return self.get_response(request)
