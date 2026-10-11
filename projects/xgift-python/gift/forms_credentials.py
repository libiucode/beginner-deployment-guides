"""Allowlisted encrypted credential schemas; card data is never a configuration type."""
import json
import re
from urllib.parse import urlsplit
from django.core.exceptions import ValidationError

CREDENTIAL_TYPES = [('x-auth', 'X 会话'), ('stripe-key', '支付发布密钥'), ('proxy', '代理出口')]


def _endpoint(value):
    if not isinstance(value, str) or any(c in value for c in '\r\n'):
        raise ValueError
    parsed = urlsplit(value)
    if parsed.scheme not in {'http', 'https', 'socks5', 'socks5h'} or not parsed.hostname or not parsed.port:
        raise ValueError
    if parsed.path not in {'', '/'} or parsed.query or parsed.fragment:
        raise ValueError


def validate_credential(name, value):
    """Return canonical serialized values, never include submitted secrets in errors."""
    try:
        if name == 'stripe-key':
            if not re.fullmatch(r'pk_live_[A-Za-z0-9]{8,}', value):
                raise ValueError
            return value
        data = json.loads(value)
        if not isinstance(data, dict):
            raise ValueError
        if name == 'x-auth':
            if set(data) != {'auth_token', 'ct0', 'authorization', 'user_agent'}:
                raise ValueError
            for item in data.values():
                if not isinstance(item, str) or not item.strip() or len(item) > 2048 or any(c in item for c in '\r\n{}[]'):
                    raise ValueError
        elif name == 'proxy':
            if set(data) - {'x_region', 'payment_outbounds'}:
                raise ValueError
            if data.get('x_region') is not None:
                _endpoint(data['x_region'])
            outbounds = data.get('payment_outbounds', [])
            if not isinstance(outbounds, list) or len(outbounds) > 100:
                raise ValueError
            names = set()
            for outbound in outbounds:
                if not isinstance(outbound, dict) or set(outbound) != {'name', 'url'}:
                    raise ValueError
                label = outbound['name']
                if not isinstance(label, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', label) or label in names:
                    raise ValueError
                names.add(label)
                if label == 'direct':
                    if outbound['url'] is not None:
                        raise ValueError
                else:
                    _endpoint(outbound['url'])
        else:
            raise ValueError
        return json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    except (ValueError, TypeError, KeyError):
        raise ValidationError('配置格式无效。请仅填写所选类型允许的字段，不得填写卡号或安全验证码。') from None
