# Cloud setup — Supabase + Stripe (one-time, ~15 minutes)

The app code is fully wired for accounts, billing, and cloud session
storage. It activates the moment the keys below exist; until then it
runs in LOCAL DEV MODE (no auth, no paywall — behaves like the
original personal tool).

## 1. Supabase project (free tier)

1. https://supabase.com → New project (any name, e.g. `race-engineer`).
2. SQL Editor → paste and run `supabase/schema.sql` from this folder.
   **Existing project?** Run `supabase/migrations/20260929000000_billing_sync.sql`
   instead (adds `stripe_subscription_id`, `current_period_end`,
   `billing_synced_at`); or `supabase db push`.
3. Authentication → Providers → Email: for easiest testing, turn OFF
   "Confirm email" (otherwise signups must click an email link first).
4. Project Settings → API: copy the **Project URL** and **anon public**
   key.

## 2. Stripe (test mode)

1. https://stripe.com → create account, stay in **Test mode**.
2. Products → Add product: "Race Engineer", recurring **$4.99/month**.
   Copy the **price id** (`price_…`).
3. Developers → API keys: copy the **Secret key** (`sk_test_…`).
4. Settings → Billing → **Customer portal**: activate it (test mode),
   allow **cancel subscriptions** with "at end of billing period", and
   allow payment-method updates. This is where MANAGE SUBSCRIPTION sends
   users.

## 3. Licence signing keys (offline grace)

The app caches a server-signed licence so it can start offline for up to
7 days. Generate the key pair once (needs Deno, `brew install deno`):

```sh
deno run scripts/gen_license_keys.ts
```

It prints `LICENSE_SIGNING_KEY=…` (secret: it goes to Supabase below,
never into git or the app) and `LICENSE_PUBLIC_KEY=…` (goes in `.env`
and in the `LICENSE_PUBLIC_KEY` GitHub secret for the Windows build).
Without the public key, builds still work but must reach the server
to start.

## 4. Edge functions (checkout, portal, licence, webhook)

Requires the Supabase CLI (`brew install supabase/tap/supabase`), then
from this `race-engineer/` folder:

```sh
supabase login
supabase link --project-ref <your-project-ref>
supabase secrets set STRIPE_SECRET_KEY=sk_test_... \
                     STRIPE_PRICE_ID=price_... \
                     APP_URL=http://127.0.0.1:8777 \
                     LICENSE_SIGNING_KEY=<from step 3>
# optional free trial: supabase secrets set TRIAL_DAYS=7
supabase functions deploy create-checkout
supabase functions deploy create-portal-session
supabase functions deploy issue-license
supabase functions deploy stripe-webhook --no-verify-jwt
```

Then in Stripe: Developers → Webhooks → Add endpoint:
- URL: `https://<project-ref>.supabase.co/functions/v1/stripe-webhook`
- Events: `checkout.session.completed`,
  `customer.subscription.created`, `customer.subscription.updated`,
  `customer.subscription.deleted`, `customer.subscription.paused`,
  `customer.subscription.resumed`, `invoice.paid`,
  `invoice.payment_failed`
- Copy the **Signing secret** (`whsec_…`) and:

```sh
supabase secrets set STRIPE_WEBHOOK_SECRET=whsec_...
```

How billing stays correct:
- **One customer, one subscription.** `create-checkout` creates one Stripe
  customer per user and reuses it. It asks Stripe directly and refuses
  (HTTP 409, "use MANAGE SUBSCRIPTION") while a subscription exists. It
  hands back an already-open Checkout instead of starting a second one.
- **Webhook order doesn't matter.** Each event only says which customer
  changed. The webhook re-reads that customer's subscriptions from Stripe
  and writes the current state, so late, repeated or out-of-order events
  all converge. Database errors return 500, so Stripe retries (for up to
  3 days).
- The pure rules have tests: `deno test supabase/functions/_shared`.

## 5. App config

Create `race-engineer/.env`:

```
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_ANON_KEY=<anon public key>
LICENSE_PUBLIC_KEY=<from step 3>
```

Restart `main.py`. The dashboard now opens on the login screen; the
engine stays silent until a logged-in, subscribed user is present.

## 6. Verify end-to-end (Stripe test card)

1. Dashboard → SIGN UP with any email/password → LOG IN. Before paying,
   start a session in the game: the box under the paywall should say
   CONSOLE CONNECTED.
2. SUBSCRIBE WITH STRIPE → test card `4242 4242 4242 4242`, any future
   expiry, any CVC.
3. Back in the dashboard: status flips to ACTIVE within seconds
   (webhook), the gate drops, and the app works.
4. Restart the app: still logged in (refresh token persisted in
   `auth.json`), and the startup license check logs
   `license check: active (<email>)`. `license.json` now holds a signed
   token.
5. SUBSCRIBE again from a second browser tab: refused with "use MANAGE
   SUBSCRIPTION", and no second subscription appears in Stripe.
6. MANAGE SUBSCRIPTION → cancel in the portal. It stays ACTIVE until
   the period ends (Stripe keeps the subscription active until then).
   To see the paywall now, cancel immediately in Stripe's dashboard and
   press REFRESH STATUS (or restart).

## Notes

- Everything is **Stripe test mode**. Going live = new live keys +
  live webhook endpoint + Jordan's explicit go-ahead. Nothing else
  changes.
- **License model**: the subscription is checked once at startup. When
  it's active (or `past_due` while Stripe retries the card), the app
  fetches an Ed25519-signed licence from `issue-license` and caches it
  in `license.json` (chmod 600). If the server is unreachable, the app
  trusts the cache only if the signature verifies against
  `LICENSE_PUBLIC_KEY`, and only for 7 days from issue
  (`LICENSE_GRACE_DAYS` in cloud.py). A licence dated in the future
  (clock wound back) is refused. Editing the file breaks the signature.
  Entitlement never changes mid-session on a timer, only at startup and
  on user auth actions.
- **Login never touches the browser**: the dashboard posts
  email/password to the local app, which calls Supabase Auth itself.
  The page loads no third-party script, so login works without a CDN,
  and its Content-Security-Policy blocks any other origin.
- `auth.json` (local login token) is per-machine and chmod 600. Delete
  it (and `license.json`) to force a fresh login.
- Session data stays local in `driver_db.json` — nothing is uploaded.
  The `sessions`/`laps` tables in the schema are currently unused
  (kept for a possible future opt-in sync).
