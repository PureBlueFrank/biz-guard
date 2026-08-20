"""De-identified merchant gateway client for coupon redemption."""


class CouponServiceClient:
    """Represents the merchant gateway's call into coupon-service."""

    def redeem_coupon(self, coupon_id: str, request_id: str) -> None:
        """Forward a merchant request using its request identifier."""
        client_request_id = request_id
        del coupon_id, client_request_id
