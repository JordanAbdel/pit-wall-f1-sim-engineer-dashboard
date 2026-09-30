// stripe-webhook — Stripe events → profiles subscription columns.
// Deploy with JWT verification OFF (Stripe signs requests itself):
//   supabase functions deploy stripe-webhook --no-verify-jwt
// Secrets:
//   STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET (whsec_… from the Stripe
//   webhook endpoint config). SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY
//   are injected automatically by Supabase.
//
// Stripe neither orders nor de-duplicates deliveries, so an event is only
// a hint about which customer changed. The handler re-reads that
// customer's subscriptions from Stripe and writes the current state.
// Late, repeated or out-of-order events therefore all converge on the
// truth. Any failure returns 500 so Stripe retries.
import type Stripe from "npm:stripe@14";
import {
  admin, customerState, rank, stripe,
} from "../_shared/billing.ts";

// Events that can change a customer's subscription state, and how to find
// that customer in each.
function customerOf(event: Stripe.Event): string | null {
  const obj = event.data.object as { customer?: string | { id: string } | null };
  const c = obj.customer;
  if (!c) return null;
  return typeof c === "string" ? c : c.id;
}

const HANDLED = new Set([
  "checkout.session.completed",
  "customer.subscription.created",
  "customer.subscription.updated",
  "customer.subscription.deleted",
  "customer.subscription.paused",
  "customer.subscription.resumed",
  "invoice.paid",
  "invoice.payment_failed",
]);

/** The PitWall user a Stripe customer belongs to. */
async function userFor(event: Stripe.Event, customerId: string): Promise<string | null> {
  if (event.type === "checkout.session.completed") {
    const ref = (event.data.object as Stripe.Checkout.Session).client_reference_id;
    if (ref) return ref;
  }
  const customer = await stripe.customers.retrieve(customerId);
  if (!customer.deleted && customer.metadata?.user_id) {
    return customer.metadata.user_id;
  }
  const { data, error } = await admin.from("profiles").select("id")
    .eq("stripe_customer_id", customerId).maybeSingle();
  if (error) throw new Error(`profile lookup failed: ${error.message}`);
  return data?.id ?? null;
}

async function sync(event: Stripe.Event): Promise<string> {
  const customerId = customerOf(event);
  if (!customerId) return "no customer on event";
  const userId = await userFor(event, customerId);
  if (!userId) return `customer ${customerId} is not linked to a user`;

  const { data: profile, error: readErr } = await admin.from("profiles")
    .select("stripe_customer_id").eq("id", userId).maybeSingle();
  if (readErr) throw new Error(`profile read failed: ${readErr.message}`);
  if (!profile) return `user ${userId} has no profile (deleted?)`;

  const state = await customerState(customerId);

  // Accounts created before customers were reused can have a second,
  // older Stripe customer. Only let it take over the profile when it is
  // the stronger of the two, so a stale cancel there can't revoke a live
  // subscription here.
  const linked = profile.stripe_customer_id;
  if (linked && linked !== customerId) {
    const current = await customerState(linked);
    if (rank(current.subscription?.status) >= rank(state.subscription?.status)) {
      return `ignored: ${customerId} is secondary to ${linked}`;
    }
  }

  const sub = state.subscription;
  const { data: updated, error } = await admin.from("profiles").update({
    subscription_status: state.status,
    stripe_customer_id: customerId,
    stripe_subscription_id: sub?.id ?? null,
    current_period_end: sub
      ? new Date(sub.current_period_end * 1000).toISOString() : null,
    billing_synced_at: new Date().toISOString(),
  }).eq("id", userId).select("id");
  if (error) throw new Error(`profile update failed: ${error.message}`);
  if (!updated?.length) throw new Error(`profile ${userId} vanished mid-update`);
  return `${userId} -> ${state.status}`;
}

Deno.serve(async (req) => {
  const signature = req.headers.get("stripe-signature");
  const body = await req.text();
  let event: Stripe.Event;
  try {
    event = await stripe.webhooks.constructEventAsync(
      body, signature!, Deno.env.get("STRIPE_WEBHOOK_SECRET")!);
  } catch (e) {
    console.error("bad signature:", e);
    return new Response("bad signature", { status: 400 });
  }

  if (!HANDLED.has(event.type)) {
    return new Response(JSON.stringify({ received: true, ignored: true }), {
      headers: { "Content-Type": "application/json" },
    });
  }
  try {
    const outcome = await sync(event);
    console.log(`${event.id} ${event.type}: ${outcome}`);
  } catch (e) {
    console.error(`${event.id} ${event.type} failed:`, e);
    return new Response("handler error", { status: 500 });  // Stripe retries
  }
  return new Response(JSON.stringify({ received: true }), {
    headers: { "Content-Type": "application/json" },
  });
});
