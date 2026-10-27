# Cloud setup — Cloudflare + Stripe (one-time, ~15 minutes)

The licence API is a Cloudflare Worker with a D1 database, in
`cloudflare/`. It's free and, unlike Supabase's free tier, never pauses.
It answers the same paths the old Supabase project did, so the app only
needs its URL. Until `PITWALL_API_URL` is set the app runs in LOCAL DEV
MODE (no auth, no paywall — the original personal tool).

All commands run from `race-engineer/cloudflare/` unless noted. Node is
the only prerequisite; `npm install` once fetches wrangler.

## 1. Cloudflare account and database

1. Sign up at https://dash.cloudflare.com (free plan).
2. Log wrangler in — it opens the browser to approve access:

   ```sh
   npx wrangler login
   ```

3. Create the database, and paste the `database_id` it prints into
   `wrangler.toml` (replacing `REPLACE_WITH_ID_FROM_wrangler_d1_create`):

   ```sh
   npx wrangler d1 create pitwall
   npm run db:init
   ```

## 2. Stripe (test mode)

1. https://stripe.com → stay in **Test mode**.
2. Products → Add product: "PitWall", recurring **$4.99/month**. Put the
   **price id** (`price_…`) in `wrangler.toml` as `STRIPE_PRICE_ID`.
3. Developers → API keys: you'll need the **Secret key** (`sk_test_…`)
   in step 4.
4. Settings → Billing → **Customer portal**: activate it, allow
   **cancel subscriptions** ("at end of billing period") and
   payment-method updates. MANAGE SUBSCRIPTION sends users here.

## 3. Licence signing keys (offline grace)

The app caches a server-signed licence so it can start offline for up to
7 days. From `race-engineer/`:

```sh
node scripts/gen_license_keys.mjs
```

It prints `LICENSE_SIGNING_KEY=…` (secret — step 4, never into git or
the app) and `LICENSE_PUBLIC_KEY=…` (goes in `.env`, step 6).

## 4. Secrets

Each command prompts for the value, so it never lands in your shell
history or in this repo:

```sh
npx wrangler secret put STRIPE_SECRET_KEY      # sk_test_…
npx wrangler secret put LICENSE_SIGNING_KEY    # from step 3
npx wrangler secret put AUTH_SECRET            # any long random string:
                                               #   openssl rand -base64 48
```

Optional free trial: set `TRIAL_DAYS = "7"` in `wrangler.toml`.

## 5. Deploy and connect the webhook

```sh
npm run deploy
```

It prints the Worker's URL, e.g.
`https://pitwall-api.<your-subdomain>.workers.dev`. Opening it shows
`{"ok":true,"service":"pitwall-api"}`.

In Stripe: Developers → Webhooks → Add endpoint:
- URL: `https://pitwall-api.<your-subdomain>.workers.dev/functions/v1/stripe-webhook`
- Events: `checkout.session.completed`, `customer.subscription.created`,
  `customer.subscription.updated`, `customer.subscription.deleted`,
  `customer.subscription.paused`, `customer.subscription.resumed`,
  `invoice.paid`, `invoice.payment_failed`
- Copy the **Signing secret** (`whsec_…`), then:

```sh
npx wrangler secret put STRIPE_WEBHOOK_SECRET
```

## 6. App config

`race-engineer/.env` (replace the old `SUPABASE_*` lines):

```
PITWALL_API_URL=https://pitwall-api.<your-subdomain>.workers.dev
LICENSE_PUBLIC_KEY=<from step 3>
```

Quit PitWall and open it again. The dashboard opens on the sign-in
screen; the engine stays silent until a signed-in, subscribed user is
present.

## 7. Verify (Stripe test card)

1. Dashboard → SIGN UP → you're signed in straight away (no confirmation
   email).
2. SUBSCRIBE WITH STRIPE → card `4242 4242 4242 4242`, any future
   expiry, any CVC.
3. Back in the dashboard the status flips to ACTIVE within seconds.
4. Restart the app: still signed in, and the log says
   `license check: active (<email>)`.
5. MANAGE SUBSCRIPTION → cancel; it stays ACTIVE until the period ends.

`npx wrangler tail` streams the Worker's logs live (webhook outcomes,
errors) while you test.

## Testing without any accounts

From `race-engineer/`:

```sh
build-venv/bin/python cloudflare/test/e2e.py
```

Runs the Worker locally under `wrangler dev` with a fake Stripe and
throwaway keys, then checks sign-up, sign-in, the lockout, token
rotation, checkout, signed webhooks, cancellation and licences — and
finally drives the app's own `cloud.Cloud` through sign-up → subscribe →
relaunch. Nothing leaves the machine.

## How it works

- **Accounts**: email + password, hashed with PBKDF2-SHA256 (100k
  rounds, per-user salt). Ten wrong passwords for one email lock it for
  15 minutes. Unknown emails take as long as known ones, so the sign-in
  form doesn't reveal who has an account.
- **Tokens**: a signed access token lasting an hour, and a refresh token
  (60 days) kept by the app in `auth.json`. Refresh tokens are stored
  hashed and replaced on every use, so a copied, already-used one is
  worthless.
- **One customer, one subscription.** Checkout reuses the user's single
  Stripe customer, asks Stripe directly and refuses (409, "use MANAGE
  SUBSCRIPTION") while a subscription exists, and hands back an
  already-open Checkout instead of a second one.
- **Webhook order doesn't matter.** Each event only says which customer
  changed; the Worker re-reads that customer's subscriptions from Stripe
  and writes the current state. Signatures are checked, and events more
  than 5 minutes old are refused. Any failure returns 500 so Stripe
  retries.
- **Licence**: on a live check showing `active` (or `past_due` while
  Stripe retries the card) the app fetches an Ed25519-signed licence and
  caches it in `license.json`. Offline, it's trusted only if the
  signature verifies against `LICENSE_PUBLIC_KEY`, for 7 days from
  issue.
- **No password reset yet** — the Supabase version had none in the app
  either. Sending reset emails needs an email provider; until then a
  forgotten password is a support request.
- Session data stays local in `driver_db.json` — nothing is uploaded.
- Everything is **Stripe test mode** until Jordan says otherwise. Going
  live = live keys + a live webhook endpoint; nothing else changes.

The old Supabase project (`supabase/`) is no longer used and can be
deleted once the Worker is live.
