from .types import Checkout, PaymentResult, Quote

class SimulatedProvider:
    """Local demonstration only; no remote requests or real payment credentials."""
    mode = 'simulated'
    payment_method = 'simulated'

    def __init__(self, outcome='succeeded'):
        self.outcome = outcome
        self.confirm_calls = 0
        self.results = {}

    def eligibility(self, username):
        return 'simulated:' + username

    def quote(self, recipient, plan):
        return Quote(plan.merchant, plan.product_id, plan.months, plan.amount_minor, plan.currency)

    def create_checkout(self, order):
        return Checkout('simulated:' + str(order.id), 'https://example.invalid/simulated/' + str(order.id), self.quote(order.recipient, order.plan))

    def inspect(self, checkout):
        from gift.models import PaymentAttempt
        attempt = PaymentAttempt.objects.filter(order_id=checkout.session_id.removeprefix('simulated:')).first()
        if attempt and attempt.simulated_result:
            return PaymentResult(attempt.simulated_result)
        return self.results.get(checkout.session_id, PaymentResult('unpaid'))

    def confirm(self, checkout, payment_method, key):
        self.confirm_calls += 1
        from gift.models import PaymentAttempt
        if self.outcome in ('succeeded', 'crash', 'failed'):
            PaymentAttempt.objects.filter(key=key).update(simulated_result='succeeded' if self.outcome == 'crash' else self.outcome)
        if self.outcome == 'crash':
            raise SystemExit('simulated process interruption')
        if self.outcome == 'timeout':
            raise TimeoutError('simulated timeout')
        result = PaymentResult(self.outcome, self.outcome in ('failed', 'unknown'))
        self.results[checkout.session_id] = result
        return result
