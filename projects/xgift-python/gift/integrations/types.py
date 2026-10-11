"""Immutable provider contract; never contains card data or CVC."""
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

@dataclass(frozen=True)
class PlanSnapshot:
    plan_id: int
    name: str
    months: int
    amount_minor: int
    currency: str
    merchant: str
    product_id: str

@dataclass(frozen=True)
class OrderSnapshot:
    id: UUID
    username: str
    recipient: str
    plan: PlanSnapshot

@dataclass(frozen=True)
class Quote:
    merchant: str
    product_id: str
    months: int
    amount_minor: int
    currency: str

@dataclass(frozen=True)
class Checkout:
    session_id: str
    payment_url: str
    quote: Quote

@dataclass(frozen=True)
class PaymentResult:
    status: Literal['unpaid', 'succeeded', 'failed', 'unknown', 'awaiting_payment']
    no_retry: bool = False
    unpaid_confirmed: bool = False
    evidence: str = ''

class Provider(Protocol):
    mode: Literal['simulated', 'live']
    def eligibility(self, username: str) -> str: ...
    def quote(self, recipient: str, plan: PlanSnapshot) -> Quote: ...
    def create_checkout(self, order: OrderSnapshot) -> Checkout: ...
    def inspect(self, checkout: Checkout) -> PaymentResult: ...
    def confirm(self, checkout: Checkout, payment_method: str, key: str) -> PaymentResult: ...
