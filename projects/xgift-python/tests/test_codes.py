from concurrent.futures import ThreadPoolExecutor
import pytest
from django.db import connections

pytestmark = pytest.mark.django_db(transaction=True)

@pytest.fixture
def catalog(tmp_path, settings):
    from gift.models import Plan
    settings.XGIFT_KEY_FILE = tmp_path / 'master.key'
    settings.XGIFT_KEY_FILE.write_bytes(b'a' * 32)
    return Plan.objects.create(name='年度', months=12, amount_minor=100, currency='USD', merchant='merchant', product_id='premium')

def issue(plan):
    from gift.services.codes import generate_codes
    return generate_codes(plan.pk, 1, None)[0]

def test_encrypted_code(catalog):
    from gift.models import Code
    from gift.services.vault import unseal
    value = issue(catalog)
    row = Code.objects.get()
    assert value not in str(row.__dict__)
    assert unseal(bytes(row.ciphertext), 'redeem-code').decode() == value

def test_replay_and_binding(catalog):
    from gift.services.orders import redeem
    value = issue(catalog)
    first = redeem(value, '@Alice')
    assert redeem(value, 'alice').pk == first.pk
    with pytest.raises(ValueError):
        redeem(value, 'Bob')
    assert first.access_token
    assert redeem(value, 'alice').access_token is None

def test_disabled(catalog):
    from gift.models import Code, Order
    from gift.services.orders import redeem
    value = issue(catalog)
    Code.objects.update(enabled=False)
    with pytest.raises(ValueError):
        redeem(value, 'alice')
    assert Order.objects.count() == 0

def test_snapshot_and_validation(catalog):
    from gift.services.orders import redeem
    from gift.services.codes import generate_codes
    value = issue(catalog)
    for username in ['bad/name', '', 'a' * 16]:
        with pytest.raises(ValueError):
            redeem(value, username)
    for count in [0, 1001, True]:
        with pytest.raises(ValueError):
            generate_codes(catalog.pk, count, None)
    order = redeem(value, 'alice')
    catalog.amount_minor = 200
    catalog.save()
    order.refresh_from_db()
    assert order.plan_snapshot['amount_minor'] == 100
    order.plan_snapshot['amount_minor'] = 200
    with pytest.raises(ValueError):
        order.save()

def test_concurrent_file_database(catalog):
    from gift.models import Order
    from gift.services.orders import redeem
    assert 'memory' not in str(connections['default'].settings_dict['NAME'])
    value = issue(catalog)
    def submit(_):
        try:
            return redeem(value, 'Alice').pk
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(submit, range(12)))
    assert len(set(results)) == 1
    assert Order.objects.count() == 1

def test_snapshot_database_guard(catalog):
    from django.db import IntegrityError, transaction
    from gift.models import Order
    from gift.services.orders import redeem
    order = redeem(issue(catalog), 'alice')
    with pytest.raises(IntegrityError), transaction.atomic():
        Order.objects.filter(pk=order.pk).update(plan_snapshot={'amount_minor': 1})
    Order.objects.filter(pk=order.pk).update(status=Order.Status.CHECKING, token_digest='a' * 64)
    order.refresh_from_db()
    assert order.status == Order.Status.CHECKING
    assert order.token_digest == 'a' * 64
