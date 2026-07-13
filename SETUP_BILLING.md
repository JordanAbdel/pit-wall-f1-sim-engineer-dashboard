# Cloud setup — Supabase + Stripe (one-time, ~15 minutes)

The app code is fully wired for accounts, billing, and cloud session
storage. It activates the moment the keys below exist; until then it
runs in LOCAL DEV MODE (no auth, no paywall — behaves like the
original personal tool).

## 1. Supabase project (free tier)

1. https://supabase.com → New project (any name, e.g. `race-engineer`).
2. SQL Editor → paste and run `supabase/schema.sql` from this folder.
3. Authentication → Providers → Email: for easiest testing, turn OFF
   "Confirm email" (otherwise signups must click an email link first).
4. Project Settings → API: copy the **Project URL** and **anon public**
   key.

## 2. Stripe (test mode)

1. https://stripe.com → create account, stay in **Test mode**.
2. Products → Add product: "Race Engineer", recurring **$4.99/month**.
   Copy the **price id** (`price_…`).
3. Developers → API keys: copy the **Secret key** (`sk_test_…`).

## 3. Edge functions (checkout + webhook)

Requires the Supabase CLI (`brew install supabase/tap/supabase`), then
from this `race-engineer/` folder:

```sh
supabase login
supabase link --project-ref <your-project-ref>
supabase secrets set STRIPE_SECRET_KEY=sk_test_... \
                     STRIPE_PRICE_ID=price_... \
                     APP_URL=http://127.0.0.1:8777
supabase functions deploy create-checkout
supabase functions deploy stripe-webhook --no-verify-jwt
```

Then in Stripe: Developers → Webhooks → Add endpoint:
- URL: `https://<project-ref>.supabase.co/functions/v1/stripe-webhook`
- Events: `checkout.session.completed`,
  `customer.subscription.updated`, `customer.subscription.deleted`,
  `invoice.payment_failed`
- Copy the **Signing secret** (`whsec_…`) and:

```sh
supabase secrets set STRIPE_WEBHOOK_SECRET=whsec_...
```

## 4. App config

Create `race-engineer/.env`:

```
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_ANON_KEY=<anon public key>
```

Restart `main.py`. The dashboard now opens on the login screen; the
engine stays silent until a logged-in, subscribed user is present.

## 5. Verify end-to-end (Stripe test card)

1. Dashboard → SIGN UP with any email/password → LOG IN.
2. SUBSCRIBE WITH STRIPE → test card `4242 4242 4242 4242`, any future
   expiry, any CVC.
3. Back in the dashboard: status flips to ACTIVE within seconds
   (webhook), the gate drops, and the app works.
4. Run a session; check Supabase Table Editor → `sessions` and `laps`
   rows appeared for your user. Restart the app: still logged in
   (refresh token persisted in `auth.json`).
5. Cancel the subscription in Stripe's test dashboard → status flips to
   `canceled` and the paywall returns.

## Notes

- Everything is **Stripe test mode**. Going live = new live keys +
  live webhook endpoint + Jordan's explicit go-ahead. Nothing else
  changes.
- `auth.json` (local login token) is per-machine and chmod 600. Delete
  it to force a fresh login.
- Local `driver_db.json` keeps working as the offline cache; cloud
  upload happens in addition, per session, when logged in.
