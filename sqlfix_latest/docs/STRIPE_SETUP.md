# Stripe setup (add your keys, then verify in 10 minutes)

The billing code is complete and was tested against a local Stripe stand-in (checkout session creation, signed webhooks, plan changes
from the Stripe **price id**, replay safety, portal session). It has **not** been run against real Stripe, because no keys were available.

## 1. Create products and prices (Stripe dashboard, test mode first)
Create three products (Starter, Growth, Agency), each with a monthly and an annual recurring price. Copy the six `price_...` ids.

## 2. Set environment variables (API and worker, never the browser)
```
STRIPE_SECRET_KEY=sk_test_...            # sk_live_... in production
STRIPE_WEBHOOK_SECRET=whsec_...          # from step 3
STRIPE_PRICE_STARTER_MONTHLY=price_...   STRIPE_PRICE_STARTER_ANNUAL=price_...
STRIPE_PRICE_GROWTH_MONTHLY=price_...    STRIPE_PRICE_GROWTH_ANNUAL=price_...
STRIPE_PRICE_AGENCY_MONTHLY=price_...    STRIPE_PRICE_AGENCY_ANNUAL=price_...
APP_PUBLIC_URL=https://your-portal-domain   # success/cancel/portal return URLs are built from this
```
A missing price returns a clear 503 for that plan only; a missing secret key returns 503 "Stripe payments are not configured yet" and the rest of the app keeps working.
`STRIPE_API_BASE` exists only for tests and is ignored when `ENVIRONMENT` is production or staging.

## 3. Webhook
Add an endpoint `https://<api-domain>/billing/webhook` and subscribe to: `checkout.session.completed`, `customer.subscription.created`,
`customer.subscription.updated`, `customer.subscription.deleted`, `invoice.payment_failed`, `invoice.paid`, `charge.refunded`, `charge.dispute.created`.
Copy the signing secret into `STRIPE_WEBHOOK_SECRET`. Locally: `stripe listen --forward-to localhost:8000/billing/webhook`.

## 4. Enable the customer portal
Stripe dashboard → Settings → Billing → Customer portal: allow cancel, plan switch and payment-method update.

## 5. Verify (test card 4242 4242 4242 4242)
1. Pricing → Choose Growth → Continue to secure payment → pay. Expect redirect to `/checkout?success=1`.
2. Within a few seconds the workspace plan becomes Growth (the webhook sets it from the price id, not from metadata). Check Settings → Billing.
3. Stripe CLI: `stripe trigger invoice.payment_failed` → workspace shows the payment-failed state and grace period.
4. Settings → Billing → Manage billing opens the portal; cancel there and confirm the plan drops to Free at period end.
5. Resend any event from the dashboard: the response must say `duplicate: true` and nothing changes.

## Things the owner must still decide
Tax (Stripe Tax or manual), invoice details, refund policy wording on `/terms`, and whether metered/usage billing is wanted (`STRIPE_METER_EVENT_NAME`).
