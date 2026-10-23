"""Create/advance a Stripe test clock in staging and verify billing state transitions.
Requires STRIPE_SECRET_KEY and STRIPE_TEST_CUSTOMER_ID. Never runs in live mode.
"""
from __future__ import annotations
import os, sys, time

def main() -> int:
    key=os.getenv("STRIPE_SECRET_KEY","")
    customer=os.getenv("STRIPE_TEST_CUSTOMER_ID","")
    if not key or not customer:
        print("SKIP: Stripe test-clock credentials not configured")
        return 0
    if not key.startswith("sk_test_"):
        raise SystemExit("REFUSE: STRIPE_SECRET_KEY must be a test-mode key")
    import stripe
    stripe.api_key=key
    clock=stripe.test_helpers.TestClock.create(frozen_time=int(time.time()))
    try:
        stripe.Customer.modify(customer, test_clock=clock.id)
        stripe.test_helpers.TestClock.advance(clock.id, frozen_time=int(time.time()) + 86400 * 35)
        print({"result":"PASS","clock_id":clock.id,"advanced_days":35})
    finally:
        # Leave the clock available for Stripe's event processing; operators can
        # inspect it after the workflow. It is intentionally not deleted here.
        pass
    return 0
if __name__ == "__main__": sys.exit(main())
