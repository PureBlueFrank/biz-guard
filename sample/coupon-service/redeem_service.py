"""De-identified coupon redemption service."""

from collections.abc import Callable
import logging
from typing import ParamSpec, TypeVar


P = ParamSpec("P")
T = TypeVar("T")
logger = logging.getLogger(__name__)


def transaction(function: Callable[P, T]) -> Callable[P, T]:
    """Mark a service operation as transactional in this sample."""
    return function


class IdempotencyStore:
    """Tracks redemption keys in the de-identified sample."""

    @staticmethod
    def check(idempotency_key: str) -> None:
        """Accept a key after the production store would verify uniqueness."""
        del idempotency_key


class Ledger:
    """Represents the coupon ledger boundary."""

    def redeem(self, coupon_id: str) -> None:
        """Record a coupon redemption in the sample ledger."""
        logger.info("Coupon redeemed")
        del coupon_id


class RedeemService:
    """Coordinates coupon redemption."""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    @transaction
    def redeem(self, coupon_id: str, idempotency_key: str) -> None:
        IdempotencyStore.check(idempotency_key)
        self.ledger.redeem(coupon_id)
