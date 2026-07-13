// create-checkout — returns a Stripe Checkout URL for the $4.99/mo plan.
// Called by the dashboard with the user's Supabase JWT in Authorization.
// Secrets (set with `supabase secrets set`):
//   STRIPE_SECRET_KEY  — sk_test_… (test mode until Jordan says otherwise)
//   STRIPE_PRICE_ID    — price_… for the $4.99/month subscription
//   APP_URL            — where Checkout returns, e.g. http://127.0.0.1:8777
import Stripe from "npm:stripe@14";
import { createClient } from "npm:@supabase/supabase-js@2";

const stripe = new Stripe(Deno.env.get("STRIPE_SECRET_KEY")!, {
  apiVersion: "2023-10-16",
});

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type",
};

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  try {
    const supabase = createClient(
      Deno.env.get("SUPABASE_URL")!,
      Deno.env.get("SUPABASE_ANON_KEY")!,
      { global: { headers: { Authorization: req.headers.get("Authorization")! } } },
    );
    const { data: { user }, error } = await supabase.auth.getUser();
    if (error || !user) {
      return new Response(JSON.stringify({ error: "not authenticated" }), {
        status: 401, headers: { ...cors, "Content-Type": "application/json" },
      });
    }
    const appUrl = Deno.env.get("APP_URL") ?? "http://127.0.0.1:8777";
    const session = await stripe.checkout.sessions.create({
      mode: "subscription",
      line_items: [{ price: Deno.env.get("STRIPE_PRICE_ID")!, quantity: 1 }],
      client_reference_id: user.id,
      customer_email: user.email,
      success_url: `${appUrl}/?billing=success`,
      cancel_url: `${appUrl}/?billing=cancelled`,
    });
    return new Response(JSON.stringify({ url: session.url }), {
      headers: { ...cors, "Content-Type": "application/json" },
    });
  } catch (e) {
    console.error(e);
    return new Response(JSON.stringify({ error: String(e) }), {
      status: 500, headers: { ...cors, "Content-Type": "application/json" },
    });
  }
});
