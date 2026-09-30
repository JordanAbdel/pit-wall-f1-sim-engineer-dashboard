-- Billing sync: the webhook now mirrors the customer's live Stripe state
-- (see supabase/functions/stripe-webhook). These columns are written by
-- the service role only; the column-level grant in the initial schema
-- still limits clients to updating display_name.
alter table public.profiles
  add column if not exists stripe_subscription_id text,
  add column if not exists current_period_end timestamptz,
  add column if not exists billing_synced_at timestamptz;
