// stripe-webhook — Stripe events → profiles.subscription_status.
// Deploy with JWT verification OFF (Stripe signs requests itself):
//   supabase functions deploy stripe-webhook --no-verify-jwt
// Secrets:
//   STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET (whsec_… from the Stripe
//   webhook endpoint config). SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY
//   are injected automatically by Supabase.
import Stripe from "npm:stripe@14";
import { createClient } from "npm:@supabase/supabase-js@2";

const stripe = new Stripe(Deno.env.get("STRIPE_SECRET_KEY")!, {
  apiVersion: "2023-10-16",
});
const admin = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
);

function mapStatus(subStatus: string): string {
  if (subStatus === "active" || subStatus === "trialing") return "active";
  if (subStatus === "past_due") return "past_due";
  return "canceled"; // canceled, unpaid, incomplete_expired, paused
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

  try {
    if (event.type === "checkout.session.completed") {
      const session = event.data.object as Stripe.Checkout.Session;
      const userId = session.client_reference_id;
      if (userId) {
        await admin.from("profiles").update({
          subscription_status: "active",
          stripe_customer_id: String(session.customer),
        }).eq("id", userId);
      }
    } else if (event.type === "customer.subscription.updated"
        || event.type === "customer.subscription.deleted") {
      const sub = event.data.object as Stripe.Subscription;
      const status = event.type.endsWith("deleted")
        ? "canceled" : mapStatus(sub.status);
      await admin.from("profiles").update({ subscription_status: status })
        .eq("stripe_customer_id", String(sub.customer));
    } else if (event.type === "invoice.payment_failed") {
      const invoice = event.data.object as Stripe.Invoice;
      await admin.from("profiles").update({ subscription_status: "past_due" })
        .eq("stripe_customer_id", String(invoice.customer));
    }
  } catch (e) {
    console.error("handler error:", e);
    return new Response("handler error", { status: 500 });
  }
  return new Response(JSON.stringify({ received: true }), {
    headers: { "Content-Type": "application/json" },
  });
});
