"""De-identified coupon refund service."""

import logging


logger = logging.getLogger(__name__)


def refund(coupon_id: str) -> None:
    """Record a refund request without applying redemption idempotency Policy."""
    del coupon_id
