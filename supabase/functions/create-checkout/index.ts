// create-checkout — returns a Stripe Checkout URL for the $4.99/mo plan.
// Called by the PitWall app with the user's Supabase JWT in Authorization.
// Secrets (set with `supabase secrets set`):
//   STRIPE_SECRET_KEY  — sk_test_… (test mode until Jordan says otherwise)
//   STRIPE_PRICE_ID    — price_… for the $4.99/month subscription
//   APP_URL            — where Checkout returns, e.g. http://127.0.0.1:8777
//   TRIAL_DAYS         — optional free-trial length; unset = no trial
//
// Never lets a user hold two subscriptions: it reuses the user's single
// Stripe customer, refuses while a subscription exists (Stripe is asked
// directly, so a lagging webhook can't open the gap), and hands back an
// already-open Checkout instead of starting a second one.
import {
  APP_URL, blocksNewCheckout, callerUser, ensureCustomer, json,
  loadProfile, preflight, stripe,
} from "../_shared/billing.ts";

Deno.serve(async (req) => {
  const pre = preflight(req);
  if (pre) return pre;
  try {
    const user = await callerUser(req);
    if (!user) return json({ error: "not authenticated" }, 401);

    const customer = await ensureCustomer(await loadProfile(user.id));

    const subs = await stripe.subscriptions.list({
      customer, status: "all", limit: 100,
    });
    if (subs.data.some((s) => blocksNewCheckout(s.status))) {
      return json({
        error: "You already have a subscription. Use MANAGE SUBSCRIPTION "
          + "to update or cancel it.",
        manage: true,
      }, 409);
    }

    const open = await stripe.checkout.sessions.list({
      customer, status: "open", limit: 1,
    });
    if (open.data[0]?.url) return json({ url: open.data[0].url });

    const trialDays = Number(Deno.env.get("TRIAL_DAYS") ?? 0);
    const session = await stripe.checkout.sessions.create({
      mode: "subscription",
      customer,
      client_reference_id: user.id,
      line_items: [{ price: Deno.env.get("STRIPE_PRICE_ID")!, quantity: 1 }],
      subscription_data: {
        metadata: { user_id: user.id },
        ...(trialDays > 0 ? { trial_period_days: trialDays } : {}),
      },
      success_url: `${APP_URL}/?billing=success`,
      cancel_url: `${APP_URL}/?billing=cancelled`,
    }, {
      // A double-click (or two tabs) inside the same minute gets the
      // same session back rather than two payable checkouts.
      idempotencyKey: `pitwall-checkout-${customer}-${Math.floor(Date.now() / 60_000)}`,
    });
    return json({ url: session.url });
  } catch (e) {
    console.error("create-checkout:", e);
    return json({ error: "Could not start checkout — try again shortly." }, 500);
  }
});
