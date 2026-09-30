// create-portal-session — returns a Stripe Customer Portal URL where the
// user can cancel, update their card, or see invoices. Called by the
// PitWall app with the user's Supabase JWT in Authorization.
// Needs the Customer Portal switched on in Stripe (Settings → Billing →
// Customer portal) with cancellation allowed. Secrets: STRIPE_SECRET_KEY,
// APP_URL (where the portal's "Return" link goes).
import {
  APP_URL, callerUser, json, loadProfile, preflight, stripe,
} from "../_shared/billing.ts";

Deno.serve(async (req) => {
  const pre = preflight(req);
  if (pre) return pre;
  try {
    const user = await callerUser(req);
    if (!user) return json({ error: "not authenticated" }, 401);

    const profile = await loadProfile(user.id);
    if (!profile.stripe_customer_id) {
      return json({ error: "No subscription to manage yet." }, 404);
    }
    const session = await stripe.billingPortal.sessions.create({
      customer: profile.stripe_customer_id,
      return_url: `${APP_URL}/?billing=portal`,
    });
    return json({ url: session.url });
  } catch (e) {
    console.error("create-portal-session:", e);
    return json({ error: "Could not open subscription management — try again shortly." }, 500);
  }
});
